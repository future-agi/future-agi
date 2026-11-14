"""Grade a finished run's calls again with chosen evals, without rerunning them.

The run-test page's ``run-new-evals`` and the environments API's
``runs/{id}/evaluations/run`` both go through here, so the two can never
disagree about what is refused, what is marked and what is dispatched. Each
caller keeps its own request parsing and words its own answers; this module
raises a typed exception for every refusal.
"""

import structlog
from django.db import transaction
from django.utils import timezone

from simulate.models import (
    CallExecution,
    HostedHarnessJob,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.services.harness_evals import regrade_mapping
from simulate.services.harness_run_evals import EVAL_QUEUED_KEY, stamp_eval_queued
from simulate.services.test_executor import run_new_evals_on_call_executions_task

logger = structlog.get_logger(__name__)


class RegradeStillFinishing(Exception):
    """A harness job that ran one of these runs has not ended yet."""

    def __init__(self):
        super().__init__("This run is still finishing. Try again in a moment.")


class RegradeEvalNotFound(Exception):
    """An eval config asked for is missing or belongs to another run test.

    Nothing was written.
    """


class RegradeRefused(Exception):
    """An eval the platform can't grade was asked for. Nothing was written."""


class RegradeNoCalls(Exception):
    """The chosen runs have no calls to grade. Nothing was written."""


class RegradeNoCompletedCall(Exception):
    """A chosen run has no completed call. Nothing was written.

    The graders skip every call that did not complete, and a run leaves
    EVALUATING only once a completed call of it has been graded, so such a
    run would stay EVALUATING for good.
    """


class RegradeAlreadyRunning(Exception):
    """Another request moved one of the runs to EVALUATING first.

    Nothing this request would have written was kept.
    """

    def __init__(self):
        super().__init__("Grading is already running on this run.")


class RegradeDispatchFailed(Exception):
    """The grading job could not be queued.

    Raised after every call's previous outputs and every run's previous
    status were put back; the markers that say the dispatch failed stay.
    """

    def __init__(self, call_execution_count):
        super().__init__("The grading job could not be queued.")
        self.call_execution_count = call_execution_count


def regrade_still_finishing(execution_ids, *, harness_run):
    """Raise ``RegradeStillFinishing`` while a harness job of these runs is live.

    The execution turns COMPLETED at the last call's ingest, but teardown
    later writes the job's end state onto it, which would overwrite
    EVALUATING mid-grade. Teardown commits the job and the run together, so
    callers ask this before they read the runs' status: that read then sees
    whatever teardown wrote.
    """
    if (
        harness_run
        and HostedHarnessJob.no_workspace_objects.filter(
            test_execution__in=execution_ids
        )
        .exclude(
            state__in=(
                HostedHarnessJob.State.COMPLETED,
                HostedHarnessJob.State.FAILED,
                HostedHarnessJob.State.CANCELED,
            )
        )
        .exists()
    ):
        raise RegradeStillFinishing()


def regrade_run_evals(
    run_test,
    execution_ids,
    previous_test_execution_statuses,
    eval_config_ids,
    *,
    harness_run,
    enable_tool_evaluation=None,
):
    """Mark the runs evaluating and queue one grading job for all their calls.

    ``execution_ids`` are finished runs of ``run_test`` and
    ``previous_test_execution_statuses`` their statuses as the caller read
    them; the caller has checked both. ``eval_config_ids`` are checked here,
    so neither caller can queue the grader with another run test's config.
    Every refusal leaves the database as it was. Returns how many calls
    were queued.
    """
    # The grader loads these ids without a run-test scope, so the scope is
    # enforced here, where both callers share it.
    eval_configs = SimulateEvalConfig.objects.filter(
        id__in=eval_config_ids, run_test=run_test
    ).select_related("eval_template")
    if eval_configs.count() != len(eval_config_ids):
        raise RegradeEvalNotFound()

    # On a harness run, a result column the platform can't grade
    # would be marked pending and never scored, so the whole request
    # is refused before anything is written. The grader works out a
    # harness suite eval's inputs itself, so its config keeps its
    # empty mapping and later harness runs treat it exactly as before.
    if harness_run:
        for eval_config in eval_configs:
            if regrade_mapping(eval_config) is None:
                raise RegradeRefused(
                    f"{eval_config.name or 'This evaluation'} is scored by "
                    "the harness during the call. Only rerunning the call "
                    "refreshes it."
                )

    # Bulk-fetch all matching CallExecution ids in a single query
    # rather than one query per test execution (N+1).
    call_execution_ids = [
        str(ce_id)
        for ce_id in CallExecution.objects.filter(test_execution_id__in=execution_ids)
        .values_list("id", flat=True)
        .iterator(chunk_size=1000)
    ]
    if not call_execution_ids:
        raise RegradeNoCalls()

    # A run returns to COMPLETED only once a completed call of it has
    # been graded, so a run with none would stay EVALUATING for good.
    executions_with_a_completed_call = {
        str(execution_id)
        for execution_id in CallExecution.objects.filter(
            test_execution_id__in=execution_ids,
            status=CallExecution.CallStatus.COMPLETED,
        )
        .order_by()
        .values_list("test_execution_id", flat=True)
        .distinct()
    }
    without_a_completed_call = [
        execution_id
        for execution_id in execution_ids
        if execution_id not in executions_with_a_completed_call
    ]
    if without_a_completed_call:
        if len(execution_ids) == 1:
            raise RegradeNoCompletedCall(
                "Nothing to grade again: no call in this run completed."
            )
        missing = len(without_a_completed_call)
        shown = ", ".join(without_a_completed_call[:3])
        if missing > 3:
            shown += ", ..."
        raise RegradeNoCompletedCall(
            f"{missing} of the {len(execution_ids)} selected runs "
            f"{'has' if missing == 1 else 'have'} no completed call: {shown}."
        )

    # The claim, the column order and the placeholders commit together,
    # so a failure part way leaves the run COMPLETED as it was, and the
    # graders are dispatched only after the commit. The claim is
    # conditional, so two requests racing past the status check above
    # can't both dispatch graders: the second moves nothing and stops.
    with transaction.atomic():
        claimed = TestExecution.objects.filter(
            id__in=execution_ids,
            status=TestExecution.ExecutionStatus.COMPLETED,
        ).update(
            status=TestExecution.ExecutionStatus.EVALUATING,
            picked_up_by_executor=False,
        )
        if claimed != len(execution_ids):
            # Raising out of ``atomic`` rolls the partial claim back.
            raise RegradeAlreadyRunning()

        # Update run_test.enable_tool_evaluation if provided
        if enable_tool_evaluation is not None:
            run_test.enable_tool_evaluation = enable_tool_evaluation
            run_test.save(update_fields=["enable_tool_evaluation"])
            logger.info(
                f"Updated enable_tool_evaluation to {enable_tool_evaluation} for run test {run_test.id}"
            )

        # Update each test execution's column order.
        #
        # Memory-bounded rewrite: stream test executions with
        # ``.iterator(chunk_size=100)`` so the queryset is not fully
        # materialized, and flush ``bulk_update`` in batches of 100 so the
        # buffer itself stays bounded for large runs.
        BATCH_SIZE = 100
        test_execution_count = 0
        updated_test_executions = []
        test_executions_to_update = []

        # Precompute the eval-config columns once; they are identical
        # for every test_execution so recomputing inside the loop is
        # pure overhead.
        eval_column_entries = [
            {
                "column_name": eval_config.name,
                "id": str(eval_config.id),
                "eval_config": eval_config.eval_template.config,
                "visible": True,
                "type": "evaluation",
            }
            for eval_config in eval_configs
        ]

        def _flush_bulk_update(buffer):
            if buffer:
                TestExecution.objects.bulk_update(
                    buffer,
                    [
                        "execution_metadata",
                        "picked_up_by_executor",
                    ],
                )
                buffer.clear()

        for test_execution in TestExecution.objects.filter(
            id__in=execution_ids
        ).iterator(chunk_size=BATCH_SIZE):
            test_execution_count += 1

            # Update column_order to include new eval configs
            if not test_execution.execution_metadata:
                test_execution.execution_metadata = {}
            test_execution.execution_metadata.pop("eval_dispatch_failed", None)

            column_order = test_execution.execution_metadata.get("column_order", [])
            if not column_order:
                column_order = []

            # Normalize legacy camelCase keys in stored column_order entries.
            for _col in column_order:
                if (
                    isinstance(_col, dict)
                    and "columnName" in _col
                    and "column_name" not in _col
                ):
                    _col["column_name"] = _col.pop("columnName")

            # Get existing eval config IDs in column order
            existing_eval_ids = set()
            for col in column_order:
                if col.get("type") == "evaluation":
                    existing_eval_ids.add(col.get("id"))

            # Add new eval configs to column order if they don't exist
            for entry in eval_column_entries:
                if entry["id"] not in existing_eval_ids:
                    column_order.append(dict(entry))
                    logger.info(
                        f"Added eval config {entry['column_name']} to column order "
                        f"for test execution {test_execution.id}"
                    )

            test_execution.execution_metadata["column_order"] = column_order
            test_execution.picked_up_by_executor = False
            test_executions_to_update.append(test_execution)
            updated_test_executions.append(str(test_execution.id))

            if len(test_executions_to_update) >= BATCH_SIZE:
                _flush_bulk_update(test_executions_to_update)

        # Flush remainder
        _flush_bulk_update(test_executions_to_update)

        # Convert eval_config_ids to strings
        eval_config_ids_str = [str(ec_id) for ec_id in eval_config_ids]

        # Bulk update eval_started flag and initialize eval_outputs for all call executions before triggering tasks
        call_executions_to_update = CallExecution.objects.filter(
            id__in=call_execution_ids
        )
        call_executions_list = []
        # What each call held for these evals before the placeholders, so
        # a failed dispatch can put back a score (a harness one included)
        # instead of leaving it pending until a retry succeeds.
        no_output = object()
        previous_outputs = {}
        # And whether its own evaluations had finished: adding an eval to
        # the run later queues only calls that say so.
        previous_eval_completed = {}
        # And its dispatch stamps, so a failed dispatch leaves no stamp that
        # would read a restored call as still scoring.
        previous_eval_queued = {}
        stamped_at = timezone.now()
        for call_execution in call_executions_to_update:
            # Provider-agnostic eval flags live in call_metadata
            call_execution.call_metadata = call_execution.call_metadata or {}
            previous_eval_completed[call_execution.id] = (
                call_execution.call_metadata.get("eval_completed", no_output)
            )
            previous_eval_queued[call_execution.id] = call_execution.call_metadata.get(
                EVAL_QUEUED_KEY, no_output
            )
            call_execution.call_metadata["eval_started"] = True
            call_execution.call_metadata["eval_completed"] = False
            # The placeholders' scoring clock starts at this dispatch, not
            # at the call's own, possibly old, completion.
            stamp_eval_queued(
                call_execution.call_metadata, eval_config_ids_str, now=stamped_at
            )

            # Initialize eval_outputs for the new eval configs
            if not call_execution.eval_outputs:
                call_execution.eval_outputs = {}

            previous_outputs[call_execution.id] = {
                str(eval_config.id): call_execution.eval_outputs.get(
                    str(eval_config.id), no_output
                )
                for eval_config in eval_configs
            }

            # Set placeholder values for each eval config that will be run
            for eval_config in eval_configs:
                call_execution.eval_outputs[str(eval_config.id)] = {"status": "pending"}

            call_executions_list.append(call_execution)

        if call_executions_list:
            CallExecution.objects.bulk_update(
                call_executions_list, ["call_metadata", "eval_outputs"]
            )
            logger.info(
                f"Bulk updated eval_started flag and initialized eval_outputs for "
                f"{len(call_executions_list)} call executions with {len(eval_configs)} eval configs"
            )

    # Trigger the async task to run evaluations. If the async backend is
    # unavailable, put every call and run back and let the caller say so.
    try:
        task = run_new_evals_on_call_executions_task.apply_async(
            args=(call_execution_ids, eval_config_ids_str)
        )
        logger.info(
            f"Triggered new evaluations task {task.id} for {len(call_execution_ids)} call executions "
            f"across {test_execution_count} test executions with {len(eval_config_ids)} eval configs. "
            f"Updated {len(updated_test_executions)} test executions to EVALUATING status. "
            f"Individual tasks will be spawned for parallel processing."
        )
    except Exception as dispatch_error:
        for call_execution in call_executions_list:
            call_execution.call_metadata = call_execution.call_metadata or {}
            call_execution.call_metadata["eval_started"] = False
            call_execution.call_metadata["eval_dispatch_failed"] = str(dispatch_error)
            eval_completed = previous_eval_completed[call_execution.id]
            if eval_completed is no_output:
                call_execution.call_metadata.pop("eval_completed", None)
            else:
                call_execution.call_metadata["eval_completed"] = eval_completed
            eval_queued = previous_eval_queued[call_execution.id]
            if eval_queued is no_output:
                call_execution.call_metadata.pop(EVAL_QUEUED_KEY, None)
            else:
                call_execution.call_metadata[EVAL_QUEUED_KEY] = eval_queued
            for config_id, previous in previous_outputs[call_execution.id].items():
                if previous is no_output:
                    call_execution.eval_outputs.pop(config_id, None)
                else:
                    call_execution.eval_outputs[config_id] = previous
        if call_executions_list:
            CallExecution.objects.bulk_update(
                call_executions_list, ["call_metadata", "eval_outputs"]
            )
        failed_test_executions = list(
            TestExecution.objects.filter(id__in=updated_test_executions)
        )
        for test_execution in failed_test_executions:
            test_execution.status = previous_test_execution_statuses.get(
                str(test_execution.id), test_execution.status
            )
            test_execution.picked_up_by_executor = False
            test_execution.execution_metadata = test_execution.execution_metadata or {}
            test_execution.execution_metadata["eval_dispatch_failed"] = str(
                dispatch_error
            )
        if failed_test_executions:
            TestExecution.objects.bulk_update(
                failed_test_executions,
                ["status", "picked_up_by_executor", "execution_metadata"],
            )
        logger.exception(
            "run_new_evals_dispatch_failed",
            run_test_id=str(run_test.id),
            call_execution_count=len(call_execution_ids),
            eval_config_count=len(eval_config_ids),
            error=str(dispatch_error),
        )
        raise RegradeDispatchFailed(len(call_execution_ids)) from dispatch_error

    return len(call_execution_ids)
