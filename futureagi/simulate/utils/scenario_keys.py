"""The one spelling of a hosted scenario's key, shared by every writer and comparison."""

from __future__ import annotations

import hashlib
import re


def canonical_scenario_key(value: object) -> str:
    """The key the harness derives for a scenario name, key or folder name.

    Mirrors the harness exactly, digest fallback included, so a name, its hyphenated key and an
    older underscored folder all compare equal. Blank input has no key.
    """
    raw = str(value or "")
    if not raw.strip():
        return ""
    cleaned = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")
    return cleaned or "scenario-" + hashlib.sha256(raw.encode()).hexdigest()[:12]
