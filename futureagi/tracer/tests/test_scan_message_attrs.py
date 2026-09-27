"""Pin which raw span attributes survive the fetch layer (no DB).

`_ch_span_to_span` decides what the judge is allowed to see. Two filters there
were silently discarding real content in production:

  * `_MESSAGE_ATTR_RE` matched only keys ending `.role` or `.content`, so
    structured multi-part content and per-message `tool_calls` were dropped —
    834/6,266 spans (13.3%) on the 2026-08-18 prod corpus.
  * exception attributes were never read at all. The judge saw
    `status_code=Error` and never the reason, on 142/6,266 spans (2.27%).

Fixtures are CH-shaped: attributes arrive in the `attrs_string` Map column.
"""

from datetime import datetime

from tracer.queries.trace_scanner import _ch_span_to_span
from tracer.services.clickhouse.v2.span_reader import CHSpan

_MSG = "gen_ai.input.messages.0.message"


def _span(**overrides) -> CHSpan:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "project_id": "22222222-2222-2222-2222-222222222222",
        "trace_id": "33333333-3333-3333-3333-333333333333",
        "parent_span_id": "",
        "name": "chat",
        "observation_type": "llm",
        "operation_name": "chat",
        "start_time": datetime(2026, 8, 18, 1, 2, 3),
        "end_time": datetime(2026, 8, 18, 1, 2, 4),
        "latency_ms": 1000,
        "model": "gemini-3.6-flash",
        "provider": "vertex_ai",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "status": "OK",
        "status_message": "",
        "org_id": None,
        "project_version_id": None,
        "end_user_id": None,
        "trace_session_id": None,
        "prompt_version_id": None,
        "prompt_label_id": None,
        "custom_eval_config_id": None,
        "input": "hi",
        "output": "hello",
        "tags": "[]",
        "span_events": "",
        "metadata": "{}",
        "resource_attrs": "{}",
        "attributes_extra": "{}",
        "trace_name": "my-trace",
    }
    base.update(overrides)
    return CHSpan(**base)


def _attrs(attrs_string: dict) -> dict:
    return _ch_span_to_span(_span(attrs_string=attrs_string)).span_attributes


class TestPerMessageAttributesSurvive:
    def test_role_and_content_still_pass(self):
        """The pre-existing behaviour must not regress."""
        out = _attrs({f"{_MSG}.role": "user", f"{_MSG}.content": "where is my refund"})
        assert out[f"{_MSG}.role"] == "user"
        assert out[f"{_MSG}.content"] == "where is my refund"

    def test_structured_multipart_content_passes(self):
        """These messages carry no flat .content — dropping this key hides the whole turn."""
        key = f"{_MSG}.contents.0.message_content.text"
        assert key in _attrs({key: "describe this image"}), (
            "structured message content was filtered out before the judge"
        )

    def test_per_message_tool_calls_pass(self):
        name = f"{_MSG}.tool_calls.0.tool_call.function.name"
        args = f"{_MSG}.tool_calls.0.tool_call.function.arguments"
        out = _attrs({name: "search_pokedex", args: '{"q":"krabby"}'})
        assert name in out, "the judge could not see which tool a message invoked"
        assert args in out, "the judge could not see what the tool was called with"

    def test_openinference_messages_pass(self):
        key = "llm.output_messages.0.message.tool_calls.0.tool_call.function.name"
        assert key in _attrs({key: "lookup_order"})

    def test_unrelated_attributes_are_still_dropped(self):
        """Widening the filter must not turn it into a passthrough."""
        out = _attrs({"some.vendor.debug.blob": "x", "llm.usage.total": "42"})
        assert "some.vendor.debug.blob" not in out
        assert "llm.usage.total" not in out


class TestExceptionAttributesAreRead:
    def test_standard_otel_exception_attrs_pass(self):
        out = _attrs(
            {
                "exception.type": "TimeoutError",
                "exception.message": "upstream did not respond in 30s",
                "exception.stacktrace": "Traceback...",
            }
        )
        assert out["exception.type"] == "TimeoutError"
        assert out["exception.message"] == "upstream did not respond in 30s"
        assert out["exception.stacktrace"] == "Traceback..."

    def test_a_non_standard_exception_key_is_not_swept_in(self):
        assert "exception.custom_vendor_field" not in _attrs(
            {"exception.custom_vendor_field": "noise"}
        )
