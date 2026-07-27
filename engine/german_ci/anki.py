"""Export a study set as an Anki package (or TSV).

Note GUIDs are derived from (source, timestamp, sentence) rather than being
random. That is the difference between re-running an export and getting an
updated deck, versus re-running it and getting a second copy of every card.
"""

from __future__ import annotations

import csv
import hashlib
import html
import os
import re

MODEL_NAME = "German CI"
FIELDS = [
    "Sentence",
    "SentenceTranslation",
    "TargetWord",
    "TargetLemma",
    "WordGloss",
    "Audio",
    "Screenshot",
    "Source",
    "Timestamp",
    "Rank",
]

CSS = """
.card {
  font-family: -apple-system, 'Segoe UI', system-ui, sans-serif;
  font-size: 21px;
  text-align: center;
  color: #1a1c22;
  background: #faf9f7;
  line-height: 1.5;
}
.night_mode .card { color: #e8eaf0; background: #15171d; }
.sentence { font-size: 26px; margin: 18px 12px; }
.sentence b { color: #b8860b; }
.night_mode .sentence b { color: #f5c518; }
.translation { color: #5c6172; font-size: 19px; margin: 14px 12px; }
.night_mode .translation { color: #9aa2b4; }
.gloss { margin: 14px 12px; font-size: 19px; }
.lemma { color: #5c6172; font-size: 15px; }
.night_mode .lemma { color: #9aa2b4; }
.meta { color: #8a8f9e; font-size: 13px; margin-top: 20px; }
img { max-width: 92%; border-radius: 8px; margin-top: 12px; }
hr { border: none; border-top: 1px solid #d8d4cc; margin: 18px 0; }
.night_mode hr { border-top-color: #282d39; }
"""

FRONT = """
<div class="sentence">{{Sentence}}</div>
{{Audio}}
"""

BACK = """
<div class="sentence">{{Sentence}}</div>
{{Audio}}
<hr>
<div class="gloss"><b>{{TargetWord}}</b> &mdash; {{WordGloss}}</div>
<div class="lemma">{{TargetLemma}}{{#Rank}} &middot; rank {{Rank}}{{/Rank}}</div>
{{#SentenceTranslation}}<div class="translation">{{SentenceTranslation}}</div>{{/SentenceTranslation}}
{{#Screenshot}}<div>{{Screenshot}}</div>{{/Screenshot}}
<div class="meta">{{Source}} &middot; {{Timestamp}}</div>
"""


def pretty_source(name: str) -> str:
    """Turn a subtitle filename into something readable in Anki's deck list.

    Downloaded subtitles arrive as
    `22_Juli_2026_Tagesschau_in_100_Sekunden.de-DE-9WqM8fC0bpI.srt`, and the
    extension plus the language tag are noise you then have to look at every
    day in the sidebar.
    """
    stem = re.sub(r"\.(srt|vtt|ass|ssa)$", "", name, flags=re.IGNORECASE)
    # Trailing language tag: ".de", ".en-US", ".de-DE-9WqM8fC0bpI", ".de-orig"
    stem = re.sub(r"\.[a-z]{2}(?:-[A-Za-z0-9]+)*$", "", stem)
    stem = stem.replace("_", " ").replace(".", " ")
    return re.sub(r"\s+", " ", stem).strip() or name


def _stable_id(text: str) -> int:
    """A deterministic 31-bit id, so decks and models keep their identity."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % (1 << 31)


def note_guid(source: str, timestamp: str, sentence: str) -> str:
    return hashlib.sha1(
        f"{source}|{timestamp}|{sentence}".encode("utf-8")
    ).hexdigest()[:20]


def highlight(text: str, entries, target_lemma: str) -> str:
    """Bold the target word inside the sentence.

    Matches on the inflected surface form actually present, since the lemma
    usually is not (`gegangen` in the sentence, `gehen` as the lemma).
    """
    escaped = html.escape(text)
    if not target_lemma:
        return escaped

    surfaces = [
        entry.surface for entry in entries or [] if entry.lemma == target_lemma
    ]
    for surface in sorted(set(surfaces), key=len, reverse=True):
        pattern = re.compile(rf"(?<!\w){re.escape(html.escape(surface))}(?!\w)")
        replaced, count = pattern.subn(
            lambda match: f"<b>{match.group(0)}</b>", escaped, count=1
        )
        if count:
            return replaced
    return escaped


def build_rows(items, source: str) -> list[dict]:
    """Flatten scored sentences into export-ready field dicts."""
    rows = []
    for item in items:
        target_lemma = item.target or (
            item.unknown_lemmas[0] if item.unknown_lemmas else ""
        )
        entry = next(
            (e for e in item.entries if e.lemma == target_lemma), None
        )
        timestamp = item.sentence.timestamp()
        rows.append(
            {
                "Sentence": highlight(item.text, item.entries, target_lemma),
                "SentenceTranslation": html.escape(item.translation or ""),
                "TargetWord": html.escape(entry.surface if entry else target_lemma),
                "TargetLemma": html.escape(target_lemma),
                "WordGloss": html.escape(entry.gloss if entry else ""),
                "Audio": item.extras.get("audio_field", ""),
                "Screenshot": item.extras.get("screenshot_field", ""),
                # Displayed tidily, but the GUID below stays keyed to the raw
                # filename so cleaning this up cannot orphan existing notes.
                "Source": html.escape(pretty_source(source)),
                "Timestamp": timestamp,
                "Rank": str(entry.rank) if entry and entry.rank else "",
                "_guid": note_guid(source, timestamp, item.text),
                "_lemma": target_lemma,
            }
        )
    return rows


def export_tsv(items, source: str, out_path: str) -> dict:
    """Dependency-free export: a TSV you import yourself."""
    rows = build_rows(items, source)
    with open(out_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(FIELDS)
        for row in rows:
            writer.writerow([row[field] for field in FIELDS])
    return {"path": out_path, "notes": len(rows), "format": "tsv", "rows": rows}


def export_apkg(items, source: str, out_path: str, deck_name: str | None = None,
                media_files: list[str] | None = None) -> dict:
    """Write a real .apkg with a dedicated note type."""
    try:
        import genanki
    except ImportError as error:  # pragma: no cover - environment dependent
        raise SystemExit(
            "genanki is required for .apkg export.\n"
            "Install it (pip install genanki) or use --format tsv."
        ) from error

    deck_name = deck_name or f"German CI::{pretty_source(source)}"
    model = genanki.Model(
        _stable_id(MODEL_NAME),
        MODEL_NAME,
        fields=[{"name": name} for name in FIELDS],
        templates=[{"name": "Comprehensible Input", "qfmt": FRONT, "afmt": BACK}],
        css=CSS,
    )
    deck = genanki.Deck(_stable_id(deck_name), deck_name)

    rows = build_rows(items, source)
    for row in rows:
        deck.add_note(
            genanki.Note(
                model=model,
                fields=[row[field] for field in FIELDS],
                guid=row["_guid"],
                tags=["german-ci"],
            )
        )

    package = genanki.Package(deck)
    existing = [path for path in (media_files or []) if os.path.exists(path)]
    package.media_files = existing
    package.write_to_file(out_path)

    return {
        "path": out_path,
        "notes": len(rows),
        "format": "apkg",
        "deck": deck_name,
        "media": len(existing),
        "rows": rows,
    }


def export(items, source: str, out_path: str, fmt: str = "apkg", **kwargs) -> dict:
    if fmt == "tsv":
        return export_tsv(items, source, out_path)
    if fmt == "apkg":
        return export_apkg(items, source, out_path, **kwargs)
    raise ValueError(f"unknown export format {fmt!r}")
