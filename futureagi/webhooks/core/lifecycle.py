"""Retention windows and member erasure as pure rules (DESIGN.md sections 7.5 and 13.4; P1, P4, R12).

The adapters apply them in chunks (``webhooks.retention`` and ``webhooks.lifecycle``); nothing here touches storage.

**Erasure (P1).** People appear in a body as ``{"id": <user id>, "name": <display name or null>}`` (or ``{"id"}``
with names off; ``payloads.person``). Erasing a person nulls ``name`` wherever ``id`` is theirs and re-serializes the
body with the same canonical writer, so every other byte stays as it was; a later redelivery sends the redacted body,
signed afresh. Receiver excerpts are free text: the names found in the bodies are removed from them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from .payloads import canonical
from .schedule import _ms

BODY_DAYS = 30
"""``webhooks_payload.body`` is dropped after 30 days; its hash and size stay (section 7.5)."""
ZERO_DELIVERY_EVENT_DAYS = 7
"""Events that reached no endpoint are deleted after 7 days (R12)."""
LOG_DAYS_DEFAULT = 90
LOG_DAYS_RANGE = (7, 365)
PURGE_CHUNK = 1000
"""Rows deleted per statement and transaction; purges never run as one long transaction (section 13.4)."""
LISTEN_IDLE_HOURS = 24
"""``cli_listen`` endpoints are deleted 24 h after their last poll (WH-4b builds the kind)."""
ERASED_TEXT = ""


def clamp_log_days(days: int | None) -> int:
    lo, hi = LOG_DAYS_RANGE
    if days is None:
        return LOG_DAYS_DEFAULT
    return max(lo, min(hi, int(days)))


def cutoffs(now_ms: int, log_days: int | None, scale: float = 1.0) -> dict[str, int]:
    """Epoch-ms cutoffs: rows older than each are dropped or deleted."""
    day = 86_400
    return {
        "body": now_ms - _ms(BODY_DAYS * day, scale),
        "zero_delivery_event": now_ms - _ms(ZERO_DELIVERY_EVENT_DAYS * day, scale),
        "log": now_ms - _ms(clamp_log_days(log_days) * day, scale),
        "listen_idle": now_ms - _ms(LISTEN_IDLE_HOURS * 3600, scale),
    }


def erase_person(obj: Any, person_id: str) -> tuple[Any, list[str]]:
    """``obj`` with ``name`` set to null in every person ``{"id": person_id, "name": ...}``, and the names it held.
    A person is the value of a ``*_by`` key (``created_by``, ``changed_by``: ``payloads.person``); a prompt or a label
    with the same id is not a person and is left alone."""
    names: list[str] = []

    def walk(v: Any, key: str = "") -> Any:
        if isinstance(v, dict):
            out = {k: walk(x, k) for k, x in v.items()}
            if (
                key.endswith("_by")
                and str(out.get("id")) == person_id
                and "name" in out
            ):
                if isinstance(out["name"], str) and out["name"]:
                    names.append(out["name"])
                out["name"] = None
            return out
        if isinstance(v, list):
            return [walk(x, key) for x in v]
        return v

    return walk(obj), names


def erase_body(body: bytes, person_id: str) -> tuple[bytes | None, list[str]]:
    """The body re-written without the person's name, or None when it never named them."""
    obj = json.loads(body)
    new, names = erase_person(obj, person_id)
    if new == obj and not names:
        return None, []
    out = canonical(new)
    return (None, []) if out == body else (out, names)


def scrub_names(text: str | None, names: Iterable[str]) -> str | None:
    """Free text (a receiver excerpt) without the given names; names shorter than 2 characters are left alone."""
    if text is None:
        return None
    for n in sorted({n for n in names if len(n) >= 2}, key=len, reverse=True):
        text = text.replace(n, ERASED_TEXT)
    return text
