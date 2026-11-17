"""The form a trace id is looked up in."""

import re
import uuid

_OPENTELEMETRY_TRACE_ID = re.compile(r"[0-9a-fA-F]{32}")
_DASHED_TRACE_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def stored_trace_id(value):
    """The span store's form of a trace id: a lowercase dashed UUID string.

    OpenTelemetry writes a trace id as 32 hex digits, and experiment runs
    record it that way. The span store (fi-collector) keeps the same 128 bits
    as a dashed UUID string, which is the form trace reads match. Either form,
    in any case, gives that string. Any other value is returned as given, so
    it is looked up as before and a trace the store doesn't have is not found.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if _OPENTELEMETRY_TRACE_ID.fullmatch(text) or _DASHED_TRACE_ID.fullmatch(text):
        return str(uuid.UUID(text))
    return value
