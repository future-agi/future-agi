"""The `/workers/` answer for one call or one of its earlier attempts: the record, still coming, none, or not kept."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from django.http import Http404
from rest_framework import status

from simulate.models import CallExecution
from simulate.services.harness_worker_steps import served_record
from simulate.services.harness_workers import (
    READ_KEY,
    WORKERS_KEY,
    check_again_at,
    first_read_due,
    read_pending,
)
from simulate.types import CallWorkersRecord, WorkersState, WorkerTimeline


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


def _answer(metadata: Mapping[str, object], now: datetime) -> WorkersReply | None:
    """The kept record, or a failed read, or None when the read ran and found no hand-off or has not run."""
    record = metadata.get(WORKERS_KEY)
    if isinstance(record, Mapping) and record:
        return WorkersReply(
            status.HTTP_200_OK, served_record(cast(WorkerTimeline, record), now)
        )
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
        return _answer(snapshot.call_metadata, now) or WorkersReply(
            status.HTTP_204_NO_CONTENT
        )
    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    answer = _answer(metadata, now)
    if answer is not None:
        return answer
    due = first_read_due(call)
    if due is not None and read_pending(due, now):
        # The first read has not run yet: the client checks again at `next_read_at` rather than reading "no workers".
        return WorkersReply(
            status.HTTP_202_ACCEPTED, next_read_at=check_again_at(due, now)
        )
    return WorkersReply(status.HTTP_204_NO_CONTENT)
