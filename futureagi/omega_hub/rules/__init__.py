"""The one rules file (SPEC §6.8): every surface that shows a rule reads it from here.

`rules-v<N>.json` files sit next to this module. The newest is current; older versions stay
loadable so `GET /rules?version=N` and stored decisions can be explained later.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent
RULES_SCHEMA = "omega-hub-rules/v1"
_FILE = re.compile(r"^rules-v(\d+)\.json$")


@lru_cache(maxsize=None)
def _all() -> dict[int, dict]:
    found: dict[int, dict] = {}
    for path in RULES_DIR.iterdir():
        match = _FILE.match(path.name)
        if not match:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != RULES_SCHEMA or data.get("version") != int(match.group(1)):
            raise ValueError(f"{path.name}: schema or version does not match its file name")
        found[data["version"]] = data
    if not found:
        raise ValueError("no rules-v<N>.json file in omega_hub/rules")
    return found


def versions() -> list[int]:
    return sorted(_all())


def current_version() -> int:
    return versions()[-1]


def get_rules(version: int | None = None) -> dict:
    """The rules document of `version` (default: current). Raises KeyError for an unknown version."""
    return _all()[current_version() if version is None else int(version)]


def rules_bytes(version: int | None = None) -> bytes:
    rules = get_rules(version)
    return (json.dumps(rules, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def rules_etag(version: int | None = None) -> str:
    return '"' + hashlib.sha256(rules_bytes(version)).hexdigest()[:32] + '"'


def checklist_keys(version: int | None = None) -> list[str]:
    return [item["key"] for item in get_rules(version)["checklist"]]


def trending_weights() -> dict[str, int]:
    return dict(get_rules()["trending"]["weights"])


def contacts() -> dict:
    return dict(get_rules()["contacts"])
