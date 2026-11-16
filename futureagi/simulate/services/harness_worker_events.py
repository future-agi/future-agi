"""A call's worker record from the worker events the harness sealed on each tool-call row, or from spans before them."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from simulate.services.harness_worker_text import (
    epoch_iso,
    payload,
    row_output,
    row_started,
    slug,
    summary,
    task_text,
)
from simulate.services.harness_worker_timeline import (
    HAND_OFF_TYPES,
    PAYLOADS,
    Roster,
    finished_timeline,
    label_of,
    own_id,
    worker_timeline,
)
from simulate.types import (
    CallTurn,
    DeclaredWorker,
    SealedToolCall,
    SealedWorkerEvent,
    WorkerEvent,
    WorkerSpan,
    WorkerTimeline,
)


def _sealed_events(row: SealedToolCall) -> list[SealedWorkerEvent]:
    listed = row.get("worker_events")
    return (
        [one for one in listed if isinstance(one, Mapping)]
        if isinstance(listed, list)
        else []
    )


def _status(event: SealedWorkerEvent) -> Literal["ok", "error"]:
    # A refused call is one that did not succeed, as the span path reads it.
    return "ok" if event.get("status") == "ok" else "error"


class _Agents:
    """Who the sealed events say the coordinator is and which agents are workers."""

    def __init__(self, rows: list[SealedToolCall], roster: Roster) -> None:
        self.roster = roster
        self.labels = {
            slug(str(row["worker_id"])): label_of(row)
            for row in rows
            if row.get("worker_id")
        }
        events = [one for row in rows for one in _sealed_events(row)]
        handed = {
            slug(str(one.get("worker_id")))
            for one in events
            if one.get("type") in HAND_OFF_TYPES and one.get("worker_id")
        }
        makers = {
            slug(str(name))
            for one in events
            if (
                name := one.get("by")
                if one.get("type") in HAND_OFF_TYPES
                else one.get("worker_id")
            )
        }
        # An agent that is never handed work and is not a declared worker is the coordinator.
        self.coordinators = {
            name for name in makers - handed if roster.match(name) is None
        }
        self.coordinator = next(
            (self.labels.get(name, name) for name in sorted(self.coordinators)), None
        )

    def worker(self, name: object) -> str | None:
        if not name or slug(str(name)) in self.coordinators:
            return None
        return self.roster.add_named(
            str(name), self.labels.get(slug(str(name)), str(name))
        )


def _placed(
    event: WorkerEvent, sealed: SealedWorkerEvent, *, call: bool
) -> WorkerEvent:
    """Where the harness recorded the event ran: under its parent hand-off, else whether its place is known."""
    parent = str(sealed.get("parent_id") or "")
    if parent and sealed.get("relationship") == "observed":
        event["delegation_id" if call else "parent_delegation_id"] = parent
    else:
        event["relationship"] = (
            "observed" if sealed.get("relationship") == "observed" else "unknown"
        )
    return event


def _from_row(
    row: SealedToolCall, index: int, agents: _Agents, spans: Mapping[str, WorkerSpan]
) -> list[WorkerEvent]:
    out: list[WorkerEvent] = []
    for sealed in _sealed_events(row):
        kind = sealed.get("type")
        ident = str(sealed.get("id") or "") or own_id(row, index)
        span = spans.get(ident)
        if kind == "tool_call":
            event: WorkerEvent = {
                "type": "tool_call",
                "worker_id": agents.worker(sealed.get("worker_id")),
                "tool": str(sealed.get("tool") or row.get("name") or ""),
                "status": _status(sealed),
                "input": payload(row.get("arguments")),
                "output": payload(row_output(row)),
                "at": row_started(sealed) or epoch_iso(sealed.get("ended_at")),
                # No agent named for the call: neither the coordinator nor a worker can be said to have made it.
                "attribution": "reported"
                if sealed.get("worker_id")
                else "unattributed",
                "row": index,
            }
            if sealed.get("id"):
                event["runtime_event_id"] = ident
            if row.get("refusal_code"):
                # Why the world refused it, as its action log coded it.
                event["refusal_code"] = str(row["refusal_code"])
            for _key, flag in PAYLOADS:
                if row.get(flag):
                    event[flag] = True
            if row.get("task_label") or row.get("task_id"):
                event["task"] = str(row.get("task_label") or row.get("task_id"))
            out.append(_placed(event, sealed, call=True))
        elif kind in HAND_OFF_TYPES:
            event = {
                "type": "transferred" if kind == "transferred" else "delegated",
                "by": agents.worker(sealed.get("by")),
                "worker_id": agents.worker(sealed.get("worker_id")),
                "delegation_id": ident,
                "at": row_started(sealed),
                "attribution": "reported",
                "row": index,
            }
            if kind == "delegated":
                event["task"] = task_text(row.get("arguments"))
            out.append(_placed(event, sealed, call=False))
        elif kind == "returned":
            event = {
                "type": "returned",
                "worker_id": agents.worker(sealed.get("worker_id")),
                "delegation_id": ident,
                "result": summary(row.get("result")),
                "status": _status(sealed),
                "at": epoch_iso(sealed.get("at")),
                "attribution": "reported",
                "row": index,
            }
            out.append(event)
        else:
            continue
        # The event's own span, when the app exported one under the same id, opens in Traces.
        if span is not None and kind != "returned":
            out[-1]["span_id"] = ident
            out[-1]["trace_id"] = str(span.get("trace_id") or "")
    return out


def recorded_timeline(
    spans: Iterable[WorkerSpan],
    declared: Iterable[DeclaredWorker],
    *,
    run_key: str,
    traced_calls: Iterable[SealedToolCall] = (),
    turns: Iterable[CallTurn] = (),
    started_at: str | None = None,
) -> WorkerTimeline:
    """The record from the worker events the harness sealed on the rows; a call sealed before them is read from spans."""
    rows = [row for row in traced_calls if isinstance(row, Mapping)]
    spans = list(spans)
    if not any(_sealed_events(row) for row in rows):
        return worker_timeline(
            spans,
            declared,
            run_key=run_key,
            traced_calls=rows,
            turns=turns,
            started_at=started_at,
        )
    roster = Roster(declared, run_key)
    agents = _Agents(rows, roster)
    by_id = {str(span["span_id"]): span for span in spans}
    events = [
        event
        for index, row in enumerate(rows)
        for event in _from_row(row, index, agents, by_id)
    ]
    return finished_timeline(
        events,
        roster,
        coordinator=agents.coordinator,
        spans_received=bool(by_id),
        turns=turns,
        started_at=started_at,
    )
