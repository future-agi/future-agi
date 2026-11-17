"""The `/workers/` answer for one call or one of its earlier attempts: the record, still coming, none, or not kept."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from django.http import Http404
from rest_framework import status

from simulate.models import CallExecution, HostedHarnessJob, TestExecution
from simulate.services.harness_worker_states import served_workers, stopped_by_user
from simulate.services.harness_worker_steps import served_record
from simulate.services.harness_workers import (
    FIRST_READ,
    READ_KEY,
    WORKERS_KEY,
    check_again_at,
    first_read_due,
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
    attempt: UUID | None,
) -> WorkersReply | None:
    """The kept record, or a failed read, or None when the read ran and found no hand-off or has not run."""
    record = metadata.get(WORKERS_KEY)
    if isinstance(record, Mapping) and record:
        # A worker's own checks and the failed checks' places come from the same metadata as the record.
        served = served_record(
            served_workers(call, cast(WorkerTimeline, record), metadata),
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
        snapshot = call.snapshots.filter(id=attempt).only("call_metadata").first()
        if snapshot is None:
            raise Http404
        if snapshot.call_metadata is None:
            # Snapshotted before snapshots kept the call's metadata: its record, if any, was not kept.
            return _stated("not_kept", now.isoformat(), now)
        return _answer(
            call, snapshot.call_metadata, now, attempt=attempt
        ) or WorkersReply(status.HTTP_204_NO_CONTENT)
    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    answer = _answer(call, metadata, now, attempt=None)
    if answer is not None:
        return answer
    due = first_read_due(call)
    if due is not None and read_pending(due, now):
        # The first read has not run yet: the client checks again at `next_read_at` rather than reading "no workers".
        return WorkersReply(
            status.HTTP_202_ACCEPTED, next_read_at=check_again_at(due, now)
        )
    return WorkersReply(status.HTTP_204_NO_CONTENT)


def _trial_route(
    call: CallExecution, now: datetime, *, hosted: bool
) -> tuple[TrialRoute, CallWorkersRecord | None]:
    """How far a trial's worker record can be compared, read from the call alone: a whole record; a read that ran and
    found no hand-off; or why it is not compared (cut short or a later read due, a read not yet run, a call that did
    not complete, a read that failed or never ran)."""
    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    kept = metadata.get(WORKERS_KEY)
    read = metadata.get(READ_KEY)
    if isinstance(kept, Mapping) and kept:
        record = served_record(cast(WorkerTimeline, kept), now)
        if record["state"] != "recorded":
            return "unreadable", None
        if record["cut_off"] or record["pending"]:
            return "partial", None
        return "recorded", record
    if isinstance(read, Mapping):
        return ("unreadable" if read.get("failed") else "no_hand_off"), None
    if call.status != CallExecution.CallStatus.COMPLETED or call.completed_at is None:
        return "not_finished", None
    due = (call.completed_at + timedelta(seconds=FIRST_READ)).isoformat()
    if hosted and read_pending(due, now):
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
