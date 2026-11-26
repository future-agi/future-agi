"""The `/workers/` answer for one call or one of its earlier attempts: the record, still coming, none, or not kept."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from django.http import Http404
from rest_framework import status

from simulate.constants.hosted_harness import HAND_OFFS_INCOMPLETE, TRIAL_STATE_KEY
from simulate.models import CallExecution, HostedHarnessJob, TestExecution
from simulate.models.test_execution import CallExecutionSnapshot
from simulate.services.harness_worker_states import (
    attempt_metadata,
    job_for_call,
    served_workers,
    stopped_by_user,
)
from simulate.services.harness_worker_steps import served_record
from simulate.services.harness_workers import (
    FIRST_READ,
    READ_KEY,
    WORKERS_KEY,
    attempt_timeline,
    check_again_at,
    declared_workers,
    first_read_due,
    later_read_due,
    read_pending,
)
from simulate.services.run_results_v3 import receipt_sub_goals
from simulate.types import (
    CallWorkersRecord,
    TrialRoute,
    TrialRoutes,
    WorkersState,
    WorkerTimeline,
)


@dataclass(frozen=True)
class WorkersReply:
    """The HTTP status and body the view sends: 200 a record, 202 a read still due, 204 no hand-off was seen."""

    status: int
    record: CallWorkersRecord | None = None
    next_read_at: str | None = None
    # With a 202: whether the run's version declares any participant, so a single-agent call shows no tab meanwhile.
    participants_declared: bool = False


def _empty(at: str) -> WorkerTimeline:
    return {
        "workers": [],
        "events": [],
        "events_omitted": 0,
        "coordinator": None,
        "spans_received": False,
        "started_at": None,
        "collected_at": at,
    }


def _stated(state: WorkersState, at: str, now: datetime) -> WorkersReply:
    """A record with no events that says why there is none."""
    return WorkersReply(status.HTTP_200_OK, served_record(_empty(at), now, state=state))


def _answer(
    call: CallExecution,
    metadata: Mapping[str, object],
    now: datetime,
    *,
    attempt: CallExecutionSnapshot | None,
) -> WorkersReply | None:
    """The kept record, or a failed read, or None when the read ran and found no hand-off or has not run."""
    record = metadata.get(WORKERS_KEY)
    if isinstance(record, Mapping) and record:
        # A worker's own checks and the failed checks' places come from the same metadata as the record.
        job = job_for_call(call)
        # How each declared worker reaches the world, from the contract of the version the run was pinned to.
        bindings = {
            str(one["id"]): str(cast(Mapping[str, object], one).get("binding") or "")
            for one in (declared_workers(job) if job is not None else [])
        }
        served = served_record(
            served_workers(
                call, cast(WorkerTimeline, record), metadata, bindings, attempt
            ),
            now,
            receipt_sub_goals(metadata),
            stopped_by_user=stopped_by_user(call, attempt),
        )
        return WorkersReply(status.HTTP_200_OK, served)
    read = metadata.get(READ_KEY)
    if isinstance(read, Mapping) and read.get("failed"):
        return _stated("unreadable", str(read.get("at") or now.isoformat()), now)
    return None if read is None else WorkersReply(status.HTTP_204_NO_CONTENT)


def call_workers(
    call: CallExecution, attempt: UUID | None, now: datetime
) -> WorkersReply:
    """The call's worker answer, or an earlier attempt's as its rerun snapshot kept it; 404 for an attempt id that is
    not one of this call's."""
    if attempt is not None:
        # Only an attempt a rerun of the call kept, as `earlier_attempts` lists them; an evals-only rerun is none.
        snapshot = (
            call.snapshots.filter(
                id=attempt, rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL
            )
            .only(
                "status",
                "snapshot_timestamp",
                "started_at",
                "completed_at",
                "provider_call_data",
                "transcripts",
            )
            .first()
        )
        if snapshot is None:
            raise Http404
        kept = attempt_metadata(call, snapshot)
        if kept is None:
            # Rerun before attempts were kept, or one of the oldest no longer kept: its record, if any, is gone.
            return _stated("not_kept", now.isoformat(), now)
        if WORKERS_KEY not in kept and READ_KEY not in kept:
            # Rerun before its own read ran: its record is built from the tool calls its snapshot sealed.
            built = attempt_timeline(call, snapshot, kept)
            if built is not None:
                kept = {**kept, WORKERS_KEY: built}
        return _answer(call, kept, now, attempt=snapshot) or WorkersReply(
            status.HTTP_204_NO_CONTENT
        )
    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    answer = _answer(call, metadata, now, attempt=None)
    if answer is not None:
        return answer
    first = first_read_due(call)
    # Before the first read, or after one that found nothing until the later read has run (it notes what it finds):
    # the client checks again at `next_read_at` rather than reading "no workers".
    for due in (first, later_read_due(first)) if first is not None else ():
        if read_pending(due, now):
            job = job_for_call(call)
            return WorkersReply(
                status.HTTP_202_ACCEPTED,
                next_read_at=check_again_at(due, now),
                participants_declared=bool(job is not None and declared_workers(job)),
            )
    return WorkersReply(status.HTTP_204_NO_CONTENT)


def _hand_offs_incomplete(metadata: Mapping[str, object]) -> bool:
    """Whether the harness said some of the trial's hand-offs could not be recorded."""
    trial = metadata.get(TRIAL_STATE_KEY)
    notices = trial.get("notices") if isinstance(trial, Mapping) else None
    return any(
        isinstance(one, Mapping) and one.get("code") == HAND_OFFS_INCOMPLETE
        for one in (notices if isinstance(notices, list) else [])
    )


def _trial_route(
    call: CallExecution, now: datetime, *, hosted: bool
) -> tuple[TrialRoute, CallWorkersRecord | None]:
    """How far a trial's worker record can be compared, read from the call alone: a whole record of a completed call;
    a read that found no hand-off; or why it is not compared, never as a route it did not take."""
    # A call that did not complete stopped part way: whatever was kept of it is not its whole route.
    if call.status != CallExecution.CallStatus.COMPLETED or call.completed_at is None:
        return "not_finished", None
    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    kept = metadata.get(WORKERS_KEY)
    read = metadata.get(READ_KEY)
    if isinstance(kept, Mapping) and kept:
        if kept.get("collection") == "no_evidence":
            # Nothing was recorded for its workers: an unmeasured route, never one with no steps.
            return "no_evidence", None
        record = served_record(cast(WorkerTimeline, kept), now)
        if record["state"] != "recorded":
            return "unreadable", None
        if record["cut_off"] or record["pending"] or _hand_offs_incomplete(metadata):
            return "partial", None
        return "recorded", record
    if isinstance(read, Mapping):
        if read.get("failed"):
            return "unreadable", None
        return ("partial" if _hand_offs_incomplete(metadata) else "no_hand_off"), None
    due = (call.completed_at + timedelta(seconds=FIRST_READ)).isoformat()
    # Until the later read has run, a first read that found nothing leaves no note.
    if hosted and read_pending(later_read_due(due), now):
        return "pending", None
    # Completed and past its read with no read noted: what its workers did is not known.
    return "unreadable", None


def scenario_trial_routes(
    execution: TestExecution, scenario_key: str, now: datetime, *, tool_calls: bool
) -> TrialRoutes:
    """One scenario's trials in the run, in trial order, lined up from each trial's worker record; the run's job is
    read once for every trial."""
    from simulate.services.harness_trial_routes import Trial, trial_routes
    from simulate.services.run_results_v3_queries import run_calls_queryset

    # The key the calls list serves as `source_scenario_key`.
    calls = list(
        run_calls_queryset(execution).filter(
            call_metadata__harness_scenario_key=scenario_key
        )
    )
    hosted = HostedHarnessJob.no_workspace_objects.filter(
        test_execution_id=execution.id
    ).exists()

    def placed(call: CallExecution) -> tuple[int, datetime]:
        metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
        index = metadata.get("harness_trial_index")
        return (index if isinstance(index, int) else len(calls), call.created_at)

    trials = []
    for call in sorted(calls, key=placed):
        route, record = _trial_route(call, now, hosted=hosted)
        trials.append(
            Trial(
                call_execution_id=str(call.id),
                result=str(getattr(call, "result_outcome", "") or ""),
                record=record,
                route=route,
            )
        )
    return trial_routes(scenario_key, trials, tool_calls=tool_calls)
