"""Turn scored sentences into an ordered study set.

Sorting by difficulty is the obvious thing and the least useful one. What
actually builds vocabulary is the i+1 sentence: everything known except one
word, so the unknown word is pinned down by context instead of floating free.

Three orderings:

  i+1        only sentences with exactly one unknown word, best exemplar per
             word, most useful word first. The default, and the reason this
             engine exists.
  coverage   greedy set-cover -- the fewest sentences that introduce the most
             new high-value vocabulary. For mining a whole episode.
  difficulty plain easy-to-hard, for reading practice rather than card mining.
"""

from __future__ import annotations

from .score import ScoredSentence

MODES = ("i+1", "coverage", "difficulty")

# Sentences this short rarely give enough context to infer a word's meaning;
# sentences this long stop being good cards.
MIN_TOKENS = 4
MAX_TOKENS = 18


def _usefulness(lemma: str, lexicon) -> float:
    """How much is learning this lemma worth? Higher is better.

    Straight frequency: a rank-800 word repays study far more than a rank-40000
    one, however exciting the rare word looks.
    """
    info = lexicon.lemma_info(lemma)
    if info is None or not info.rank:
        return 0.0
    return info.zipf


def _exemplar_quality(item: ScoredSentence) -> tuple:
    """Sort key picking the best sentence to teach a word.

    Prefers a sentence that has a translation, is a comfortable length, and is
    otherwise easy -- the target word should be the only hard thing in it.
    """
    has_translation = 0 if item.translation else 1
    length_penalty = abs(item.token_count - 9)
    return (has_translation, length_penalty, item.difficulty)


def _within_length(item: ScoredSentence, min_tokens: int, max_tokens: int) -> bool:
    return min_tokens <= item.token_count <= max_tokens


def select_i_plus_1(
    scored: list[ScoredSentence],
    lexicon,
    profile=None,
    limit: int = 50,
    min_tokens: int = MIN_TOKENS,
    max_tokens: int = MAX_TOKENS,
    skip_mined: bool = True,
) -> list[ScoredSentence]:
    """One sentence per new word, ordered by how useful that word is."""
    best: dict[str, ScoredSentence] = {}

    for item in scored:
        if item.unknown_count != 1:
            continue
        if not _within_length(item, min_tokens, max_tokens):
            continue
        target = item.unknown_lemmas[0]
        if skip_mined and profile is not None and profile.already_mined(target):
            continue
        incumbent = best.get(target)
        if incumbent is None or _exemplar_quality(item) < _exemplar_quality(incumbent):
            item.target = target
            best[target] = item

    ranked = sorted(
        best.items(), key=lambda pair: (-_usefulness(pair[0], lexicon), pair[0])
    )
    return [item for _, item in ranked[:limit]]


def select_coverage(
    scored: list[ScoredSentence],
    lexicon,
    profile=None,
    limit: int = 50,
    max_unknown: int = 3,
    min_tokens: int = MIN_TOKENS,
    max_tokens: int = MAX_TOKENS,
    skip_mined: bool = True,
) -> list[ScoredSentence]:
    """Greedy set-cover over unknown lemmas, weighted by usefulness.

    Each round takes the sentence delivering the most value per new word,
    then discounts those words so the next round moves on.
    """
    mined = set()
    if skip_mined and profile is not None:
        mined = set(profile.exported)

    candidates = [
        item
        for item in scored
        if item.unknown_count
        and item.unknown_count <= max_unknown
        and _within_length(item, min_tokens, max_tokens)
        and not (set(item.unknown_lemmas) <= mined)
    ]

    weights = {
        lemma: _usefulness(lemma, lexicon)
        for item in candidates
        for lemma in item.unknown_lemmas
    }

    covered: set[str] = set(mined)
    chosen: list[ScoredSentence] = []
    pool = list(candidates)

    while pool and len(chosen) < limit:
        best_item = None
        best_gain = 0.0
        best_new: list[str] = []

        for item in pool:
            new = [lemma for lemma in item.unknown_lemmas if lemma not in covered]
            if not new:
                continue
            # Value per sentence studied, so a sentence teaching two useful
            # words beats one teaching a single word of the same total value.
            gain = sum(weights.get(lemma, 0.0) for lemma in new)
            if gain > best_gain:
                best_item, best_gain, best_new = item, gain, new

        if best_item is None:
            break

        best_item.target = max(best_new, key=lambda l: weights.get(l, 0.0))
        best_item.extras["teaches"] = best_new
        chosen.append(best_item)
        covered.update(best_new)
        pool.remove(best_item)

    return chosen


def select_by_difficulty(
    scored: list[ScoredSentence],
    lexicon=None,
    profile=None,
    limit: int = 50,
    min_tokens: int = MIN_TOKENS,
    max_tokens: int = MAX_TOKENS,
    **_,
) -> list[ScoredSentence]:
    """Easiest first -- graded reading rather than card mining."""
    candidates = [
        item for item in scored if _within_length(item, min_tokens, max_tokens)
    ]
    ordered = sorted(candidates, key=lambda item: item.difficulty)
    for item in ordered:
        if item.unknown_lemmas and not item.target:
            item.target = item.unknown_lemmas[0]
    return ordered[:limit]


def select(mode: str, scored: list[ScoredSentence], lexicon, profile=None, **kwargs):
    """Dispatch to a selection mode by name."""
    if mode == "i+1":
        return select_i_plus_1(scored, lexicon, profile, **kwargs)
    if mode == "coverage":
        return select_coverage(scored, lexicon, profile, **kwargs)
    if mode == "difficulty":
        return select_by_difficulty(scored, lexicon, profile, **kwargs)
    raise ValueError(f"unknown mode {mode!r}; use one of {MODES}")
