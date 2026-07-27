"""Save and reload a study set, so analysis and export are separate steps.

Scoring an episode, reviewing it, and exporting it happen at different times:
analyse once, mark words over a cup of coffee, export when the set looks
right. This is the file that carries state between those steps.
"""

from __future__ import annotations

import json
import os

from .score import ScoredSentence

FORMAT_VERSION = 1


def save(path: str, items, source: str, stats: dict, mode: str,
         extra: dict | None = None) -> None:
    payload = {
        "version": FORMAT_VERSION,
        "source": source,
        "mode": mode,
        "stats": stats,
        "items": [item.to_dict() for item in items],
        **(extra or {}),
    }
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["items"] = [
        ScoredSentence.from_dict(raw) for raw in payload.get("items", [])
    ]
    return payload
