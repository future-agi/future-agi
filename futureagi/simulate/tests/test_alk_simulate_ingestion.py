"""Integration tests for the ALK sim ingestion surface.

Covers the full external-runner flow end to end against the real DB:
  start test execution -> batch call executions -> ingest result
plus recording upload and the backend-owned derivations (conversation
metrics, duration backfill, token usage, CSAT preservation).

External side effects are patched at the service boundary: Temporal
dispatch (evals / CSAT / monitor), the websocket notification, and the
object-storage upload. Everything else — metric computation, DB writes,
the API envelope — runs for real.
"""

from unittest.mock import patch

import pytest

from model_hub.models.choices import StatusType
from simulate.models import (
    AgentDefinition,
    RunTest,
    Scenarios,
    SimulatorAgent,
)
from simulate.models.test_execution import (
    CallExecution,
    CallTranscript,
)
from simulate.models.test_execution import TestExecution as SimTestExecution

ALK_BASE = "/simulate/api/alk-simulate"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="ALK Ingestion Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12813716796",
        inbound=True,
        description="Agent under test for ALK ingestion",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="ALK Simulator",
        prompt="You are a customer.",
        voice_provider="livekit",
        voice_name="alk-simulator",
        model="gpt-4o",
        initial_message="Hi!",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def scenario(db, organization, workspace, agent_definition, simulator_agent):
    return Scenarios.objects.create(
        name="ALK Ingestion Scenario",
        description="Scenario for ALK ingestion tests",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    rt = RunTest.objects.create(
        name="ALK Ingestion Run Test",
        description="Run for ALK ingestion tests",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture(autouse=True)
def _patch_side_effects():
    """No-op the async dispatch + websocket so the service runs inline."""
    targets = (
        "simulate.services.alk_simulate_ingestion.notify_simulation_update",
        "simulate.services.test_executor._run_simulate_evaluations_task.apply_async",
    )
    with patch(targets[0]), patch(targets[1]):
        with (
            patch(
                "simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async"
            ),
            patch("simulate.tasks.alk_sim.calculate_alk_voice_csat_score.apply_async"),
        ):
            yield


def _transcript_payload():
    """A short two-turn transcript with real speech offsets (ms)."""
    return [
        {
            "speaker_role": "user",
            "content": "Hi, my package is late. Can you check the status?",
            "start_time_ms": 0,
            "end_time_ms": 5000,
        },
        {
            "speaker_role": "assistant",
            "content": "Of course, let me look that up for you right away.",
            "start_time_ms": 6000,
            "end_time_ms": 11000,
        },
        {
            "speaker_role": "user",
            "content": "Thank you, I appreciate it.",
            "start_time_ms": 12000,
            "end_time_ms": 15000,
        },
    ]


def _start_and_batch(auth_client, run_test):
    """Helper: start a test execution and allocate its call executions."""
    start = auth_client.post(
        f"{ALK_BASE}/run-tests/{run_test.id}/test-executions/",
        {},
        format="json",
    )
    assert start.status_code == 200, start.content
    test_execution_id = start.json()["result"]["test_execution_id"]

    batch = auth_client.post(
        f"{ALK_BASE}/test-executions/{test_execution_id}/batch/",
        {},
        format="json",
    )
    assert batch.status_code == 200, batch.content
    call_ids = batch.json()["result"]["call_execution_ids"]
    return test_execution_id, call_ids


# ---------------------------------------------------------------------------
# start_test_execution
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestStartTestExecution:
    def test_creates_execution_inheriting_run_test(
        self, auth_client, run_test, scenario
    ):
        resp = auth_client.post(
            f"{ALK_BASE}/run-tests/{run_test.id}/test-executions/",
            {},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        result = resp.json()["result"]
        assert result["run_test_id"] == str(run_test.id)
        assert result["total_scenarios"] == 1
        assert str(scenario.id) in result["scenario_ids"]

        te = SimTestExecution.objects.get(id=result["test_execution_id"])
        # Inherits agent_definition + scenarios from the run test, no orchestration.
        assert te.agent_definition_id == run_test.agent_definition_id
        assert te.scenario_ids == [str(scenario.id)]
        assert te.status == SimTestExecution.ExecutionStatus.PENDING

    def test_unknown_run_test_returns_404(self, auth_client):
        unknown = "00000000-0000-4000-8000-0000deadbeef"
        resp = auth_client.post(
            f"{ALK_BASE}/run-tests/{unknown}/test-executions/", {}, format="json"
        )
        assert resp.status_code == 404
        assert resp.json()["status"] is False

    def test_scenario_not_on_run_test_returns_400(self, auth_client, run_test):
        other = "11111111-1111-4111-8111-111111111111"
        resp = auth_client.post(
            f"{ALK_BASE}/run-tests/{run_test.id}/test-executions/",
            {"scenario_ids": [other]},
            format="json",
        )
        assert resp.status_code == 400
        assert resp.json()["status"] is False


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestBatchCreate:
    def test_batch_creates_pending_voice_call(self, auth_client, run_test, scenario):
        te_id, call_ids = _start_and_batch(auth_client, run_test)
        assert len(call_ids) == 1

        call = CallExecution.objects.get(id=call_ids[0])
        assert call.status == CallExecution.CallStatus.PENDING
        assert call.simulation_call_type == CallExecution.SimulationCallType.VOICE
        assert call.scenario_id == scenario.id
        assert call.call_metadata["external_runner"] == "alk"

    def test_second_batch_has_nothing_to_create(self, auth_client, run_test):
        te_id, _ = _start_and_batch(auth_client, run_test)
        second = auth_client.post(
            f"{ALK_BASE}/test-executions/{te_id}/batch/", {}, format="json"
        )
        assert second.status_code == 400
        assert second.json()["status"] is False


# ---------------------------------------------------------------------------
# result ingest — metrics, duration, tokens, csat
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestResultIngest:
    def test_ingest_computes_metrics_and_duration(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]

        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_id}/result/",
            {"status": "completed", "transcript": _transcript_payload()},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        call = CallExecution.objects.get(id=call_id)
        assert call.status == CallExecution.CallStatus.COMPLETED
        # Transcript persisted.
        assert CallTranscript.objects.filter(call_execution=call).count() == 3
        # Metrics recomputed server-side from the transcript.
        cmd = call.conversation_metrics_data or {}
        assert cmd.get("turn_count") == 1  # one assistant/bot turn
        assert cmd.get("message_count") == 3
        assert call.user_wpm is not None and call.user_wpm > 0
        # Duration backfilled from the last transcript offset (15000 ms).
        assert call.duration_seconds == 15

    def test_ingest_writes_token_usage_from_provider_data(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]

        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_id}/result/",
            {
                "status": "completed",
                "transcript": _transcript_payload(),
                "provider_call_data": {
                    "vapi": {
                        "usage": {
                            "llm": {
                                "prompt_tokens": 1200,
                                "completion_tokens": 450,
                                "total_tokens": 1650,
                            }
                        }
                    }
                },
                "costs": {"cost_cents": 16},
            },
            format="json",
        )
        assert resp.status_code == 200, resp.content

        call = CallExecution.objects.get(id=call_id)
        cmd = call.conversation_metrics_data or {}
        assert cmd.get("input_tokens") == 1200
        assert cmd.get("output_tokens") == 450
        assert cmd.get("total_tokens") == 1650
        assert call.cost_cents == 16

    def test_retell_total_only_tokens(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]

        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_id}/result/",
            {
                "status": "completed",
                "transcript": _transcript_payload(),
                "provider_call_data": {
                    "retell": {"usage": {"llm": {"total_tokens": 1500}}}
                },
            },
            format="json",
        )
        assert resp.status_code == 200, resp.content

        cmd = CallExecution.objects.get(id=call_id).conversation_metrics_data or {}
        assert cmd.get("total_tokens") == 1500
        assert cmd.get("input_tokens") is None

    def test_reingest_preserves_csat(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]
        body = {"status": "completed", "transcript": _transcript_payload()}

        first = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_id}/result/", body, format="json"
        )
        assert first.status_code == 200

        # Simulate the async CSAT task having written a score.
        call = CallExecution.objects.get(id=call_id)
        cmd = dict(call.conversation_metrics_data or {})
        cmd["csat_score"] = 6.0
        call.conversation_metrics_data = cmd
        call.overall_score = 6.0
        call.save(update_fields=["conversation_metrics_data", "overall_score"])

        # A second idempotent ingest must not wipe csat_score.
        second = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_id}/result/", body, format="json"
        )
        assert second.status_code == 200
        cmd = CallExecution.objects.get(id=call_id).conversation_metrics_data or {}
        assert cmd.get("csat_score") == 6.0

    def test_unknown_call_returns_404(self, auth_client):
        unknown = "00000000-0000-4000-8000-0000deadbeef"
        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{unknown}/result/",
            {"status": "completed"},
            format="json",
        )
        assert resp.status_code == 404
        assert resp.json()["status"] is False


# ---------------------------------------------------------------------------
# recording upload
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestRecordingUpload:
    def test_multipart_upload_persists_recording(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]

        from unittest.mock import MagicMock

        from django.core.files.uploadedfile import SimpleUploadedFile

        audio = SimpleUploadedFile(
            "combined.wav", b"RIFFfakewavdata", content_type="audio/wav"
        )

        fake_client = MagicMock()
        with (
            patch(
                "simulate.services.alk_simulate_ingestion.get_storage_client",
                return_value=fake_client,
            ),
            patch(
                "simulate.services.alk_simulate_ingestion.get_object_url",
                return_value="https://storage.example.com/fi-content/alk-sim/recordings/x.wav",
            ),
        ):
            resp = auth_client.post(
                f"{ALK_BASE}/call-executions/{call_id}/recording/",
                {"file": audio, "filename": "combined.wav"},
                format="multipart",
            )

        assert resp.status_code == 200, resp.content
        result = resp.json()["result"]
        assert result["recording_url"].endswith(".wav")
        assert result["object_key"].startswith("alk-sim/recordings/")
        # Bytes were written to the upload bucket via the storage client.
        fake_client.put_object.assert_called_once()

    def test_missing_file_returns_400(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        resp = auth_client.post(
            f"{ALK_BASE}/call-executions/{call_ids[0]}/recording/",
            {},
            format="multipart",
        )
        assert resp.status_code == 400
        assert resp.json()["status"] is False
