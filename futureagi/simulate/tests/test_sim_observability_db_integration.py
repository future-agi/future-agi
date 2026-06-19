"""Real-DB integration tests for sim observability emit (TH-5642).

These run emit_sim_trace + attach_sim_evals_to_trace against the migrated test
Postgres (NOT mocked) and assert the EXPORTED span tree — sim now exports to the
fi-collector instead of writing PG ObservationSpan rows, so the regression
coverage moves to the span dicts handed to the collector. The collector export
itself is captured; everything up to it (real turn-reading, project resolution,
deterministic parent links) runs against the real DB.

Regression intent preserved:
1. FLAT TREE — build_sim_spans must parent each LLM turn under the root via the
   key the OTLP layer reads; a mock could not catch the original flat-tree bug.
2. EVAL ATTACH — attach re-emits with verdicts on the root; the voice turns it
   needs are re-read from CallTranscript (the new re-resolution path).
"""

import pytest

from simulate.models.agent_definition import AgentDefinition
from simulate.models.run_test import RunTest
from simulate.models.scenarios import Scenarios
from simulate.models.test_execution import (
    CallExecution,
    CallTranscript,
    TestExecution,
)
from simulate.services import sim_collector_emit as sce
from simulate.services.sim_observability import (
    attach_sim_evals_to_trace,
    emit_sim_trace,
)


def _capture_exports(monkeypatch) -> list[dict]:
    """Capture the span lists handed to the collector across all exports."""
    exports: list[dict] = []

    def _fake_export(spans, *, project_name, project_type, api_key, secret_key):
        exports.append(
            {"spans": spans, "project_name": project_name, "api_key": api_key}
        )
        return len(spans)

    monkeypatch.setattr(sce, "export_sim_spans", _fake_export)
    return exports


def _seed_voice_call(organization, workspace):
    ad = AgentDefinition.objects.create(
        agent_name="AUT voice",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=False,
        description="obs db test",
        organization=organization,
        workspace=workspace,
        provider="retell",
        assistant_id="agent_x",
    )
    sc = Scenarios.objects.create(
        name="obs scenario",
        description="d",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=ad,
    )
    rt = RunTest.objects.create(
        name="obs run",
        description="d",
        agent_definition=ad,
        organization=organization,
        workspace=workspace,
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
    )
    te = TestExecution.objects.create(
        run_test=rt,
        status=TestExecution.ExecutionStatus.RUNNING,
        total_scenarios=1,
        total_calls=1,
        agent_definition=ad,
    )
    return CallExecution.objects.create(
        test_execution=te,
        scenario=sc,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_emit_sim_trace_exports_real_nested_tree(organization, workspace, monkeypatch):
    exports = _capture_exports(monkeypatch)
    ce = _seed_voice_call(organization, workspace)
    turns = [
        {"role": "assistant", "content": "Hi, this is the clinic.", "latency_ms": 800},
        {"role": "user", "content": "I need to reschedule my appointment."},
        {"role": "assistant", "content": "Sure — what day works?", "latency_ms": 850},
    ]
    n = emit_sim_trace(ce, turns=turns)

    assert n == 3  # root + 2 LLM turns
    assert len(exports) == 1
    assert exports[0]["project_name"] == "Simulations"
    assert exports[0]["api_key"]  # an org-scoped ingest key was resolved
    spans = exports[0]["spans"]

    root = next(s for s in spans if s["parent_span_id"] is None)
    # Voice sim roots emit as CONVERSATION so the voice-call list includes them.
    assert root["attributes"]["gen_ai.span.kind"] == "CONVERSATION"

    # REGRESSION (flat tree): both LLM turns must be parented to the root, via
    # BOTH parent_span_id and parent_id (the OTLP layer reads parent_id).
    children = [s for s in spans if s["parent_span_id"] == root["span_id"]]
    assert len(children) == 2
    assert all(c["attributes"]["gen_ai.span.kind"] == "LLM" for c in children)
    assert all(c["parent_id"] == root["span_id"] for c in children)


@pytest.mark.integration
@pytest.mark.django_db
def test_attach_sim_evals_reemits_with_voice_turns_from_transcript(
    organization, workspace, monkeypatch
):
    exports = _capture_exports(monkeypatch)
    ce = _seed_voice_call(organization, workspace)
    # Voice transcript is the source eval-attach re-reads turns from.
    CallTranscript.objects.create(
        call_execution=ce, speaker_role="assistant", content="Hello!", start_time_ms=0
    )
    CallTranscript.objects.create(
        call_execution=ce,
        speaker_role="user",
        content="Cancel my appointment.",
        start_time_ms=1500,
    )

    updated = attach_sim_evals_to_trace(ce, {"eval.score": 0.9, "eval.passed": True})
    assert updated >= 1  # re-emitted the trace

    spans = exports[-1]["spans"]
    root = next(s for s in spans if s["parent_span_id"] is None)
    # Eval verdicts merged onto the root by the re-emit.
    assert root["attributes"]["eval.score"] == 0.9
    assert root["attributes"]["eval.passed"] is True
    # Turns were re-resolved from CallTranscript (one assistant turn → one LLM span).
    assert any(s["attributes"]["gen_ai.span.kind"] == "LLM" for s in spans)
