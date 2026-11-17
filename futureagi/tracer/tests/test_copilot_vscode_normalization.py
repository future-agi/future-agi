"""Sanitized native VS Code 1.143.1 capture: five requests, not ten."""

import json
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.protobuf.json_format import MessageToDict, Parse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from tracer.utils.adapters import normalize_span_attributes
from tracer.utils.normalization import normalize_attributes
from tracer.utils.otel import _convert_single_span
from tracer.utils.trace_ingestion import _parse_otel_request


def test_real_vscode_capture_retains_spans_and_counts_requests_once():
    payload = json.loads(
        files("tracer.utils.normalization")
        .joinpath("vscode-copilot-1.0.100-1.json")
        .read_text()
    )
    # Match OTLPTraceView's protobuf JSON -> snake-case processing boundary.
    request = Parse(json.dumps(payload), ExportTraceServiceRequest())
    parsed = _parse_otel_request(
        MessageToDict(request, preserving_proto_field_name=True)
    )
    normalize_span_attributes(parsed)
    project = SimpleNamespace(id="project")
    with patch("tracer.utils.otel.calculate_cost_from_tokens", return_value=0):
        rows = [
            _convert_single_span(
                span,
                {("sanitized-vscode-copilot-capture", "org", "observe"): project},
                {},
                "org",
            )["observation_span"]
            for span in parsed
        ]
    assert len(rows) == 91
    assert sum(row["prompt_tokens"] or 0 for row in rows) == 87533
    assert sum(row["completion_tokens"] or 0 for row in rows) == 540
    assert (
        sum(
            (row["prompt_tokens"] or 0) + (row["completion_tokens"] or 0)
            for row in rows
        )
        == 88073
    )
    assert sum(row["observation_type"] == "llm" for row in rows) == 5
    assert sum(row["observation_type"] == "internal" for row in rows) == 72
    summary = next(row for row in rows if row["name"] == "invoke_agent")
    assert summary["observation_type"] == "agent"
    assert not summary["prompt_tokens"]
    assert not summary["completion_tokens"]


@pytest.mark.parametrize("version", ["1.0.99", "1.0.100", "1.0.107", "2.0.0", ""])
def test_unqualified_copilot_versions_keep_usage_and_timing_visible(version):
    attrs = {"gen_ai.operation.name": "invoke_agent", "gen_ai.usage.input_tokens": 193}
    assert (
        normalize_attributes(attrs, {"name": "github.copilot", "version": version})
        == attrs
    )
    assert (
        normalize_attributes(
            {},
            {"name": "github.copilot", "version": version},
            name="session.timing.model_resolution",
        )
        == {}
    )


@pytest.mark.parametrize(
    "attrs",
    [
        {"gen_ai.operation.name": "chat"},
        {"gen_ai.usage.input_tokens": 1},
        {"gen_ai.usage.input_tokens": 0},
        {"openinference.span.kind": "TOOL"},
        {"llm.cost.total": 0.1},
    ],
)
def test_timing_name_does_not_hide_real_work(attrs):
    result = normalize_attributes(
        attrs,
        {"name": "github.copilot", "version": "1.0.100-1"},
        name="session.timing.model_resolution",
    )
    assert result.get("fi.observation.category") is None
    assert result.get("fi.span.kind") != "INTERNAL"


def test_non_timing_host_activity_stays_visible():
    attrs = normalize_attributes(
        {}, {"name": "vscode.agent-host"}, name="vscode.agent_host.session"
    )
    assert attrs == {"fi.normalization.profile": "vscode-agent-host-unversioned"}
