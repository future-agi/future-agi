"""Integer and date-time settings read from environment variables.

Stdlib, django.core.exceptions and runtime_setting_specs (stdlib-only) only,
so settings.py and the Django-free CLIs (the outbox CDC installer) can use it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime

from django.core.exceptions import ImproperlyConfigured

from tfc.settings.runtime_setting_specs import NumericSettingSpec


def env_int(
    name: str,
    default: int,
    *,
    env: Mapping[str, str] | None = None,
    minimum: int | None = None,
) -> int:
    """``name`` from ``env`` (default ``os.environ``) as an int.

    Parsed as NumericSettingSpec parses the runtime numeric settings: unset or
    blank means ``default``; a value that is not an integer, is below
    ``minimum`` or does not fit in 64 bits raises ImproperlyConfigured naming
    the variable, never a bare ValueError and never a silent fallback.
    """
    spec = NumericSettingSpec(
        int,
        default,
        -sys.maxsize - 1 if minimum is None else minimum,
        sys.maxsize,
    )
    try:
        return int(spec.parse(name, (os.environ if env is None else env).get(name)))
    except ValueError as exc:
        raise ImproperlyConfigured(str(exc)) from None


def env_datetime(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
) -> datetime | None:
    """``name`` from ``env`` (default ``os.environ``) as an aware datetime.

    Unset or blank means ``None``. The value is an ISO-8601 date and time; ``Z``
    or an offset keeps its zone and a value without one is UTC, so it compares
    with the database's aware times. Anything else, a date without a time
    included, raises ImproperlyConfigured naming the variable.
    """
    raw = ((os.environ if env is None else env).get(name) or "").strip()
    if not raw:
        return None
    invalid = ImproperlyConfigured(
        f"{name} must be an ISO-8601 date-time, such as 2026-10-14T12:30:00Z"
    )
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise invalid from None
    if _is_date_only(raw):
        raise invalid
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


def _is_date_only(raw: str) -> bool:
    try:
        date.fromisoformat(raw)
    except ValueError:
        return False
    return True
