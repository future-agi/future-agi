"""Typed shapes of the simulate app's JSON payloads."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Required, TypedDict


class WorkerSpan(TypedDict):
    span_id: str
    parent_span_id: str | None
    name: str
    observation_type: str
    start_time: str | None
    end_time: str | None
    status: str
    input: NotRequired[Any]
    output: NotRequired[Any]
    attributes: dict[str, str]


class DeclaredWorker(TypedDict):
    id: str
    name: NotRequired[str]


class CallTurn(TypedDict):
    role: Literal["user", "agent"]
    text: str | None
    at: str


class TurnSummary(TypedDict):
    text: str | None
    at: str | None


class HarnessWorker(TypedDict):
    worker_id: str
    id: str
    name: str
    observed: bool
    undeclared: bool
    instance_ids: list[str]


class WorkerEvent(TypedDict, total=False):
    seq: int
    type: Required[Literal["delegated", "transferred", "tool_call", "returned"]]
    worker_id: Required[str | None]
    by: str | None
    delegation_id: str
    task: str | None
    result: str | None
    tool: str
    span_id: str
    status: Literal["ok", "error"]
    input: Any
    output: Any
    payload_omitted: bool
    runtime_event_id: str
    runtime_parent_event_id: str
    at: Required[str | None]
    attribution: Required[Literal["reported", "unattributed"]]


class Coordinator(TypedDict):
    name: str


class WorkerTimeline(TypedDict):
    """A call's record under `call_metadata["harness_workers"]`."""

    workers: list[HarnessWorker]
    events: list[WorkerEvent]
    events_omitted: int
    coordinator: Coordinator | None
    spans_received: bool
    request: TurnSummary | None
    response: TurnSummary | None
    started_at: str | None
    collected_at: NotRequired[str]
