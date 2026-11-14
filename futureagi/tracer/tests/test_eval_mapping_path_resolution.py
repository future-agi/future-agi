"""Regression tests for the span and trace path resolvers behind eval mappings.

Two fixes in ``tracer/utils/eval.py`` shipped without tests:

- #455: a picker path into a JSON-string flat key, such as
  ``input.value.messages.0.content``, must parse the string and walk into it.
- #471: a span column such as ``latency_ms``, ``cost`` or ``model`` must resolve
  from the model when ``span_attributes`` does not carry it, including through a
  trace path such as ``spans.1.latency_ms``.

The spans are shaped like real SDK ingestion: OpenInference flat keys whose
values are JSON strings, a nested metadata dict, and the numeric columns on the
model. They are unsaved, so these tests need no database.
"""

import json
import uuid

import pytest

from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.utils.eval import (
    _MISSING,
    _resolve_span_path,
    _resolve_trace_path,
    _trace_span_memo,
)

pytestmark = pytest.mark.unit

PROJECT_ID = uuid.uuid4()

LLM_ATTRIBUTES = {
    "input.value": json.dumps(
        {
            "messages": [
                {"role": "system", "content": "You are a support agent."},
                {"role": "user", "content": "What is the refund policy?"},
            ]
        }
    ),
    "input.mime_type": "application/json",
    "output.value": json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Refunds are accepted within 30 days.",
                    }
                }
            ]
        }
    ),
    "llm.model_name": "gpt-4o-mini",
    "llm.token_count.prompt": 1200,
    "metadata": {"customer_tier": "pro", "tags": ["refund", "billing"]},
}


def llm_span(**overrides):
    fields = {
        "id": str(uuid.uuid4()),
        "name": "ChatCompletion",
        "observation_type": "llm",
        "span_attributes": dict(LLM_ATTRIBUTES),
        "latency_ms": 812,
        "prompt_tokens": 1200,
        "completion_tokens": 42,
        "total_tokens": 1242,
        "cost": 0.0042,
        "model": "gpt-4o-mini",
        "provider": "openai",
        "status": "OK",
    }
    fields.update(overrides)
    return ObservationSpan(**fields)


def tool_span():
    return ObservationSpan(
        id=str(uuid.uuid4()),
        name="issue_refund",
        observation_type="tool",
        span_attributes={"output.value": "Refund issued for order 1182."},
        latency_ms=95,
    )


class TestJsonStringFlatKeys:
    """#455: picker paths inside JSON-string ``input.value`` / ``output.value``."""

    def test_the_flat_key_itself_returns_the_raw_string(self):
        assert (
            _resolve_span_path(llm_span(), "input.value")
            == LLM_ATTRIBUTES["input.value"]
        )

    def test_a_path_inside_the_json_string_is_walked(self):
        assert (
            _resolve_span_path(llm_span(), "input.value.messages.1.content")
            == "What is the refund policy?"
        )

    def test_a_path_without_the_value_suffix_reads_the_value_key(self):
        assert _resolve_span_path(llm_span(), "input.messages.0.role") == "system"

    def test_output_paths_resolve_the_same_way(self):
        assert (
            _resolve_span_path(llm_span(), "output.value.choices.0.message.content")
            == "Refunds are accepted within 30 days."
        )

    def test_the_legacy_span_attributes_head_uses_the_same_resolver(self):
        assert (
            _resolve_span_path(
                llm_span(), "span_attributes.input.value.messages.1.role"
            )
            == "user"
        )

    def test_a_nested_dict_attribute_is_walked_without_parsing(self):
        assert _resolve_span_path(llm_span(), "metadata.tags.1") == "billing"

    def test_a_plain_text_value_is_not_walked_into(self):
        assert _resolve_span_path(tool_span(), "output.value.choices.0") is _MISSING

    def test_an_index_past_the_end_is_a_miss(self):
        assert (
            _resolve_span_path(llm_span(), "input.value.messages.5.content") is _MISSING
        )


class TestSpanColumns:
    """#471: span columns resolve from the model when the attributes lack them."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("latency_ms", 812),
            ("prompt_tokens", 1200),
            ("completion_tokens", 42),
            ("total_tokens", 1242),
            ("cost", 0.0042),
            ("model", "gpt-4o-mini"),
            ("provider", "openai"),
            ("name", "ChatCompletion"),
            ("observation_type", "llm"),
            ("status", "OK"),
        ],
    )
    def test_a_column_resolves_from_the_model(self, path, expected):
        assert _resolve_span_path(llm_span(), path) == expected

    def test_an_attribute_with_the_same_name_wins_over_the_column(self):
        attributes = dict(LLM_ATTRIBUTES, model="model-from-attributes")
        span = llm_span(span_attributes=attributes)
        assert _resolve_span_path(span, "model") == "model-from-attributes"

    def test_a_path_below_a_column_is_a_miss(self):
        assert _resolve_span_path(llm_span(), "latency_ms.value") is _MISSING

    def test_a_model_attribute_outside_the_allow_list_is_a_miss(self):
        assert _resolve_span_path(llm_span(), "trace_id") is _MISSING

    def test_an_unknown_path_is_a_miss(self):
        assert _resolve_span_path(llm_span(), "does.not.exist") is _MISSING


class TestLegacyEvalAttributes:
    def test_spans_with_only_eval_attributes_still_resolve(self):
        span = llm_span(span_attributes={}, eval_attributes=dict(LLM_ATTRIBUTES))
        assert _resolve_span_path(span, "input.value.messages.1.role") == "user"


@pytest.fixture
def trace_with_spans():
    trace = Trace(id=uuid.uuid4(), project_id=PROJECT_ID)
    spans = [llm_span(), tool_span()]
    token = _trace_span_memo.set({f"{trace.project_id}:{trace.id}": spans})
    yield trace
    _trace_span_memo.reset(token)


class TestTracePaths:
    """Trace mappings reach spans through ``spans.<n>.<path>``."""

    def test_a_span_column_resolves_through_the_trace(self, trace_with_spans):
        assert _resolve_trace_path(trace_with_spans, "spans.1.latency_ms") == 95

    def test_a_json_string_path_resolves_through_the_trace(self, trace_with_spans):
        assert (
            _resolve_trace_path(
                trace_with_spans, "spans.0.input.value.messages.1.content"
            )
            == "What is the refund policy?"
        )

    def test_first_and_last_select_by_position(self, trace_with_spans):
        assert _resolve_trace_path(trace_with_spans, "spans.first.cost") == 0.0042
        assert (
            _resolve_trace_path(trace_with_spans, "spans.last.name") == "issue_refund"
        )

    def test_an_index_past_the_last_span_is_a_miss(self, trace_with_spans):
        assert _resolve_trace_path(trace_with_spans, "spans.2.latency_ms") is _MISSING
