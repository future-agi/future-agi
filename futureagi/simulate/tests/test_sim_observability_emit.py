"""DB test for the sim-observability emit layer (TH-5642).

Verifies emit_sim_trace reads a CallExecution's ChatMessage rows, builds the span
tree, and exports it to the fi-collector — i.e. the sim actually becomes a trace
in CH spans. The collector export itself (export_sim_spans) is mocked so the test
pins OUR orchestration (turn reading, span shape, project resolution), not the
OTLP/collector internals.
"""

import uuid

import pytest

from model_hub.models.choices import StatusType
from simulate.models import AgentDefinition, CallExecution, RunTest, Scenarios
from simulate.models.chat_message import ChatMessageModel
from simulate.models.test_execution import TestExecution


def _chat_call(organization, workspace):
    ad = AgentDefinition.objects.create(
        agent_name="Obs Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="obs",
        organization=organization,
        workspace=workspace,
        provider="retell",
        assistant_id="agent_1",
        languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name="Obs Scenario",
        description="obs",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=ad,
        status=StatusType.COMPLETED.value,
    )
    rt = RunTest.objects.create(
        name="Obs Run",
        description="obs",
        agent_definition=ad,
        organization=organization,
        workspace=workspace,
    )
    te = TestExecution.objects.create(
        run_test=rt,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        agent_definition=ad,
    )
    return CallExecution.objects.create(
        test_execution=te,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
    )


@pytest.mark.unit
@pytest.mark.django_db
def test_emit_sim_trace_builds_and_emits_span_tree(
    organization, workspace, monkeypatch
):
    ce = _chat_call(organization, workspace)
    # Two turns: simulator (user) opens, agent-under-test (assistant) replies.
    ChatMessageModel.objects.create(
        id=uuid.uuid4(),
        role="user",
        call_execution=ce,
        messages=["Hi, do you sell scooters?"],
        content=[],
        session_id="sess-9",
        organization=organization,
        workspace=workspace,
        tokens=10,
    )
    ChatMessageModel.objects.create(
        id=uuid.uuid4(),
        role="assistant",
        call_execution=ce,
        messages=["Yes! The X1 is $199."],
        content=[],
        session_id="sess-9",
        organization=organization,
        workspace=workspace,
        tokens=8,
        latency_ms=420,
    )

    captured = {}
    import simulate.services.sim_collector_emit as sce

    def _fake_export(
        spans,
        *,
        project_name,
        project_type,
        api_key,
        secret_key,
        eval_tags=None,
        service_name="fi-simulation",
    ):
        captured["spans"] = spans
        captured["project_name"] = project_name
        captured["api_key"] = api_key
        return len(spans)

    monkeypatch.setattr(sce, "export_sim_spans", _fake_export)

    from simulate.services.sim_observability import emit_sim_trace

    emitted = emit_sim_trace(ce)
    spans = captured["spans"]

    # Root AGENT span + one LLM span for the single assistant turn.
    kinds = [s["attributes"]["gen_ai.span.kind"] for s in spans]
    assert emitted == 2
    assert kinds.count("AGENT") == 1
    assert kinds.count("LLM") == 1
    # No observability project → falls back to "Simulations".
    assert captured["project_name"] == "Simulations"
    # An org-scoped ingest key was resolved for the collector auth.
    assert captured["api_key"]
    # The agent's reply is the LLM span output + the root output.
    llm = next(s for s in spans if s["attributes"]["gen_ai.span.kind"] == "LLM")
    assert llm["attributes"]["output.value"] == "Yes! The X1 is $199."
    assert llm["attributes"]["gen_ai.usage.output_tokens"] == 8
    assert llm["latency"] == 420
    # session.id lives on the root AGENT span (links the whole conversation).
    root = next(s for s in spans if s["attributes"]["gen_ai.span.kind"] == "AGENT")
    assert root["attributes"]["session.id"] == "sess-9"


@pytest.mark.unit
@pytest.mark.django_db
def test_emit_sim_trace_accepts_voice_turns(organization, workspace, monkeypatch):
    # VOICE: the completion hook passes its normalized transcript directly (voice
    # transcripts live outside ChatMessageModel).
    ce = _chat_call(organization, workspace)
    ce.simulation_call_type = CallExecution.SimulationCallType.VOICE
    ce.save(update_fields=["simulation_call_type"])

    voice_turns = [
        {"role": "user", "content": "Is my appointment confirmed?"},
        {
            "role": "assistant",
            "content": "Yes, you're confirmed for 3pm.",
            "voice_latency": {"ttfb": 480, "total": 950},
        },
    ]
    captured = {}
    import simulate.services.sim_collector_emit as sce

    def _fake_export(
        spans,
        *,
        project_name,
        project_type,
        api_key,
        secret_key,
        eval_tags=None,
        service_name="fi-simulation",
    ):
        captured["spans"] = spans
        return len(spans)

    monkeypatch.setattr(sce, "export_sim_spans", _fake_export)

    from simulate.services.sim_observability import emit_sim_trace

    emitted = emit_sim_trace(ce, turns=voice_turns)
    spans = captured["spans"]
    assert emitted == 2
    # Voice roots emit as CONVERSATION so they appear in the voice-call list
    # next to pulled provider calls (observation_type='conversation').
    root = next(
        s for s in spans if s["attributes"]["gen_ai.span.kind"] == "CONVERSATION"
    )
    assert root["name"] == "voice simulation"
    assert root["attributes"]["call.status"] == "completed"
    assert "start_time" in root and "end_time" in root
    llm = next(s for s in spans if s["attributes"]["gen_ai.span.kind"] == "LLM")
    assert llm["attributes"]["gen_ai.voice.latency.ttfb"] == 480
