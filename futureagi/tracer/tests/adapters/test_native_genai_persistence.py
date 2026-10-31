"""Replay real Anthropic C# native OTLP through Python ingestion and PostgreSQL.

Fixtures: published Anthropic 12.56.0 (tracing floor) / 13.3.0, .NET 8,
OpenTelemetry.Exporter.OpenTelemetryProtocol 1.20.1, synthetic JSON/SSE on
loopback, outbound networking denied. Generated with traceAI PR #447's
csharp/tests/AnthropicNativeContract/wire.py. The .pb files are unchanged
exporter request bodies. This is not an HTTP/ClickHouse/UI certification.
"""

import json
from pathlib import Path

import pytest

from model_hub.models.ai_model import AIModel
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.services.clickhouse.v2.deterministic_id import (
    deterministic_trace_session_id,
)
from tracer.tests.adapters.test_e2e_adapters import ingest_payload_sync
from tracer.utils.parsers import deserialize_trace_payload

FIXTURES = Path(__file__).parent.parent / "fixtures" / "anthropic_csharp"


@pytest.mark.django_db
@pytest.mark.parametrize("version", ["12.56.0", "13.3.0"])
@pytest.mark.parametrize(
    "case",
    [
        "content",
        "concurrent",
        "tool-content",
        "tool-metadata",
        "binary-content",
        "truncation",
    ],
)
def test_captured_native_spans_persist(version, case, organization, user, workspace):
    project = Project.objects.create(
        name="anthropic-csharp-contract",
        organization=organization,
        workspace=workspace,
        trace_type="observe",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
    )
    payload = deserialize_trace_payload(
        (FIXTURES / f"{version}-{case}.pb").read_bytes(), "protobuf"
    )
    converted = ingest_payload_sync(
        payload, str(organization.id), str(user.id), str(workspace.id)
    )
    native = []
    sessions = set()
    for row in converted:
        mapped = row["observation_span"]
        span = ObservationSpan.objects.select_related("trace").get(pk=mapped["id"])
        assert span.project_id == project.id
        assert span.org_id == organization.id
        assert span.parent_span_id == mapped["parent_span_id"]
        attrs = span.span_attributes
        operation = attrs.get("gen_ai.operation.name")
        if not operation:
            continue  # The fixture's application parent is not an SDK observation.
        native.append(span)
        if operation == "invoke_agent":
            assert span.observation_type == "agent"
            assert span.total_tokens is None
        elif operation == "execute_tool":
            assert span.observation_type == "tool"
            if case == "tool-content":
                assert span.input == {}
                assert span.output == [{"type": "text", "text": "TOOL_RESULT_SENTINEL"}]
        else:
            assert operation == "chat" and span.observation_type == "llm"
            assert (span.prompt_tokens, span.completion_tokens, span.total_tokens) == (
                12,
                5,
                17,
            )
            if case in {"content", "tool-content", "binary-content"}:
                assert span.input is not None and span.output is not None
                assert "SENTINEL" in json.dumps(span.input)
                assert "SENTINEL" in json.dumps(
                    span.output
                ) or "tool_call" in json.dumps(span.output)
        if case in {"concurrent", "tool-metadata"}:
            assert span.input is None and span.output is None
        if case == "concurrent":
            conversation = attrs["gen_ai.conversation.id"]
            assert row["session_name"] == conversation
            assert span.trace.session_id == deterministic_trace_session_id(
                project.id, conversation
            )
            sessions.add(span.trace.session_id)
        if case == "truncation":
            assert attrs["anthropic.content.truncated"] is True
            assert "PROMPT_SENTINEL" not in json.dumps(span.input)
        assert "BINARY_SENTINEL" not in json.dumps([span.input, span.output])
    assert native
    if case.startswith("tool-"):
        assert sorted(span.observation_type for span in native) == [
            "agent",
            "llm",
            "llm",
            "tool",
        ]
        runner = next(span for span in native if span.observation_type == "agent")
        assert all(
            span.parent_span_id == runner.id for span in native if span != runner
        )
    if case == "concurrent":
        assert len(sessions) == 2
