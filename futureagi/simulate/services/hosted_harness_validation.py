"""Non-mutating validation at hosted harness persistence boundaries."""

from __future__ import annotations

import math
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from simulate.services.hosted_harness import HostedHarnessError


def validate_jsonb_strings(value: object, *, field: str) -> None:
    """Reject text JSONB cannot store, including keys, without exposing content.

    Check before hashing or encoding: lone surrogates also fail UTF-8 encoding.
    Never repair the input here, since callers may be verifying its digest.
    ``field`` must be a server-defined label, not a key taken from the input.
    """
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if "\x00" in item:
                raise HostedHarnessError(
                    "invalid_jsonb_string",
                    f"{field} contains U+0000; encode binary output before sending",
                    status_code=400,
                )
            if any("\ud800" <= char <= "\udfff" for char in item):
                raise HostedHarnessError(
                    "invalid_jsonb_string",
                    f"{field} contains an invalid Unicode surrogate",
                    status_code=400,
                )
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise HostedHarnessError(
                "invalid_jsonb_number",
                f"{field} contains a non-finite number",
                status_code=400,
            )


def receipt_timestamp(value: object, *, field: str) -> datetime:
    """Accept aware datetimes or timezone-bearing ISO timestamps, without coercing JSON."""
    parsed = value
    if isinstance(value, str):
        try:
            parsed = parse_datetime(value)
        except (ValueError, OverflowError):
            parsed = None
    if not isinstance(parsed, datetime) or timezone.is_naive(parsed):
        raise HostedHarnessError(
            "invalid_receipt_timestamp",
            f"{field} must be a valid timestamp with a timezone",
            status_code=400,
        )
    return parsed


def receipt_call_times(call_data: object) -> tuple[datetime, datetime]:
    """Validate the offline receipt's call before any of its fields are persisted."""
    if not isinstance(call_data, dict):
        raise HostedHarnessError(
            "invalid_receipt_timestamp",
            "call must contain started_at and ended_at timestamps with timezones",
            status_code=400,
        )
    return (
        receipt_timestamp(call_data.get("started_at"), field="call.started_at"),
        receipt_timestamp(call_data.get("ended_at"), field="call.ended_at"),
    )
