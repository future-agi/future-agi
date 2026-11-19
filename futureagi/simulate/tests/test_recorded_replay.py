import json

import pytest
from django.test import override_settings

from simulate.models import CallExecution
from simulate.selectors import recorded_replay as recorded_replay_module
from simulate.selectors.recorded_replay import (
    RECORDED_REPLAY_KEY,
    recorded_replay,
    replay_of,
)
from simulate.serializers.harness_job import HarnessRunCreateSerializer
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_selected_harness_run,
)
from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
from simulate.services.recorded_replay import create_recorded_replay_run
from simulate.tests.test_harness_unpinned_runs import _environment
from simulate.views.run_results_v3 import build_call_execution_detail

pytestmark = pytest.mark.django_db

# The agent's calls as the world's door logged them, with what each caller received.
AGENT_ROWS = [
    {
        "name": "find_booking",
        "action": "find_booking",
        "method": "POST",
        "arguments": {"booking_id": "B-7"},
        "effect": "read",
        "outcome": "committed",
        "refusal_code": None,
        "error": "",
        "fault": None,
        "received": {"status": 200, "content_type": "text/plain", "body": '{"id": "B-7"}', "value": '{"id": "B-7"}',
                     "exact": True},
        "result": {"id": "B-7"},
        "at": 3.5,
        "action_id": "a1",
        "actor_id": "agent",
    },
    {
        "name": "cancel_booking",
        "action": "cancel_booking",
        "method": "POST",
        "arguments": {"booking_id": "B-7"},
        "effect": "write",
        "outcome": "refused",
        "refusal_code": "world_rule",
        "error": "late cancellation",
        "fault": None,
        "received": {"status": 400, "content_type": "text/plain", "body": "late cancellation", "value": "null",
                     "exact": True},
        "at": 4.0,
        "action_id": "a2",
        "actor_id": "agent",
    },
]
# An actor's action and the mark of a request stopped at the world's edge: never part of what the agent received.
NOT_THE_AGENTS = [
    {"name": "approve", "actor_id": "manager", "outcome": "committed", "received": {"status": 200}},
    {"name": "fetch", "actor_id": "agent", "refusal_code": "egress_blocked", "outcome": "refused"},
]
# The world's records at the trial's end: kept for re-grading, and evaluator data a replay never carries.
TABLES = {"bookings": [{"id": "B-7", "status": "booked", "internal_note": "VIP"}]}

KEPT = ("action", "name", "method", "arguments", "effect", "outcome", "refusal_code", "error", "fault", "received")


class _Stored:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def close(self):
        return None

    def release_conn(self):
        return None


@pytest.fixture
def sealed(monkeypatch):
    """Each trial's sealed end state, by storage key, as the job stored it."""
    kept: dict[str, bytes] = {}

    class _Client:
        def get_object(self, bucket, key):
            return _Stored(kept[key])

    monkeypatch.setattr(recorded_replay_module, "get_storage_client", lambda: _Client())
    return kept


def _original(user, workspace, key, sealed, *, trials=1, log=None, **call_fields):
    """A finished run of one scenario whose trials sealed the world's door log with their end state."""
    environment = _environment(user, workspace, key)
    with override_settings(HARNESS_MAX_ARTIFACT_BYTES=10_737_418_240):
        run, _ = create_selected_harness_run(
            environment, scenario_keys=["scenario-a"], trials=trials, idempotency_key=f"{key}-run"
        )
    calls = list(CallExecution.objects.filter(test_execution=run.test_execution).order_by("created_at"))
    for call in calls:
        object_key = f"harness/world_state/{call.id}.json"
        sealed[object_key] = json.dumps(
            {"schema": "end", "tables": TABLES, "skipped": [],
             "action_log": log if log is not None else [*AGENT_ROWS, *NOT_THE_AGENTS]},
            sort_keys=True,
        ).encode()
        call.status = CallExecution.CallStatus.FAILED
        call.simulation_call_type = CallExecution.SimulationCallType.TEXT
        call.call_metadata = {
            **(call.call_metadata or {}),
            TRIAL_STATE_KEY: {"door": True},
            "hosted_harness_artifacts": {"world_state": {"object_key": object_key, "url": "https://store/x"}},
        }
        for field, value in call_fields.items():
            setattr(call, field, value)
        call.save()
    return environment, run, calls


def _replay(environment, call, key="replay", agent_version_id=None):
    with override_settings(HARNESS_MAX_ARTIFACT_BYTES=10_737_418_240):
        return create_recorded_replay_run(
            environment, call_execution_id=call.id, idempotency_key=key, agent_version_id=agent_version_id
        )


def test_a_replay_runs_the_trials_scenario_once_with_what_its_agent_received(user, workspace, settings, sealed):
    settings.HARNESS_VERSIONING_ENABLED = False
    environment, run, [call] = _original(user, workspace, "replay-shape", sealed)
    before = (call.status, call.call_metadata)

    replay, created = _replay(environment, call)

    assert created
    held = replay.payload["metadata"][RECORDED_REPLAY_KEY]
    assert held["source_call_execution_id"] == str(call.id)
    assert held["scenario_key"] == "scenario-a"
    # The agent's own calls, in order, with what each asked, how it ended and what its code received: no actor's
    # action, no stopped request, no timing, and never the world's records.
    assert held["calls"] == [{key: row[key] for key in KEPT} for row in AGENT_ROWS]
    assert "internal_note" not in json.dumps(replay.payload)
    assert replay.payload["metadata"]["selected_scenario_keys"] == ["scenario-a"]
    assert replay.payload["metadata"]["trials"] == 1
    assert replay.seed == run.seed
    # The run's own record names the trial it replays without carrying the recording.
    assert replay.test_execution.execution_metadata[RECORDED_REPLAY_KEY] == {
        key: value for key, value in held.items() if key != "calls"
    }
    assert replay_of(replay.test_execution) == {
        "call_execution_id": str(call.id),
        "test_execution_id": str(run.test_execution_id),
    }
    call.refresh_from_db()
    assert (call.status, call.call_metadata) == before


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"status": CallExecution.CallStatus.ONGOING}, "once this trial has finished"),
        ({"simulation_call_type": CallExecution.SimulationCallType.VOICE}, "chat trials only"),
        ({"call_metadata": {TRIAL_STATE_KEY: {"door": True}, "harness_call_actors": [{"id": "m"}]}}, "other people"),
        ({"call_metadata": {TRIAL_STATE_KEY: {"recorded": False, "why": "in_process_tools"}}}, "Rebuild it"),
        ({"call_metadata": {}}, "Rebuild it"),
        ({"call_metadata": {TRIAL_STATE_KEY: {"door": True}}}, "kept no recording"),
    ],
)
def test_a_trial_that_cannot_be_replayed_says_why(user, workspace, settings, sealed, fields, reason):
    settings.HARNESS_VERSIONING_ENABLED = False
    environment, _, [call] = _original(user, workspace, "replay-refused", sealed, **fields)

    with pytest.raises(HostedHarnessError) as refused:
        _replay(environment, call)

    assert refused.value.code == "replay_unavailable"
    assert reason in refused.value.message
    assert recorded_replay(call)["available"] is False
    assert reason in recorded_replay(call)["unavailable_reason"]


def test_a_trial_recorded_before_answers_were_kept_says_so(user, workspace, settings, sealed):
    settings.HARNESS_VERSIONING_ENABLED = False
    old = [{key: value for key, value in row.items() if key != "received"} for row in AGENT_ROWS]
    environment, _, [call] = _original(user, workspace, "replay-old", sealed, log=old)

    with pytest.raises(HostedHarnessError) as refused:
        _replay(environment, call)

    assert "recorded before recorded replay kept tool answers" in refused.value.message


def test_a_trial_that_made_no_tool_call_is_replayed_with_an_empty_recording(user, workspace, settings, sealed):
    settings.HARNESS_VERSIONING_ENABLED = False
    environment, _, [call] = _original(user, workspace, "replay-empty", sealed, log=[])

    replay, _ = _replay(environment, call)

    assert replay.payload["metadata"][RECORDED_REPLAY_KEY]["calls"] == []


def test_a_replay_is_not_replayed_again(user, workspace, settings, sealed):
    settings.HARNESS_VERSIONING_ENABLED = False
    environment, _, [call] = _original(user, workspace, "replay-twice", sealed)
    replay, _ = _replay(environment, call)
    replayed_call = CallExecution.objects.get(test_execution=replay.test_execution)
    replayed_call.status = CallExecution.CallStatus.COMPLETED
    replayed_call.simulation_call_type = CallExecution.SimulationCallType.TEXT
    replayed_call.call_metadata = {**(replayed_call.call_metadata or {}), TRIAL_STATE_KEY: {"door": True}}
    replayed_call.save()

    assert "Replay the original trial instead" in (recorded_replay(replayed_call)["unavailable_reason"] or "")


def test_a_key_repeated_for_the_same_trial_returns_its_replay_and_for_another_is_refused(
    user, workspace, settings, sealed
):
    settings.HARNESS_VERSIONING_ENABLED = False
    environment, _, [first, second] = _original(user, workspace, "replay-key", sealed, trials=2)
    replay, created = _replay(environment, first, key="same-key")

    again, created_again = _replay(environment, first, key="same-key")
    assert created and not created_again and again.id == replay.id

    with pytest.raises(HostedHarnessError) as refused:
        _replay(environment, second, key="same-key")
    assert refused.value.code == "idempotency_conflict"


def test_a_trial_of_another_environment_is_unknown(user, workspace, settings, sealed):
    settings.HARNESS_VERSIONING_ENABLED = False
    _, _, [call] = _original(user, workspace, "replay-mine", sealed)
    other = _environment(user, workspace, "replay-other")

    with pytest.raises(HostedHarnessError) as refused:
        _replay(other, call)

    assert refused.value.code == "replay_trial_unknown"


@pytest.mark.parametrize(
    ("body", "error_field"),
    [
        ({}, "scenario_ids"),
        ({"recorded_replay_of": "8d1c5f5e-6b4c-4b8e-9d0f-0e7b8a1c2d3e", "scenario_ids": ["a"]}, "scenario_ids"),
        ({"recorded_replay_of": "8d1c5f5e-6b4c-4b8e-9d0f-0e7b8a1c2d3e", "trials": 2}, "trials"),
        ({"recorded_replay_of": "8d1c5f5e-6b4c-4b8e-9d0f-0e7b8a1c2d3e", "seed": "7"}, "seed"),
    ],
)
def test_the_run_request_takes_a_replay_or_scenarios_never_both(body, error_field):
    serializer = HarnessRunCreateSerializer(data=body)

    assert not serializer.is_valid()
    assert error_field in serializer.errors


def test_a_replay_request_may_name_another_agent_version():
    serializer = HarnessRunCreateSerializer(
        data={
            "recorded_replay_of": "8d1c5f5e-6b4c-4b8e-9d0f-0e7b8a1c2d3e",
            "agent_version_id": "1d1c5f5e-6b4c-4b8e-9d0f-0e7b8a1c2d3e",
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_a_replays_detail_names_its_original_and_how_it_ended(user, workspace, settings, sealed):
    settings.HARNESS_VERSIONING_ENABLED = False
    environment, _, [call] = _original(user, workspace, "replay-detail", sealed)
    replay, _ = _replay(environment, call)
    replayed_call = CallExecution.objects.get(test_execution=replay.test_execution)

    shown = build_call_execution_detail(replayed_call)["recorded_replay"]
    original = build_call_execution_detail(call)["recorded_replay"]

    assert shown["available"] is False
    assert shown["original"]["call_execution_id"] == str(call.id)
    assert shown["original"]["outcome"]
    assert original == {"available": True, "unavailable_reason": None, "original": None}
