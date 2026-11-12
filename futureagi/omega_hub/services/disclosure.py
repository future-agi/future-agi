"""The structured disclosure (SPEC §6.7) and its check against the checker's `observed` facts (§5.4)."""

from __future__ import annotations

import re

from omega_hub.disclosure_shape import FIELDS, PARTS, PART_LENGTH, SUMMARY_LENGTH, shape_errors
from omega_hub.errors import HubError

# Which observed facts each part must speak to (SPEC §4.2 row 8).
OBSERVED_FOR_PART = {
    "network": ("hosts", "vendors", "rawNetwork"),
    "files": ("filesOutside", "writesCode"),
    "shell": ("shell",),
    "background": ("keepsRunning", "workers"),
    "credentials": ("keys", "envEnumeration"),
}
_EMPTY = re.compile(r"^\s*(none\.?)?\s*$", re.IGNORECASE)


def require_shape(disclosure) -> dict:
    errors = shape_errors(disclosure)
    if errors:
        raise HubError("OMEGA_HUB_DISCLOSURE_INVALID", 400, "; ".join(errors))
    return {field: disclosure[field].strip() for field in FIELDS}


def is_empty_part(value) -> bool:
    """Blank or "None." counts as empty when the checks observed something for that part."""
    return not isinstance(value, str) or _EMPTY.match(value) is not None


def gaps(disclosure: dict, observed: dict | None) -> list[str]:
    observed = observed or {}
    return [
        part
        for part in PARTS
        if any(observed.get(key) for key in OBSERVED_FOR_PART[part]) and is_empty_part(disclosure.get(part))
    ]


def observed_of(report: dict | None) -> dict:
    for step in (report or {}).get("steps", []):
        if step.get("id") == "capabilities":
            return step.get("observed") or {}
    return {}


def require_confirmed(disclosure, report: dict | None) -> dict:
    if disclosure is None:
        raise HubError("OMEGA_HUB_DISCLOSURE_REQUIRED", 400)
    confirmed = require_shape(disclosure)
    missing = gaps(confirmed, observed_of(report))
    if missing:
        raise HubError("OMEGA_HUB_DISCLOSURE_GAP", 400, f"empty while the checks observed facts: {', '.join(missing)}")
    return confirmed
