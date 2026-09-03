"""Sim -> fi-collector OTLP export: ids/parent survive, nested attrs coerced, auth/resource honored."""

import json

import pytest

from simulate.services import sim_collector_emit as sce


@pytest.mark.unit
def test_otlp_attributes_coerces_nested_and_drops_none():
    out = sce._otlp_attributes(
        {
            "input.value": "hi",
            "gen_ai.usage.input_tokens": 5,
            "metadata": {"call_execution_id": "abc", "simulation_modality": "chat"},
            "tags": ["a", "b"],
            "dropme": None,
        }
    )
    assert out["input.value"] == "hi"
    assert out["gen_ai.usage.input_tokens"] == 5
    assert out["tags"] == ["a", "b"]
    # Nested dict JSON-serialized (OTLP cannot carry a dict value).
    assert json.loads(out["metadata"])["call_execution_id"] == "abc"
    assert "dropme" not in out


@pytest.mark.unit
def test_readable_span_preserves_deterministic_ids_and_parent():
    from opentelemetry.sdk.resources import Resource

    trace_hex = "0123456789abcdef0123456789abcdef"  # 32 hex = 128-bit
    span_hex = "1122334455667788"  # 16 hex = 64-bit
    parent_hex = "8877665544332211"
    span = {
        "trace_id": trace_hex,
        "span_id": span_hex,
        "parent_span_id": parent_hex,
        "name": "chat.turn 1",
        "attributes": {"gen_ai.span.kind": "LLM", "output.value": "hello"},
        "start_time": 1_000_000_000,
        "end_time": 2_000_000_000,
    }
    rs = sce._readable_span(span, Resource.create({}))
    assert rs.context.trace_id == int(trace_hex, 16)
    assert rs.context.span_id == int(span_hex, 16)
    assert rs.parent is not None
    assert rs.parent.span_id == int(parent_hex, 16)
    assert rs.start_time == 1_000_000_000
    assert rs.attributes["output.value"] == "hello"


@pytest.mark.unit
def test_root_span_has_no_parent():
    from opentelemetry.sdk.resources import Resource

    span = {
        "trace_id": "0123456789abcdef0123456789abcdef",
        "span_id": "1122334455667788",
        "parent_span_id": None,
        "name": "chat simulation",
        "attributes": {"gen_ai.span.kind": "AGENT"},
        "start_time": 1,
        "end_time": 2,
    }
    rs = sce._readable_span(span, Resource.create({}))
    assert rs.parent is None


@pytest.mark.unit
def test_export_sim_spans_passes_auth_and_resource(monkeypatch):
    captured = {}

    class _FakeExporter:
        def __init__(self, *, endpoint, insecure, headers):
            captured["endpoint"] = endpoint
            captured["headers"] = headers

        def export(self, spans):
            captured["spans"] = list(spans)
            captured["resource"] = captured["spans"][0].resource
            from opentelemetry.sdk.trace.export import SpanExportResult

            return SpanExportResult.SUCCESS

        def shutdown(self):
            captured["shutdown"] = True

    monkeypatch.setattr(sce, "OTLPSpanExporter", _FakeExporter)

    spans = [
        {
            "trace_id": "0123456789abcdef0123456789abcdef",
            "span_id": "1122334455667788",
            "parent_span_id": None,
            "name": "chat simulation",
            "attributes": {"gen_ai.span.kind": "AGENT"},
            "start_time": 1,
            "end_time": 2,
        }
    ]
    n = sce.export_sim_spans(
        spans,
        project_name="Simulations",
        project_type="observe",
        api_key="ak_123",
        secret_key="sk_456",
    )
    assert n == 1
    assert dict(captured["headers"])["x-api-key"] == "ak_123"
    assert dict(captured["headers"])["x-secret-key"] == "sk_456"
    res_attrs = captured["resource"].attributes
    assert res_attrs[sce.RES_PROJECT_NAME] == "Simulations"
    assert res_attrs[sce.RES_PROJECT_TYPE] == "observe"
    assert captured["shutdown"] is True


@pytest.mark.unit
def test_export_sim_spans_returns_zero_on_failure(monkeypatch):
    class _FailExporter:
        def __init__(self, **kwargs):
            pass

        def export(self, spans):
            from opentelemetry.sdk.trace.export import SpanExportResult

            return SpanExportResult.FAILURE

        def shutdown(self):
            pass

    monkeypatch.setattr(sce, "OTLPSpanExporter", _FailExporter)
    n = sce.export_sim_spans(
        [
            {
                "trace_id": "0123456789abcdef0123456789abcdef",
                "span_id": "1122334455667788",
                "parent_span_id": None,
                "name": "x",
                "attributes": {},
                "start_time": 1,
                "end_time": 2,
            }
        ],
        project_name="p",
        project_type="observe",
        api_key="a",
        secret_key="b",
    )
    assert n == 0


@pytest.mark.unit
def test_export_sim_spans_empty_is_noop():
    assert (
        sce.export_sim_spans(
            [], project_name="p", project_type="observe", api_key="a", secret_key="b"
        )
        == 0
    )
