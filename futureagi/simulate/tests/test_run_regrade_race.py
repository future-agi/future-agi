"""A writer on another connection while a re-grade's dispatch fails.

The put-back runs after the claim has committed, so it meets whatever
another request wrote to the same calls in between. It must wait for that
writer's lock, and keep what the writer left.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
from django.db import connection
from django.db import transaction as db_transaction

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    CallExecution,
    CallExecutionSnapshot,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.services.harness_run_evals import EVAL_QUEUED_KEY
from simulate.services.run_regrade import RegradeDispatchFailed, regrade_run_evals

COMPLETED = TestExecution.ExecutionStatus.COMPLETED
EVALUATING = TestExecution.ExecutionStatus.EVALUATING
STAMP = "2026-01-01T00:00:00+00:00"
# Long enough for a loaded worker to reach the put-back; short enough that a
# put-back that never waits fails the test instead of hanging it.
WAIT_FOR_THE_PUT_BACK = 30


def _another_backend_waits_on_a_lock():
    """True once some other backend of this database is blocked on a lock."""
    with connection.cursor() as cursor:
        # pg_stat_activity is read from a snapshot kept for the rest of the
        # transaction, so without this each poll could repeat the first one.
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() "
            "AND pid <> pg_backend_pid() AND wait_event_type = 'Lock'"
        )
        return cursor.fetchone()[0] > 0


@pytest.fixture
def run_test(db, organization, workspace):
    return RunTest.objects.create(
        name="Regrade race", organization=organization, workspace=workspace
    )


@pytest.fixture
def scenario(db, organization, workspace):
    return Scenarios.objects.create(
        name="Scenario",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
    )


def _config(run_test, organization, name):
    template = EvalTemplate.objects.create(
        name=f"{name}_race_template",
        config={"required_keys": ["output"], "output": "Pass/Fail"},
        organization=organization,
    )
    return SimulateEvalConfig.objects.create(
        name=name,
        eval_template=template,
        run_test=run_test,
        config={},
        mapping={"output": "transcript"},
    )


@pytest.mark.django_db(transaction=True)
def test_a_write_made_during_the_dispatch_survives_the_put_back(
    run_test, scenario, organization
):
    graded_a = _config(run_test, organization, "alpha")
    graded_b = _config(run_test, organization, "beta")
    other = _config(run_test, organization, "gamma")
    a, b, c = str(graded_a.id), str(graded_b.id), str(other.id)
    run = TestExecution.objects.create(
        run_test=run_test, status=COMPLETED, total_scenarios=1, total_calls=1
    )
    previous_b = {"output": 0.4, "output_type": "score", "status": "completed"}
    call = CallExecution.objects.create(
        test_execution=run,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        eval_outputs={b: previous_b},
        call_metadata={"eval_started": True, "eval_completed": True},
    )
    result_for_a = {"output": 0.9, "output_type": "score", "status": "completed"}
    writer_failed = []

    def another_request_writes():
        try:
            with db_transaction.atomic():
                locked = CallExecution.objects.select_for_update().get(id=call.id)
                locked_metadata = dict(locked.call_metadata)
                locked_metadata[EVAL_QUEUED_KEY] = {
                    **locked_metadata.get(EVAL_QUEUED_KEY, {}),
                    c: STAMP,
                }
                locked_metadata["eval_started"] = True
                locked_metadata["eval_completed"] = False
                locked_metadata["csat_status"] = "completed"
                locked_outputs = dict(locked.eval_outputs)
                locked_outputs[a] = result_for_a
                locked_outputs[c] = {"status": "pending"}
                locked.call_metadata = locked_metadata
                locked.eval_outputs = locked_outputs
                locked.save(update_fields=["call_metadata", "eval_outputs"])
                written.set()
                # Held until the put-back is seen waiting for it, however
                # slow the worker: a put-back that reads the call without
                # waiting would see this write only by luck of timing.
                deadline = time.monotonic() + WAIT_FOR_THE_PUT_BACK
                while not _another_backend_waits_on_a_lock():
                    if time.monotonic() > deadline:
                        raise AssertionError("the put-back never waited for the lock")
                    time.sleep(0.05)
        except Exception as error:  # noqa: BLE001
            writer_failed.append(error)
            written.set()
        finally:
            connection.close()

    written = threading.Event()
    writer = threading.Thread(target=another_request_writes)

    def broker_fails_while_the_writer_holds_the_call(*args, **kwargs):
        writer.start()
        assert written.wait(timeout=WAIT_FOR_THE_PUT_BACK)
        raise ConnectionError("broker unreachable")

    with patch(
        "simulate.services.test_executor.run_new_evals_on_call_executions_task.apply_async",
        side_effect=broker_fails_while_the_writer_holds_the_call,
    ):
        with pytest.raises(RegradeDispatchFailed):
            regrade_run_evals(
                run_test,
                [str(run.id)],
                {str(run.id): COMPLETED},
                [a, b],
                harness_run=True,
                call_execution_ids=[str(call.id)],
                snapshot_eval_outputs=True,
            )
    writer.join(timeout=WAIT_FOR_THE_PUT_BACK)

    assert writer_failed == []
    call.refresh_from_db()
    # The other writer's work stays ...
    assert call.eval_outputs[a] == result_for_a
    assert call.eval_outputs[c] == {"status": "pending"}
    assert call.call_metadata[EVAL_QUEUED_KEY] == {c: STAMP}
    assert call.call_metadata["eval_started"] is True
    assert call.call_metadata["eval_completed"] is False
    assert call.call_metadata["csat_status"] == "completed"
    # ... and this request's other cell is back as it was.
    assert call.eval_outputs[b] == previous_b
    assert "eval_dispatch_failed" in call.call_metadata
    assert not CallExecutionSnapshot.objects.exists()
    run.refresh_from_db()
    # The other writer's config is still being graded, so the run is too.
    assert run.status == EVALUATING
