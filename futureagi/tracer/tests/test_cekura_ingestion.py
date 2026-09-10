"""Tests for Cekura transcript transformation and ingestion."""

import pytest

from integrations.transformers.cekura_transformer import CekuraTransformer
from model_hub.models.ai_model import AIModel
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace


@pytest.fixture
def cekura_run():
    return {
        "id": "cekura-run-42",
        "testName": "Refund flow",
        "metadata": {"suite": "regression"},
        "turns": [
            {
                "id": "turn-user-1",
                "role": "user",
                "content": "I need a refund",
                "timestamp": "2026-07-18T10:00:00Z",
            },
            {
                "id": "turn-agent-1",
                "role": "assistant",
                "content": "Let me look that up.",
                "timestamp": "2026-07-18T10:00:01Z",
                "toolCalls": [
                    {
                        "id": "lookup-order",
                        "name": "lookup_order",
                        "arguments": {"order_id": "123"},
                        "result": {"eligible": True},
                        "status": "completed",
                    }
                ],
            },
        ],
    }


@pytest.mark.unit
def test_transformer_creates_turns_and_nested_tool_spans(cekura_run):
    transformer = CekuraTransformer()

    trace = transformer.transform_trace(cekura_run, "project-1")
    spans = transformer.transform_observations(cekura_run, "trace-1", "project-1")

    assert trace["external_id"] == "cekura-run-42"
    assert trace["metadata"]["integration_source"] == "cekura"
    assert "cekura" in trace["tags"]
    assert [span["observation_type"] for span in spans] == [
        "conversation",
        "conversation",
        "tool",
    ]
    assert spans[2]["parent_span_id"] == spans[1]["id"]
    assert spans[0]["id"].startswith("cekura-turn-")
    assert spans[2]["id"].startswith("cekura-tool-")


@pytest.mark.django_db
@pytest.mark.api
def test_ingestion_upserts_trace_and_spans(auth_client, organization, workspace, cekura_run):
    project = Project.objects.create(
        name="Cekura traces",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    payload = {"project_id": str(project.id), "run": cekura_run}

    first = auth_client.post("/api/public/cekura/ingestion", payload, format="json")
    assert first.status_code == 201
    assert first.data == {"created": True, "spans_ingested": 3}

    cekura_run["turns"][1]["content"] = "Your order is eligible for a refund."
    second = auth_client.post("/api/public/cekura/ingestion", payload, format="json")
    assert second.status_code == 200
    assert second.data == {"created": False, "spans_ingested": 3}

    trace = Trace.objects.get(project=project, external_id="cekura-run-42")
    spans = ObservationSpan.objects.filter(trace=trace)
    assert spans.count() == 4  # deterministic synthetic root + 2 turns + 1 tool

    root = spans.get(id="root-cekura-run-42")
    agent_turn = spans.get(name="Assistant turn")
    tool = spans.get(name="lookup_order")
    assert agent_turn.parent_span_id == root.id
    assert tool.parent_span_id == agent_turn.id
    assert agent_turn.output == {
        "role": "assistant",
        "content": "Your order is eligible for a refund.",
    }


@pytest.mark.django_db
@pytest.mark.api
def test_ingestion_rejects_a_run_without_a_stable_id(auth_client, organization, workspace):
    project = Project.objects.create(
        name="Cekura invalid run",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )

    response = auth_client.post(
        "/api/public/cekura/ingestion",
        {"project_id": str(project.id), "run": {"turns": []}},
        format="json",
    )

    assert response.status_code == 400
    assert Trace.objects.filter(project=project).count() == 0
