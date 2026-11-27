"""A call-and-eval rerun keeps the call's result in a snapshot, then resets the row to how a new call starts. A hosted
rerun also drops the call's metadata except its row id and the keys it names; a native rerun keeps it, less the eval
flags."""

from __future__ import annotations

import pytest

from simulate.models.test_execution import CallExecution, CallExecutionSnapshot
from simulate.services.call_rerun import clear_call_for_rerun
from simulate.services.harness_worker_states import ATTEMPTS_KEY, MAX_KEPT_ATTEMPTS
from simulate.tests.test_rerun_api import (  # fixtures this file builds on
    agent_definition,
    call_execution,
    dataset_for_scenario,
    run_test,
    scenario,
    simulator_agent,
    test_execution,
)

pytestmark = pytest.mark.django_db

FIXTURES = (
    agent_definition,
    call_execution,
    dataset_for_scenario,
    run_test,
    scenario,
    simulator_agent,
    test_execution,
)


def _finished(call):
    call.provider_call_data = {"vapi": {"id": "x"}}
    call.customer_call_id = "customer-1"
    call.storage_cost_cents = 1.5
    call.cost_cents = 12
    call.duration_seconds = 30
    call.transcript_available = True
    call.tool_outputs = {"t": 1}
    call.call_metadata = {
        **call.call_metadata,
        "row_id": "row-1",
        "attempt_id": "a-1",
        "alk_batch_claimed": True,
    }
    call.save()
    return call


def test_a_hosted_rerun_resets_the_row_and_keeps_only_its_row_id_and_named_keys(
    call_execution,
):
    call = _finished(call_execution)

    clear_call_for_rerun(call, keep_metadata_keys=("attempt_id",))
    call.refresh_from_db()

    assert (call.status, call.service_provider_call_id) == (
        CallExecution.CallStatus.PENDING,
        None,
    )
    # Reset as a native rerun resets: no provider payload, no customer call id, no storage cost.
    assert (
        call.provider_call_data,
        call.customer_call_id,
        call.storage_cost_cents,
    ) == (None, None, None)
    assert (call.cost_cents, call.duration_seconds, call.transcript_available) == (
        None,
        None,
        False,
    )
    assert (call.eval_outputs, call.tool_outputs) == ({}, {})
    snapshot = CallExecutionSnapshot.objects.get(call_execution=call)
    # With the closed attempt's kept record, by its snapshot's id.
    assert call.call_metadata == {
        "row_id": "row-1",
        "attempt_id": "a-1",
        ATTEMPTS_KEY: {
            str(snapshot.id): call.call_metadata[ATTEMPTS_KEY][str(snapshot.id)]
        },
    }
    assert (
        snapshot.provider_call_data,
        snapshot.cost_cents,
        snapshot.service_provider_call_id,
    ) == (
        {"vapi": {"id": "x"}},
        12,
        "vapi-test-123",
    )


def test_a_native_rerun_keeps_the_metadata_less_the_eval_flags(call_execution):
    call = _finished(call_execution)

    clear_call_for_rerun(call, clear_metadata=False)
    call.refresh_from_db()

    assert (
        "eval_started" not in call.call_metadata
        and "eval_completed" not in call.call_metadata
    )
    assert call.call_metadata["alk_batch_claimed"] is True
    assert call.call_metadata["base_prompt"] == "You are a test agent"


def test_a_native_rerun_never_carries_the_earlier_attempt_s_worker_record_into_the_new_one(
    call_execution,
):
    from simulate.models.test_execution import CallExecutionSnapshot
    from simulate.services.harness_workers import READ_KEY, WORKERS_KEY

    call = _finished(call_execution)
    record = {"workers": [{"id": "refund"}], "events": []}
    call.call_metadata = {
        **call.call_metadata,
        WORKERS_KEY: record,
        READ_KEY: {"at": "2026-11-17T09:00:00+00:00", "failed": False},
    }
    call.save()

    clear_call_for_rerun(call, clear_metadata=False)
    call.refresh_from_db()

    # The new attempt starts with no record of its own; the earlier one is kept by its snapshot's id.
    assert WORKERS_KEY not in call.call_metadata and READ_KEY not in call.call_metadata
    assert call.call_metadata["alk_batch_claimed"] is True
    snapshot = CallExecutionSnapshot.objects.get(call_execution=call)
    kept = call.call_metadata[ATTEMPTS_KEY][str(snapshot.id)]
    assert (kept[WORKERS_KEY], kept[READ_KEY]["failed"]) == (record, False)
    # Only what the attempt's worker record and trial read: not the call's other metadata.
    assert "alk_batch_claimed" not in kept and "row_id" not in kept


@pytest.mark.parametrize("clear_metadata", [True, False])
def test_each_rerun_keeps_the_earlier_attempts_and_only_the_latest_ten(
    call_execution, clear_metadata
):
    from simulate.services.harness_workers import WORKERS_KEY

    call = _finished(call_execution)
    for index in range(MAX_KEPT_ATTEMPTS + 2):
        call.call_metadata = {
            **call.call_metadata,
            WORKERS_KEY: {"workers": [{"id": f"w{index}"}], "events": []},
        }
        call.save()
        clear_call_for_rerun(call, clear_metadata=clear_metadata)
        call.refresh_from_db()

    snapshots = list(
        CallExecutionSnapshot.objects.filter(call_execution=call).order_by(
            "snapshot_timestamp"
        )
    )
    kept = call.call_metadata[ATTEMPTS_KEY]
    # A reset keeps the attempts; the two oldest are dropped, the latest ten kept with their own record.
    assert set(kept) == {str(one.id) for one in snapshots[2:]}
    assert [
        kept[str(one.id)][WORKERS_KEY]["workers"][0]["id"] for one in snapshots[2:]
    ] == [f"w{index}" for index in range(2, MAX_KEPT_ATTEMPTS + 2)]


def test_a_kept_attempt_holds_only_what_its_worker_answer_and_trial_view_read(
    call_execution,
):
    from simulate.constants.hosted_harness import TRIAL_STATE_KEY
    from simulate.services.harness_workers import READ_KEY, WORKERS_KEY
    from simulate.services.hosted_harness_ingestion import WORLD_ACTIONS_KEY

    call = _finished(call_execution)
    call.call_metadata = {
        **call.call_metadata,
        WORKERS_KEY: {"workers": [{"id": "refund"}], "events": []},
        READ_KEY: {"at": "2026-11-17T09:00:00+00:00", "failed": False},
        TRIAL_STATE_KEY: {"state_changes": [{"who": "worker:refund", "attempted": 1}]},
        WORLD_ACTIONS_KEY: [
            {
                "actor_id": "a1",
                "action": "approve",
                "refusal_code": "x",
                "result": {"big": "x" * 500},
            }
        ],
        "hosted_harness_receipt": {
            "sub_goals": [{"name": "s"}],
            "failure": None,
            "call": {"transcript": "x" * 500},
        },
    }
    call.save()

    clear_call_for_rerun(call, clear_metadata=False)
    call.refresh_from_db()

    [kept] = call.call_metadata[ATTEMPTS_KEY].values()
    assert set(kept) <= {
        WORKERS_KEY,
        READ_KEY,
        TRIAL_STATE_KEY,
        WORLD_ACTIONS_KEY,
        "hosted_harness_receipt",
        "sub_goals",
        "harness_attempt_end",
        "kept_at",
    }
    # The receipt and the world's actions only in the fields the views read.
    assert kept["hosted_harness_receipt"] == {
        "sub_goals": [{"name": "s"}],
        "failure": None,
    }
    assert kept[WORLD_ACTIONS_KEY] == [
        {"actor_id": "a1", "action": "approve", "refusal_code": "x"}
    ]
    assert "alk_batch_claimed" not in kept and "base_prompt" not in kept
    # The live call keeps none of them: the new attempt's trial, receipt and record start empty.
    assert not {
        WORKERS_KEY,
        READ_KEY,
        TRIAL_STATE_KEY,
        WORLD_ACTIONS_KEY,
        "hosted_harness_receipt",
    } & set(call.call_metadata)
    assert call.call_metadata["alk_batch_claimed"] is True


def test_an_attempt_record_past_the_size_bound_is_not_kept(call_execution, monkeypatch):
    from simulate.services import call_rerun
    from simulate.services.harness_workers import WORKERS_KEY

    call = _finished(call_execution)
    call.call_metadata = {
        **call.call_metadata,
        WORKERS_KEY: {"workers": [{"id": "w"}], "events": []},
    }
    call.save()
    clear_call_for_rerun(call, clear_metadata=False)
    call.refresh_from_db()
    monkeypatch.setattr(call_rerun, "MAX_KEPT_ATTEMPT_BYTES", 64)
    call.call_metadata = {
        **call.call_metadata,
        WORKERS_KEY: {"workers": [{"id": "big" * 50}], "events": []},
    }
    call.save()

    clear_call_for_rerun(call, clear_metadata=False)
    call.refresh_from_db()

    # The oversized attempt reads "not kept"; the one before it is kept as it was.
    first, _second = CallExecutionSnapshot.objects.filter(call_execution=call).order_by(
        "snapshot_timestamp"
    )
    assert list(call.call_metadata[ATTEMPTS_KEY]) == [str(first.id)]


def test_a_rerun_keeps_what_the_row_holds_now_not_what_it_held_when_loaded(
    call_execution,
):
    call = _finished(call_execution)
    # A worker read or a receipt writes the row after the rerun loaded it.
    CallExecution.objects.filter(id=call.id).update(
        call_metadata={**call.call_metadata, "written_meanwhile": True}
    )

    clear_call_for_rerun(call, clear_metadata=False)
    call.refresh_from_db()

    assert call.call_metadata["written_meanwhile"] is True
