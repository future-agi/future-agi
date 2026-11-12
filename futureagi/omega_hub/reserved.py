"""Reserved handles and trust words (SPEC §2.3), loaded from `rules/reserved.json`.

Words are matched on a normalised form: lower case with spaces, `_` and `-` removed, so
"Staff Pick", "staff_pick" and "staff-pick" are the same word.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

RESERVED_FILE = Path(__file__).resolve().parent / "rules" / "reserved.json"
HANDLE = re.compile(r"^[a-z][a-z0-9-]{1,38}$")
NAME = re.compile(r"^[a-z][a-z0-9-]{2,39}$")
OFFICIAL_HANDLE = "future-agi"


def normalise(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value).lower())


@lru_cache(maxsize=1)
def _lists() -> dict[str, frozenset[str]]:
    data = json.loads(RESERVED_FILE.read_text(encoding="utf-8"))
    return {
        "handles": frozenset(data["handles"]),
        "status": frozenset(normalise(word) for word in data["statusWords"]),
        "tag": frozenset(normalise(word) for word in data["tagOnlyWords"]),
    }


def is_reserved_handle(handle: str) -> bool:
    """A reserved route/brand handle, or a status word as the whole handle or one hyphen part."""
    lists = _lists()
    if handle in lists["handles"]:
        return True
    if normalise(handle) in lists["status"]:
        return True
    return any(normalise(part) in lists["status"] for part in handle.split("-") if part)


def tag_is_reserved(tag: str) -> bool:
    lists = _lists()
    word = normalise(tag)
    return word in lists["status"] or word in lists["tag"]


def display_name_is_reserved(display_name: str, handle: str | None = None) -> bool:
    """A status word as a word (or the whole name, normalised) in a display name. future-agi is exempt."""
    if handle == OFFICIAL_HANDLE:
        return False
    status = _lists()["status"]
    if normalise(display_name) in status:
        return True
    words = [normalise(word) for word in re.split(r"[\s,.;:!?/()]+", display_name) if word]
    if any(word in status for word in words):
        return True
    # Two-word status phrases ("staff pick", "trusted publisher").
    return any(a + b in status for a, b in zip(words, words[1:]))
