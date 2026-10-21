"""Integer settings read from environment variables.

Stdlib, django.core.exceptions and runtime_setting_specs (stdlib-only) only,
so settings.py and the Django-free CLIs (the outbox CDC installer) can use it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

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
