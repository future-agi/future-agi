"""Grade a finished run's calls again with chosen evals, without rerunning them.

The run-test page's ``run-new-evals`` and the environments API's
``runs/{id}/evaluations/run`` both go through here, so the two can never
disagree about what is refused, what is marked and what is dispatched. Each
caller keeps its own request parsing and words its own answers; this module
raises a typed exception for every refusal.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from django.db import transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from simulate.models import (
    CallExecution,
    CallExecutionSnapshot,
    HostedHarnessJob,
    RunTest,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.services.harness_evals import regrade_mapping
from simulate.services.harness_run_evals import EVAL_QUEUED_KEY, stamp_eval_queued
from simulate.services.kept_harness_entry import pending_placeholder
from simulate.services.scoring_status import CALL_SCORING_OPEN_Q, pending_eval_ids
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


class RegradeNothingToGrade(Exception):
    """eval_config_ids is empty. Nothing was written."""


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

    Raised after every call's previous outputs, and the previous status of
    every run still evaluating, were put back; the markers that say the
    dispatch failed stay.
    """

    def __init__(self, call_execution_count):
        super().__init__("The grading job could not be queued.")
        self.call_execution_count = call_execution_count


class HarnessRegradeStillFinishing(Exception):
    """A harness job of some chosen runs has not ended yet.

    ``execution_ids`` are those runs. Nothing was written.
    """

    def __init__(self, execution_ids: Sequence[str]) -> None:
        super().__init__(list(execution_ids))
        self.execution_ids = list(execution_ids)


class HarnessRegradeRunsNotCompleted(Exception):
    """Chosen runs are not COMPLETED. ``statuses`` maps each to its status.

    A run that no longer exists reads ``"deleted"``. Nothing was written.
    """

    def __init__(self, statuses: dict[str, str]) -> None:
        super().__init__(dict(statuses))
        self.statuses = dict(statuses)


class HarnessRegradeNoCompletedCall(Exception):
    """Chosen runs hold no completed call to grade. Nothing was written.

    ``execution_ids`` are those runs.
    """

    def __init__(self, execution_ids: Sequence[str]) -> None:
        super().__init__(list(execution_ids))
        self.execution_ids = list(execution_ids)


class HarnessRegradeNothingToGrade(Exception):
    """No eval of the run test is one the platform can grade. Nothing was written."""


class HarnessRegradeNoRuns(Exception):
    """The request names no run at all. Nothing was written."""


@dataclass(frozen=True)
class HarnessRegradePlan:
    """What a harness run's eval-only re-run will grade, decided before any write.

    ``execution_ids`` are the runs to grade, ``previous_statuses`` their
    statuses as read, ``graded`` and ``left_out`` each one's completed calls
    and the calls it leaves out. ``skipped`` maps each run a ``select_all``
    request passed over to the reason.
    """

    execution_ids: list[str]
    previous_statuses: dict[str, str]
    graded: dict[str, list[str]]
    left_out: dict[str, list[str]]
    skipped: dict[str, str]
    eval_config_ids: list[str]


def _live_jobs(execution_ids: Sequence[str]) -> QuerySet[HostedHarnessJob]:
    """The harness jobs of these runs that have not ended."""
    return HostedHarnessJob.no_workspace_objects.filter(
        test_execution__in=execution_ids
    ).exclude(
        state__in=(
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        )
    )


def regrade_still_finishing(execution_ids, *, harness_run):
    """Raise ``RegradeStillFinishing`` while a harness job of these runs is live.

    The execution turns COMPLETED at the last call's ingest, but teardown
    later writes the job's end state onto it, which would overwrite
    EVALUATING mid-grade. Teardown commits the job and the run together, so
    callers ask this before they read the runs' status: that read then sees
    whatever teardown wrote.
    """
    if harness_run and _live_jobs(execution_ids).exists():
        raise RegradeStillFinishing()


def plan_harness_regrade(
    run_test: RunTest,
    execution_ids: Sequence[str],
    calls: QuerySet[CallExecution],
    *,
    select_all: bool,
) -> HarnessRegradePlan:
    """Decide what an eval-only re-run of a harness run grades, writing nothing.

    ``calls`` are the selected calls of ``execution_ids``. Picked by id, a run
    that is still finishing, not completed, or without a completed call
    refuses the whole request. With ``select_all`` such runs are passed over
    and named in ``skipped``, so one old run cannot block the rest; the
    request is refused only when nothing is left to grade.
    """
    execution_ids = [str(execution_id) for execution_id in execution_ids]
    if not execution_ids:
        raise HarnessRegradeNoRuns()
    finishing = {
        str(execution_id)
        for execution_id in _live_jobs(execution_ids).values_list(
            "test_execution_id", flat=True
        )
    }
    finishing_ids = [i for i in execution_ids if i in finishing]
    if finishing_ids and not select_all:
        raise HarnessRegradeStillFinishing(finishing_ids)

    # Read after the job check: teardown commits the job's end and the run's
    # status together, so this read sees whatever teardown wrote.
    statuses = {
        str(execution_id): execution_status
        for execution_id, execution_status in TestExecution.objects.filter(
            id__in=execution_ids
        ).values_list("id", "status")
    }
    not_completed = {
        execution_id: statuses.get(execution_id, "deleted")
        for execution_id in execution_ids
        if statuses.get(execution_id) != TestExecution.ExecutionStatus.COMPLETED
    }
    skipped: dict[str, str] = {}
    if select_all:
        for execution_id in execution_ids:
            if execution_id in finishing:
                skipped[execution_id] = "The run's job is still finishing."
            elif execution_id in not_completed:
                skipped[execution_id] = (
                    f"The run is {not_completed[execution_id]}, not completed."
                )
    elif not_completed:
        raise HarnessRegradeRunsNotCompleted(not_completed)
    runs = [i for i in execution_ids if i not in skipped]

    graded: dict[str, list[str]] = {i: [] for i in runs}
    left_out: dict[str, list[str]] = {i: [] for i in runs}
    for call_id, execution_id, call_status in calls.values_list(
        "id", "test_execution_id", "status"
    ):
        by_execution = (
            graded if call_status == CallExecution.CallStatus.COMPLETED else left_out
        )
        if str(execution_id) in by_execution:
            by_execution[str(execution_id)].append(str(call_id))
    without_a_completed_call = [i for i in runs if not graded[i]]
    if without_a_completed_call and not select_all:
        raise HarnessRegradeNoCompletedCall(without_a_completed_call)
    for execution_id in without_a_completed_call:
        skipped[execution_id] = "No call in this run completed."
    # A set: a list here is searched once per run, which `select_all` over
    # thousands of runs would feel.
    left_without = set(without_a_completed_call)
    runs = [i for i in runs if i not in left_without]

    if not runs:
        # select_all, and nothing is left to grade.
        if without_a_completed_call:
            raise HarnessRegradeNoCompletedCall(without_a_completed_call)
        if finishing_ids:
            raise HarnessRegradeStillFinishing(finishing_ids)
        raise HarnessRegradeRunsNotCompleted(not_completed)

    eval_config_ids = [
        str(eval_config.id)
        for eval_config in SimulateEvalConfig.objects.filter(
            run_test=run_test, deleted=False
        ).select_related("eval_template")
        if regrade_mapping(eval_config) is not None
    ]
    if not eval_config_ids:
        raise HarnessRegradeNothingToGrade()
    return HarnessRegradePlan(
        execution_ids=runs,
        previous_statuses={i: statuses[i] for i in runs},
        graded={i: graded[i] for i in runs},
        left_out={i: left_out[i] for i in runs},
        skipped=skipped,
        eval_config_ids=eval_config_ids,
    )


# Stands for "the call did not have this key" in what a failed dispatch
# restores a call from. Never stored.
_ABSENT = object()


@dataclass
class _BeforeRegrade:
    """What one call held before this request's placeholders were written."""

    outputs: dict[str, object]
    # The placeholder written into each cell, to tell it from a later write.
    placeholders: dict[str, object]
    # Each config's stamp, and the whole stamps entry.
    stamps: dict[str, object]
    stamp_key: object
    started: object
    completed: object


def _no_completed_call_message(
    execution_ids: Sequence[str], without_a_completed_call: Sequence[str]
) -> str:
    if len(execution_ids) <= 1:
        return "Nothing to grade again: no call in this run completed."
    missing = len(without_a_completed_call)
    shown = ", ".join(without_a_completed_call[:3])
    if missing > 3:
        shown += ", ..."
    return (
        f"{missing} of the {len(execution_ids)} selected runs "
        f"{'has' if missing == 1 else 'have'} no completed call: {shown}."
    )


def _lock_run_test(run_test: RunTest) -> None:
    """Hold the run test's row to the end of the transaction.

    Add-eval holds it too, before it touches runs or calls, so every path
    that locks both runs and calls reaches them behind this one lock.
    """
    list(RunTest.objects.select_for_update(of=("self",)).filter(pk=run_test.pk))


def _put_back_after_failed_dispatch(
    run_test: RunTest,
    execution_ids: Sequence[str],
    before: dict[object, _BeforeRegrade],
    snapshot_ids: Sequence[object],
    previous_statuses: dict[str, str],
    error: str,
) -> None:
    """Undo a re-grade whose grading job could not be queued.

    Its own transaction, locking in the same order as the claim's. Rows are
    read again under the lock, because other writers have had the calls
    since the claim committed: a cell goes back only while it still holds
    this request's placeholder, each stamp goes back per config, and the
    call's flags go back only when no config is left pending on it.

    The locks are taken run test, then runs, then calls. A grader finishing
    a call of one of these runs takes them the other way round (the call,
    then the run), and the claim has already committed the run as
    evaluating, so the grader's settle does not skip it. If the two meet,
    Postgres aborts one of them: a known limit of the put-back, rare because
    it needs a grader of an older job to finish while the dispatch fails.
    """
    with transaction.atomic():
        _lock_run_test(run_test)
        runs = list(
            TestExecution.objects.select_for_update(of=("self",))
            .filter(id__in=execution_ids)
            .order_by("id")
        )
        calls = list(
            CallExecution.objects.select_for_update(of=("self",))
            .filter(id__in=list(before))
            .order_by("id")
        )
        # Per run, so each run's guard below carries only its own ids: one
        # shared list would make the put-back cost runs times calls while
        # every lock is held.
        restored_call_ids = defaultdict(list)
        for call_execution in calls:
            prior = before[call_execution.id]
            metadata = (
                dict(call_execution.call_metadata)
                if isinstance(call_execution.call_metadata, dict)
                else {}
            )
            outputs = (
                dict(call_execution.eval_outputs)
                if isinstance(call_execution.eval_outputs, dict)
                else {}
            )
            stamps = metadata.get(EVAL_QUEUED_KEY)
            stamps = dict(stamps) if isinstance(stamps, dict) else {}
            for config_id, placeholder in prior.placeholders.items():
                if outputs.get(config_id) == placeholder:
                    if prior.outputs[config_id] is _ABSENT:
                        outputs.pop(config_id, None)
                    else:
                        outputs[config_id] = prior.outputs[config_id]
                if prior.stamps[config_id] is _ABSENT:
                    stamps.pop(config_id, None)
                else:
                    stamps[config_id] = prior.stamps[config_id]
            if stamps or prior.stamp_key is not _ABSENT:
                metadata[EVAL_QUEUED_KEY] = stamps
            else:
                metadata.pop(EVAL_QUEUED_KEY, None)
            if not pending_eval_ids(metadata, outputs):
                for key, value in (
                    ("eval_started", prior.started),
                    ("eval_completed", prior.completed),
                ):
                    if value is _ABSENT:
                        metadata.pop(key, None)
                    else:
                        metadata[key] = value
                restored_call_ids[str(call_execution.test_execution_id)].append(
                    call_execution.id
                )
            metadata["eval_dispatch_failed"] = error
            call_execution.call_metadata = metadata
            call_execution.eval_outputs = outputs
        if calls:
            CallExecution.objects.bulk_update(calls, ["call_metadata", "eval_outputs"])
        if snapshot_ids:
            # The re-grade never happened, so nothing is left to compare to.
            CallExecutionSnapshot.objects.filter(id__in=list(snapshot_ids)).delete()
        for test_execution in runs:
            test_execution.execution_metadata = test_execution.execution_metadata or {}
            test_execution.execution_metadata["eval_dispatch_failed"] = error
        if runs:
            TestExecution.objects.bulk_update(runs, ["execution_metadata"])
        # A run that moved on since the claim, such as an SDK batch turning
        # it RUNNING, is no longer this request's to put back. Nor is one
        # that another writer's grading still holds open: it stays
        # evaluating, for that grading's settle to complete. A call of this
        # request that went back to what it held is not counted: it was as
        # open before as it is now, and the run was completed with it.
        for execution_id in execution_ids:
            previous_status = previous_statuses.get(execution_id)
            TestExecution.objects.filter(
                id=execution_id, status=TestExecution.ExecutionStatus.EVALUATING
            ).update(picked_up_by_executor=False)
            if previous_status is not None:
                still_scoring = CallExecution.objects.filter(
                    CALL_SCORING_OPEN_Q,
                    test_execution_id=OuterRef("pk"),
                    deleted=False,
                    status=CallExecution.CallStatus.COMPLETED,
                ).exclude(id__in=restored_call_ids.get(str(execution_id), []))
                TestExecution.objects.filter(
                    ~Exists(still_scoring),
                    id=execution_id,
                    status=TestExecution.ExecutionStatus.EVALUATING,
                ).update(status=previous_status)


def regrade_run_evals(
    run_test,
    execution_ids,
    previous_test_execution_statuses,
    eval_config_ids,
    *,
    harness_run,
    enable_tool_evaluation=None,
    call_execution_ids=None,
    snapshot_eval_outputs=False,
    skip_runs_without_a_completed_call=False,
):
    """Mark the runs evaluating and queue one grading job for all their calls.

    ``execution_ids`` are finished runs of ``run_test`` and
    ``previous_test_execution_statuses`` their statuses as the caller read
    them; the caller has checked both. ``eval_config_ids`` are checked here,
    so neither caller can queue the grader with another run test's config.
    Every refusal leaves the database as it was. Returns the ids of the calls
    it stamped and queued, which can be fewer than were asked for: a call
    that moved on or was deleted before the lock is not among them.

    ``call_execution_ids``, when given, limits the grading to those of the
    listed calls that belong to these runs and completed. With
    ``snapshot_eval_outputs``, each graded call's earlier results are saved
    as an eval-only snapshot in the claim's transaction, so a refused or
    lost claim leaves none behind. A failed dispatch deletes them too: the
    outputs go back, so no re-grade happened to compare against.

    A run whose completed calls all moved on before the lock refuses the
    request, unless ``skip_runs_without_a_completed_call`` is set: then that
    run is left as it was (the caller can tell it by its calls being absent
    from the result), and the request is refused only when no run is left.
    """
    # Passing a run over is judged by the calls kept for it, so without call
    # ids the runs to pass over are never worked out, and the calls of a run
    # that is not claimed would still be stamped and dispatched.
    if skip_runs_without_a_completed_call and call_execution_ids is None:
        raise TypeError("skip_runs_without_a_completed_call needs call_execution_ids")

    # With no eval, the claim below would move the runs to EVALUATING with
    # nothing queued that could ever move them back.
    if not eval_config_ids:
        raise RegradeNothingToGrade()

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

    completed_only = call_execution_ids is not None
    if call_execution_ids is None:
        # Bulk-fetch all matching CallExecution ids in a single query
        # rather than one query per test execution (N+1).
        call_execution_ids = [
            str(ce_id)
            for ce_id in CallExecution.objects.filter(
                test_execution_id__in=execution_ids
            )
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
    else:
        # The graders skip a call that did not complete, so a placeholder on
        # one would never be scored. A run is judged by the calls kept here
        # alone: its other calls are not graded, so they never settle it.
        selected = list(
            CallExecution.objects.filter(
                id__in=call_execution_ids,
                test_execution_id__in=execution_ids,
                status=CallExecution.CallStatus.COMPLETED,
            ).values_list("id", "test_execution_id")
        )
        call_execution_ids = [str(call_id) for call_id, _ in selected]
        executions_with_a_completed_call = {
            str(execution_id) for _, execution_id in selected
        }
    without_a_completed_call = [
        execution_id
        for execution_id in execution_ids
        if execution_id not in executions_with_a_completed_call
    ]
    if (
        skip_runs_without_a_completed_call
        and without_a_completed_call
        and call_execution_ids
    ):
        execution_ids = [
            i for i in execution_ids if i in executions_with_a_completed_call
        ]
        without_a_completed_call = []
    if without_a_completed_call or not call_execution_ids:
        raise RegradeNoCompletedCall(
            _no_completed_call_message(execution_ids, without_a_completed_call)
        )

    # The claim, the column order and the placeholders commit together,
    # so a failure part way leaves the run COMPLETED as it was, and the
    # graders are dispatched only after the commit. The claim is
    # conditional, so two requests racing past the status check above
    # can't both dispatch graders: the second moves nothing and stops.
    #
    # Locks are taken run test, then runs, then calls (in id order): the
    # order add-eval takes, so the two can't deadlock on each other.
    eval_config_ids_str = [str(ec_id) for ec_id in eval_config_ids]
    with transaction.atomic():
        _lock_run_test(run_test)

        # Update run_test.enable_tool_evaluation if provided
        if enable_tool_evaluation is not None:
            run_test.enable_tool_evaluation = enable_tool_evaluation
            run_test.save(update_fields=["enable_tool_evaluation"])
            logger.info(
                f"Updated enable_tool_evaluation to {enable_tool_evaluation} for run test {run_test.id}"
            )

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

        # The calls were read before this transaction, so one may have moved
        # on since: a placeholder on a call the graders skip would never be
        # scored. Its status is read again under the lock.
        call_executions_to_update = [
            call_execution
            for call_execution in CallExecution.objects.select_for_update(of=("self",))
            .filter(id__in=call_execution_ids)
            .order_by("id")
            if not completed_only
            or call_execution.status == CallExecution.CallStatus.COMPLETED
        ]
        call_execution_ids = [str(c.id) for c in call_executions_to_update]
        if completed_only:
            still_graded = {
                str(call_execution.test_execution_id)
                for call_execution in call_executions_to_update
            }
            lost = [i for i in execution_ids if i not in still_graded]
            if lost and (
                not skip_runs_without_a_completed_call
                or len(lost) == len(execution_ids)
            ):
                # Raising out of ``atomic`` rolls the claim back.
                raise RegradeNoCompletedCall(
                    _no_completed_call_message(execution_ids, lost)
                )
            if lost:
                # Back to the state they were claimed from, as the put-back
                # of a failed dispatch leaves a run: the claim's row lock is
                # still held, so nothing else has touched them.
                TestExecution.objects.filter(id__in=lost).update(
                    status=TestExecution.ExecutionStatus.COMPLETED,
                    picked_up_by_executor=False,
                )
                lost_runs = set(lost)
                execution_ids = [i for i in execution_ids if i not in lost_runs]

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

        snapshot_ids = []
        if snapshot_eval_outputs:
            # Copied before any placeholder is written: a placeholder holds
            # the harness entry itself, so the snapshot must not share it.
            snapshots = CallExecutionSnapshot.objects.bulk_create(
                [
                    CallExecutionSnapshot(
                        call_execution=call_execution,
                        rerun_type=CallExecutionSnapshot.RerunType.EVAL_ONLY,
                        eval_outputs=copy.deepcopy(call_execution.eval_outputs),
                        provider_call_data=call_execution.provider_call_data,
                        overall_score=call_execution.overall_score,
                        tool_outputs=copy.deepcopy(call_execution.tool_outputs),
                    )
                    for call_execution in call_executions_to_update
                ],
                batch_size=1000,
            )
            snapshot_ids = [snapshot.id for snapshot in snapshots]
        call_executions_list = []
        # What each call held before the placeholders, so a failed dispatch
        # can put back a score (a harness one included) instead of leaving
        # it pending until a retry succeeds.
        before = {}
        stamped_at = timezone.now()
        for call_execution in call_executions_to_update:
            # Provider-agnostic eval flags live in call_metadata
            call_execution.call_metadata = call_execution.call_metadata or {}
            metadata = call_execution.call_metadata
            if not call_execution.eval_outputs:
                call_execution.eval_outputs = {}
            old_stamps = metadata.get(EVAL_QUEUED_KEY)
            old_stamps = old_stamps if isinstance(old_stamps, dict) else {}
            record = _BeforeRegrade(
                outputs={
                    config_id: call_execution.eval_outputs.get(config_id, _ABSENT)
                    for config_id in eval_config_ids_str
                },
                placeholders={},
                stamps={
                    config_id: old_stamps.get(config_id, _ABSENT)
                    for config_id in eval_config_ids_str
                },
                stamp_key=metadata.get(EVAL_QUEUED_KEY, _ABSENT),
                started=metadata.get("eval_started", _ABSENT),
                completed=metadata.get("eval_completed", _ABSENT),
            )
            metadata["eval_started"] = True
            metadata["eval_completed"] = False
            # The placeholders' scoring clock starts at this dispatch, not
            # at the call's own, possibly old, completion.
            stamp_eval_queued(metadata, eval_config_ids_str, now=stamped_at)

            # Set placeholder values for each eval config that will be run
            for config_id in eval_config_ids_str:
                placeholder = pending_placeholder(
                    call_execution.eval_outputs.get(config_id)
                )
                call_execution.eval_outputs[config_id] = placeholder
                record.placeholders[config_id] = copy.deepcopy(placeholder)
            before[call_execution.id] = record
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
        _put_back_after_failed_dispatch(
            run_test,
            updated_test_executions,
            before,
            snapshot_ids,
            previous_test_execution_statuses,
            str(dispatch_error),
        )
        logger.exception(
            "run_new_evals_dispatch_failed",
            run_test_id=str(run_test.id),
            call_execution_count=len(call_execution_ids),
            eval_config_count=len(eval_config_ids),
            error=str(dispatch_error),
        )
        raise RegradeDispatchFailed(len(call_execution_ids)) from dispatch_error

    return call_execution_ids
