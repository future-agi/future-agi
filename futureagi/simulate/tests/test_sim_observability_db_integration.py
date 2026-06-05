"""Real-DB integration tests for sim observability persistence (TH-5642).

These run emit_sim_trace + attach_sim_evals_to_trace against the migrated test
Postgres (NOT mocked) and assert the persisted ObservationSpan tree. They exist
because two bugs hid behind mocked unit tests until a real DB-verified run:

1. FLAT TREE — build_sim_spans emitted the parent under ``parent_span_id`` but the
   OTel ingest converter reads ``parent_id``; children persisted with a NULL
   parent, so sim traces rendered flat instead of nested.
2. EVAL ATTACH — attach_sim_evals_to_trace filtered a non-existent ``span_id``
   column (the PK is ``id``), raising FieldError against a real DB.

A mock cannot catch either; only a real write/read can.
"""
import pytest

from simulate.models.agent_definition import AgentDefinition
from simulate.models.run_test import RunTest
from simulate.models.scenarios import Scenarios
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services.sim_observability import (
    _det_span_id,
    attach_sim_evals_to_trace,
    emit_sim_trace,
)
from tracer.models.observation_span import ObservationSpan


def _seed_voice_call(organization, workspace):
    ad = AgentDefinition.objects.create(
        agent_name="AUT voice", agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=False, description="obs db test", organization=organization,
        workspace=workspace, provider="retell", assistant_id="agent_x")
    sc = Scenarios.objects.create(
        name="obs scenario", description="d", source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET, organization=organization,
        workspace=workspace, agent_definition=ad)
    rt = RunTest.objects.create(
        name="obs run", description="d", agent_definition=ad, organization=organization,
        workspace=workspace, source_type=RunTest.SourceTypes.AGENT_DEFINITION)
    te = TestExecution.objects.create(
        run_test=rt, status=TestExecution.ExecutionStatus.RUNNING,
        total_scenarios=1, total_calls=1, agent_definition=ad)
    return CallExecution.objects.create(
        test_execution=te, scenario=sc, status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.VOICE)


@pytest.mark.integration
@pytest.mark.django_db
def test_emit_sim_trace_persists_real_nested_tree(organization, workspace):
    ce = _seed_voice_call(organization, workspace)
    turns = [
        {"role": "assistant", "content": "Hi, this is the clinic. How can I help?", "latency_ms": 800},
        {"role": "user", "content": "I need to reschedule my appointment."},
        {"role": "assistant", "content": "Sure — what day works for you?", "latency_ms": 850},
    ]
    emit_sim_trace(ce, turns=turns)

    root_id = _det_span_id(str(ce.id), "root")
    root = ObservationSpan.objects.get(id=root_id)
    assert root.parent_span_id is None
    assert root.observation_type.lower() == "agent"
    assert root.trace is not None
    assert root.project.name == "Simulations"

    # REGRESSION (flat tree): both LLM turns must persist parented to the root.
    children = list(ObservationSpan.objects.filter(parent_span_id=root_id))
    assert len(children) == 2, f"expected 2 nested LLM spans, got {len(children)}"
    assert all(c.observation_type.lower() == "llm" for c in children)


@pytest.mark.integration
@pytest.mark.django_db
def test_attach_sim_evals_writes_onto_root_span(organization, workspace):
    ce = _seed_voice_call(organization, workspace)
    emit_sim_trace(ce, turns=[{"role": "assistant", "content": "Hello!", "latency_ms": 700}])

    # REGRESSION (eval attach): must resolve the root span by PK ``id``.
    updated = attach_sim_evals_to_trace(ce, {"eval.score": 0.9, "eval.passed": True})
    assert updated == 1

    root = ObservationSpan.objects.get(id=_det_span_id(str(ce.id), "root"))
    assert root.span_attributes["eval.score"] == 0.9
    assert root.span_attributes["eval.passed"] is True
