"""Pair German sentences with an English subtitle track by timestamp.

If you can get both subtitle tracks for what you are watching, you get
human-quality sentence translations for free -- no machine translation, no API
key, and in the register the show actually uses.

The catch is that subtitle releases drift: the English track may be cut for a
different broadcast master and run seconds off the German one. A constant
offset is estimated automatically before matching, which handles the common
case; genuinely variable drift is not corrected.
"""

from __future__ import annotations

from dataclasses import dataclass

from .subtitles import Sentence, merge_into_sentences, parse_cues

# Fraction of a German sentence's span that an English sentence must cover
# before we treat them as the same moment.
MIN_OVERLAP = 0.20

# Range and granularity of the automatic offset search.
OFFSET_RANGE = 45.0
OFFSET_STEP = 0.25


@dataclass
class Alignment:
    offset: float
    matched: int
    total: int

    @property
    def rate(self) -> float:
        return (self.matched / self.total * 100) if self.total else 0.0


def load_reference(path: str) -> list[Sentence]:
    """Load the English track as sentences, keeping duplicates.

    Deduplication is right for the German side (mining the same sentence twice
    is waste) but wrong here: two different German lines can legitimately map
    to the same English rendering, and dropping repeats would strand one.
    """
    with open(path, encoding="utf-8", errors="replace") as handle:
        return merge_into_sentences(parse_cues(handle.read()))


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def total_overlap(
    targets: list[Sentence], references: list[Sentence], offset: float
) -> float:
    """Total intersecting duration with the reference shifted by `offset`.

    Both lists are time-sorted, so a single merge scan suffices.
    """
    total = 0.0
    index = 0
    for target in targets:
        # Advance past references that end before this target starts.
        while index < len(references) and references[index].end + offset < target.start:
            index += 1
        probe = index
        while probe < len(references) and references[probe].start + offset < target.end:
            total += _overlap(
                target.start,
                target.end,
                references[probe].start + offset,
                references[probe].end + offset,
            )
            probe += 1
    return total


def estimate_offset(
    targets: list[Sentence],
    references: list[Sentence],
    search: float = OFFSET_RANGE,
    step: float = OFFSET_STEP,
) -> float:
    """Find the constant shift that best lines the two tracks up.

    Coarse sweep then a fine local search, so a 45-second window costs a few
    hundred scans rather than a few thousand.
    """
    if not targets or not references:
        return 0.0

    def best_in(candidates) -> tuple[float, float]:
        best_offset, best_score = 0.0, -1.0
        for candidate in candidates:
            score = total_overlap(targets, references, candidate)
            if score > best_score:
                best_offset, best_score = candidate, score
        return best_offset, best_score

    coarse_steps = int(search / 1.0)
    coarse = [(-search + i * 1.0) for i in range(coarse_steps * 2 + 1)]
    centre, _ = best_in(coarse)

    fine = [centre + i * step for i in range(-4, 5)]
    refined, _ = best_in(fine)
    return round(refined, 3)


def align(
    targets: list[Sentence],
    references: list[Sentence],
    offset: float | None = None,
    min_overlap: float = MIN_OVERLAP,
) -> tuple[dict[int, str], Alignment]:
    """Map target-sentence index -> English text.

    Returns the mapping plus a report, so the caller can tell the difference
    between "no English track" and "the tracks do not line up".
    """
    if not references:
        return {}, Alignment(offset=0.0, matched=0, total=len(targets))

    if offset is None:
        offset = estimate_offset(targets, references)

    translations: dict[int, str] = {}
    index = 0
    for position, target in enumerate(targets):
        while index < len(references) and references[index].end + offset < target.start:
            index += 1

        probe = index
        pieces: list[str] = []
        span = max(target.end - target.start, 0.001)
        while probe < len(references) and references[probe].start + offset < target.end:
            reference = references[probe]
            overlap = _overlap(
                target.start,
                target.end,
                reference.start + offset,
                reference.end + offset,
            )
            reference_span = max(reference.end - reference.start, 0.001)
            # Accept on either side's coverage: a short exclamation inside a
            # long German span, or a long English line spanning a short one.
            if overlap / span >= min_overlap or overlap / reference_span >= min_overlap:
                pieces.append(reference.text)
            probe += 1

        if pieces:
            translations[position] = " ".join(pieces).strip()

    return translations, Alignment(
        offset=offset, matched=len(translations), total=len(targets)
    )
