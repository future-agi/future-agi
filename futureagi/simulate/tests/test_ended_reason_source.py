"""Where a call's end reason came from, and what a shared link to the call shows.

Every write of a call's end reason records where the reason came from, in the
call's metadata, with a digest of that reason. A shared link to a call shows
the stored reason when the source recorded for it is one a shared link shows
(our code, the simulator, the usage check, a provider's enumerated reason, the
organization's own text) or the reason is one of our fixed sentences, and "The
call ended." otherwise, also for a reason written without its source (a hosted
retry's failure text, a voice engine's own save, a re-run call). A shared link
sends no provider payload. The organization's own answers show the stored text.
No call answer sends the source or a secret kept in the call's metadata.
"""

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.utils import timezone

from model_hub.models.choices import QueueItemSourceType
from model_hub.utils import annotation_queue_helpers
from simulate.models import (
    AgentDefinition,
    CallExecution,
    CallExecutionSnapshot,
    HostedHarnessJob,
    RunTest,
    Scenarios,
)
from simulate.models.test_execution import TestExecution as SimulationTestExecution
from simulate.serializers.test_execution import CallExecutionSerializer
from simulate.services import alk_simulate_ingestion, hosted_harness_ingestion
from simulate.services.chat_sim import (
    CHAT_COULD_NOT_START,
    CHAT_STOPPED,
    SEND_REFUSAL_REASONS,
)
from simulate.services.hosted_harness import project_terminal_job_onto_run
from simulate.services.test_executor import (
    CALL_COULD_NOT_START,
    CALL_STOPPED,
    USAGE_CHECK_FAILED,
)
from simulate.temporal.types.activities import FetchAndPersistCallResultInput
from simulate.temporal.workflows.rerun_coordinator_workflow import RERUN_STOPPED
from simulate.utils.ended_reason import (
    CALL_METADATA_SECRET_KEYS,
    ENDED_REASON_SOURCE_KEY,
    SHARED_LINK_ENDED,
    SHARED_LINK_FIXED_REASONS,
    EndedReasonSource,
    end_calls_with_reason,
    recorded_ended_reason_source,
    set_ended_reason,
    stored_ended_reason_source,
)
from simulate.views.run_results_v3 import build_call_execution_detail
from simulate.views.run_test import (
    RERUN_DISPATCH_FAILED,
    CallExecutionRerunView,
    TestExecutionRerunView,
)
from tfc.temporal.common.heartbeat import Heartbeater
from tracer.models.shared_link import AccessType, ResourceType, SharedLink
from tracer.views.shared_link import _resolve_shared_call_execution

# Made-up text, worded as a provider's error might be.
MADE_UP_REASON = (
    "connect to https://provider.example.test failed for key sk-example-0000"
)
# Every made-up secret value starts with this.
SECRET_VALUE = "secret-example-0000"
# A provider's enumerated reason that is not one of our fixed sentences.
PROVIDER_REASON = "call-transferred"
# A reason the organization's own client sends with a status update.
CLIENT_REASON = "The runner stopped after three turns"


def _marker(source, reason):
    """What set_ended_reason records: the source, then the first 16 hex digits
    of the SHA-256 of the reason it was recorded for."""
    digest = hashlib.sha256(reason.encode("utf-8")).hexdigest()[:16] if reason else ""
    return f"{source}:{digest}"


@pytest.fixture
def ended_run(organization, workspace):
    agent_definition = AgentDefinition.objects.create(
        agent_name=f"End Source Agent {workspace.id}",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        description="Agent for end reason source tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name=f"End Source Scenario {workspace.id}",
        description="Scenario for end reason source tests",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
    )
    run = RunTest.objects.create(
        name=f"End Source Run {workspace.id}",
        description="Run for end reason source tests",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    run.scenarios.add(scenario)
    execution = SimulationTestExecution.objects.create(
        run_test=run,
        status=SimulationTestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        agent_definition=agent_definition,
    )
    return SimpleNamespace(execution=execution, scenario=scenario)


def _create_call(run, *, ended_reason=None, source=None, call_metadata=None, **fields):
    """A voice call, finished unless ``fields`` give another status. With
    ``source``, its reason is written the way our code writes one; without,
    the call carries no source."""
    fields.setdefault("status", CallExecution.CallStatus.COMPLETED)
    call = CallExecution.objects.create(
        test_execution=run.execution,
        scenario=run.scenario,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        ended_reason=ended_reason,
        call_metadata=dict(call_metadata or {}),
        **fields,
    )
    if source is not None:
        call.save(update_fields=set_ended_reason(call, ended_reason, source))
    return call


def _shared_answer(call, user, workspace):
    """What a public shared link to ``call`` returns."""
    link = SharedLink.objects.create(
        resource_type=ResourceType.CALL_EXECUTION.value,
        resource_id=str(call.id),
        access_type=AccessType.PUBLIC,
        created_by=user,
        organization=workspace.organization,
        workspace=workspace,
    )
    return _resolve_shared_call_execution(link)


@pytest.mark.integration
def test_a_shared_call_with_no_source_shows_the_call_ended(ended_run, user, workspace):
    call = _create_call(ended_run, ended_reason=MADE_UP_REASON)

    answer = _shared_answer(call, user, workspace)

    assert answer["ended_reason"] == SHARED_LINK_ENDED
    assert "provider.example.test" not in json.dumps(answer, default=str)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("reason", "source", "shown"),
    [
        pytest.param(
            "Thanks, that is all I needed",
            EndedReasonSource.SIMULATOR,
            "Thanks, that is all I needed",
            id="simulator",
        ),
        pytest.param(
            "customer-did-not-answer",
            EndedReasonSource.PROVIDER,
            "customer-did-not-answer",
            id="provider",
        ),
        pytest.param(
            "Stopped from our own dashboard",
            EndedReasonSource.ORGANIZATION,
            "Stopped from our own dashboard",
            id="organization",
        ),
        pytest.param(
            "Usage limit exceeded",
            EndedReasonSource.USAGE,
            "Usage limit exceeded",
            id="usage",
        ),
        pytest.param(
            RERUN_DISPATCH_FAILED,
            None,
            RERUN_DISPATCH_FAILED,
            id="fixed-sentence-without-source",
        ),
        pytest.param(
            MADE_UP_REASON,
            EndedReasonSource.PROVIDER_ERROR,
            SHARED_LINK_ENDED,
            id="provider-error",
        ),
    ],
)
def test_a_shared_call_shows_its_reason_by_where_it_came_from(
    ended_run, user, workspace, reason, source, shown
):
    call = _create_call(ended_run, ended_reason=reason, source=source)

    assert _shared_answer(call, user, workspace)["ended_reason"] == shown


@pytest.mark.integration
def test_a_shared_call_sends_none_of_its_earlier_runs(ended_run, user, workspace):
    call = _create_call(
        ended_run, ended_reason=CALL_STOPPED, source=EndedReasonSource.FIXED
    )
    for reason in (MADE_UP_REASON, RERUN_DISPATCH_FAILED):
        CallExecutionSnapshot.objects.create(
            call_execution=call,
            rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
            ended_reason=reason,
            analysis_data={"note": MADE_UP_REASON},
            evaluation_data={"note": MADE_UP_REASON},
        )

    shared = _shared_answer(call, user, workspace)
    own = build_call_execution_detail(call, workspace=workspace)

    assert shared["rerun_snapshots"] == []
    assert MADE_UP_REASON not in json.dumps(shared, default=str)
    # The organization's own answer sends the earlier runs as stored.
    own_reasons = sorted(
        snapshot["ended_reason"] for snapshot in own["rerun_snapshots"]
    )
    assert own_reasons == sorted([MADE_UP_REASON, RERUN_DISPATCH_FAILED])


# What the shared call screen reads from the shared answer:
# frontend/src/pages/shared/SharedSimulationCallView.jsx and SharedVoiceView.jsx,
# the drawer parts they reuse (CallDetailsBar, VoiceAudioBridge,
# AudioPlayerCustom), and callTranscript / mapCallDetail in
# src/api/simulate-environments/runDetail.js. None reads provider_call_data or
# rerun_snapshots.
SHARED_SCREEN_FIELDS = {
    "id",
    "simulation_call_type",
    "scenario",
    "status",
    "transcript",
    "function_calls",
    "recordings",
    "audio_url",
    "ended_reason",
    "call_type",
    "phone_number",
    "provider",
    "timestamp",
    "duration",
    "duration_seconds",
    "avg_agent_latency",
    "avg_agent_latency_ms",
    "avg_latency_ms",
    "turn_count",
    "agent_talk_percentage",
    "total_tokens",
    "cost_cents",
    "call_summary",
    "eval_metrics",
    "evaluations",
}


@pytest.mark.integration
def test_a_shared_call_sends_what_its_screen_reads_and_no_provider_payload(
    ended_run, user, workspace
):
    call = _create_call(
        ended_run, ended_reason=CALL_STOPPED, source=EndedReasonSource.FIXED
    )
    provider_payload = {"bland": {"error_message": MADE_UP_REASON}}
    CallExecutionSnapshot.objects.create(
        call_execution=call,
        rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
        ended_reason=MADE_UP_REASON,
        provider_call_data=provider_payload,
    )

    shared = _shared_answer(call, user, workspace)
    own = build_call_execution_detail(call, workspace=workspace)

    assert SHARED_SCREEN_FIELDS - set(shared) == set()
    assert "provider_call_data" not in shared
    assert shared["rerun_snapshots"] == []
    assert "provider.example.test" not in json.dumps(shared, default=str)
    # The organization's own answer sends the snapshot's provider payload.
    assert own["rerun_snapshots"][0]["provider_call_data"] == provider_payload


@pytest.mark.integration
@pytest.mark.parametrize(
    "source",
    [None, EndedReasonSource.PROVIDER_ERROR],
    ids=["no-source", "provider-error"],
)
def test_the_organizations_own_answers_show_the_stored_reason(
    ended_run, workspace, source
):
    call = _create_call(ended_run, ended_reason=MADE_UP_REASON, source=source)

    assert CallExecutionSerializer(call).data["ended_reason"] == MADE_UP_REASON
    detail = build_call_execution_detail(call, workspace=workspace)
    assert detail["ended_reason"] == MADE_UP_REASON


@pytest.mark.integration
def test_no_call_answer_sends_the_source_or_a_secret(ended_run, user, workspace):
    stored = {
        "call_direction": "inbound",
        "error": CALL_STOPPED,
        **{name: f"{SECRET_VALUE}-{name}" for name in CALL_METADATA_SECRET_KEYS},
        # A client's own metadata can hold a secret one level down.
        "client_metadata": {"api_key": f"{SECRET_VALUE}-nested", "team": "support"},
    }
    call = _create_call(
        ended_run,
        ended_reason=CALL_STOPPED,
        source=EndedReasonSource.FIXED,
        call_metadata=stored,
    )
    call.refresh_from_db()
    assert recorded_ended_reason_source(call) == EndedReasonSource.FIXED

    listed = CallExecutionSerializer(call).data
    detail = build_call_execution_detail(call, workspace=workspace)
    shared = _shared_answer(call, user, workspace)

    # The call list sends every other key as stored.
    assert listed["call_metadata"] == {
        "call_direction": "inbound",
        "error": CALL_STOPPED,
        "client_metadata": {"team": "support"},
    }
    # The detail answer, which a shared link reuses, sends no call_metadata.
    assert "call_metadata" not in detail
    assert "call_metadata" not in shared
    for answer in (listed, detail, shared):
        text = json.dumps(answer, default=str)
        assert ENDED_REASON_SOURCE_KEY not in text
        assert SECRET_VALUE not in text


def test_call_answers_leave_out_seven_secret_metadata_names():
    assert CALL_METADATA_SECRET_KEYS == {
        "customer_livekit_api_key",
        "customer_livekit_api_secret",
        "customer_api_key",
        "client_api_key",
        "api_key",
        "api_secret",
        "user_api_key",
    }


@pytest.mark.integration
def test_an_annotation_queue_item_sends_no_source_or_secret(ended_run, monkeypatch):
    call = _create_call(
        ended_run,
        ended_reason=CALL_STOPPED,
        source=EndedReasonSource.FIXED,
        call_metadata={
            "scenario_name": "Billing question",
            "customer_api_key": f"{SECRET_VALUE}-customer",
            "row_data": {"question": "Billed twice", "api_key": f"{SECRET_VALUE}-row"},
        },
    )
    # This reads what the item's answer carries; the queue's own access check
    # has its own tests.
    monkeypatch.setattr(
        annotation_queue_helpers,
        "queue_item_source_is_available",
        lambda *_, **__: True,
    )
    item = SimpleNamespace(
        source_type=QueueItemSourceType.CALL_EXECUTION.value, call_execution=call
    )

    content = annotation_queue_helpers.resolve_source_content(item, ch_cache=object())

    # The export reads the same answer's "metadata".
    assert content["metadata"] == {
        "scenario_name": "Billing question",
        "row_data": {"question": "Billed twice"},
    }
    assert content["input"] == {"question": "Billed twice"}
    text = json.dumps(content, default=str)
    assert ENDED_REASON_SOURCE_KEY not in text
    assert SECRET_VALUE not in text


@pytest.mark.integration
def test_set_ended_reason_keeps_every_other_metadata_key(ended_run):
    stored = {
        "simulator_llm_model": "model-example",
        "row_id": "row-example",
        "eval_started": True,
    }
    call = _create_call(ended_run, call_metadata=stored)

    fields = set_ended_reason(call, "customer-ended-call", EndedReasonSource.PROVIDER)
    call.save(update_fields=fields)

    call.refresh_from_db()
    assert fields == ["ended_reason", "call_metadata"]
    assert call.ended_reason == "customer-ended-call"
    assert call.call_metadata == {
        **stored,
        ENDED_REASON_SOURCE_KEY: _marker(
            EndedReasonSource.PROVIDER, "customer-ended-call"
        ),
    }


def test_set_ended_reason_takes_only_a_known_source():
    call = CallExecution(call_metadata=None)

    with pytest.raises(ValueError, match="Unknown end reason source"):
        set_ended_reason(call, MADE_UP_REASON, "request")

    assert call.ended_reason is None
    assert set_ended_reason(call, CALL_STOPPED, EndedReasonSource.FIXED) == [
        "ended_reason",
        "call_metadata",
    ]
    assert call.call_metadata == {
        ENDED_REASON_SOURCE_KEY: _marker(EndedReasonSource.FIXED, CALL_STOPPED)
    }
    assert recorded_ended_reason_source(call) == EndedReasonSource.FIXED


@pytest.mark.integration
def test_end_calls_with_reason_writes_each_chosen_call_and_keeps_its_metadata(
    ended_run,
):
    chosen = [
        _create_call(ended_run, call_metadata={"row_id": "row-1"}),
        _create_call(ended_run, call_metadata={"row_id": "row-2"}),
    ]
    other = _create_call(ended_run, call_metadata={"row_id": "row-3"})

    written = end_calls_with_reason(
        CallExecution.objects.filter(id__in=[call.id for call in chosen]),
        RERUN_DISPATCH_FAILED,
        EndedReasonSource.FIXED,
        status=CallExecution.CallStatus.FAILED,
    )

    assert written == 2
    for call, row_id in zip(chosen, ("row-1", "row-2"), strict=True):
        call.refresh_from_db()
        assert call.status == CallExecution.CallStatus.FAILED
        assert call.ended_reason == RERUN_DISPATCH_FAILED
        assert call.call_metadata == {
            "row_id": row_id,
            ENDED_REASON_SOURCE_KEY: _marker(
                EndedReasonSource.FIXED, RERUN_DISPATCH_FAILED
            ),
        }
    other.refresh_from_db()
    assert other.status == CallExecution.CallStatus.COMPLETED
    assert other.ended_reason is None
    assert other.call_metadata == {"row_id": "row-3"}


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        pytest.param(
            {
                ENDED_REASON_SOURCE_KEY: _marker(
                    EndedReasonSource.PROVIDER, PROVIDER_REASON
                )
            },
            EndedReasonSource.PROVIDER,
            id="recorded",
        ),
        pytest.param(
            {
                ENDED_REASON_SOURCE_KEY: _marker(
                    EndedReasonSource.PROVIDER, "customer-did-not-answer"
                )
            },
            EndedReasonSource.PROVIDER_ERROR,
            id="recorded-for-another-reason",
        ),
        pytest.param(
            {ENDED_REASON_SOURCE_KEY: _marker("request", PROVIDER_REASON)},
            EndedReasonSource.PROVIDER_ERROR,
            id="unknown",
        ),
        pytest.param(
            {ENDED_REASON_SOURCE_KEY: EndedReasonSource.PROVIDER},
            EndedReasonSource.PROVIDER_ERROR,
            id="no-digest",
        ),
        pytest.param({}, EndedReasonSource.PROVIDER_ERROR, id="none-recorded"),
        pytest.param(None, EndedReasonSource.PROVIDER_ERROR, id="no-metadata"),
    ],
)
def test_a_reason_written_again_keeps_its_source_or_stays_hidden(metadata, expected):
    assert stored_ended_reason_source(metadata, PROVIDER_REASON) == expected


@pytest.mark.integration
@pytest.mark.parametrize(
    ("new_reason", "shown"),
    [
        pytest.param(MADE_UP_REASON, SHARED_LINK_ENDED, id="free-text"),
        pytest.param(RERUN_DISPATCH_FAILED, RERUN_DISPATCH_FAILED, id="fixed"),
    ],
)
def test_a_reason_written_without_its_source_shows_only_when_fixed(
    ended_run, user, workspace, new_reason, shown
):
    call = _create_call(
        ended_run, ended_reason=PROVIDER_REASON, source=EndedReasonSource.PROVIDER
    )
    assert _shared_answer(call, user, workspace)["ended_reason"] == PROVIDER_REASON

    CallExecution.objects.filter(id=call.id).update(ended_reason=new_reason)

    call.refresh_from_db()
    assert recorded_ended_reason_source(call) is None
    assert _shared_answer(call, user, workspace)["ended_reason"] == shown


@pytest.fixture
def hosted_receipt(monkeypatch):
    """Applies a hosted harness result receipt to a saved call. The artifact,
    modality and eval-output steps the receipt runs are stubbed, as in
    test_hosted_end_reason_projection.py."""
    monkeypatch.setattr(
        hosted_harness_ingestion,
        "_resolve_scenario_modality",
        lambda *_: CallExecution.SimulationCallType.TEXT,
    )
    artifacts = MagicMock()
    artifacts.order_by.return_value.last.return_value = None
    monkeypatch.setattr(
        hosted_harness_ingestion.HostedHarnessArtifact.no_workspace_objects,
        "filter",
        lambda **_: artifacts,
    )
    monkeypatch.setattr(
        alk_simulate_ingestion, "_apply_harness_evaluation_outputs", lambda _: None
    )
    monkeypatch.setattr(
        hosted_harness_ingestion.transaction, "on_commit", lambda *_, **__: None
    )

    def apply(call, stop_reason):
        hosted_harness_ingestion._apply_receipt_to_call(
            SimpleNamespace(
                call_execution=call, job=SimpleNamespace(), scenario_key="s"
            ),
            {
                "digest": f"receipt-{stop_reason}",
                "status": "passed",
                "call": {
                    "started_at": "2026-09-30T10:00:00Z",
                    "ended_at": "2026-09-30T10:01:00Z",
                    "duration_ms": 60000,
                    "stop_reason": stop_reason,
                },
            },
        )

    return apply


@pytest.mark.integration
def test_a_hosted_retry_failure_text_shows_the_call_ended(
    ended_run, user, workspace, hosted_receipt
):
    call = _create_call(ended_run, status=CallExecution.CallStatus.ONGOING)
    # Attempt N's receipt records the harness's stop reason as the provider's.
    hosted_receipt(call, PROVIDER_REASON)
    call.refresh_from_db()
    assert recorded_ended_reason_source(call) == EndedReasonSource.PROVIDER
    assert _shared_answer(call, user, workspace)["ended_reason"] == PROVIDER_REASON

    # Attempt N+1's scenario_started reopens the call, as _store_event does.
    CallExecution.objects.filter(id=call.id).update(
        status=CallExecution.CallStatus.ONGOING, completed_at=None, error_message=""
    )
    # The attempt fails: the team's projection stores the job's failure text.
    project_terminal_job_onto_run(
        SimpleNamespace(
            test_execution_id=ended_run.execution.id,
            failure={"message": MADE_UP_REASON},
            payload={"metadata": {}},
            state=HostedHarnessJob.State.FAILED,
        ),
        timezone.now(),
    )

    call.refresh_from_db()
    assert call.ended_reason == MADE_UP_REASON
    # Attempt N's record is still there, for attempt N's reason.
    assert call.call_metadata[ENDED_REASON_SOURCE_KEY] == _marker(
        EndedReasonSource.PROVIDER, PROVIDER_REASON
    )
    assert recorded_ended_reason_source(call) is None
    assert _shared_answer(call, user, workspace)["ended_reason"] == SHARED_LINK_ENDED
    detail = build_call_execution_detail(call, workspace=workspace)
    assert detail["ended_reason"] == MADE_UP_REASON


@pytest.mark.integration
@pytest.mark.parametrize(
    ("earlier", "provider", "engine_reason", "source", "shown"),
    [
        pytest.param(
            (PROVIDER_REASON, EndedReasonSource.PROVIDER),
            "livekit",
            "voicemail",
            EndedReasonSource.PROVIDER,
            "voicemail",
            id="provider",
        ),
        pytest.param(
            (PROVIDER_REASON, EndedReasonSource.PROVIDER),
            "bland",
            MADE_UP_REASON,
            EndedReasonSource.PROVIDER_ERROR,
            SHARED_LINK_ENDED,
            id="bland",
        ),
        pytest.param(
            (MADE_UP_REASON, EndedReasonSource.PROVIDER_ERROR),
            "livekit",
            MADE_UP_REASON,
            EndedReasonSource.PROVIDER_ERROR,
            SHARED_LINK_ENDED,
            id="provider-error-written-again",
        ),
    ],
)
def test_a_reason_the_voice_engine_saves_shows_once_the_activity_records_it(
    ended_run,
    user,
    workspace,
    monkeypatch,
    earlier,
    provider,
    engine_reason,
    source,
    shown,
):
    voice_large = pytest.importorskip("ee.voice.temporal.activities.voice_large")
    # The activity closes stale database connections; here that would close
    # the test's own connection.
    monkeypatch.setattr(voice_large, "close_old_connections", lambda: None)
    # An earlier write recorded the call's reason and its source.
    earlier_reason, earlier_source = earlier
    call = _create_call(ended_run, ended_reason=earlier_reason, source=earlier_source)
    seen = {}

    async def engine_saves_its_reason(**_):
        # As each voice engine does in step 1: the reason alone is saved.
        await CallExecution.objects.filter(id=call.id).aupdate(
            ended_reason=engine_reason
        )
        seen["engine"] = await sync_to_async(_shared_answer)(call, user, workspace)
        return (0, False, False)

    async def recordings(_call_id):
        seen["source"] = recorded_ended_reason_source(
            await CallExecution.objects.aget(id=call.id)
        )
        return SimpleNamespace(
            recording_url=None,
            stereo_recording_url=None,
            customer_recording_url=None,
            assistant_recording_url=None,
            provider_call_data=None,
        )

    async def no_costs(_call_id):
        return SimpleNamespace(
            total=None, stt=None, llm=None, tts=None, transport=None, storage=None
        )

    manager = SimpleNamespace(
        fetch_and_store_call_data=engine_saves_its_reason,
        extract_and_persist_recordings=recordings,
        extract_costs=no_costs,
    )
    monkeypatch.setattr(
        "ee.voice.services.voice_service_manager.VoiceServiceManager",
        lambda **_: manager,
    )
    monkeypatch.setattr("temporalio.activity.info", MagicMock())
    monkeypatch.setattr("temporalio.activity.heartbeat", MagicMock())
    monkeypatch.setattr(
        Heartbeater, "__aenter__", AsyncMock(return_value=MagicMock(details=None))
    )
    monkeypatch.setattr(Heartbeater, "__aexit__", AsyncMock(return_value=None))

    result = async_to_sync(voice_large.fetch_and_persist_call_result)(
        FetchAndPersistCallResultInput(
            call_id=str(call.id),
            status="completed",
            provider_call_id="provider-call-1",
            provider=provider,
            call_type="inbound",
        )
    )

    assert result.success is True
    # Saved by the engine alone, the reason shows "The call ended.".
    assert seen["engine"]["ended_reason"] == SHARED_LINK_ENDED
    # The activity records its source before it reads the recordings.
    assert seen["source"] == source
    call.refresh_from_db()
    assert call.ended_reason == engine_reason
    assert call.call_metadata[ENDED_REASON_SOURCE_KEY] == _marker(source, engine_reason)
    assert _shared_answer(call, user, workspace)["ended_reason"] == shown


def _rerun_one_call(call, run):
    """The reset a re-run of chosen calls makes (CallExecutionRerunView)."""
    CallExecutionRerunView()._clear_call_execution_data(call, run.execution.run_test)


def _rerun_in_bulk(call, run):
    """The reset a re-run of a whole run makes (TestExecutionRerunView)."""
    rerun = TestExecutionRerunView()._prepare_call_executions_for_rerun_bulk(
        CallExecution.objects.filter(id=call.id),
        "call_and_eval",
        run.execution.run_test,
    )
    assert rerun == ([str(call.id)], [])


@pytest.mark.integration
@pytest.mark.parametrize(
    "rerun", [_rerun_one_call, _rerun_in_bulk], ids=["chosen-calls", "whole-run"]
)
def test_a_reason_written_without_its_source_after_a_rerun_shows_the_call_ended(
    ended_run, user, workspace, monkeypatch, rerun
):
    # The re-run queues the call for the LiveKit provider, which needs no
    # phone number id.
    monkeypatch.setenv("SYSTEM_VOICE_PROVIDER", "livekit")
    call = _create_call(
        ended_run,
        ended_reason=PROVIDER_REASON,
        source=EndedReasonSource.PROVIDER,
        call_metadata={
            "eval_started": True,
            "eval_completed": True,
            "eval_queued": {"made-up-config": "2026-01-01T00:00:00Z"},
            "processing_skipped": True,
            "processing_skip_reason": "made-up skip reason",
            "recording_offset_ms": 120,
        },
        phone_number="+15550100000",
    )

    rerun(call, ended_run)

    call.refresh_from_db()
    assert call.ended_reason is None
    # Either kind of re-run clears the previous result's evaluation stamps,
    # skip state and end-reason source; any other metadata stays as saved.
    for key in CallExecution.RESET_METADATA_KEYS:
        assert key not in call.call_metadata
    assert call.call_metadata["recording_offset_ms"] == 120
    # The re-run's reason is written without a source. It is the same text as
    # before, so only the reset's clearing keeps it from showing.
    CallExecution.objects.filter(id=call.id).update(ended_reason=PROVIDER_REASON)
    call.refresh_from_db()
    assert _shared_answer(call, user, workspace)["ended_reason"] == SHARED_LINK_ENDED


@pytest.mark.integration
@pytest.mark.parametrize(
    ("new_status", "source", "shown"),
    [
        pytest.param(
            "cancelled",
            EndedReasonSource.ORGANIZATION,
            CLIENT_REASON,
            id="cancelled",
        ),
        pytest.param(
            "completed",
            EndedReasonSource.PROVIDER_ERROR,
            SHARED_LINK_ENDED,
            id="completed",
        ),
    ],
)
def test_a_status_update_records_where_its_reason_came_from(
    ended_run, auth_client, user, workspace, new_status, source, shown
):
    call = _create_call(ended_run, status=CallExecution.CallStatus.ONGOING)

    response = auth_client.patch(
        f"/simulate/call-executions/{call.id}/",
        {"status": new_status, "ended_reason": CLIENT_REASON},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert ENDED_REASON_SOURCE_KEY not in response.content.decode()
    call.refresh_from_db()
    assert call.ended_reason == CLIENT_REASON
    assert call.call_metadata[ENDED_REASON_SOURCE_KEY] == _marker(source, CLIENT_REASON)
    assert _shared_answer(call, user, workspace)["ended_reason"] == shown


def test_every_fixed_sentence_shows_on_a_shared_link():
    fixed = {
        CALL_STOPPED,
        CALL_COULD_NOT_START,
        USAGE_CHECK_FAILED,
        CHAT_COULD_NOT_START,
        CHAT_STOPPED,
        *SEND_REFUSAL_REASONS,
        RERUN_DISPATCH_FAILED,
        RERUN_STOPPED,
        "Cancelled by user",
        "Rerun cancelled by user",
        "Error processing simulation",
        "Configuration error: Missing API key for outbound call.",
        "Max conversation turns reached",
        "Chat ended by simulator",
        "call_completed",
    }

    assert fixed - SHARED_LINK_FIXED_REASONS == set()


FUTUREAGI = Path(__file__).resolve().parents[2]
SIMULATE = FUTUREAGI / "simulate"
HELPER = SIMULATE / "utils" / "ended_reason.py"
# The two ee/voice activities that store a provider's reason on a call. An OSS
# build has no ee, so the pin reads them only when they are there.
VOICE_ACTIVITIES = (
    FUTUREAGI / "ee" / "voice" / "temporal" / "activities" / "voice_large.py",
    FUTUREAGI / "ee" / "voice" / "temporal" / "activities" / "voice_xl.py",
)
# Not read by the pin: team code whose projection writes a reason with no
# source (a ** keyword); the digest makes a shared link show "The call ended."
NOT_PINNED = {SIMULATE / "services" / "hosted_harness.py"}
# Passes its caller's ended_reason_source on; the callers' keyword is pinned.
FORWARDS_SOURCE = SIMULATE / "repositories" / "call_execution.py"
# The helper's functions that write a call's end reason with its source.
SOURCE_WRITERS = {"set_ended_reason", "end_calls_with_reason"}
QUERYSET_UPDATES = {"update", "aupdate"}
CREATES = {
    "create",
    "acreate",
    "get_or_create",
    "aget_or_create",
    "update_or_create",
    "aupdate_or_create",
}
# The names an error's text is held in where these modules handle a failure,
# as in test_voice_call_ended_reason.py and test_rerun_call_ended_reason.py.
ERROR_TEXT_NAMES = {
    "e",
    "error",
    "error_message",
    "error_msg",
    "dispatch_error",
    "dispatch_error_message",
}


def _pinned_files():
    for path in sorted(SIMULATE.rglob("*.py")):
        parts = path.relative_to(SIMULATE).parts
        if "tests" in parts or "migrations" in parts:
            continue
        if path == HELPER or path in NOT_PINNED:
            continue
        yield path
    yield from (path for path in VOICE_ACTIVITIES if path.exists())


def _names_in(node):
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}


def _called_name(call):
    """The name a call goes through, seeing through ``sync_to_async(f)(...)``."""
    func = call.func
    wrapped = isinstance(func, ast.Call) and func.args
    if wrapped and getattr(func.func, "id", None) == "sync_to_async":
        func = func.args[0]
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _writes_a_call(call):
    """A queryset update, or a CallExecution created with its fields."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "CallExecution"
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in QUERYSET_UPDATES:
        return True
    return func.attr in CREATES and "CallExecution" in _names_in(func.value)


def _is_constant(node, value):
    return isinstance(node, ast.Constant) and node.value == value


def _reads_call_metadata(node):
    """Whether ``node`` reads a call's stored metadata: ``<x>.call_metadata``,
    ``<x>.get("call_metadata")`` or ``<x>["call_metadata"]``."""
    if isinstance(node, ast.Attribute):
        return node.attr == "call_metadata"
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and bool(node.args)
            and _is_constant(node.args[0], "call_metadata")
        )
    if isinstance(node, ast.Subscript):
        return _is_constant(node.slice, "call_metadata")
    return False


def _is_fixed_source(node, *, allow_none=False):
    """A source our code chose: an EndedReasonSource attribute, a choice
    between two of them, or the source the call's own writer recorded, read
    from the call's stored metadata."""
    if isinstance(node, ast.Attribute):
        return (
            isinstance(node.value, ast.Name)
            and node.value.id == "EndedReasonSource"
            and node.attr.isupper()
            and node.attr in vars(EndedReasonSource)
        )
    if isinstance(node, ast.IfExp):
        branches = (node.body, node.orelse)
        return all(_is_fixed_source(b, allow_none=allow_none) for b in branches)
    if isinstance(node, ast.Call):
        return (
            getattr(node.func, "id", None) == "stored_ended_reason_source"
            and bool(node.args)
            and _reads_call_metadata(node.args[0])
        )
    return allow_none and isinstance(node, ast.Constant) and node.value is None


def _is_setattr(node):
    return (
        isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "setattr"
        and len(node.args) == 3
    )


def _holds_ended_reason_key(node):
    """Whether a ``**`` argument holds a dict literal with an "ended_reason"
    key, at any depth."""
    return any(
        isinstance(sub, ast.Dict)
        and any(_is_constant(key, "ended_reason") for key in sub.keys)
        for sub in ast.walk(node)
    )


def _from_error_text(reason):
    """Whether a reason is built from an error's text. chat_sim's
    ``_stopped_reason(e)`` returns CHAT_STOPPED or the chat service's own
    fixed refusal (test_chat_call_ended_reason.py drives it)."""
    if isinstance(reason, ast.Call):
        if getattr(reason.func, "id", None) == "_stopped_reason":
            return False
    return bool(_names_in(reason) & ERROR_TEXT_NAMES)


def _scan(path):
    """The places ``path`` writes a call's end reason outside the helper,
    gives a source our code did not choose, or builds a reason from an
    error's text; and how many writes go through the helper."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems = []
    written = 0

    def problem(node):
        where = f"{path.relative_to(FUTUREAGI)}:{node.lineno}"
        problems.append(f"{where}: {ast.unparse(node)}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == ENDED_REASON_SOURCE_KEY:
            # Only the helper names the key; everyone else imports it.
            problem(node)
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            writes = any(
                isinstance(sub, ast.Attribute)
                and sub.attr == "ended_reason"
                and isinstance(sub.ctx, ast.Store)
                for target in targets
                for sub in ast.walk(target)
            )
            # Clearing a call's reason for a rerun writes no reason.
            clears = isinstance(node.value, ast.Constant) and node.value.value is None
            if writes and not clears:
                problem(node)
        loops_over_the_name = (
            isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and isinstance(node.iter, ast.Tuple | ast.List)
            and any(_is_constant(item, "ended_reason") for item in node.iter.elts)
        )
        if loops_over_the_name:
            # setattr(<x>, field, ...) for field in ("ended_reason", ...).
            field = node.target.id
            for sub in ast.walk(node):
                if _is_setattr(sub) and getattr(sub.args[1], "id", None) == field:
                    problem(sub)
        if not isinstance(node, ast.Call):
            continue
        if _is_setattr(node) and _is_constant(node.args[1], "ended_reason"):
            problem(node)
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        splats = [keyword.value for keyword in node.keywords if keyword.arg is None]
        named = "ended_reason" in keywords
        splatted = any(_holds_ended_reason_key(value) for value in splats)
        if (named or splatted) and _writes_a_call(node):
            problem(node)
        if "ended_reason_source" in keywords and not _is_fixed_source(
            keywords["ended_reason_source"], allow_none=True
        ):
            problem(node)
        if _called_name(node) not in SOURCE_WRITERS:
            continue
        written += 1
        reason = node.args[1] if len(node.args) > 1 else keywords.get("reason")
        source = node.args[2] if len(node.args) > 2 else keywords.get("source")
        forwarded = (
            path == FORWARDS_SOURCE
            and isinstance(source, ast.Name)
            and source.id == "ended_reason_source"
        )
        if source is None or not (forwarded or _is_fixed_source(source)):
            problem(node)
        if reason is not None and _from_error_text(reason):
            problem(node)
    return problems, written


def test_every_end_reason_write_records_a_source_our_code_chose():
    """Every write of a call's end reason goes through the helper with a
    source our code chose, never a request's or a payload's value, and no
    reason is built from an error's text. An assignment, an ``ended_reason=``
    keyword, a ``**`` dict with that key or a setattr naming it is a write.
    The writes run across the chat, voice, rerun, ingestion and webhook paths,
    so this reads the source."""
    problems = []
    writing_files = set()
    for path in _pinned_files():
        found, written = _scan(path)
        problems.extend(found)
        if written:
            writing_files.add(path.relative_to(FUTUREAGI).as_posix())

    assert problems == []
    assert {
        "simulate/services/chat_sim.py",
        "simulate/services/test_executor.py",
        "simulate/tasks/chat_sim.py",
        "simulate/views/run_test.py",
        "simulate/services/alk_simulate_ingestion.py",
    } <= writing_files
