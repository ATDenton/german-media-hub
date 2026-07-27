"""Score sentence difficulty and measure how comprehensible a source is.

Difficulty is driven mostly by the *rarest* word in a sentence, because that
is what actually stops a learner -- a twenty-word sentence of common vocabulary
reads easily, while a six-word sentence containing one obscure noun does not.
Length and clause structure are secondary terms.

Proper nouns and foreign words are excluded from every rarity calculation.
They are unpredictably rare by construction, and counting them would make any
scene containing a character's name look like advanced German.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .lexicon import CEFR_ORDER, Entry, Lexicon
from .subtitles import Sentence

# The Zipf value of a word that is effectively universal ("ich", "und").
# Used as the ceiling when converting frequency into a 0..1 rarity term.
MAX_ZIPF = 7.0

# Sentence length past which extra words stop adding much difficulty.
LENGTH_SATURATION = 25

# Subordinating conjunctions and relative pronouns push the finite verb to the
# end of the clause, which is a genuine comprehension hurdle in German.
SUBORDINATORS = {
    "dass", "weil", "wenn", "ob", "obwohl", "damit", "während", "bevor",
    "nachdem", "als", "seit", "seitdem", "falls", "sobald", "solange", "bis",
    "indem", "sodass", "wobei", "wenngleich", "da", "zumal", "sofern",
    "anstatt", "ohne", "außer", "welcher", "welche", "welches", "deren",
    "dessen", "worauf", "wodurch", "womit",
}


@dataclass
class ScoredSentence:
    """A sentence with everything the selector and the UI need."""

    sentence: Sentence
    entries: list[Entry]
    difficulty: float
    mean_zipf: float
    min_zipf: float
    cefr: str
    token_count: int
    content_count: int
    unknown_lemmas: list[str]
    complexity: int
    translation: str = ""
    #: filled in by select.py once a target word is chosen
    target: str = ""
    extras: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.sentence.text

    @property
    def unknown_count(self) -> int:
        return len(self.unknown_lemmas)

    @property
    def unknown_ratio(self) -> float:
        if not self.content_count:
            return 0.0
        return self.unknown_count / self.content_count

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "translation": self.translation,
            "start": round(self.sentence.start, 3),
            "end": round(self.sentence.end, 3),
            "timestamp": self.sentence.timestamp(),
            "difficulty": round(self.difficulty, 1),
            "cefr": self.cefr,
            "mean_zipf": round(self.mean_zipf, 2),
            "min_zipf": round(self.min_zipf, 2),
            "tokens": self.token_count,
            "unknown": self.unknown_lemmas,
            "target": self.target,
            "words": [
                {
                    "surface": entry.surface,
                    "lemma": entry.lemma,
                    "pos": entry.pos,
                    "gloss": entry.gloss,
                    "cefr": entry.cefr,
                    "rank": entry.rank,
                    "scoreable": entry.known_word,
                }
                for entry in self.entries
            ],
            **self.extras,
        }


    @classmethod
    def from_dict(cls, raw: dict) -> "ScoredSentence":
        """Rebuild from `to_dict` output.

        Lets `analyze` and `export` be separate commands: the study set is
        saved once, reviewed, edited, and exported later without re-scoring.
        """
        entries = [
            Entry(
                surface=word["surface"],
                lemma=word["lemma"],
                pos=word.get("pos", ""),
                glosses=[word["gloss"]] if word.get("gloss") else [],
                rank=word.get("rank"),
                cefr=word.get("cefr", "C2"),
                source="lexicon" if word.get("scoreable") else "frequency",
                foreign_or_name=not word.get("scoreable", True),
            )
            for word in raw.get("words", [])
        ]
        known = {"text", "translation", "start", "end", "timestamp", "difficulty",
                 "cefr", "mean_zipf", "min_zipf", "tokens", "unknown", "target",
                 "words"}
        item = cls(
            sentence=Sentence(
                text=raw["text"], start=raw.get("start", 0.0), end=raw.get("end", 0.0)
            ),
            entries=entries,
            difficulty=raw.get("difficulty", 0.0),
            mean_zipf=raw.get("mean_zipf", 0.0),
            min_zipf=raw.get("min_zipf", 0.0),
            cefr=raw.get("cefr", "C2"),
            token_count=raw.get("tokens", len(entries)),
            content_count=sum(1 for e in entries if e.known_word),
            unknown_lemmas=list(raw.get("unknown", [])),
            complexity=0,
            translation=raw.get("translation", ""),
            target=raw.get("target", ""),
        )
        item.extras = {k: v for k, v in raw.items() if k not in known}
        return item


def _clause_complexity(text: str, entries: list[Entry]) -> int:
    """Count structural hurdles: subordinate clauses and comma-joined parts."""
    lowered = {entry.surface.lower() for entry in entries}
    subordinate = len(lowered & SUBORDINATORS)
    commas = text.count(",")
    # A comma in German usually introduces a clause rather than a list pause,
    # so it is a reasonable proxy on its own.
    return subordinate + commas


def _rarity(zipf_value: float) -> float:
    """Convert a Zipf value into 0 (universal) .. 1 (vanishingly rare)."""
    return min(1.0, max(0.0, (MAX_ZIPF - zipf_value) / MAX_ZIPF))


def score_sentence(
    sentence: Sentence,
    lexicon: Lexicon,
    profile=None,
    translation: str = "",
) -> ScoredSentence:
    """Score one sentence against the frequency data and the learner profile."""
    entries = lexicon.analyze_tokens(sentence.text)
    scoreable = [entry for entry in entries if entry.known_word]

    zipfs = [entry.zipf for entry in scoreable if entry.zipf > 0]
    mean_zipf = sum(zipfs) / len(zipfs) if zipfs else 0.0
    min_zipf = min(zipfs) if zipfs else 0.0

    unknown: list[str] = []
    if profile is not None:
        for entry in scoreable:
            if not profile.knows(entry.lemma) and entry.lemma not in unknown:
                unknown.append(entry.lemma)

    complexity = _clause_complexity(sentence.text, entries)
    token_count = len(entries)

    difficulty = 100.0 * (
        0.45 * _rarity(min_zipf)
        + 0.25 * _rarity(mean_zipf)
        + 0.15 * min(1.0, token_count / LENGTH_SATURATION)
        + 0.15 * min(1.0, complexity / 3.0)
    )

    # The level of a sentence is the level of its hardest real word.
    hardest = "A1"
    for entry in scoreable:
        if CEFR_ORDER.index(entry.cefr) > CEFR_ORDER.index(hardest):
            hardest = entry.cefr

    return ScoredSentence(
        sentence=sentence,
        entries=entries,
        difficulty=round(difficulty, 2),
        mean_zipf=mean_zipf,
        min_zipf=min_zipf,
        cefr=hardest if scoreable else "C2",
        token_count=token_count,
        content_count=len(scoreable),
        unknown_lemmas=unknown,
        complexity=complexity,
        translation=translation,
    )


def score_all(
    sentences: list[Sentence],
    lexicon: Lexicon,
    profile=None,
    translations: dict[int, str] | None = None,
) -> list[ScoredSentence]:
    translations = translations or {}
    return [
        score_sentence(sentence, lexicon, profile, translations.get(index, ""))
        for index, sentence in enumerate(sentences)
    ]


def corpus_stats(scored: list[ScoredSentence], profile=None) -> dict:
    """Whole-source readout: is this show at the learner's level?

    Comprehension here is token coverage -- the share of running words already
    known -- which is the figure the extensive-reading literature ties to
    comfortable comprehension (~95%+ for enjoyable viewing, ~98% for fluent).
    """
    total_tokens = known_tokens = 0
    lemma_counts: dict[str, int] = {}
    bands = {band: 0 for band in CEFR_ORDER}
    difficulties: list[float] = []

    for item in scored:
        difficulties.append(item.difficulty)
        for entry in item.entries:
            if not entry.known_word:
                continue
            total_tokens += 1
            lemma_counts[entry.lemma] = lemma_counts.get(entry.lemma, 0) + 1
            bands[entry.cefr] = bands.get(entry.cefr, 0) + 1
            if profile is not None and profile.knows(entry.lemma):
                known_tokens += 1

    unknown_lemmas = (
        {lemma for lemma in lemma_counts if not profile.knows(lemma)}
        if profile is not None
        else set()
    )
    coverage = (known_tokens / total_tokens * 100) if total_tokens else 0.0
    one_unknown = sum(1 for item in scored if item.unknown_count == 1)

    return {
        "sentences": len(scored),
        "tokens": total_tokens,
        "unique_lemmas": len(lemma_counts),
        "unknown_lemmas": len(unknown_lemmas),
        "comprehension": round(coverage, 1),
        "mean_difficulty": round(
            sum(difficulties) / len(difficulties), 1
        ) if difficulties else 0.0,
        "cefr_distribution": bands,
        "i_plus_1_sentences": one_unknown,
    }
