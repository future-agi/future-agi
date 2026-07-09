"""Temporal activities for per-task eval execution.

Each activity is a thin async wrapper over a sync helper that reloads its
target by id and delegates to the eval-task services (reconciler, entry store,
run_entry, reaper) and the progress selectors. The sync helpers hold all the
logic and are unit-tested directly; the wrappers add heartbeating and OTel
context propagation, mirroring ``tfc/temporal/evaluations/activities.py``.
"""

from django.db import close_old_connections
from temporalio import activity

from tfc.telemetry import otel_sync_to_async
from tfc.temporal.common.heartbeat import Heartbeater
from tfc.temporal.eval_tasks.types import (
    ClaimBatchInput,
    ClaimBatchOutput,
    FinalizeInput,
    FinalizeOutput,
    ReapInput,
    ReapOutput,
    ReconcileActivityInput,
    ReconcileActivityOutput,
    RequeueEntriesInput,
    RequeueEntriesOutput,
    RunEntryInput,
    RunEntryOutput,
    SetStatusInput,
    SetStatusOutput,
    TaskStateInput,
    TaskStateOutput,
    WorkflowLabelsInput,
    WorkflowLabelsOutput,
)

# =============================================================================
# Synchronous helpers (the testable core)
# =============================================================================


def _reconcile_sync(task_id: str) -> dict:
    """Make the task's live entries match its desired (sampled rows × evals) set.

    Creates the missing pending entries, re-queues stale/errored ones, and drops
    out-of-scope pending ones — idempotent, so re-running it is a no-op. Returns
    the create/requeue/drop counts. The historical run calls it once up front;
    the continuous loop calls it repeatedly to pull in newly-arrived rows.
    """
    close_old_connections()
    try:
        from tracer.models.eval_task import EvalTask
        from tracer.services.eval_tasks.reconciler import reconcile

        task = EvalTask.objects.get(id=task_id)
        result = reconcile(task)
        return {
            "task_id": str(task_id),
            "created": result.created,
            "requeued": result.requeued,
            "dropped": result.dropped,
        }
    finally:
        close_old_connections()


def _claim_batch_sync(task_id: str, n: int) -> dict:
    """Atomically claim up to ``n`` pending entries and flip them to running.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so concurrent claims never grab
    the same row, then returns the claimed entry ids for the workflow to fan out
    one ``run_eval_entry`` activity per id. Returns ``{"entry_ids": []}`` when no
    pending work remains, which the drain loop treats as "batch is drained".
    """
    close_old_connections()
    try:
        from tracer.models.eval_task import EvalTask
        from tracer.services.eval_tasks.entries import claim_pending_batch

        task = EvalTask.objects.get(id=task_id)
        entries = claim_pending_batch(task, n)
        return {"entry_ids": [str(e.id) for e in entries]}
    finally:
        close_old_connections()


def _run_entry_sync(entry_id: str) -> dict:
    """Run one claimed entry's eval and record its terminal status.

    Delegates to the ``run_entry`` service, which executes the eval, writes the
    result, and stamps the entry ``completed`` / ``errored`` / ``skipped`` plus
    its config hash. Returns ``"deleted"`` if the entry was soft-deleted mid-run
    (a Delete & rerun landing while it ran), so the workflow just moves on.
    """
    close_old_connections()
    try:
        from tracer.models.observation_span import EvalLogger
        from tracer.services.eval_tasks.run_entry import run_entry

        entry = EvalLogger.objects.filter(id=entry_id).first()
        if entry is None:
            return {"entry_id": str(entry_id), "status": "deleted"}
        return {"entry_id": str(entry_id), "status": str(run_entry(entry))}
    finally:
        close_old_connections()


def _fail_entry_sync(entry_id: str) -> dict:
    """Mark an entry errored after its ``run_entry`` activity exhausted its
    retries on an infrastructure fault (worker/DB).

    ``run_entry`` self-converges for eval failures, so an activity reaching this
    path failed at the infra level, not the eval level. Only a still-running
    entry is touched, so a late failure can't clobber a result that completed (or
    a Delete & rerun that soft-deleted it) in the meantime.
    """
    close_old_connections()
    try:
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.observation_span import EvalEntryStatus, EvalLogger
        from tracer.services.eval_tasks.config_hash import resolved_config_hash
        from tracer.services.eval_tasks.entries import mark_terminal

        entry = EvalLogger.objects.filter(
            id=entry_id, status=EvalEntryStatus.RUNNING
        ).first()
        if entry is None:
            return {"entry_id": str(entry_id), "status": "noop"}
        config = CustomEvalConfig.objects.get(id=entry.custom_eval_config_id)
        mark_terminal(
            entry,
            EvalEntryStatus.ERRORED,
            config_hash=resolved_config_hash(config),
            error=True,
            error_message="run_entry activity failed after retries",
        )
        return {"entry_id": str(entry_id), "status": str(EvalEntryStatus.ERRORED)}
    finally:
        close_old_connections()


def _reap_sync(task_id: str, older_than_seconds: int, max_attempts: int) -> dict:
    """Reclaim entries stuck running past ``older_than_seconds`` (e.g. a worker
    crashed mid-eval).

    Stale running entries go back to pending with ``attempts`` incremented;
    those already at ``max_attempts`` are marked errored so one poison row can't
    loop forever. Returns ``{"requeued", "failed"}``. Called once at workflow
    start to clear leftovers from a previous, crashed execution.
    """
    close_old_connections()
    try:
        from tracer.models.eval_task import EvalTask
        from tracer.services.eval_tasks.reaper import reap_stale_running

        task = EvalTask.objects.get(id=task_id)
        requeued, failed = reap_stale_running(
            task, older_than_seconds=older_than_seconds, max_attempts=max_attempts
        )
        return {"requeued": requeued, "failed": failed}
    finally:
        close_old_connections()


def _get_task_state_sync(task_id: str) -> dict:
    """Read the control state the drain loop checks between batches.

    Returns ``active`` (False once the task is paused/deleted/failed — the
    workflow then exits cleanly) and ``has_undrained_work`` (any entry still
    pending/running). Keeping the status-enum comparison here means the workflow
    stays free of Django imports.
    """
    close_old_connections()
    try:
        from tracer.models.eval_task import EvalTask, EvalTaskStatus
        from tracer.selectors.eval_tasks.progress import has_undrained_work

        task = EvalTask.objects.get(id=task_id)
        inactive = {
            EvalTaskStatus.PAUSED,
            EvalTaskStatus.DELETED,
            EvalTaskStatus.FAILED,
        }
        return {
            "task_id": str(task_id),
            "status": task.status or "",
            "active": task.status not in inactive,
            "has_undrained_work": has_undrained_work(task),
        }
    finally:
        close_old_connections()


def _get_workflow_labels_sync(task_id: str) -> dict:
    """Fetch the task's Search-Attribute values (org/project/run_type) and memo
    context (names + a config summary) for the workflow to upsert.

    The workflow calls this at the start of every run, so the labels are
    re-applied after each continue-as-new (the values are run-scoped).
    """
    close_old_connections()
    try:
        from tracer.models.eval_task import EvalTask

        task = EvalTask.objects.select_related("project", "project__organization").get(
            id=task_id
        )
        project = task.project
        org = getattr(project, "organization", None)
        eval_count = task.evals.count()
        config_summary = (
            f"row_type={task.row_type}, sampling={task.sampling_rate}, "
            f"limit={task.spans_limit}, evals={eval_count}"
        )
        return {
            "org_id": str(org.id) if org else "",
            "project_id": str(project.id) if project else "",
            "run_type": task.run_type or "",
            "task_name": task.name or "",
            "project_name": (project.name or "") if project else "",
            "org_name": (org.name or "") if org else "",
            "config_summary": config_summary,
        }
    finally:
        close_old_connections()


def _requeue_entries_sync(task_id: str, entry_ids: list[str]) -> dict:
    """Reset claimed-but-unrun entries (skipped when a pause landed mid-drain)
    from ``running`` back to ``pending`` so a resume re-drains them. Filtered on
    ``running`` so an entry that actually completed in the meantime is untouched.
    """
    close_old_connections()
    try:
        from tracer.models.observation_span import EvalEntryStatus, EvalLogger

        requeued = EvalLogger.objects.filter(
            id__in=entry_ids,
            eval_task_id=task_id,
            status=EvalEntryStatus.RUNNING,
        ).update(status=EvalEntryStatus.PENDING)
        return {"requeued": int(requeued)}
    finally:
        close_old_connections()


def _set_task_status_sync(
    task_id: str, status: str, expected_status: str | None = None
) -> dict:
    """Set the task's DB ``status`` (the UI's source of truth), optionally only
    when it is currently ``expected_status``.

    The workflow advertises status via a Temporal Search Attribute, but the UI
    reads ``EvalTask.status`` from the DB — so the row must be written here too.
    ``expected_status`` makes the change a guarded, atomic transition: the
    filtered ``update`` touches the row only if it still holds that status, so a
    concurrent pause/delete is never clobbered. The lifecycle timestamp that goes
    with a status is stamped alongside it (``running`` → ``start_time``, terminal
    ``completed``/``failed`` → ``end_time``).
    """
    close_old_connections()
    try:
        from django.utils import timezone

        from tracer.models.eval_task import EvalTask, EvalTaskStatus

        fields: dict = {"status": status}
        if status == EvalTaskStatus.RUNNING:
            fields["start_time"] = timezone.now()
        elif status in (EvalTaskStatus.COMPLETED, EvalTaskStatus.FAILED):
            fields["end_time"] = timezone.now()

        qs = EvalTask.objects.filter(id=task_id)
        if expected_status is not None:
            qs = qs.filter(status=expected_status)
        changed = qs.update(**fields)

        task = EvalTask.objects.get(id=task_id)
        return {
            "task_id": str(task_id),
            "changed": bool(changed),
            "status": task.status or "",
        }
    finally:
        close_old_connections()


def _finalize_task_sync(task_id: str) -> dict:
    """Mark the task completed once the drain is fully done.

    Sets ``status=completed`` + ``end_time`` only when no entry is still
    pending/running; otherwise it's a no-op returning ``finalized=False`` (the
    loop keeps draining). Only the historical workflow calls this at end-of-drain
    — continuous tasks never finalize.
    """
    close_old_connections()
    try:
        from django.utils import timezone

        from tracer.models.eval_task import EvalTask, EvalTaskStatus
        from tracer.selectors.eval_tasks.progress import has_undrained_work

        task = EvalTask.objects.get(id=task_id)
        if has_undrained_work(task):
            return {
                "task_id": str(task_id),
                "finalized": False,
                "status": task.status or "",
            }
        task.status = EvalTaskStatus.COMPLETED
        task.end_time = timezone.now()
        task.save(update_fields=["status", "end_time"])
        return {
            "task_id": str(task_id),
            "finalized": True,
            "status": str(EvalTaskStatus.COMPLETED),
        }
    finally:
        close_old_connections()


# =============================================================================
# Activities
# =============================================================================


@activity.defn
async def reconcile_eval_task_activity(
    input: ReconcileActivityInput,
) -> ReconcileActivityOutput:
    async with Heartbeater():
        result = await otel_sync_to_async(_reconcile_sync, thread_sensitive=False)(
            input.task_id
        )
    return ReconcileActivityOutput(
        task_id=result["task_id"],
        created=result["created"],
        requeued=result["requeued"],
        dropped=result["dropped"],
    )


@activity.defn
async def claim_eval_batch_activity(input: ClaimBatchInput) -> ClaimBatchOutput:
    async with Heartbeater():
        result = await otel_sync_to_async(_claim_batch_sync, thread_sensitive=False)(
            input.task_id, input.n
        )
    return ClaimBatchOutput(entry_ids=result["entry_ids"])


@activity.defn
async def run_eval_entry_activity(input: RunEntryInput) -> RunEntryOutput:
    async with Heartbeater():
        result = await otel_sync_to_async(_run_entry_sync, thread_sensitive=False)(
            input.entry_id
        )
    return RunEntryOutput(entry_id=result["entry_id"], status=result["status"])


@activity.defn
async def fail_eval_entry_activity(input: RunEntryInput) -> RunEntryOutput:
    async with Heartbeater():
        result = await otel_sync_to_async(_fail_entry_sync, thread_sensitive=False)(
            input.entry_id
        )
    return RunEntryOutput(entry_id=result["entry_id"], status=result["status"])


@activity.defn
async def reap_stale_running_activity(input: ReapInput) -> ReapOutput:
    async with Heartbeater():
        result = await otel_sync_to_async(_reap_sync, thread_sensitive=False)(
            input.task_id, input.older_than_seconds, input.max_attempts
        )
    return ReapOutput(requeued=result["requeued"], failed=result["failed"])


@activity.defn
async def get_eval_task_state_activity(input: TaskStateInput) -> TaskStateOutput:
    async with Heartbeater():
        result = await otel_sync_to_async(_get_task_state_sync, thread_sensitive=False)(
            input.task_id
        )
    return TaskStateOutput(
        task_id=result["task_id"],
        status=result["status"],
        active=result["active"],
        has_undrained_work=result["has_undrained_work"],
    )


@activity.defn
async def get_workflow_labels_activity(
    input: WorkflowLabelsInput,
) -> WorkflowLabelsOutput:
    async with Heartbeater():
        result = await otel_sync_to_async(
            _get_workflow_labels_sync, thread_sensitive=False
        )(input.task_id)
    return WorkflowLabelsOutput(**result)


@activity.defn
async def requeue_eval_entries_activity(
    input: RequeueEntriesInput,
) -> RequeueEntriesOutput:
    async with Heartbeater():
        result = await otel_sync_to_async(
            _requeue_entries_sync, thread_sensitive=False
        )(input.task_id, input.entry_ids)
    return RequeueEntriesOutput(requeued=result["requeued"])


@activity.defn
async def set_eval_task_status_activity(input: SetStatusInput) -> SetStatusOutput:
    async with Heartbeater():
        result = await otel_sync_to_async(
            _set_task_status_sync, thread_sensitive=False
        )(input.task_id, input.status, input.expected_status)
    return SetStatusOutput(
        task_id=result["task_id"],
        changed=result["changed"],
        status=result["status"],
    )


@activity.defn
async def finalize_eval_task_activity(input: FinalizeInput) -> FinalizeOutput:
    async with Heartbeater():
        result = await otel_sync_to_async(_finalize_task_sync, thread_sensitive=False)(
            input.task_id
        )
    return FinalizeOutput(
        task_id=result["task_id"],
        finalized=result["finalized"],
        status=result["status"],
    )


__all__ = [
    "reconcile_eval_task_activity",
    "claim_eval_batch_activity",
    "run_eval_entry_activity",
    "fail_eval_entry_activity",
    "reap_stale_running_activity",
    "get_eval_task_state_activity",
    "get_workflow_labels_activity",
    "requeue_eval_entries_activity",
    "set_eval_task_status_activity",
    "finalize_eval_task_activity",
]
