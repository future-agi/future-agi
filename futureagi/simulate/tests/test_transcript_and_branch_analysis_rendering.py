"""Rendering + speaker-role assertions for call transcripts, aggregated transcripts, and branch analysis."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from model_hub.models.choices import StatusType
from simulate.models import (
    AgentDefinition,
    CallExecution,
    CallTranscript,
    RunTest,
    Scenarios,
)
from simulate.models.scenario_graph import ScenarioGraph
from simulate.models.test_execution import (
    TestExecution as SimulationTestExecution,
)
from simulate.services.branch_deviation_analyzer import BranchAnalysis


def _create_voice_call_execution(
    organization,
    workspace,
    *,
    provider_call_data,
    call_metadata,
    call_type,
    name_suffix="",
):
    """Seed a full RunTest->TestExecution->CallExecution chain for VOICE.

    Provider metadata is parameterised so the same helper produces
    VAPI-inbound, VAPI-outbound and LiveKit rows without repeating the
    surrounding scaffolding.
    """
    agent_definition = AgentDefinition.objects.create(
        agent_name=f"Voice Agent {workspace.id}{name_suffix}",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        description="Agent for transcript-rendering tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name=f"Scenario {workspace.id}{name_suffix}",
        description="Scenario for transcript-rendering tests",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )
    run_test = RunTest.objects.create(
        name=f"Run {workspace.id}{name_suffix}",
        description="Run for transcript-rendering tests",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = SimulationTestExecution.objects.create(
        run_test=run_test,
        status=SimulationTestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        agent_definition=agent_definition,
    )
    call_execution = CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        provider_call_data=provider_call_data,
        call_metadata=call_metadata,
        call_type=call_type,
    )
    return call_execution


def _seed_transcripts(call_execution, rows):
    """Persist the transcript rows in the given order.

    Each row is (speaker_role, content, start_time_ms). Rows are stored
    in a scrambled order relative to start_time_ms so the endpoint's
    ordering guarantee gets exercised.
    """
    for speaker_role, content, start_time_ms in rows:
        CallTranscript.objects.create(
            call_execution=call_execution,
            speaker_role=speaker_role,
            content=content,
            start_time_ms=start_time_ms,
            end_time_ms=start_time_ms + 500,
        )


@pytest.fixture
def vapi_inbound_call(db, organization, workspace):
    return _create_voice_call_execution(
        organization,
        workspace,
        provider_call_data={"vapi": {"id": "vapi-inbound-1"}},
        call_metadata={"call_direction": "inbound"},
        call_type="inboundPhoneCall",
        name_suffix=" vapi-in",
    )


@pytest.fixture
def vapi_outbound_call(db, organization, workspace):
    return _create_voice_call_execution(
        organization,
        workspace,
        provider_call_data={"vapi": {"id": "vapi-outbound-1"}},
        call_metadata={"call_direction": "outbound"},
        call_type="outboundPhoneCall",
        name_suffix=" vapi-out",
    )


@pytest.fixture
def livekit_call(db, organization, workspace):
    return _create_voice_call_execution(
        organization,
        workspace,
        provider_call_data={"livekit": {"room": "room-1"}},
        call_metadata={"call_direction": "inbound"},
        call_type="inboundPhoneCall",
        name_suffix=" livekit",
    )


@pytest.mark.integration
@pytest.mark.api
class TestCallTranscriptRendering:
    """GET /simulate/call-executions/<id>/transcripts/ body assertions."""

    def test_vapi_inbound_transcripts_apply_speaker_role_swap(
        self, auth_client, vapi_inbound_call
    ):
        # DB rows use the raw Vapi shape: `assistant` holds the FAGI
        # simulator persona, `user` holds the tested agent. The read
        # boundary must swap them so the FE sees tested-agent content
        # under `assistant`.
        _seed_transcripts(
            vapi_inbound_call,
            [
                ("assistant", "Hello, this is the simulator persona.", 2000),
                ("user", "Hi, tested agent speaking.", 1000),
                ("assistant", "Follow-up simulator line.", 3000),
            ],
        )

        response = auth_client.get(
            f"/simulate/call-executions/{vapi_inbound_call.id}/transcripts/"
        )

        assert response.status_code == 200
        body = response.data
        assert body["call_execution_id"] == str(vapi_inbound_call.id)
        transcripts = body["transcripts"]
        assert body["total_transcripts"] == 3
        assert len(transcripts) == 3

        # Ordering is by start_time_ms ascending regardless of insert order.
        assert [t["start_time_ms"] for t in transcripts] == [1000, 2000, 3000]

        # VAPI inbound swap: DB `user` (tested agent) -> emitted `assistant`;
        # DB `assistant` (simulator) -> emitted `user`.
        assert transcripts[0]["speaker_role"] == "assistant"
        assert "tested agent" in transcripts[0]["content"]
        assert transcripts[1]["speaker_role"] == "user"
        assert "simulator persona" in transcripts[1]["content"]
        assert transcripts[2]["speaker_role"] == "user"
        assert "Follow-up simulator" in transcripts[2]["content"]

    def test_vapi_outbound_transcripts_pass_through_roles(
        self, auth_client, vapi_outbound_call
    ):
        # VAPI outbound: raw shape already matches the platform
        # convention (`assistant` = tested agent), so no swap should fire.
        _seed_transcripts(
            vapi_outbound_call,
            [
                ("assistant", "Tested agent greeting.", 1000),
                ("user", "Simulator reply.", 2000),
            ],
        )

        response = auth_client.get(
            f"/simulate/call-executions/{vapi_outbound_call.id}/transcripts/"
        )

        assert response.status_code == 200
        transcripts = response.data["transcripts"]
        assert transcripts[0]["speaker_role"] == "assistant"
        assert "Tested agent greeting" in transcripts[0]["content"]
        assert transcripts[1]["speaker_role"] == "user"
        assert "Simulator reply" in transcripts[1]["content"]

    def test_livekit_transcripts_pass_through_roles(
        self, auth_client, livekit_call
    ):
        # LiveKit rows are normalised at the worker; the read boundary
        # must not re-swap them regardless of direction.
        _seed_transcripts(
            livekit_call,
            [
                ("assistant", "Tested agent line.", 1000),
                ("user", "Simulator line.", 2000),
            ],
        )

        response = auth_client.get(
            f"/simulate/call-executions/{livekit_call.id}/transcripts/"
        )

        assert response.status_code == 200
        transcripts = response.data["transcripts"]
        assert transcripts[0]["speaker_role"] == "assistant"
        assert "Tested agent line" in transcripts[0]["content"]
        assert transcripts[1]["speaker_role"] == "user"
        assert "Simulator line" in transcripts[1]["content"]

    def test_empty_transcripts_returns_empty_list(
        self, auth_client, vapi_inbound_call
    ):
        response = auth_client.get(
            f"/simulate/call-executions/{vapi_inbound_call.id}/transcripts/"
        )

        assert response.status_code == 200
        assert response.data["transcripts"] == []
        assert response.data["total_transcripts"] == 0
        assert response.data["call_execution_id"] == str(vapi_inbound_call.id)


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionTranscriptRendering:
    """GET /simulate/test-executions/<id>/transcripts/ body assertions."""

    def test_test_execution_aggregates_transcripts_with_swap(
        self, auth_client, vapi_inbound_call
    ):
        _seed_transcripts(
            vapi_inbound_call,
            [
                ("assistant", "Simulator opening.", 1000),
                ("user", "Tested agent opening.", 500),
            ],
        )

        response = auth_client.get(
            f"/simulate/test-executions/{vapi_inbound_call.test_execution_id}"
            "/transcripts/"
        )

        assert response.status_code == 200
        body = response.data
        # The view forwards the URL-captured UUID directly (unlike the
        # per-call transcript view, which stringifies its id). Compare as
        # UUID so the assertion survives that shape.
        assert str(body["test_execution_id"]) == str(
            vapi_inbound_call.test_execution_id
        )
        assert body["total_calls"] == 1
        assert body["total_transcripts"] == 2
        calls = body["calls"]
        assert len(calls) == 1
        call = calls[0]
        assert call["call_execution_id"] == str(vapi_inbound_call.id)
        assert call["scenario_name"] == vapi_inbound_call.scenario.name

        transcripts = call["transcripts"]
        # Order by start_time_ms ascending across the aggregated bundle.
        assert [t["start_time_ms"] for t in transcripts] == [500, 1000]
        # VAPI inbound swap fires at the aggregated view too.
        assert transcripts[0]["speaker_role"] == "assistant"
        assert "Tested agent opening" in transcripts[0]["content"]
        assert transcripts[1]["speaker_role"] == "user"
        assert "Simulator opening" in transcripts[1]["content"]

    def test_test_execution_livekit_transcripts_pass_through(
        self, auth_client, livekit_call
    ):
        _seed_transcripts(
            livekit_call,
            [
                ("assistant", "Tested agent LK.", 1000),
                ("user", "Simulator LK.", 2000),
            ],
        )

        response = auth_client.get(
            f"/simulate/test-executions/{livekit_call.test_execution_id}"
            "/transcripts/"
        )

        assert response.status_code == 200
        transcripts = response.data["calls"][0]["transcripts"]
        assert transcripts[0]["speaker_role"] == "assistant"
        assert "Tested agent LK" in transcripts[0]["content"]
        assert transcripts[1]["speaker_role"] == "user"

    def test_test_execution_empty_transcripts_returns_empty_list(
        self, auth_client, vapi_inbound_call
    ):
        response = auth_client.get(
            f"/simulate/test-executions/{vapi_inbound_call.test_execution_id}"
            "/transcripts/"
        )

        assert response.status_code == 200
        body = response.data
        assert body["total_calls"] == 1
        assert body["total_transcripts"] == 0
        assert body["calls"][0]["transcripts"] == []


@pytest.mark.integration
@pytest.mark.api
class TestCallBranchAnalysisRendering:
    """GET+POST /simulate/call-executions/<id>/branch-analysis/ body shape."""

    def test_branch_analysis_get_returns_analysis_shape(
        self, auth_client, vapi_inbound_call
    ):
        # Prime analysis_data so the view returns the cached branch
        # analysis instead of invoking the LLM-backed analyzer.
        vapi_inbound_call.analysis_data = {
            "branch_analysis": {
                "new_nodes": [{"id": "n1", "label": "Greet"}],
                "new_edges": [{"source": "start", "target": "n1"}],
                "current_path": ["start", "n1"],
                "expected_path": ["start", "n1", "n2"],
                "analysis_summary": "Agent deviated at step 2.",
            }
        }
        vapi_inbound_call.save(update_fields=["analysis_data"])

        response = auth_client.get(
            f"/simulate/call-executions/{vapi_inbound_call.id}/branch-analysis/"
        )

        assert response.status_code == 200
        body = response.data
        assert body["call_execution_id"] == str(vapi_inbound_call.id)
        assert body["scenario_id"] == str(vapi_inbound_call.scenario.id)
        assert body["scenario_name"] == vapi_inbound_call.scenario.name
        analysis = body["analysis"]
        for key in (
            "new_nodes",
            "new_edges",
            "current_path",
            "expected_path",
            "analysis_summary",
        ):
            assert key in analysis
        assert analysis["current_path"] == ["start", "n1"]
        assert analysis["expected_path"] == ["start", "n1", "n2"]
        assert analysis["analysis_summary"] == "Agent deviated at step 2."

    def test_branch_analysis_post_returns_deviation_payload(
        self, auth_client, organization, vapi_inbound_call
    ):
        # POST requires a live ScenarioGraph; the analyzer + graph
        # scenario lookup are mocked so we exercise the response
        # assembly deterministically without touching the LLM path.
        scenario_graph = ScenarioGraph.objects.create(
            scenario=vapi_inbound_call.scenario,
            organization=organization,
            name="Test Scenario Graph",
        )

        fake_analysis = BranchAnalysis(
            new_nodes=[{"id": "d1", "label": "Deviation"}],
            new_edges=[{"source": "n1", "target": "d1"}],
            current_path=["start", "n1", "d1"],
            expected_path=["start", "n1", "n2"],
            analysis_summary="One deviation detected.",
        )
        fake_deviation = {
            "nodes": fake_analysis.new_nodes,
            "edges": fake_analysis.new_edges,
            "current_path": fake_analysis.current_path,
            "expected_path": fake_analysis.expected_path,
            "analysis_summary": fake_analysis.analysis_summary,
            "deviation_count": 1,
        }

        with patch(
            "simulate.views.call_transcript.BranchDeviationAnalyzer"
            ".analyze_call_execution_branch",
            return_value=fake_analysis,
        ), patch(
            "simulate.views.call_transcript.BranchDeviationAnalyzer"
            "._get_scenario_graph",
            return_value=scenario_graph,
        ), patch(
            "simulate.views.call_transcript.BranchDeviationAnalyzer"
            ".create_deviation_nodes_and_edges",
            return_value=fake_deviation,
        ):
            response = auth_client.post(
                f"/simulate/call-executions/{vapi_inbound_call.id}"
                "/branch-analysis/",
                {},
                format="json",
            )

        assert response.status_code == 200
        body = response.data
        assert body["call_execution_id"] == str(vapi_inbound_call.id)
        assert body["scenario_graph_id"] == str(scenario_graph.id)
        assert body["deviation_data"] == fake_deviation
        assert "1 deviation" in body["message"]
