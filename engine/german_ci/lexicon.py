"""Word-level lookups: frequency, lemma resolution, and English glosses.

Three layers, consulted in order:

  1. overrides.json  hand-authored glosses for auxiliaries and modals, whose
                     Wiktionary entries are grammatical prose rather than
                     anything a learner can read off a card.
  2. lexicon.sqlite  form -> lemma -> gloss/rank/CEFR, built by
                     scripts/build_lexicon.py.
  3. freq table      raw form-level counts, which still work for a word the
                     dictionary never heard of.

A token that has a frequency entry but *no* dictionary entry is almost always
a proper noun or an English word leaking through the OpenSubtitles-derived
list (`truck`, `davina`). Those are flagged, not scored as rare vocabulary --
otherwise every scene with a character's name looks like advanced German.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DEFAULT_DB = os.path.join(DATA, "lexicon.sqlite")
DEFAULT_OVERRIDES = os.path.join(DATA, "overrides.json")

# Unicode-aware German word pattern: letters plus umlauts/ß, with internal
# hyphens and apostrophes kept ("Ess-Störung", "geht's").
WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüßÀ-ÿ]+(?:['’\-][A-Za-zÄÖÜäöüßÀ-ÿ]+)*")

# Wiktionary stub senses that redirect rather than define, e.g.
# "archaic form of Schmied", "alternative spelling of Foto".
FORM_OF_GLOSS = re.compile(
    r"^\W*(?:\w+\s+){0,2}(?:form|spelling|misspelling)\s+of\b", re.IGNORECASE
)

# How much a proper-name reading is discounted against a common noun of the
# same spelling. Small on purpose: `Bauer` should read as "farmer", but a
# surname must still win when no everyday noun competes for the spelling.
NAME_PENALTY = 0.95

CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]
CEFR_BANDS = [(500, "A1"), (1500, "A2"), (3500, "B1"), (8000, "B2"), (16000, "C1")]


def cefr_for_rank(rank: int | None) -> str:
    if rank is None:
        return "C2"
    for threshold, band in CEFR_BANDS:
        if rank <= threshold:
            return band
    return "C2"


def tokenize(text: str) -> list[str]:
    """Split a sentence into word tokens, dropping punctuation and digits."""
    return WORD_RE.findall(text)


@dataclass
class Entry:
    """What we know about one surface form."""

    surface: str
    lemma: str
    pos: str = ""
    glosses: list[str] = field(default_factory=list)
    rank: int | None = None
    zipf: float = 0.0
    cefr: str = "C2"
    #: "override" | "lexicon" | "frequency" | "unknown"
    source: str = "unknown"
    #: True when we have frequency but no German dictionary entry.
    foreign_or_name: bool = False

    @property
    def gloss(self) -> str:
        return self.glosses[0] if self.glosses else ""

    @property
    def known_word(self) -> bool:
        """Is this a real German vocabulary item we can score and teach?"""
        return self.source in ("override", "lexicon") and not self.foreign_or_name


class Lexicon:
    """Read-only access to the built lexicon."""

    def __init__(self, db_path: str = DEFAULT_DB, overrides_path: str = DEFAULT_OVERRIDES):
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"Lexicon not built: {db_path}\n"
                "Run: python3 scripts/build_lexicon.py"
            )
        self.db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.overrides: dict[str, dict] = {}
        if os.path.exists(overrides_path):
            with open(overrides_path, encoding="utf-8") as handle:
                self.overrides = json.load(handle)
        row = self.db.execute(
            "SELECT value FROM meta WHERE key = 'total_tokens'"
        ).fetchone()
        self.total_tokens = int(row["value"]) if row else 0

    def close(self) -> None:
        self.db.close()

    # -- internals ---------------------------------------------------------

    def _frequency(self, form: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT count, rank, zipf FROM freq WHERE word = ?", (form,)
        ).fetchone()

    def _candidates(self, form: str) -> list[sqlite3.Row]:
        return self.db.execute(
            """
            SELECT l.lemma, l.pos, l.glosses, l.rank, l.zipf, l.cefr, l.count,
                   f.tags
            FROM form f JOIN lemma l ON l.id = f.lemma_id
            WHERE f.form = ?
            ORDER BY l.count DESC
            """,
            (form,),
        ).fetchall()

    @lru_cache(maxsize=100_000)
    def _resolve(self, surface: str, capitalized: bool, sentence_initial: bool) -> Entry:
        form = surface.lower()

        # Single letters are dialogue speaker labels ("A: ...", "B: ..."), list
        # bullets or stray initials -- never German vocabulary worth a card.
        if len(form) < 2:
            return Entry(
                surface=surface, lemma=surface, source="unknown",
                foreign_or_name=True,
            )

        override = self.overrides.get(form)
        if override:
            frequency = self._frequency(form)
            rank = frequency["rank"] if frequency else None
            return Entry(
                surface=surface,
                lemma=override["lemma"],
                pos=override.get("pos", ""),
                glosses=[override["gloss"]],
                rank=rank,
                zipf=frequency["zipf"] if frequency else 0.0,
                cefr=cefr_for_rank(rank),
                source="override",
            )

        candidates = self._candidates(form)
        if not candidates:
            # German writes ß where some subtitles write ss, and vice versa.
            for variant in (form.replace("ß", "ss"), form.replace("ss", "ß")):
                if variant != form:
                    candidates = self._candidates(variant)
                    if candidates:
                        break

        if candidates:
            def substantive(candidate) -> bool:
                """Does this entry define the word, or just point elsewhere?

                Wiktionary carries stub senses like "archaic form of Schmied",
                which are not real meanings. Telling them apart from genuine
                definitions is what separates the surname `Schmidt` (whose only
                noun sense is such a stub) from `Bauer`, `Koch`, `Stein` and
                `Berg` -- everyday nouns that merely happen to be surnames too,
                and must stay mineable as vocabulary.
                """
                glosses = json.loads(candidate["glosses"] or "[]")
                return any(not FORM_OF_GLOSS.search(gloss) for gloss in glosses)

            def preference(candidate) -> tuple:
                # 1. An exact headword match beats an inflected one: `Essen`
                #    is the meal, not the dative plural of `Esse` (a
                #    chimney-hood). Compared case-sensitively, because German
                #    capitalizes nouns -- lowercase `freier` cannot be the noun
                #    `Freier`, so it should still resolve to the adjective.
                #
                #    At the start of a sentence the capital letter carries no
                #    information, so match against the lowercased surface
                #    instead. Otherwise "Ich sehe..." resolves to the noun
                #    `Ich` (Freud's ego) rather than the pronoun, and every
                #    sentence-initial word with a noun homograph looks unknown.
                #    This has to be a hard tier, not a weight. Wiktionary
                #    presents the German personal pronouns as one combined
                #    table, so `ich`, `du` and `er` are all listed as forms of
                #    `ihr` -- and that lemma's pooled count buries each of them
                #    unless being a headword outranks raw frequency outright.
                if sentence_initial:
                    exact = candidate["lemma"] == surface.lower()
                else:
                    exact = candidate["lemma"] == surface
                # 2. German capitalizes every noun, so a capitalized token in
                #    mid-sentence is a noun or a name -- near enough always.
                #    That makes it a hard filter, not a nudge: `Freier` is the
                #    noun even though the adjective `frei` is sixteen times
                #    commoner, and `Koch`/`Essen` are nouns rather than the
                #    verbs `kochen`/`essen`.
                nominal = capitalized and candidate["pos"] in ("noun", "name")

                # 3. Frequency settles what is left, discounting proper names
                #    so an everyday noun of the same spelling wins (`Bauer`
                #    the farmer over `Bauer` the surname) without a name ever
                #    being ruled out -- `Schmidt` and `Berlin` still resolve
                #    as names, because no ordinary noun competes for them.
                score = candidate["count"] or 0
                if candidate["pos"] == "name":
                    score *= NAME_PENALTY
                return (exact, substantive(candidate), nominal, score)

            chosen = max(candidates, key=preference)

            frequency = self._frequency(form)
            return Entry(
                surface=surface,
                lemma=chosen["lemma"],
                pos=chosen["pos"] or "",
                glosses=json.loads(chosen["glosses"] or "[]"),
                rank=chosen["rank"],
                zipf=chosen["zipf"] or (frequency["zipf"] if frequency else 0.0),
                cefr=chosen["cefr"] or cefr_for_rank(chosen["rank"]),
                source="lexicon",
                # Wiktionary carries entries for names, so `Schmidt` and
                # `Berlin` resolve happily -- but a surname is not vocabulary
                # worth a flashcard, and a cast list would otherwise dominate
                # every study set. Flag them out of scoring and targeting.
                foreign_or_name=(chosen["pos"] == "name"),
            )

        frequency = self._frequency(form)
        if frequency:
            # Frequent but absent from the German dictionary -> a name or an
            # English word that leaked into the subtitle corpus.
            return Entry(
                surface=surface,
                lemma=surface,
                rank=frequency["rank"],
                zipf=frequency["zipf"],
                cefr=cefr_for_rank(frequency["rank"]),
                source="frequency",
                foreign_or_name=True,
            )

        return Entry(
            surface=surface,
            lemma=surface,
            source="unknown",
            foreign_or_name=capitalized,
        )

    # -- public API --------------------------------------------------------

    def lookup(self, surface: str, sentence_initial: bool = False) -> Entry:
        """Resolve one surface form.

        `sentence_initial` suppresses the capitalization heuristic, since the
        first word of a sentence is capitalized regardless of part of speech.
        """
        capitalized = bool(surface[:1].isupper()) and not sentence_initial
        return self._resolve(surface, capitalized, sentence_initial)

    def analyze_tokens(self, text: str) -> list[Entry]:
        """Resolve every token in a sentence, in order."""
        return [
            self.lookup(token, sentence_initial=(position == 0))
            for position, token in enumerate(tokenize(text))
        ]

    def top_lemmas(self, limit: int) -> list[str]:
        """The `limit` most frequent lemmas -- used to seed a known-word set.

        DISTINCT matters: `der` has six dictionary entries across its article
        and pronoun senses, and without it a seed of 500 would be spent on a
        few hundred repeated function words.
        """
        rows = self.db.execute(
            "SELECT DISTINCT lemma, rank FROM lemma WHERE rank IS NOT NULL "
            "ORDER BY rank ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [row["lemma"] for row in rows]

    def lemma_info(self, lemma: str) -> Entry | None:
        row = self.db.execute(
            "SELECT lemma, pos, glosses, rank, zipf, cefr FROM lemma "
            "WHERE lemma = ? ORDER BY count DESC LIMIT 1",
            (lemma,),
        ).fetchone()
        if not row:
            return None
        return Entry(
            surface=lemma,
            lemma=row["lemma"],
            pos=row["pos"] or "",
            glosses=json.loads(row["glosses"] or "[]"),
            rank=row["rank"],
            zipf=row["zipf"] or 0.0,
            cefr=row["cefr"] or cefr_for_rank(row["rank"]),
            source="lexicon",
        )

    def stats(self) -> dict:
        counts = {
            "lemmas": self.db.execute("SELECT COUNT(*) FROM lemma").fetchone()[0],
            "forms": self.db.execute("SELECT COUNT(*) FROM form").fetchone()[0],
            "frequency_entries": self.db.execute(
                "SELECT COUNT(*) FROM freq"
            ).fetchone()[0],
            "overrides": len(self.overrides),
        }
        return counts
