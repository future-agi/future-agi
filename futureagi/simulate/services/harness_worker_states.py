"""How each worker in a call ended: by its own hand-offs and returns, by how the trial ended, and by its deadline."""

from __future__ import annotations

from collections.abc import Iterable

from simulate.constants.hosted_harness import SCENARIO_FAILURE_CODES, TRIAL_STATE_KEY
from simulate.models import CallExecution, HostedHarnessJob
from simulate.types import (
    HarnessWorker,
    TrialEnding,
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
    timeline: WorkerTimeline, *, trial: TrialEnding, past_deadline: Iterable[str] = ()
) -> WorkerTimeline:
    """The timeline with each worker's end state, matching each hand-off to its own return by delegation id.
    A refusal past the deadline says its later world calls were refused, never that it was stopped; no return is
    invented."""
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
    late = set(past_deadline) | {
        str(event["worker_id"]).rsplit("/", 1)[-1]
        for event in events
        if event.get("refusal_code") == "deadline_passed" and event.get("worker_id")
    }
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
            workers.append(ended)
            continue
        elif not worker["observed"]:
            state = "not_observed"
        elif out:
            state = _STILL_OUT[trial]
        else:
            # Seen only through its tool calls: no recorded end, so no end state rather than an invented one.
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
    # A scenario's own problem (simulator or environment side, or a scenario hold) is not an infrastructure fault.
    if (
        failure.get("code") in SCENARIO_FAILURE_CODES
        or failure.get("domain") in _SCENARIO_DOMAINS
    ):
        return "ended"
    return "infrastructure" if failure.get("domain") not in (None, "agent") else "ended"
