"""DB test for the sim-observability emit layer (TH-5642).

Verifies emit_sim_trace reads a CallExecution's ChatMessage rows, builds the span
tree, and hands each span to the tracer OTel write path — i.e. the sim actually
becomes a trace. The tracer write itself (create_single_otel_span) is mocked so the
test pins OUR orchestration, not the tracer's ingest internals.
"""

import uuid

import pytest

from model_hub.models.choices import StatusType
from simulate.models import AgentDefinition, CallExecution, RunTest, Scenarios
from simulate.models.chat_message import ChatMessageModel
from simulate.models.test_execution import TestExecution


def _chat_call(organization, workspace):
    ad = AgentDefinition.objects.create(
        agent_name="Obs Test Agent", agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True, description="obs", organization=organization, workspace=workspace,
        provider="retell", assistant_id="agent_1", languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name="Obs Scenario", description="obs", source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET, organization=organization,
        workspace=workspace, agent_definition=ad, status=StatusType.COMPLETED.value,
    )
    rt = RunTest.objects.create(
        name="Obs Run", description="obs", agent_definition=ad,
        organization=organization, workspace=workspace,
    )
    te = TestExecution.objects.create(
        run_test=rt, status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1, total_calls=1, agent_definition=ad,
    )
    return CallExecution.objects.create(
        test_execution=te, scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
    )


@pytest.mark.unit
@pytest.mark.django_db
def test_emit_sim_trace_builds_and_emits_span_tree(organization, workspace, monkeypatch):
    ce = _chat_call(organization, workspace)
    # Two turns: simulator (user) opens, agent-under-test (assistant) replies.
    ChatMessageModel.objects.create(
        id=uuid.uuid4(), role="user", call_execution=ce,
        messages=["Hi, do you sell scooters?"], content=[], session_id="sess-9",
        organization=organization, workspace=workspace, tokens=10,
    )
    ChatMessageModel.objects.create(
        id=uuid.uuid4(), role="assistant", call_execution=ce,
        messages=["Yes! The X1 is $199."], content=[], session_id="sess-9",
        organization=organization, workspace=workspace, tokens=8, latency_ms=420,
    )

    captured = []
    import tracer.utils.create_otel_span as cos

    monkeypatch.setattr(
        cos, "create_single_otel_span",
        lambda span, org, user, ws=None: captured.append((span, org, ws)),
    )

    from simulate.services.sim_observability import emit_sim_trace

    emitted = emit_sim_trace(ce)

    # Root AGENT span + one LLM span for the single assistant turn.
    kinds = [s["attributes"]["gen_ai.span.kind"] for s, _, _ in captured]
    assert emitted == 2
    assert kinds.count("AGENT") == 1
    assert kinds.count("LLM") == 1
    # The org id was threaded; no observability project → falls back to "Simulations".
    org_ids = {org for _, org, _ in captured}
    assert org_ids == {str(organization.id)}
    assert all(s["project_name"] == "Simulations" for s, _, _ in captured)
    # The agent's reply is the LLM span output + the root output.
    llm = next(s for s, _, _ in captured if s["attributes"]["gen_ai.span.kind"] == "LLM")
    assert llm["attributes"]["output.value"] == "Yes! The X1 is $199."
    assert llm["attributes"]["gen_ai.usage.output_tokens"] == 8
    assert llm["latency"] == 420
    # session.id lives on the root AGENT span (links the whole conversation).
    root = next(s for s, _, _ in captured if s["attributes"]["gen_ai.span.kind"] == "AGENT")
    assert root["attributes"]["session.id"] == "sess-9"
