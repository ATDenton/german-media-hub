"""Build `data/lexicon.sqlite` from a frequency list and Wiktionary data.

Run once (or after updating the sources):

    python3 scripts/build_lexicon.py

The kaikki.org German extract is ~1 GB of JSONL. It is *streamed* and filtered
line by line, so the full file never lands on disk unless --cache is given.

What the build produces, and why:

  lemma  dictionary headwords with English glosses, plus an aggregated
         frequency (the summed counts of all their inflected forms, which is
         the number that actually says how useful a word is to learn).
  form   every inflected surface form -> its lemma. Inverting Wiktionary's
         `forms[]` is what gives us lemmatization: `gegangen` -> `gehen`
         with no POS tagger and no spaCy dependency.
  freq   the raw form-level frequency list, used to score tokens directly.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request

KAIKKI_URL = "https://kaikki.org/dictionary/German/kaikki.org-dictionary-German.jsonl"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")

# Entry types that are not vocabulary worth mining.
SKIP_POS = {"character", "punct", "phrase", "proverb", "romanization"}

# Rank thresholds for the approximate CEFR banding. Rank-to-CEFR is a working
# convention, not a standard -- treat these as a rough difficulty ladder.
CEFR_BANDS = [(500, "A1"), (1500, "A2"), (3500, "B1"), (8000, "B2"),
              (16000, "C1")]


def cefr_for_rank(rank: int | None) -> str:
    if rank is None:
        return "C2"
    for threshold, band in CEFR_BANDS:
        if rank <= threshold:
            return band
    return "C2"


def load_frequencies(path: str) -> dict[str, tuple[int, int]]:
    """Read `word count` lines into {word: (count, rank)}."""
    frequencies: dict[str, tuple[int, int]] = {}
    with open(path, encoding="utf-8") as handle:
        for rank, line in enumerate(handle, start=1):
            parts = line.split()
            if len(parts) != 2:
                continue
            word, count = parts[0].strip().lower(), parts[1]
            if word and word not in frequencies:
                frequencies[word] = (int(count), rank)
    return frequencies


def zipf(count: int, total: int) -> float:
    """Zipf value: log10 of occurrences per billion words."""
    if count <= 0 or total <= 0:
        return 0.0
    return round(math.log10(count / total * 1_000_000_000), 3)


USER_AGENT = "german-ci-engine/1.0 (personal study tool)"


def _open_stream(source: str):
    """Open a byte stream for a URL, tolerating a bare Python SSL store.

    The python.org macOS builds ship without a wired-up CA bundle, so urllib
    raises CERTIFICATE_VERIFY_FAILED on a perfectly valid host. curl uses the
    system trust store and is present everywhere we care about, so prefer it
    and keep urllib as the fallback.
    """
    curl = shutil.which("curl")
    if curl:
        process = subprocess.Popen(
            [curl, "-fsSL", "--retry", "3", "-A", USER_AGENT, source],
            stdout=subprocess.PIPE,
        )
        return process.stdout, process

    request = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
    try:
        return urllib.request.urlopen(request), None
    except urllib.error.URLError as error:
        if "CERTIFICATE_VERIFY_FAILED" in str(error):
            raise SystemExit(
                "SSL trust store is not set up for this Python and curl is "
                "unavailable.\nEither install curl, or run:\n"
                "  /Applications/Python 3.11/Install Certificates.command\n"
                "Alternatively download the file yourself and pass --source "
                "<local path>."
            ) from error
        raise


def iter_lines(source: str, cache: str | None):
    """Yield lines from a URL (streamed) or a local file."""
    if os.path.exists(source):
        with open(source, "rb") as handle:
            for line in handle:
                yield line
        return

    stream, process = _open_stream(source)
    cache_handle = open(cache, "wb") if cache else None
    try:
        for line in stream:
            if cache_handle:
                cache_handle.write(line)
            yield line
    finally:
        if cache_handle:
            cache_handle.close()
        stream.close()
        if process is not None:
            if process.wait() != 0:
                raise SystemExit(f"download failed: {source}")


def clean_gloss(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())[:300]


def is_single_token(text: str) -> bool:
    """Reject multiword forms like `am freiesten` and `ist gegangen`."""
    return bool(text) and " " not in text.strip()


def is_table_artifact(form: dict) -> bool:
    """Is this "form" really scaffolding from a Wiktionary inflection table?

    Kaikki emits rows carrying the template's own name and its tag markers --
    `de-ndecl`, `strong`, `no-table-tags` -- alongside the genuine inflections.
    Left in, they become bogus form -> lemma mappings, and any that collide
    with a real word donate its frequency to an unrelated lemma.
    """
    tags = set(form.get("tags") or [])
    return bool(tags & {"table-tags", "inflection-template"})


def build(source: str, cache: str | None, out_path: str, freq_path: str) -> None:
    print(f"Loading frequency list: {freq_path}")
    frequencies = load_frequencies(freq_path)
    total_tokens = sum(count for count, _ in frequencies.values())
    known_forms = set(frequencies)
    print(f"  {len(frequencies):,} word forms, {total_tokens:,} tokens")

    if os.path.exists(out_path):
        os.remove(out_path)
    db = sqlite3.connect(out_path)
    db.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE lemma (
            id      INTEGER PRIMARY KEY,
            lemma   TEXT NOT NULL,
            pos     TEXT,
            glosses TEXT,
            count   INTEGER DEFAULT 0,
            rank    INTEGER,
            zipf    REAL,
            cefr    TEXT
        );
        CREATE TABLE form (
            form    TEXT NOT NULL,
            lemma_id INTEGER NOT NULL,
            tags    TEXT
        );
        CREATE TABLE freq (
            word  TEXT PRIMARY KEY,
            count INTEGER,
            rank  INTEGER,
            zipf  REAL
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )

    db.executemany(
        "INSERT INTO freq (word, count, rank, zipf) VALUES (?, ?, ?, ?)",
        [
            (word, count, rank, zipf(count, total_tokens))
            for word, (count, rank) in frequencies.items()
        ],
    )
    db.commit()

    print(f"Streaming dictionary: {source}")
    lemma_rows: list[tuple] = []
    form_rows: list[tuple] = []
    lemma_id = 0
    seen = read = kept = 0

    for raw in iter_lines(source, cache):
        read += 1
        if read % 20000 == 0:
            print(f"  read {read:,} entries, kept {kept:,}", flush=True)
        try:
            entry = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if entry.get("lang_code") != "de":
            continue

        word = (entry.get("word") or "").strip()
        pos = entry.get("pos") or ""
        if not word or pos in SKIP_POS or not is_single_token(word):
            continue
        seen += 1

        forms = [
            f for f in entry.get("forms") or []
            if is_single_token(f.get("form") or "")
            and (f.get("form") or "").lower() != word.lower()
            and not is_table_artifact(f)
        ]

        # Keep the entry only if it or one of its forms actually shows up in
        # subtitle-register German. This is what keeps the artifact small.
        surface = {word.lower()} | {(f.get("form") or "").lower() for f in forms}
        if not (surface & known_forms):
            continue

        glosses: list[str] = []
        form_of_targets: list[str] = []
        senses = entry.get("senses") or []
        inflection_senses = 0
        for sense in senses:
            is_inflection = False
            for target in sense.get("form_of") or []:
                name = (target or {}).get("word")
                if name and is_single_token(name):
                    form_of_targets.append(name)
                    is_inflection = True
            if is_inflection:
                inflection_senses += 1
                continue  # its "gloss" is just "past participle of gehen"
            for gloss in sense.get("glosses") or sense.get("raw_glosses") or []:
                text = clean_gloss(gloss)
                if text and text not in glosses:
                    glosses.append(text)
            if len(glosses) >= 4:
                break

        # An entry whose every sense is "<inflection> of X" is not a headword,
        # it is a pointer -- and Wiktionary puts that pointer text in `glosses`,
        # so checking for absent glosses is not enough to catch it. Left in the
        # lemma table these become thousands of pseudo-headwords that crowd the
        # frequency ranking.
        if form_of_targets and inflection_senses == len(senses):
            for target in set(form_of_targets):
                form_rows.append((word.lower(), target, "form-of"))
            continue

        kept += 1
        lemma_id += 1
        count = sum(
            frequencies.get(s, (0, 0))[0]
            for s in {word.lower()} | {(f.get("form") or "").lower() for f in forms}
        )
        lemma_rows.append(
            (lemma_id, word, pos, json.dumps(glosses[:4], ensure_ascii=False), count)
        )
        form_rows.append((word.lower(), lemma_id, "lemma"))
        for item in forms:
            tags = ",".join(item.get("tags") or [])
            form_rows.append(((item.get("form") or "").lower(), lemma_id, tags))

        if len(lemma_rows) >= 5000:
            _flush(db, lemma_rows, form_rows)
            lemma_rows, form_rows = [], []

    _flush(db, lemma_rows, form_rows)
    print(f"  read {read:,} entries; {seen:,} German; kept {kept:,}")

    _resolve_form_of_pointers(db)
    _rank_lemmas(db, total_tokens)

    db.executescript(
        """
        CREATE INDEX form_lookup ON form(form);
        CREATE INDEX form_lemma ON form(lemma_id);
        CREATE INDEX lemma_lookup ON lemma(lemma);
        """
    )
    db.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        [
            ("source", source),
            ("frequency_list", os.path.basename(freq_path)),
            ("total_tokens", str(total_tokens)),
            ("lemmas", str(kept)),
        ],
    )
    db.commit()
    db.execute("VACUUM")
    db.close()

    size_mb = os.path.getsize(out_path) / 1_048_576
    print(f"Wrote {out_path} ({size_mb:.1f} MB)")


def _flush(db, lemma_rows, form_rows) -> None:
    if lemma_rows:
        db.executemany(
            "INSERT INTO lemma (id, lemma, pos, glosses, count) VALUES (?,?,?,?,?)",
            lemma_rows,
        )
    if form_rows:
        db.executemany(
            "INSERT INTO form (form, lemma_id, tags) VALUES (?,?,?)", form_rows
        )
    db.commit()


def _resolve_form_of_pointers(db) -> None:
    """Turn `form -> lemma-name` pointers into `form -> lemma-id` rows.

    Standalone Wiktionary entries such as "gegangen: past participle of gehen"
    were staged with the target's *name*; swap in the real id and drop any
    pointer whose target we did not keep.
    """
    rows = db.execute(
        "SELECT rowid, form, lemma_id FROM form WHERE tags = 'form-of'"
    ).fetchall()
    resolved = dropped = 0
    for rowid, form, target_name in rows:
        match = db.execute(
            "SELECT id FROM lemma WHERE lemma = ? ORDER BY count DESC LIMIT 1",
            (str(target_name),),
        ).fetchone()
        if match:
            db.execute(
                "UPDATE form SET lemma_id = ?, tags = 'inflected' WHERE rowid = ?",
                (match[0], rowid),
            )
            resolved += 1
        else:
            db.execute("DELETE FROM form WHERE rowid = ?", (rowid,))
            dropped += 1
    db.commit()
    print(f"  resolved {resolved:,} form-of pointers, dropped {dropped:,}")


def _rank_lemmas(db, total_tokens: int) -> None:
    """Rank lemmas by aggregated frequency and assign Zipf + CEFR bands.

    A lemma's frequency has to aggregate its inflected forms -- `gehen` is not
    rare just because the bare infinitive is less common than `geht`/`ging`
    put together.

    But naive summing is far worse than no aggregation at all: German inflected
    forms collide constantly with function words, so an obscure adjective with
    a form spelled `die` inherits all 2.4 million occurrences of the article
    and rockets up the ranking. (That bug put `Haus` at rank 9884.)

    So each form's count is *split* between the lemmas claiming it. An
    ambiguous form contributes a fair share rather than its full weight, which
    keeps aggregation's benefit without letting homographs dominate.

    Ranking is over distinct lemma *strings*, not dictionary entries: `der` has
    six entries across article and pronoun senses, and they must not consume
    six separate slots at the top of the list.
    """
    frequencies = {
        word: count for word, count in db.execute("SELECT word, count FROM freq")
    }

    claimants: dict[str, set[str]] = {}
    for form, lemma in db.execute(
        "SELECT f.form, l.lemma FROM form f JOIN lemma l ON l.id = f.lemma_id"
    ):
        claimants.setdefault(form, set()).add(lemma)

    totals: dict[str, float] = {}
    for form, lemmas in claimants.items():
        count = frequencies.get(form, 0)
        if not count:
            continue
        share = count / len(lemmas)
        for lemma in lemmas:
            totals[lemma] = totals.get(lemma, 0.0) + share

    ordered = sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))
    ranking = {
        lemma: (position, count)
        for position, (lemma, count) in enumerate(ordered, start=1)
    }

    updates = []
    for lemma_id, lemma in db.execute("SELECT id, lemma FROM lemma"):
        rank, count = ranking.get(lemma, (None, 0.0))
        updates.append(
            (
                int(count),
                rank,
                zipf(int(count), total_tokens),
                cefr_for_rank(rank),
                lemma_id,
            )
        )
    db.executemany(
        "UPDATE lemma SET count = ?, rank = ?, zipf = ?, cefr = ? WHERE id = ?",
        updates,
    )
    db.commit()
    print(f"  ranked {len(ranking):,} distinct lemmas over {len(updates):,} entries")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=KAIKKI_URL,
                        help="kaikki JSONL URL or a local copy")
    parser.add_argument("--cache", help="save the streamed JSONL here for reuse")
    parser.add_argument("--out", default=os.path.join(DATA, "lexicon.sqlite"))
    parser.add_argument("--freq", default=os.path.join(DATA, "de_50k.txt"))
    args = parser.parse_args()

    if not os.path.exists(args.freq):
        print(f"Frequency list missing: {args.freq}", file=sys.stderr)
        return 1
    build(args.source, args.cache, args.out, args.freq)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
