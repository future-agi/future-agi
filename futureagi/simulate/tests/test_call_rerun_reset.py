"""A call-and-eval rerun keeps the call's result in a snapshot, then resets the row to how a new call starts. A hosted
rerun also drops the call's metadata except its row id and the keys it names; a native rerun keeps it, less the eval
flags."""

from __future__ import annotations

import pytest

from simulate.models.test_execution import CallExecution, CallExecutionSnapshot
from simulate.services.call_rerun import clear_call_for_rerun
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
    assert call.call_metadata == {"row_id": "row-1", "attempt_id": "a-1"}
    snapshot = CallExecutionSnapshot.objects.get(call_execution=call)
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
