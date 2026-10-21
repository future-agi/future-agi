"""TH-5259 review regressions: unknown/failed waits must not evaluate early."""

from unittest.mock import AsyncMock, patch

import pytest

from tfc.temporal.experiments import activities
from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
from tfc.temporal.experiments.workflows import RerunCellsV2Workflow

pytestmark = pytest.mark.django_db


async def test_visibility_outage_is_not_proof_that_outputs_are_final():
    from temporalio.client import WorkflowExecutionStatus

    from tfc.temporal.experiments.tests.test_wait_for_experiment_runs import FakeClient

    pending = await activities._experiment_runs_in_flight(
        FakeClient(WorkflowExecutionStatus.RUNNING, fail=True), "exp", "self"
    )
    assert pending, "An unavailable describe/visibility service must not release evals"


@pytest.mark.parametrize("wait_status", ["TIMED_OUT", "UNKNOWN"])
async def test_non_ready_wait_does_not_dispatch_evaluations(wait_status):
    calls = []

    async def execute(name, *args, **kwargs):
        calls.append((name, args, kwargs))
        if name == "wait_for_experiment_runs_activity":
            return {"status": wait_status}
        if name == "mark_experiment_running_activity":
            return {"status": "READY"}
        return {"status": "COMPLETED", "row_ids": [], "edt_column_ids": []}

    with (
        patch(
            "tfc.temporal.experiments.workflows.workflow.execute_activity",
            side_effect=execute,
        ),
        patch("tfc.temporal.experiments.workflows.workflow.info") as info,
    ):
        info.return_value.workflow_id = "eval-rerun"
        result = await RerunCellsV2Workflow().run(
            RerunCellsV2WorkflowInput(
                experiment_id="exp",
                dataset_id="dataset",
                eval_template_ids=["metric"],
                eval_only=True,
                wait_for_inflight_runs=True,
            )
        )
    assert result.status == "FAILED"
    assert [x[0] for x in calls] == [
        "wait_for_experiment_runs_activity",
        "fail_eval_only_rerun_activity",
    ]


async def test_wait_failure_cleans_only_queued_evals_not_the_main_run():
    calls = []

    async def execute(name, *args, **kwargs):
        calls.append((name, args, kwargs))
        if name == "wait_for_experiment_runs_activity":
            raise RuntimeError("wait exhausted its overall deadline")
        return {"status": "COMPLETED"}

    with (
        patch(
            "tfc.temporal.experiments.workflows.workflow.execute_activity",
            side_effect=execute,
        ),
        patch("tfc.temporal.experiments.workflows.workflow.info") as info,
    ):
        info.return_value.workflow_id = "eval-rerun"
        with pytest.raises(RuntimeError, match="overall deadline"):
            await RerunCellsV2Workflow().run(
                RerunCellsV2WorkflowInput(
                    experiment_id="exp",
                    dataset_id="dataset",
                    eval_template_ids=["metric"],
                    eval_only=True,
                    wait_for_inflight_runs=True,
                )
            )
    assert [x[0] for x in calls] == [
        "wait_for_experiment_runs_activity",
        "fail_eval_only_rerun_activity",
    ]
    options = calls[0][2]
    assert options["retry_policy"].maximum_attempts == 0
    assert options["schedule_to_close_timeout"].total_seconds() <= 7 * 3600


async def test_cancelled_wait_never_marks_running_or_evaluates():
    execute = AsyncMock(return_value={"status": "CANCELLED"})
    with (
        patch("tfc.temporal.experiments.workflows.workflow.execute_activity", execute),
        patch("tfc.temporal.experiments.workflows.workflow.info") as info,
    ):
        info.return_value.workflow_id = "eval-rerun"
        result = await RerunCellsV2Workflow().run(
            RerunCellsV2WorkflowInput(
                experiment_id="exp",
                dataset_id="dataset",
                eval_template_ids=["metric"],
                eval_only=True,
                wait_for_inflight_runs=True,
            )
        )
    assert result.status == "CANCELLED"
    assert execute.await_count == 1
