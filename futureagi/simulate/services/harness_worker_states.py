"""How each worker in a call ended: by its own hand-offs and returns, by how the trial ended, and by its deadline."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from simulate.constants.hosted_harness import (
    SCENARIO_FAILURE_CODES,
    TRIAL_STATE_KEY,
    USER_CANCELED,
)
from simulate.models import (
    CallExecution,
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
        if delegations and not out:
            state = (
                "failed"
                if any(one.get("status") == "error" for one in back if one)
                else "completed"
            )
        elif worker["id"] in late:
            # The world refused its calls, so the world saw it: observed, whatever the trace recorded.
            ended["observed"] = True
            ended["state"] = "deadline_passed"
            if worker["id"] in stopped:
                ended["stopped_at"] = stopped[worker["id"]]
            workers.append(ended)
            continue
        elif not worker["observed"]:
            state = "not_observed"
        elif out:
            state = _STILL_OUT[trial]
        else:
            # Seen only through its tool calls: no recorded end, so no end state rather than an invented one, even when
            # the trial was cut short (it may have finished long before).
            workers.append(ended)
            continue
        ended["state"] = state
        workers.append(ended)
    return {**timeline, "workers": workers}


def deadline_refused(call: CallExecution) -> set[str]:
    """The workers the world refused past their deadline in this call's trial, by id, from its world changes."""
    trial = (call.call_metadata or {}).get(TRIAL_STATE_KEY) or {}
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


def stopped_by_user(call: CallExecution, attempt: UUID | None) -> bool:
    """Whether the user stopped this call: cancelled, and its job cancelled for the user; a fix ending or an
    environment delete is not. An earlier attempt's own status is not kept, so it never says."""
    if attempt is not None or call.status != CallExecution.CallStatus.CANCELLED:
        return False
    job = job_for_call(call)
    return job is not None and job.cancel_reason == USER_CANCELED


def served_workers(call: CallExecution, timeline: WorkerTimeline) -> WorkerTimeline:
    """The stored record as served: a `cancel_requested` the job has since confirmed reads `cancelled`, and each
    cut-short worker carries its cleanup. The stored record is never rewritten."""
    workers = [one.copy() for one in timeline["workers"]]
    if not any(one.get("state") in CUT_SHORT_STATES for one in workers):
        return {**timeline, "workers": workers}
    execution = _execution(call)
    # Read once for every worker, not per worker.
    job = _job_of(call, execution)
    attempt = _call_attempt(execution, job) if job is not None else None
    ending = (
        trial_ending(call, job)
        if any(one.get("state") == "cancel_requested" for one in workers)
        else None
    )
    for worker in workers:
        if worker.get("state") == "cancel_requested" and ending == "cancelled":
            worker["state"] = "cancelled"
        cleanup = worker_cleanup(call, job, attempt, worker)
        if cleanup is not None:
            worker["cleanup"] = cleanup
    return {**timeline, "workers": workers}
