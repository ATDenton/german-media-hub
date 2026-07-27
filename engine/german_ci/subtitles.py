"""Parse .srt/.vtt subtitle files and rebuild real sentences from cues.

Subtitle cues are laid out for reading speed, not grammar: one sentence is
routinely split across two or three cues, and one cue routinely holds the tail
of one sentence plus the head of the next. Everything downstream scores
*sentences*, so this module's real job is step 3 below.

    1. parse   cues out of .srt/.vtt
    2. clean   formatting tags, SDH annotations, speaker labels
    3. merge   cues -> sentences, keeping the time span of each sentence
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# A pause longer than this forces a sentence boundary even with no punctuation,
# which is what happens across scene changes when a line trails off.
SCENE_GAP_SECONDS = 2.5

# German abbreviations whose trailing period must not end a sentence.
ABBREVIATIONS = {
    "z.b", "d.h", "u.a", "u.s.w", "usw", "bzw", "ca", "ggf", "evtl", "inkl",
    "exkl", "vgl", "bspw", "etc", "dr", "prof", "hr", "fr", "nr", "st", "str",
    "mio", "mrd", "std", "min", "sek", "jh", "jhd", "bzgl", "z.t", "u.u",
    "s.o", "s.u", "m.e", "o.ä", "u.ä", "a.d", "i.d.r", "abb", "tel",
}

_TIME_RE = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)
_CUE_TIMING_RE = re.compile(
    r"^\s*(?P<start>[\d:.,]+)\s*-->\s*(?P<end>[\d:.,]+)(?P<settings>.*)$"
)

# <i>, </i>, <font color="#fff">, and the ASS position overrides {\an8} that
# leak into subtitles ripped from streaming sources.
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_ASS_RE = re.compile(r"\{\\[^}]*\}")
# SDH sound/context annotations: [Musik], (seufzt), ♪ lyrics ♪
_BRACKET_RE = re.compile(r"[\[\(][^\]\)]*[\]\)]")
_MUSIC_RE = re.compile(r"[♪♫#]+")
# SDH speaker labels are conventionally uppercase: "PETER:", "FRAU MÜLLER:",
# and in transcripts of two-hander dialogue just "A:" / "B:".
_SPEAKER_RE = re.compile(r"^\s*[A-ZÄÖÜ][A-ZÄÖÜß\s.]{0,24}:\s*")
_WS_RE = re.compile(r"\s+")



@dataclass
class Cue:
    """One subtitle cue as it appears in the file."""

    index: int
    start: float
    end: float
    text: str


@dataclass
class Segment:
    """A speaker-delimited chunk of a cue -- the unit we reassemble from."""

    text: str
    start: float
    end: float
    cue_index: int
    starts_turn: bool = False


@dataclass
class Sentence:
    """A reconstructed sentence with the time span of the cues covering it."""

    text: str
    start: float
    end: float
    cue_indices: list[int] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def timestamp(self) -> str:
        """`HH:MM:SS` for the sentence start, for card provenance."""
        total = int(self.start)
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def parse_time(raw: str) -> float:
    """Seconds from `HH:MM:SS,mmm` (srt) or `MM:SS.mmm` (vtt)."""
    match = _TIME_RE.search(raw)
    if not match:
        raise ValueError(f"unparseable timestamp: {raw!r}")
    hours, minutes, seconds, millis = match.groups()
    return (
        int(hours or 0) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis.ljust(3, "0")) / 1000.0
    )


def _strip_entities(text: str) -> str:
    """Unescape HTML entities.

    German subtitles are full of `&auml;`/`&szlig;`, so this must cover the
    whole named+numeric set, not a hand-picked handful. Runs *after* tag
    stripping so a literal `&lt;i&gt;` is not resurrected into a tag.
    """
    return html.unescape(text).replace("\xa0", " ")


def clean_text(raw: str) -> str:
    """Strip formatting and SDH noise from a cue's body."""
    text = _ASS_RE.sub("", raw)
    text = _TAG_RE.sub("", text)
    text = _strip_entities(text)
    text = _BRACKET_RE.sub(" ", text)
    text = _MUSIC_RE.sub(" ", text)
    # Line-break hyphens: subtitles wrap "Ent-\nschuldigung" mid-word.
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    return text


def parse_cues(content: str) -> list[Cue]:
    """Parse .srt or .vtt content into cues, tolerating BOM/CRLF/WEBVTT."""
    content = content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    index = 0

    for block in re.split(r"\n[ \t]*\n", content):
        block = block.strip("\n")
        if not block.strip():
            continue

        lines = block.split("\n")
        timing_line = None
        timing_at = 0
        for position, line in enumerate(lines[:3]):
            if "-->" in line:
                timing_line = line
                timing_at = position
                break
        if timing_line is None:
            continue  # WEBVTT header, NOTE block, or stray numbering

        match = _CUE_TIMING_RE.match(timing_line)
        if not match:
            continue
        try:
            start = parse_time(match.group("start"))
            end = parse_time(match.group("end"))
        except ValueError:
            continue

        body = "\n".join(lines[timing_at + 1:])
        text = clean_text(body)
        if not text.strip():
            continue  # cue was pure SDH annotation or formatting

        index += 1
        cues.append(Cue(index=index, start=start, end=max(end, start), text=text))

    return _strip_rolling_duplicates(cues)


def _cue_lines(cue: Cue) -> list[str]:
    return [_WS_RE.sub(" ", line).strip() for line in cue.text.split("\n") if line.strip()]


def _looks_rolling(cues: list[Cue]) -> bool:
    """Do these cues use YouTube's rolling-window caption layout?

    Auto-generated captions scroll: each cue redisplays the previous line and
    appends a new one, padded with near-zero-duration spacer cues. Detected by
    how often a cue opens with the line the previous cue closed on.
    """
    if len(cues) < 6:
        return False
    repeats = 0
    for previous, current in zip(cues, cues[1:]):
        before, after = _cue_lines(previous), _cue_lines(current)
        if before and after and after[0] == before[-1]:
            repeats += 1
    return repeats / (len(cues) - 1) > 0.3


def _strip_rolling_duplicates(cues: list[Cue]) -> list[Cue]:
    """Collapse rolling-window captions down to each line's first appearance.

    Without this every line is parsed two or three times over, and merging
    that stream into sentences produces fluent-looking nonsense rather than
    anything you could put on a card.

    Only applied when the file actually looks like scrolling captions, so
    ordinary subtitles that legitimately repeat a line are left alone.
    """
    if not _looks_rolling(cues):
        return cues

    collapsed: list[Cue] = []
    recent: list[str] = []
    for cue in cues:
        fresh = [line for line in _cue_lines(cue) if line not in recent]
        if not fresh:
            continue
        # Keep a short window: a line repeated much later is a real repeat.
        recent = (recent + fresh)[-4:]
        collapsed.append(
            Cue(
                index=len(collapsed) + 1,
                start=cue.start,
                end=max(cue.end, cue.start),
                text=" ".join(fresh),
            )
        )
    return collapsed


def _split_turns(cue: Cue) -> list[Segment]:
    """Split a cue into speaker turns.

    A leading dash marks a new speaker, which is always a hard sentence
    boundary -- two speakers never share a sentence.
    """
    segments: list[Segment] = []
    lines = [line for line in cue.text.split("\n") if line.strip()]
    pending: list[str] = []
    pending_is_turn = False

    def flush() -> None:
        nonlocal pending, pending_is_turn
        if not pending:
            return
        text = _WS_RE.sub(" ", " ".join(pending)).strip()
        text = _SPEAKER_RE.sub("", text).strip()
        if text:
            segments.append(
                Segment(
                    text=text,
                    start=cue.start,
                    end=cue.end,
                    cue_index=cue.index,
                    starts_turn=pending_is_turn,
                )
            )
        pending = []
        pending_is_turn = False

    for line in lines:
        stripped = line.strip()
        # "-Hallo" / "- Hallo" open a turn; "--" and "..." do not.
        if re.match(r"^[-–—]\s*(?![-–—])\S", stripped):
            flush()
            pending_is_turn = True
            stripped = re.sub(r"^[-–—]\s*", "", stripped)
        pending.append(stripped)

    flush()
    return segments


def _ends_sentence(text: str) -> bool:
    """Does this text end at a genuine sentence boundary?

    Guards the two German cases that break naive punctuation splitting:
    ordinals ("am 3. Oktober") and abbreviations ("z.B.", "Dr.").
    """
    stripped = text.rstrip().rstrip('"“”»«\')')
    if not stripped or stripped[-1] not in ".!?…":
        return False
    if stripped[-1] in "!?…":
        return True

    # A period preceded by a bare number is an ordinal, not a full stop.
    last_token = re.split(r"[\s]", stripped)[-1].lower().rstrip(".")
    if last_token.isdigit():
        return False
    if last_token in ABBREVIATIONS:
        return False
    # Single letter + period is an initial ("J. Schmidt"), not a stop.
    if len(last_token) == 1 and last_token.isalpha():
        return False
    return True


def _sentence_boundaries(text: str) -> list[str]:
    """Split one segment's text at internal sentence boundaries."""
    parts: list[str] = []
    buffer = ""
    # Split into candidate pieces after each run of terminal punctuation.
    for piece in re.split(r"(?<=[.!?…])\s+", text):
        buffer = f"{buffer} {piece}".strip() if buffer else piece
        if _ends_sentence(buffer):
            parts.append(buffer)
            buffer = ""
    if buffer.strip():
        parts.append(buffer.strip())
    return parts


def merge_into_sentences(
    cues: list[Cue], scene_gap: float = SCENE_GAP_SECONDS
) -> list[Sentence]:
    """Reassemble cues into sentences, carrying time spans along.

    Accumulates segments until one ends on real sentence punctuation, a new
    speaker turn begins, or a scene-length pause intervenes.
    """
    segments: list[Segment] = []
    for cue in cues:
        segments.extend(_split_turns(cue))

    sentences: list[Sentence] = []
    buffer: list[Segment] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        text = _WS_RE.sub(" ", " ".join(seg.text for seg in buffer)).strip()
        if text:
            # One buffer can still hold several complete sentences; split
            # them but attribute the whole span to each, since sub-cue
            # timing is not recoverable.
            pieces = _sentence_boundaries(text)
            indices = sorted({seg.cue_index for seg in buffer})
            start = min(seg.start for seg in buffer)
            end = max(seg.end for seg in buffer)
            for piece in pieces:
                if piece.strip():
                    sentences.append(
                        Sentence(
                            text=piece.strip(),
                            start=start,
                            end=end,
                            cue_indices=indices,
                        )
                    )
        buffer = []

    previous: Segment | None = None
    for segment in segments:
        if previous is not None:
            gap = segment.start - previous.end
            if segment.starts_turn or gap > scene_gap:
                flush()
        buffer.append(segment)
        if _ends_sentence(segment.text):
            flush()
            previous = None
            continue
        previous = segment

    flush()
    return sentences


def dedupe(sentences: list[Sentence]) -> list[Sentence]:
    """Drop repeats, keeping first occurrence.

    Opening credits, recaps and catchphrases recur verbatim; mining the same
    sentence twice wastes a card.
    """
    seen: set[str] = set()
    unique: list[Sentence] = []
    for sentence in sentences:
        key = _WS_RE.sub(" ", sentence.text.lower()).strip(" .,!?…")
        if key in seen:
            continue
        seen.add(key)
        unique.append(sentence)
    return unique


def punctuation_ratio(sentences: list[Sentence]) -> float:
    """Share of sentences that end on real terminal punctuation.

    The useful measure of whether a subtitle file can be mined at all. A
    well-punctuated transcript scores near 1.0 and splits cleanly; a bare
    word-stream scores near 0 and every "sentence" is a splice artifact.
    This is about the text, not its provenance -- plenty of machine
    transcripts are properly punctuated, and some human ones are not.
    """
    if not sentences:
        return 0.0
    ended = sum(1 for s in sentences if s.text.rstrip()[-1:] in ".!?…")
    return ended / len(sentences)


def load(path: str, scene_gap: float = SCENE_GAP_SECONDS) -> list[Sentence]:
    """Read a subtitle file and return deduped, time-stamped sentences."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        content = handle.read()
    return dedupe(merge_into_sentences(parse_cues(content), scene_gap=scene_gap))
