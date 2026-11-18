"""Dispatch a recorded create, never infer a request from Pending status.

A lease elects the sender; the task's one create request admits one run ID.
Only a negative describe within retention permits a retry. A known execution
is watched until its worker records progress, not submitted again. An outage
past the deadline sacrifices automatic startup for certainty: keep describing
without offering a replacing Resume while the run might be alive.
"""

from datetime import timedelta
from uuid import uuid4

import structlog
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.db.models.functions import Now
from temporalio.exceptions import WorkflowAlreadyStartedError

from tfc.temporal.eval_tasks import client
from tracer.models.eval_task import (
    EvalTask,
    EvalTaskLogger,
    EvalTaskStartRequest,
    EvalTaskStatus,
    InstantEvalResume,
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
RESUME_OUTSTANDING = "A Resume is already awaiting confirmation."
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


def has_recorded_work(task):
    """Positive evidence only; neither a coverage floor nor a summary is a run."""
    return (
        task.continuous_cursor is not None
        or EvalLogger.objects.using("default")
        .filter(eval_task_id=str(task.pk))
        .exists()
        or EvalTaskLogger.objects.using("default").filter(eval_task_id=task.pk).exists()
        or _requests().filter(pk=task.pk, admitted_run_id__isnull=False).exists()
        or InstantEvalRun.objects.using("default")
        .filter(eval_task_id=task.pk)
        .exclude(hash_cursor="")
        .exists()
        or InstantEvalResume.objects.using("default")
        .filter(pk=task.pk, admitted_run_id__isnull=False)
        .exists()
    )


def resume_block_reason(task):
    if InstantEvalResume.outstanding(task.pk).exists():
        return RESUME_OUTSTANDING
    state = _requests().filter(pk=task.pk).values_list("state", flat=True).first()
    if state == State.START_FAILED:
        return START_FAILURE_MESSAGE
    if has_recorded_work(task):
        return None
    if state == State.EXHAUSTED:
        return "This start request is closed. Use Re-run to make a new start request."
    if state not in (None, State.VOID):
        return "No previous run could be confirmed for this task."
    # A void create can be RUNNING at its gate, or COMPLETED with start_fenced.
    # Only an independently started drain (e.g. a later explicit Re-run) can
    # prove prior execution here. Pre-intent tasks retain the legacy check.
    void = state == State.VOID
    try:
        history = _call(
            client.describe_eval_task_workflow_sync,
            task.pk,
            timeout=5,
            **({"distinguish_legacy": True} if void else {}),
        )
    except _TransportUnavailable:
        history = None
    closed = client.WF_LEGACY_CLOSED if void else client.WF_CLOSED
    active = client.WF_LEGACY_PROGRESSING if void else client.WF_PROGRESSING
    if history == closed or (
        history == active and task.status == EvalTaskStatus.PAUSED
    ):
        return None
    return "No previous run could be confirmed for this task."


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


def start_request_error(task):
    if task.end_time or task.status not in (
        EvalTaskStatus.FAILED,
        EvalTaskStatus.PENDING,
        EvalTaskStatus.RUNNING,
    ):
        return None
    rows = _requests().filter(pk=task.pk)
    if task.status == EvalTaskStatus.FAILED:
        row = rows.values_list("state", "last_outcome").first()
        if row and row[0] == State.START_FAILED:
            return START_FAILURE_MESSAGE
        return _MESSAGES.get(row[1]) if row and row[0] == State.EXHAUSTED else None
    # The same Temporal outage that lost the reply may prevent every scheduled
    # check. Reading Logs must explain an aged request without dispatching it.
    outcome = (
        rows.filter(
            Q(state__in=(State.PENDING, State.CLAIMED))
            | Q(state=State.ACCEPTED, lease_until__isnull=False)
            | Q(state=State.ACCEPTED, last_outcome="execution_completed"),
            created_at__lte=Now() - UNCONFIRMED_AFTER,
        )
        .values_list("last_outcome", flat=True)
        .first()
    )
    if outcome is None or not _waiting_for_work(task.pk).exists():
        return None
    return _MESSAGES.get(outcome, _MESSAGES["start_unconfirmed"])


@transaction.atomic(using="default")
def _fail_instant_resume(task_id, token):
    """A matching close/rejection loses to admission or a newer handoff."""
    task = (
        EvalTask.objects.using("default").select_for_update().filter(pk=task_id).first()
    )
    removed, _ = InstantEvalResume.outstanding(task_id).filter(token=token).delete()
    if removed and task and task.status == EvalTaskStatus.PENDING:
        task.status = EvalTaskStatus.FAILED
        task.save(update_fields=["status", "updated_at"])
    return bool(removed)


def reconcile_instant_resume(task_id, token):
    """Only a terminal describe carrying this handoff's token can settle it."""
    try:
        history = _call(
            client.describe_eval_task_workflow_sync,
            task_id,
            timeout=5,
            resume_token=str(token),
        )
    except _TransportUnavailable:
        return False
    return history == client.WF_RESUME_CLOSED and _fail_instant_resume(task_id, token)


def queue_instant_resume(task, run):
    """Both Resume endpoints hold the run (if any), then the task lock here."""
    from temporalio.service import RPCError, RPCStatusCode

    if InstantEvalResume.outstanding(task.pk).exists():
        return False
    token = uuid4()
    InstantEvalResume.objects.using("default").update_or_create(
        task=task, defaults={"token": token, "admitted_run_id": None}
    )
    if run is not None:
        InstantEvalRun.objects.using("default").filter(pk=run.pk).update(
            answers_version=F("answers_version") + 1
        )
    task.status = EvalTaskStatus.PENDING
    task.save(update_fields=["status", "updated_at"])
    EvalTaskStartRequest.void_for_tasks([task.pk])

    def send():
        try:
            client.start_eval_task_workflow_sync(
                task, replace_existing=True, resume_token=str(token)
            )
        except Exception as exc:
            logger.warning(
                "instant_eval_resume_send_failed", error_type=type(exc).__name__
            )
            # A lost reply may still have a live worker. Never resend automatically.
            if isinstance(exc, RPCError) and exc.status in (
                RPCStatusCode.INVALID_ARGUMENT,
                RPCStatusCode.PERMISSION_DENIED,
                RPCStatusCode.UNAUTHENTICATED,
            ):
                _fail_instant_resume(task.pk, token)

    transaction.on_commit(send, using="default")
    return True


@transaction.atomic(using="default")
def admit_instant_resume(task_id, token, run_id):
    """Only the worker for this handoff can admit it, even after a lost reply."""
    task = _live_tasks(task_id).select_for_update(of=("self",)).first()
    rows = InstantEvalResume.objects.using("default").filter(pk=task_id, token=token)
    resume = rows.select_for_update().first()
    if resume is None:
        return False
    if resume.admitted_run_id:
        return (
            str(resume.admitted_run_id) == run_id
            and task is not None
            and task.status in (EvalTaskStatus.PENDING, EvalTaskStatus.RUNNING)
        )
    if task is None or task.status != EvalTaskStatus.PENDING:
        rows.delete()  # Pause/delete won before this replacement reached its gate.
        return False
    rows.update(admitted_run_id=run_id)
    return True


@transaction.atomic(using="default")
def admit_start(task_id, run_id):
    """First actual execution of the live request wins, whatever its send order.

    Create is one-to-one with the task and is never renewed. Record the winning
    Temporal run ID under the task -> request locks; an activity redelivery to
    that run is safe, but an independent execution can never pass.
    """
    task = _live_tasks(task_id).select_for_update(of=("self",)).first()
    rows = (
        _requests()
        .filter(
            pk=task_id,
            attempts__gt=0,
            state__in=(State.CLAIMED, State.PENDING, State.ACCEPTED),
        )
        .exclude(last_outcome="execution_completed")
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
