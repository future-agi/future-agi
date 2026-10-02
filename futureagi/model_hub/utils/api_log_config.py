"""Helper for reading APICallLog.config safely.

``APICallLog.config`` is a JSONField, but its rows are a mix of shapes:
older write paths ``json.dumps``-ed the payload before saving (double
encoding it into a JSON *string*), the composite path stores a plain dict,
and the version backfill migration (0115) rewrites the double-encoded rows
into dicts. Any code that reads a *stored* row therefore has to accept both
a dict and a JSON-encoded string. Bare ``json.loads(log.config)`` crashes
on the dict rows.
"""

import json
from typing import Any


def parse_api_log_config(raw: Any) -> dict:
    """Return ``raw`` as a dict, tolerating double-encoded / malformed rows.

    Returns ``{}`` on anything unparseable so one bad row can't break a
    whole response.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
