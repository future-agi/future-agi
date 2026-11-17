"""How each worker in a call ended: by its own hand-offs and returns, by how the trial ended, and by its deadline."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from simulate.constants.hosted_harness import (
    SCENARIO_FAILURE_CODES,
    TRIAL_STATE_KEY,
    USER_CANCELED,
    WORKER_CHANNEL_BYPASSED,
    WORKER_ENFORCEMENT_LABELS,
    WORKER_LIMITS_NOT_ENFORCED,
)
from simulate.models import (
    CallExecution,
    CallExecutionSnapshot,
    HostedHarnessAttempt,
    HostedHarnessExecution,
    HostedHarnessJob,
)
from simulate.types import (
    HarnessWorker,
    StoppedWorker,
    TrialEnding,
    WorkerCleanup,
    WorkerEvent,
    WorkerState,
    WorkerTimeline,
    is_worker_subject,
    worker_enforcement,
    worker_subject_id,
)

_SCENARIO_DOMAINS = frozenset({"simulator", "environment"})

# A worker's end when the trial stopped it before it returned; one that returned or never ran has none.
CUT_SHORT_STATES: frozenset[WorkerState] = frozenset(
    {"no_return", "deadline_passed", "cancel_requested", "cancelled", "infrastructure"}
)

# The end state of a worker still out when the trial ended, by how the trial ended.
_STILL_OUT: dict[TrialEnding, WorkerState] = {
    "ended": "no_return",
    "infrastructure": "infrastructure",
    "cancel_requested": "cancel_requested",
    "cancelled": "cancelled",
}


def worker_states(
    timeline: WorkerTimeline,
    *,
    trial: TrialEnding,
    past_deadline: Iterable[str] = (),
    stopped: Mapping[str, float] | None = None,
) -> WorkerTimeline:
    """The timeline with each worker's end state, matching each hand-off to its own return by delegation id.
    A refusal past the deadline says its later world calls were refused, never that it was stopped (`stopped` names
    the workers whose own process the harness ended); no return is invented."""
    events = timeline["events"]
    handed: dict[str, list[str]] = {}
    for event in events:
        if event["type"] == "delegated":
            handed.setdefault(str(event["worker_id"]), []).append(
                str(event.get("delegation_id") or "")
            )
    returns = {
        str(event.get("delegation_id") or ""): event
        for event in events
        if event["type"] == "returned" and event.get("delegation_id")
    }
    # A record that kept no delegation ids pairs a worker's hand-offs with its returns in order instead.
    unkeyed: dict[str, list[WorkerEvent]] = {}
    for event in events:
        if event["type"] == "returned" and not event.get("delegation_id"):
            unkeyed.setdefault(str(event["worker_id"]), []).append(event)
    # Refused past its deadline: by the trial's world changes, or by the refusal code on its own recorded calls.
    stopped = dict(stopped or {})
    late = (
        set(past_deadline)
        | set(stopped)
        | {
            str(event["worker_id"]).rsplit("/", 1)[-1]
            for event in events
            if event.get("refusal_code") == "deadline_passed" and event.get("worker_id")
        }
    )
    workers: list[HarnessWorker] = []
    for worker in timeline["workers"]:
        ended = worker.copy()
        state: WorkerState
        delegations = handed.get(worker["worker_id"], [])
        spare = list(unkeyed.get(worker["worker_id"], []))
        back = [
            returns.get(one) if one else (spare.pop(0) if spare else None)
            for one in delegations
        ]
        # A hand-off with no return of its own is still out, whatever another hand-off to the same worker returned.
        out = any(one is None for one in back)
        if worker["id"] in late:
            # Refused past its deadline, even if it returned: it never finished as asked, and the world saw it.
            ended["observed"] = True
            ended["state"] = "deadline_passed"
            if worker["id"] in stopped:
                ended["stopped_at"] = stopped[worker["id"]]
            workers.append(ended)
            continue
        if delegations and not out:
            state = (
                "failed"
                if any(one.get("status") == "error" for one in back if one)
                else "completed"
            )
        elif not worker["observed"]:
            state = "not_observed"
        elif out:
            state = _STILL_OUT[trial]
        else:
            # Seen only through its tool calls, or handed control that never returns: no end is claimed, even when the
            # trial was cut short (it may have finished long before).
            state = "observed_no_end"
        ended["state"] = state
        workers.append(ended)
    return {**timeline, "workers": workers}


def deadline_refused(call: CallExecution) -> set[str]:
    """The workers the world refused past their deadline in this call's trial, by id, from its world changes."""
    return refused_past_deadline(call.call_metadata)


def refused_past_deadline(metadata: Mapping[str, Any] | None) -> set[str]:
    """The workers a trial's world changes (`metadata`, a call's or an earlier attempt's) show refused past their
    deadline, by id."""
    trial = (metadata or {}).get(TRIAL_STATE_KEY) or {}
    if not isinstance(trial, Mapping):
        return set()
    return {
        str(worker_subject_id(entry.get("who")))
        for entry in trial.get("state_changes") or []
        if isinstance(entry, dict)
        and is_worker_subject(entry.get("who"))
        and any(
            isinstance(one, dict) and one.get("code") == "deadline_passed"
            for one in entry.get("refused_actions") or []
        )
    }


def stopped_workers_of(state: Mapping[str, object]) -> list[StoppedWorker]:
    """The workers a trial's state says the harness stopped; an entry without a worker or a time is left out."""
    entries = state.get("stopped_workers")
    return [
        cast(StoppedWorker, one)
        for one in (entries if isinstance(entries, list) else [])
        if isinstance(one, dict)
        and one.get("worker")
        and isinstance(one.get("at"), (int, float))
        and not isinstance(one.get("at"), bool)
    ]


def stopped_workers(call: CallExecution) -> dict[str, float]:
    """The workers the harness stopped in this call's trial (their own process ended past their deadline), by id."""
    trial = (call.call_metadata or {}).get(TRIAL_STATE_KEY) or {}
    return {str(one["worker"]): float(one["at"]) for one in stopped_workers_of(trial)}


def trial_ending(call: CallExecution, job: HostedHarnessJob | None) -> TrialEnding:
    """How one call's trial ended, from the call row, its receipt and the job."""
    if call.status == CallExecution.CallStatus.CANCELLED:
        # Confirmed only once the platform has stopped the run; stopping our observation is not a confirmed cancel.
        confirmed = job is not None and job.state == HostedHarnessJob.State.CANCELED
        return "cancelled" if confirmed else "cancel_requested"
    receipt = (call.call_metadata or {}).get("hosted_harness_receipt")
    # A call the job closed without a receipt ended the way the job did.
    failure = (
        receipt.get("failure")
        if receipt
        else (job.failure if job is not None else None)
    ) or {}
    if failure.get("code") == USER_CANCELED:
        # Stopped by the user (a receipt ingested before the call was marked cancelled for it): never infrastructure.
        confirmed = job is not None and job.state == HostedHarnessJob.State.CANCELED
        return "cancelled" if confirmed else "cancel_requested"
    # A scenario's own problem (simulator or environment side, or a scenario hold) is not an infrastructure fault.
    if (
        failure.get("code") in SCENARIO_FAILURE_CODES
        or failure.get("domain") in _SCENARIO_DOMAINS
    ):
        return "ended"
    return "infrastructure" if failure.get("domain") not in (None, "agent") else "ended"


_TERMINAL_JOB_STATES = (
    HostedHarnessJob.State.COMPLETED,
    HostedHarnessJob.State.FAILED,
    HostedHarnessJob.State.CANCELED,
)


def _execution(call: CallExecution) -> HostedHarnessExecution | None:
    """The call's execution row, with its job and its receipt's attempt, read once."""
    return (
        HostedHarnessExecution.objects.select_related("job", "receipt__attempt")
        .filter(call_execution=call)
        .first()
    )


def _job_of(
    call: CallExecution, execution: HostedHarnessExecution | None
) -> HostedHarnessJob | None:
    """The job that ran this call: its execution's job, else the newest job of its test execution."""
    if execution is not None:
        return execution.job
    return (
        HostedHarnessJob.no_workspace_objects.filter(test_execution=call.test_execution)
        .order_by("-created_at")
        .first()
    )


def job_for_call(call: CallExecution) -> HostedHarnessJob | None:
    return _job_of(call, _execution(call))


def _call_attempt(
    execution: HostedHarnessExecution | None, job: HostedHarnessJob
) -> HostedHarnessAttempt | None:
    """The attempt that ran this call: its receipt's, else the job's current one."""
    receipt = getattr(execution, "receipt", None) if execution is not None else None
    if receipt is not None:
        attempt: HostedHarnessAttempt = receipt.attempt
        return attempt
    return job.attempts.filter(attempt_number=job.current_attempt_number).first()


def worker_cleanup(
    call: CallExecution,
    job: HostedHarnessJob | None,
    attempt: HostedHarnessAttempt | None,
    worker: HarnessWorker,
) -> WorkerCleanup | None:
    """What happened to a cut-short worker's process when the call ended; computed when served, never stored.
    `stopped` only when the harness ended the worker's own process; one inside the app's process goes with the sandbox."""
    if worker.get("state") not in CUT_SHORT_STATES:
        return None
    stopped_at = worker.get("stopped_at")
    if stopped_at is not None:
        return {
            "status": "stopped",
            "at": datetime.fromtimestamp(stopped_at, tz=UTC).isoformat(),
        }
    if job is None:
        return None
    if attempt is None:
        return {"status": "unknown", "at": None}
    if attempt.cleanup_verified_at is not None:
        return {
            "status": "stopped_with_sandbox",
            "at": attempt.cleanup_verified_at.isoformat(),
        }
    if attempt.terminal_event_received and job.state in _TERMINAL_JOB_STATES:
        # The guest ended on its own and nothing confirmed the sandbox went.
        return {"status": "unknown", "at": None}
    # The attempt is live, or the cancel window is open with cleanup pending.
    ended = call.completed_at.isoformat() if call.completed_at else None
    return {"status": "still_running", "at": ended}


# How an attempt ended, kept beside its record when a rerun snapshots it (`attempt_end`): read for that attempt
# instead of the call, its job and its sandbox as they are now.
ATTEMPT_END_KEY = "harness_attempt_end"
# Each earlier attempt's kept record (its worker record, trial state, receipt and `ATTEMPT_END_KEY`), on the call's own
# metadata by the id of the snapshot its rerun took; the latest `MAX_KEPT_ATTEMPTS` only.
ATTEMPTS_KEY = "harness_attempts"
MAX_KEPT_ATTEMPTS = 10


def attempt_metadata(
    call: CallExecution, attempt: CallExecutionSnapshot
) -> dict[str, Any] | None:
    """An earlier attempt's kept record, or None when its rerun kept none (before attempts were kept, or dropped as one
    of the oldest)."""
    attempts = (call.call_metadata or {}).get(ATTEMPTS_KEY)
    kept = attempts.get(str(attempt.id)) if isinstance(attempts, Mapping) else None
    return dict(kept) if isinstance(kept, Mapping) else None


def _attempt_execution(
    call: CallExecution, attempt: CallExecutionSnapshot
) -> HostedHarnessExecution | None:
    """This call's own execution that ran an earlier attempt: its latest one created before the rerun."""
    return (
        HostedHarnessExecution.objects.select_related("job", "receipt__attempt")
        .filter(call_execution=call, created_at__lte=attempt.snapshot_timestamp)
        .order_by("-created_at")
        .first()
    )


def _attempt_job(
    call: CallExecution, attempt: CallExecutionSnapshot
) -> HostedHarnessJob | None:
    """The job of this call's own execution; with none, its run's job (a run has exactly one)."""
    return _job_of(call, _attempt_execution(call, attempt))


def _kept_end(
    call: CallExecution, attempt: CallExecutionSnapshot
) -> Mapping[str, Any] | None:
    kept = (attempt_metadata(call, attempt) or {}).get(ATTEMPT_END_KEY)
    return kept if isinstance(kept, Mapping) else None


def _attempt_as_ended(call: CallExecution, attempt: CallExecutionSnapshot) -> Any:
    """An earlier attempt as its snapshot and kept record left the call when it ended: its status, metadata and end."""
    return SimpleNamespace(
        status=attempt.status,
        call_metadata=attempt_metadata(call, attempt) or {},
        completed_at=attempt.completed_at,
    )


def stopped_by_user(
    call: CallExecution, attempt: CallExecutionSnapshot | None = None
) -> bool:
    """Whether the user stopped this call, or the earlier attempt a rerun kept (`attempt`, from its own snapshot):
    cancelled, and its receipt or its job says the user stopped it; a fix ending or an environment delete is not."""
    kept = _kept_end(call, attempt) if attempt is not None else None
    if kept is not None:
        return kept.get("stopped_by_user") is True
    status = attempt.status if attempt is not None else call.status
    if status != CallExecution.CallStatus.CANCELLED:
        return False
    metadata = (
        attempt_metadata(call, attempt) if attempt is not None else call.call_metadata
    ) or {}
    receipt = metadata.get("hosted_harness_receipt")
    failure = receipt.get("failure") if isinstance(receipt, Mapping) else None
    if isinstance(failure, Mapping) and failure.get("code") == USER_CANCELED:
        return True
    job = job_for_call(call) if attempt is None else _attempt_job(call, attempt)
    return job is not None and job.cancel_reason == USER_CANCELED


def _bypassed(metadata: Mapping[str, Any] | None) -> set[str]:
    """The workers the trial says reached the world on the shared address instead of their own channel."""
    trial = (metadata or {}).get(TRIAL_STATE_KEY)
    notices = trial.get("notices") if isinstance(trial, Mapping) else None
    return {
        str(worker)
        for one in (notices if isinstance(notices, list) else [])
        if isinstance(one, Mapping) and one.get("code") == WORKER_CHANNEL_BYPASSED
        for worker in one.get("workers") or []
    }


def _at_door(metadata: Mapping[str, Any] | None) -> set[str]:
    """The workers whose own calls reached the world's door in the trial, by its state changes (`worker:<id>`)."""
    trial = (metadata or {}).get(TRIAL_STATE_KEY)
    changes = trial.get("state_changes") if isinstance(trial, Mapping) else None
    return {
        worker
        for one in (changes if isinstance(changes, list) else [])
        if isinstance(one, Mapping)
        and (worker := worker_subject_id(one.get("who")))
        and isinstance(one.get("attempted"), int)
        and one["attempted"] > 0
    }


def _unheld(metadata: Mapping[str, Any] | None) -> set[str] | None:
    """The workers whose limits the trial says were not held, by its `worker_limits_not_enforced` notices; None when a
    notice names no worker, so it falls on every one."""
    trial = (metadata or {}).get(TRIAL_STATE_KEY)
    notices = trial.get("notices") if isinstance(trial, Mapping) else None
    named: set[str] = set()
    for one in notices if isinstance(notices, list) else []:
        if isinstance(one, Mapping) and one.get("code") == WORKER_LIMITS_NOT_ENFORCED:
            workers = one.get("workers")
            if not isinstance(workers, list) or not workers:
                return None
            named.update(str(worker) for worker in workers)
    return named


def served_workers(
    call: CallExecution,
    timeline: WorkerTimeline,
    metadata: Mapping[str, Any] | None = None,
    bindings: Mapping[str, str] | None = None,
    attempt: CallExecutionSnapshot | None = None,
) -> WorkerTimeline:
    """The stored record as served: a `cancel_requested` the job has since confirmed reads `cancelled`, each cut-short
    worker carries its cleanup, and a worker the trial (`metadata`, the call's or an earlier attempt's) says bypassed
    its channel says so. The stored record is never rewritten."""
    bypassed = _bypassed(metadata)
    unheld = _unheld(metadata)
    at_door = _at_door(metadata)
    late = refused_past_deadline(metadata)
    trial = (metadata or {}).get(TRIAL_STATE_KEY)
    # A record read before every worker kept a state is served with the state its own events give each one.
    derived = (
        {
            one["worker_id"]: one["state"]
            for one in worker_states(timeline, trial="ended")["workers"]
            if "state" in one
        }
        if any("state" not in one for one in timeline["workers"])
        else {}
    )
    workers: list[HarnessWorker] = []
    for one in timeline["workers"]:
        enforcement = worker_enforcement(
            (bindings or {}).get(str(one.get("id"))),
            trial=trial if isinstance(trial, Mapping) else None,
            bypassed=str(one.get("id")) in bypassed,
            unheld=unheld is None or str(one.get("id")) in unheld,
            at_door=str(one.get("id")) in at_door,
        )
        served: HarnessWorker = {
            **one,
            "channel_bypassed": str(one.get("id")) in bypassed,
            "enforcement": enforcement,
            "enforcement_label": WORKER_ENFORCEMENT_LABELS[enforcement],
        }
        if "state" not in served and one["worker_id"] in derived:
            served["state"] = derived[one["worker_id"]]
        if str(one.get("id")) in late:
            # A record read before door refusals counted still says how its worker ended: past its deadline.
            served["state"] = "deadline_passed"
            served["observed"] = True
        workers.append(served)
    if not any(one.get("state") in CUT_SHORT_STATES for one in workers):
        return {**timeline, "workers": workers}
    # Read once for every worker, not per worker. An earlier attempt is read as its own snapshot and job kept it,
    # never as the call stands now.
    kept = _kept_end(call, attempt) if attempt is not None else None
    ended: Any = call if attempt is None else _attempt_as_ended(call, attempt)
    if attempt is None:
        execution = _execution(call)
    else:
        # An earlier attempt kept without its ending is read from this call's own execution that ran it.
        execution = None if kept is not None else _attempt_execution(call, attempt)
    # With no execution row, the run's job: a run has exactly one.
    job = _job_of(call, execution) if kept is None else None
    sandbox = _call_attempt(execution, job) if job is not None else None
    cut = any(one.get("state") == "cancel_requested" for one in workers)
    ending = (
        (kept.get("ending") if kept is not None else trial_ending(ended, job))
        if cut
        else None
    )
    for worker in workers:
        if worker.get("state") == "cancel_requested" and ending == "cancelled":
            worker["state"] = "cancelled"
        if kept is not None:
            cleanup = (
                worker_cleanup(ended, None, None, worker)
                if worker.get("stopped_at") is not None
                else kept.get("cleanup")
                if worker.get("state") in CUT_SHORT_STATES
                else None
            )
        else:
            cleanup = worker_cleanup(ended, job, sandbox, worker)
        if cleanup is not None:
            worker["cleanup"] = cleanup
    return {**timeline, "workers": workers}
