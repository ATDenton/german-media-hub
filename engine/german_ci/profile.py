"""The learner's state: which lemmas are known, and what has been mined.

Everything interesting in this engine is relative to what Aaron already
knows -- "one unknown word" is meaningless without a known-word set. The
profile holds that set, seeded from frequency rank and then corrected by hand
as words get marked in the review UI.

The exported-note ledger matters just as much: without it, every new episode
re-mines `weil` and `trotzdem`, because those stay unknown until they are
marked known. Exporting a card records the lemma so the next episode moves on.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DEFAULT_PATH = os.path.join(DATA, "profile.json")

# How many of the most frequent lemmas a learner at each level is assumed to
# know. Deliberately conservative: over-seeding hides words worth studying,
# and marking extra words known in the UI is cheap.
LEVEL_SEEDS = {
    "A1": 500,
    "A2": 1200,
    "B1": 2500,
    "B2": 5000,
    "C1": 9000,
    "C2": 16000,
}


@dataclass
class Profile:
    level: str = "A2"
    known: set[str] = field(default_factory=set)
    #: lemma -> note GUID, so re-exports update instead of duplicating
    exported: dict[str, str] = field(default_factory=dict)
    #: lemmas explicitly marked unknown, overriding the frequency seed
    unknown: set[str] = field(default_factory=set)
    path: str = DEFAULT_PATH

    # -- persistence -------------------------------------------------------

    @classmethod
    def load(cls, path: str = DEFAULT_PATH) -> "Profile":
        if not os.path.exists(path):
            return cls(path=path)
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        return cls(
            level=raw.get("level", "A2"),
            known=set(raw.get("known", [])),
            exported=dict(raw.get("exported", {})),
            unknown=set(raw.get("unknown", [])),
            path=path,
        )

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {
            "level": self.level,
            "known": sorted(self.known),
            "unknown": sorted(self.unknown),
            "exported": self.exported,
        }
        # Write via a temp file so an interrupted save cannot truncate the
        # profile -- losing it means losing all the hand-marking.
        temporary = f"{self.path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        os.replace(temporary, self.path)

    # -- vocabulary --------------------------------------------------------

    def seed_from_level(self, lexicon, level: str | None = None) -> int:
        """Mark the top-N lemmas for a CEFR level as known.

        Anything explicitly marked unknown stays unknown -- a hand correction
        must survive a reseed.
        """
        level = (level or self.level).upper()
        if level not in LEVEL_SEEDS:
            raise ValueError(f"unknown level {level!r}; use one of {list(LEVEL_SEEDS)}")
        self.level = level
        added = 0
        for lemma in lexicon.top_lemmas(LEVEL_SEEDS[level]):
            if lemma not in self.unknown and lemma not in self.known:
                self.known.add(lemma)
                added += 1
        return added

    def knows(self, lemma: str) -> bool:
        return lemma in self.known and lemma not in self.unknown

    def mark_known(self, lemma: str) -> None:
        self.known.add(lemma)
        self.unknown.discard(lemma)

    def mark_unknown(self, lemma: str) -> None:
        self.unknown.add(lemma)
        self.known.discard(lemma)

    def record_export(self, lemma: str, guid: str) -> None:
        """Note that a card exists for this lemma, so it is not mined twice."""
        self.exported[lemma] = guid

    def already_mined(self, lemma: str) -> bool:
        return lemma in self.exported

    def summary(self) -> dict:
        return {
            "level": self.level,
            "known": len(self.known),
            "marked_unknown": len(self.unknown),
            "exported": len(self.exported),
            "path": self.path,
        }
