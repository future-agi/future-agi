"""Shared conformance vectors plus actual Python ingestion/conversion regressions."""

import copy
import json
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.protobuf.json_format import Parse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from tracer.utils.adapters import normalize_span_attributes
from tracer.utils.normalization import (
    CONTRACT,
    normalize_attributes,
    requires_immutable_admission,
)
from tracer.utils.normalization.details import store_details
from tracer.utils.otel import _convert_single_span, calculate_cost
from tracer.utils.trace_ingestion import _parse_otel_request
from tracer.views.otlp import (
    OTLPExportResult,
    OTLPTraceView,
    _requires_immutable_admission,
)


@pytest.mark.parametrize(
    "vector",
    json.loads(
        files("tracer.utils.normalization").joinpath("vectors.json").read_text()
    ),
    ids=lambda v: v["name"],
)
def test_shared_vectors(vector):
    attrs = normalize_attributes(vector["attributes"], vector["scope"])
    assert attrs == vector["expected"]
    assert normalize_attributes(attrs, vector["scope"]) == attrs


def test_none_suppresses_all_accounting_aliases_and_fallback():
    attrs = normalize_attributes(
        {
            "fi.usage.contribution": "none",
            "gen_ai.cost.total": 17,
            "llm.token_count.prompt": 200,
            "gen_ai.usage.input_tokens": 200,
            "gen_ai.usage.output_tokens": 30,
            "llm.cost.completion": 4,
            "gen_ai.usage.cache_read_tokens": 100,
            "llm.model_name": "test-model",
        }
    )
    with patch("tracer.utils.otel.calculate_cost_from_tokens") as price:
        assert calculate_cost(attrs, None, None, "test-model") == 0
        price.assert_not_called()
    assert "gen_ai.cost.total" not in attrs  # not fake explicit USD zero
    assert attrs["fi.source.summary.gen_ai.cost.total"] == 17
    assert attrs["fi.cost.kind"] == "unknown"


def test_parent_only_usage_without_summary_profile_is_not_discarded():
    attrs = {"fi.span.kind": "AGENT", "gen_ai.usage.input_tokens": 100}
    assert normalize_attributes(attrs) == attrs


def test_cache_usage_is_not_guessed_with_two_count_pricing():
    attrs = normalize_attributes(
        {"gen_ai.usage.cache_read_tokens": 40}, {"name": "gemini-cli", "version": "v1"}
    )
    with patch("tracer.utils.otel.calculate_cost_from_tokens") as price:
        assert calculate_cost(attrs, 100, 20, "test-model") == 0
        price.assert_not_called()
    attrs["gen_ai.cost.total"] = 0.125
    assert calculate_cost(attrs, 100, 20, "test-model") == 0.125


def test_parse_normalize_convert_preserves_scope_session_and_accounting():
    def kv(key, value):
        return {
            "key": key,
            "value": {"string_value" if isinstance(value, str) else "int_value": value},
        }

    spans = []
    for index, (operation, input_tokens, output_tokens) in enumerate(
        [
            ("invoke_agent", 42925, 146),
            ("chat", 14095, 61),
            ("chat", 14370, 52),
            ("chat", 14460, 33),
        ]
    ):
        spans.append(
            {
                "trace_id": "0123456789abcdef0123456789abcdef",
                "span_id": f"{index + 1:016x}",
                "name": operation,
                "start_time_unix_nano": "1792024600000000000",
                "end_time_unix_nano": "1792024601000000000",
                "attributes": [
                    kv("gen_ai.operation.name", operation),
                    kv("gen_ai.conversation.id", "synthetic-session"),
                    kv("gen_ai.usage.input_tokens", input_tokens),
                    kv("gen_ai.usage.output_tokens", output_tokens),
                ],
            }
        )
    payload = {
        "resource_spans": [
            {
                "resource": {
                    "attributes": [
                        kv("project_name", "test"),
                        kv("project_type", "observe"),
                    ]
                },
                "scope_spans": [
                    {
                        "scope": {"name": "github.copilot", "version": "1.0.98"},
                        "spans": spans,
                    }
                ],
            }
        ]
    }
    parsed = _parse_otel_request(payload)
    assert parsed[0]["scope"]["version"] == "1.0.98"
    assert parsed[0]["resource"]["attributes"]["project_name"] == "test"
    normalize_span_attributes(parsed)
    project = SimpleNamespace(id="project")
    with patch("tracer.utils.otel.calculate_cost_from_tokens", return_value=0):
        converted = [
            _convert_single_span(span, {("test", "org", "observe"): project}, {}, "org")
            for span in parsed
        ]
    rows = [item["observation_span"] for item in converted]
    assert sum(row["prompt_tokens"] or 0 for row in rows) == 42925
    assert sum(row["completion_tokens"] or 0 for row in rows) == 146
    assert sum(row["observation_type"] == "llm" for row in rows) == 3
    assert rows[0]["observation_type"] == "agent"
    assert rows[0]["cost"] == 0
    assert all(item["session_name"] == "synthetic-session" for item in converted)


def test_explicit_kind_not_overwritten_by_model_guess():
    span = {
        "scope": {"name": "gemini-cli", "version": "v1"},
        "attributes": {
            "gen_ai.operation.name": "tool_call",
            "gen_ai.request.model": "test-model",
            "openinference.span.kind": "CHAIN",
        },
    }
    normalize_span_attributes([span])
    assert (
        span["attributes"].get(
            "fi.span.kind", span["attributes"].get("gen_ai.span.kind")
        )
        == "CHAIN"
    )


def test_details_are_bounded_and_keep_only_structural_fields():
    span = {
        "attributes": {"fi.normalization.profile": "test"},
        "events": [
            {
                "name": "exception",
                "attributes": {
                    "exception.type": "Timeout",
                    "exception.message": "private",
                },
            }
            for _ in range(70)
        ],
        "links": [
            {
                "trace_id": "01" * 16,
                "span_id": "02" * 8,
                "attributes": {
                    "fi.agent.caused_by.work_id": "opaque",
                    "unknown": "private",
                },
            }
            for _ in range(40)
        ],
    }
    store_details(span)
    assert len(span["events"]) == 64
    assert span["attributes"]["fi.otel.dropped_events_count"] == 6
    assert span["events"][0]["attributes"] == {"exception.type": "Timeout"}
    assert len(span["attributes"]["fi.otel.links"]) == 32
    assert "private" not in json.dumps(
        {"events": span["events"], "attributes": span["attributes"]}
    )


def test_existing_python_event_content_is_not_removed_for_unqualified_producers():
    span = {
        "attributes": {},
        "events": [
            {
                "name": "exception",
                "attributes": {"exception.message": "existing permitted content"},
            }
        ],
    }
    before = copy.deepcopy(span["events"])
    store_details(span)
    assert span["events"] == before


@pytest.mark.parametrize(
    "key", CONTRACT["reserved_admission_keys"] + ["fi.usage.contribution"]
)
def test_immutable_contract_is_held_before_async_storage(key):
    value = "turn" if key == "fi.usage.contribution" else ""
    assert requires_immutable_admission({key: value})
    req = Parse(
        json.dumps(
            {
                "resourceSpans": [
                    {
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "attributes": [
                                            {
                                                "key": key,
                                                "value": {"stringValue": value},
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ),
        ExportTraceServiceRequest(),
    )
    assert _requires_immutable_admission(req)


def test_all_rejected_is_not_an_empty_success():
    result = OTLPExportResult(
        rejected_spans=2, error_message=CONTRACT["admission_error"]
    )
    assert result.has_partial_success
    request = SimpleNamespace(
        headers={},
        content_type="application/json",
        META={"CONTENT_TYPE": "application/json"},
    )
    response = OTLPTraceView()._success_response(request, result)
    body = json.loads(response.content)
    partial = body.get("partialSuccess", body.get("partial_success"))
    assert int(partial.get("rejectedSpans", partial.get("rejected_spans"))) == 2


def test_whole_genai_messages_fill_content_without_overwriting_explicit_input():
    project = SimpleNamespace(id="project")
    span = {
        "span_id": "01" * 8,
        "trace_id": "02" * 16,
        "project_name": "test",
        "project_type": "observe",
        "start_time": 1792024600000000000,
        "end_time": 1792024601000000000,
        "attributes": {
            "gen_ai.operation.name": "chat",
            "gen_ai.input.messages": '[{"role":"user","content":"synthetic"}]',
            "gen_ai.output.messages": '[{"role":"assistant","content":"response"}]',
        },
    }
    with patch("tracer.utils.otel.calculate_cost_from_tokens", return_value=0):
        result = _convert_single_span(
            span, {("test", "org", "observe"): project}, {}, "org"
        )["observation_span"]
        assert result["input"] == [{"role": "user", "content": "synthetic"}]
        assert result["output"] == [{"role": "assistant", "content": "response"}]
        span["attributes"]["input.value"] = "explicit"
        result = _convert_single_span(
            span, {("test", "org", "observe"): project}, {}, "org"
        )["observation_span"]
        assert result["input"] == "explicit"
