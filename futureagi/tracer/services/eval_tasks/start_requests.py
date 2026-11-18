"""Dispatch a recorded create, never infer a request from Pending status.

A lease elects the sender; the task's one create request admits one run ID.
Only a negative describe within retention permits a retry. A known execution
is watched until its worker records progress, not submitted again. An outage
past the deadline sacrifices automatic startup for certainty: keep describing
without offering a replacing Resume while the run might be alive.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import structlog
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Now
from temporalio.exceptions import WorkflowAlreadyStartedError

from tfc.temporal.eval_tasks import client
from tracer.models.eval_task import (
    EvalTask,
    EvalTaskLogger,
    EvalTaskStartRequest,
    EvalTaskStatus,
    RunType,
)
from tracer.models.instant_eval import InstantEvalRun
from tracer.models.observation_span import EvalLogger
from tracer.models.project import Project
from tracer.services.eval_tasks.configuration import continuous_coverage_floor

logger = structlog.get_logger(__name__)
State = EvalTaskStartRequest.State
LEASE = timedelta(minutes=2)
UNCONFIRMED_AFTER = timedelta(minutes=30)
# Cap negative evidence at one day from the earliest possible send. This must
# not exceed namespace retention: beyond it NOT_FOUND cannot distinguish a
# missed start from an accepted execution whose history expired.
HISTORY_WINDOW = timedelta(days=1)
MAX_ATTEMPTS = 2
START_FAILURE_MESSAGE = (
    "The run couldn't start after an automatic retry. Your task is saved."
)
_MESSAGES = {
    "execution_completed": (
        "The workflow completed. Contact support if the task status doesn't update."
    ),
    "execution_closed_without_progress": (
        "The workflow ended before evaluation began. Your task is saved."
    ),
    "unconfirmed_beyond_history": (
        "No workflow history is available to confirm what happened. We did not "
        "start it again. Contact support to review this task."
    ),
    "start_unconfirmed": (
        "We couldn't confirm this task started. We'll keep checking; "
        "contact support if it doesn't start soon."
    ),
    "awaiting_worker": (
        "The workflow was accepted but hasn't begun evaluation. Contact support."
    ),
    "worker_admitted": (
        "The workflow started but hasn't recorded evaluation progress. Contact support."
    ),
}
_WATCHING = {"awaiting_worker", "worker_admitted", "unconfirmed_beyond_history"}


def _requests():
    return EvalTaskStartRequest.objects.using("default")


@dataclass(frozen=True)
class Refusal:
    code: str
    standard: str
    instant: str
    # Only recorded start outcomes belong in Logs, not generic status checks.
    log_message: str | None = None


def _common(code: str, message: str, *, log: bool = False) -> Refusal:
    return Refusal(code, message, message, message if log else None)


def _has_worker_artifacts(task):
    # A named display row can be created by a summary GET. Only the worker
    # advances a cursor or writes evaluation entries/task loggers. A fresh
    # Re-run can retire entries, so include soft-deleted work.
    return (
        task.continuous_cursor is not None
        or EvalLogger.all_objects.using("default")
        .filter(eval_task_id=str(task.pk))
        .exists()
        or EvalTaskLogger.all_objects.using("default")
        .filter(eval_task_id=task.pk)
        .exists()
        or InstantEvalRun.objects.using("default")
        .filter(eval_task_id=task.pk)
        .exclude(hash_cursor="")
        .exists()
    )


def _current_request(task):
    return _requests().filter(pk=task.pk, superseded_at__isnull=True)


def start_refusal(task, *, delivery=False) -> Refusal | None:
    """The extra guard on dev's status/action rules; never a grant to start.

    No intent means no new proof-of-execution requirement. With an intent,
    neither editable task fields nor Temporal history identifies the run.
    """
    intent = (
        _current_request(task)
        .annotate(
            watched=EvalTaskStartRequest.live_condition(),
            aged=Q(created_at__lte=Now() - UNCONFIRMED_AFTER),
        )
        .values("state", "last_outcome", "admitted_run_id", "watched", "aged")
        .first()
    )
    if intent is None:
        if task.instant_paused_before_start_at is not None:
            return _common(
                "paused_before_start", "This task was paused before it started."
            )
        if task.status == EvalTaskStatus.PENDING:
            return _common("pending", "A pending task cannot be resumed.")
        return None

    # A watched request owns the bridge even after the first worker write:
    # an orphan must never substitute an ungated start for the durable sender.
    if not (delivery and intent["watched"]) and _has_worker_artifacts(task):
        return None
    if intent["watched"]:
        message = _MESSAGES.get(intent["last_outcome"], _MESSAGES["start_unconfirmed"])
        return _common("watching", message, log=intent["aged"])
    if intent["admitted_run_id"] is not None:
        return None
    if intent["state"] == State.START_FAILED:
        return _common("start_failed", START_FAILURE_MESSAGE, log=True)
    if intent["state"] == State.EXHAUSTED:
        message = _MESSAGES["execution_closed_without_progress"]
        return Refusal(
            "exhausted",
            f"{message} Use Re-run to make a new start request.",
            message,
            message,
        )
    if intent["state"] == State.ACCEPTED:
        message = _MESSAGES.get(intent["last_outcome"], _MESSAGES["awaiting_worker"])
        return _common("accepted", message, log=intent["aged"])
    return _common("revoked", "No previous run could be confirmed for this task.")


def record_pending_instant_pause(task):
    """Called under the task lock, before Pause loses the old status."""
    if (
        task.status == EvalTaskStatus.PENDING
        and not _current_request(task).exists()
        and not _has_worker_artifacts(task)
    ):
        EvalTask.objects.using("default").filter(pk=task.pk).update(
            instant_paused_before_start_at=Now()
        )


def record_rerun(task):
    """The explicit new submission and its supersession commit together."""
    EvalTaskStartRequest.void_for_tasks([task.pk])
    _current_request(task).update(superseded_at=Now())
    if task.instant_paused_before_start_at is not None:
        EvalTask.objects.using("default").filter(pk=task.pk).update(
            instant_paused_before_start_at=None
        )


def _available():
    return _requests().filter(
        Q(state=State.PENDING)
        | Q(state=State.CLAIMED, lease_until__lte=Now())
        | Q(state=State.ACCEPTED, lease_until__lte=Now())
    )


def _live_tasks(task_id):
    return (
        EvalTask.objects.using("default")
        .filter(
            Exists(
                Project.with_simulation.filter(pk=OuterRef("project_id"), deleted=False)
            ),
            pk=task_id,
        )
        .exclude(status__in=(EvalTaskStatus.PAUSED, EvalTaskStatus.DELETED))
    )


def _waiting_for_work(task_id):
    # start_time doubles as a continuous coverage floor. It isn't evidence of
    # a send or of durable evaluation work. Nor is a summary-created InstantEvalRun.
    pending = Q(status=EvalTaskStatus.PENDING) & ~Exists(
        EvalTaskLogger.all_objects.filter(eval_task_id=task_id)
    )
    admitted = Q(status=EvalTaskStatus.RUNNING) & Exists(
        _requests().filter(pk=task_id, admitted_run_id__isnull=False)
    )
    return _live_tasks(task_id).filter(
        pending | admitted,
        ~Exists(EvalLogger.all_objects.filter(eval_task_id=str(task_id))),
        end_time__isnull=True,
        continuous_cursor__isnull=True,
    )


def _finish(owned, state, outcome, **extra):
    return owned.update(
        state=state,
        last_outcome=outcome,
        lease_until=Now() + LEASE if outcome in _WATCHING else None,
        claim_token=None,
        updated_at=Now(),
        **extra,
    )


@transaction.atomic(using="default")
def admit_start(task_id, run_id):
    """First actual execution of the live request wins, whatever its send order.

    Create is one-to-one with the task and is never renewed. Record the winning
    Temporal run ID under the task -> request locks; an activity redelivery to
    that run is safe, but an independent execution can never pass.
    """
    task = _live_tasks(task_id).select_for_update(of=("self",)).first()
    rows = _requests().filter(
        EvalTaskStartRequest.live_condition(), pk=task_id, attempts__gt=0
    )
    request = rows.select_for_update().annotate(db_now=Now()).first()
    if request is None or task is None:
        return False
    if request.admitted_run_id:
        return (
            str(request.admitted_run_id) == run_id
            and task.status == EvalTaskStatus.RUNNING
        )
    if not _waiting_for_work(task_id).exists():
        return False
    floor = request.db_now
    if task.run_type == RunType.CONTINUOUS:
        floor = continuous_coverage_floor(task, now=floor)
    EvalTask.objects.using("default").filter(pk=task_id).update(
        status=EvalTaskStatus.RUNNING, start_time=floor
    )
    _finish(
        rows,
        State.ACCEPTED,
        "worker_admitted",
        admitted_run_id=run_id,
    )
    return True


class _TransportUnavailable(Exception):
    """The transport may throw arbitrary exceptions; keep only their type."""


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except WorkflowAlreadyStartedError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize arbitrary transport failures
        error_type = type(exc).__name__[:80]
    raise _TransportUnavailable(error_type)


@transaction.atomic(using="default")
def _reserve_or_settle(task_id, owned, execution):
    # Never hold either lock over an RPC. Both admission and an explicit user
    # action take the task lock first, so neither can race an exhaustion write.
    task = _live_tasks(task_id).select_for_update(of=("self",)).first()
    request = owned.select_for_update().annotate(db_now=Now()).first()
    if request is None:
        return None
    if task is None:
        _finish(owned, State.VOID, "superseded")
        return None
    if not _waiting_for_work(task_id).exists():
        if request.attempts:
            _finish(owned, State.ACCEPTED, "execution_progress")
        else:
            _finish(owned, State.VOID, "superseded")
        return None
    age = request.db_now - request.created_at
    history_expired = (
        request.attempts > 0
        and request.db_now - (request.first_reserved_at or request.created_at)
        >= HISTORY_WINDOW
    )
    if execution == client.WF_COMPLETED:
        # A successful workflow is not a failed start, even with zero entries.
        # The task finalizer, not a describe, owns the Completed status.
        _finish(owned, State.ACCEPTED, "execution_completed")
        return None
    if execution == client.WF_CLOSED:
        outcome = "execution_closed_without_progress"
    elif execution == client.WF_ABSENT and history_expired:
        # Expired history cannot establish failure or authorize replacement.
        _finish(owned, State.ACCEPTED, "unconfirmed_beyond_history")
        return None
    elif request.admitted_run_id:
        # Its Running status and coverage floor are admission, not durable work.
        # A live or uncertain execution still owns this request: never resend.
        _finish(owned, State.ACCEPTED, "worker_admitted")
        return None
    elif execution == client.WF_PROGRESSING:
        _finish(owned, State.ACCEPTED, "awaiting_worker")
        return None
    elif request.last_outcome in ("awaiting_worker", "duplicate_seen"):
        # A later outage or NOT_FOUND cannot undo evidence of acceptance.
        _finish(owned, State.ACCEPTED, "awaiting_worker")
        return None
    elif request.last_outcome == "unconfirmed_beyond_history":
        _finish(owned, State.ACCEPTED, request.last_outcome)
        return None
    elif execution not in ("initial", client.WF_ABSENT):
        reason = (
            "start_unconfirmed" if age >= UNCONFIRMED_AFTER else "describe_unconfirmed"
        )
        _finish(owned, State.PENDING, reason)
        return None
    elif request.attempts < MAX_ATTEMPTS:
        # Reserve before the RPC. A process death cannot renew either slot.
        if owned.update(
            attempts=request.attempts + 1,
            first_reserved_at=request.first_reserved_at or request.db_now,
            last_outcome="send_reserved",
            lease_until=Now() + LEASE,
            updated_at=Now(),
        ):
            return task
        return None
    else:
        outcome = "not_started"
    state = (
        State.START_FAILED
        if request.attempts == MAX_ATTEMPTS and not request.admitted_run_id
        else State.EXHAUSTED
    )
    if _finish(owned, state, outcome):
        EvalTask.objects.using("default").filter(pk=task_id).update(
            status=EvalTaskStatus.FAILED, updated_at=Now()
        )
    return None


def dispatch_start_request(task_id, *, initial_start=None):
    """One lease. A replayed on_commit can only take an untouched request."""
    available = _available().filter(task_id=task_id)
    if initial_start is not None:
        available = available.filter(
            state=State.PENDING,
            attempts=0,
            last_outcome="requested",
        )
    token = uuid4()
    if not available.update(
        state=State.CLAIMED,
        claim_token=token,
        lease_until=Now() + LEASE,
        updated_at=Now(),
    ):
        return
    owned = _requests().filter(
        task_id=task_id, state=State.CLAIMED, claim_token=token, lease_until__gt=Now()
    )
    execution = "initial"
    if initial_start is None:
        if _waiting_for_work(task_id).exists():
            try:
                # Bound connection + RPC well inside the lease. An unavailable
                # service must return a decision, not pin every sweep worker.
                execution = _call(
                    client.describe_eval_task_workflow_sync,
                    task_id,
                    timeout=5,
                    distinguish_completion=True,
                )
            except _TransportUnavailable as failure:
                logger.warning(
                    "eval_task.describe_unconfirmed", error_type=str(failure)
                )
                execution = "unavailable"
        else:
            # If eligibility changes before we take the lock, do not start on
            # this observation: we haven't described anything.
            execution = "unavailable"
    task = _reserve_or_settle(task_id, owned, execution)
    if task is None:
        return
    options = {"start_once": True}
    if initial_start is None:
        options["workflow_confirmed_stopped"] = True
    try:
        _call(initial_start or client.start_eval_task_workflow_sync, task, **options)
    except WorkflowAlreadyStartedError:
        # A duplicate can already be CLOSED. Check its progress next tick.
        _finish(owned, State.PENDING, "duplicate_seen")
    except _TransportUnavailable as failure:
        _finish(owned, State.PENDING, "reply_unknown")
        logger.warning(
            "eval_task.create_start_unconfirmed",
            error_type=str(failure),
            exc_info=False,
        )
    else:
        # Admission may already have exchanged this claim for an observation lease.
        _finish(owned, State.ACCEPTED, "awaiting_worker")


def reconcile_start_requests(*, limit=50):
    # Snapshot once, so an uncertain reply cannot spend another slot this tick.
    ids = list(
        _available().order_by("updated_at", "pk").values_list("pk", flat=True)[:limit]
    )
    for task_id in ids:
        dispatch_start_request(task_id)
    return {"candidates": len(ids)}
