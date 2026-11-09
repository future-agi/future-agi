"""Span contexts retain identifiers while export copies drop sensitive state."""

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import (
    Link,
    SpanContext,
    Status,
    StatusCode,
    TraceFlags,
    TraceState,
)

from tfc.telemetry.redaction import redact_span

pytestmark = pytest.mark.integration
VALUE = "synthetic-context-storage-value"
PATH = "/agentcc/managed-credits/checkout/" + VALUE + "/"


@pytest.mark.parametrize("field", ["context", "parent", "link"])
@pytest.mark.parametrize("placement", ["key", "bare-value", "url-value"])
@pytest.mark.parametrize("checkout", [True, False])
def test_context_storage_export_copy(field, placement, checkout):
    entry = (
        (VALUE, "ordinary")
        if placement == "key"
        else ("vendor", PATH if placement == "url-value" and checkout else VALUE)
    )
    original = SpanContext(
        123, 456, True, TraceFlags(1), TraceState([entry, ("safe", "ok")])
    )
    ordinary = SpanContext(123, 789, False, TraceFlags(1))
    span = ReadableSpan(
        name="work",
        context=original if field == "context" else ordinary,
        parent=original if field == "parent" else None,
        links=[Link(original)] if field == "link" else [],
        attributes={"http.url": PATH} if checkout and placement != "url-value" else {},
        status=Status(StatusCode.UNSET),
    )
    before = original.trace_state.to_header()
    exported = redact_span(span)
    result = exported.links[0].context if field == "link" else getattr(exported, field)
    assert original.trace_state.to_header() == before
    for attribute in ("trace_id", "span_id", "is_remote", "trace_flags", "is_valid"):
        assert getattr(result, attribute) == getattr(original, attribute)
    assert result.trace_state.get("safe") == "ok"
    if checkout:
        assert VALUE not in result.trace_state.to_header()
        assert result is not original
    else:
        assert result is original
        assert exported is span
