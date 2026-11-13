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
    trace_id: NotRequired[str]
    model: NotRequired[str]
    tokens: NotRequired[int]


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
    model: NotRequired[str]
    tokens: NotRequired[int]


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
    trace_id: str
    status: Literal["ok", "error"]
    input: Any
    output: Any
    input_omitted: bool
    output_omitted: bool
    relationship: Literal["observed", "unknown"]
    parent_delegation_id: str
    runtime_event_id: str
    runtime_parent_event_id: str
    # Internal: the sealed row this event came from or that a span showed, to join reads; never sent to the client.
    row: int
    at: Required[str | None]
    attribution: Required[Literal["reported", "unattributed"]]


class WorkerStep(TypedDict, total=False):
    """One row of the Participants tab: a hand-off (with its return) or a tool call, under its parent hand-off."""

    kind: Required[Literal["handoff", "tool_call"]]
    key: Required[int]
    parent: Required[int | None]
    depth: Required[int]
    worker_id: Required[str | None]
    transfer: bool
    handoff: WorkerEvent | None
    returned: WorkerEvent | None
    started_at: str | None
    ended_at: str | None
    parallel: bool
    call: WorkerEvent
    # None when the record does not say which turn the step was in.
    turn: int | None
    order: int
    relationship: Literal["observed", "unknown"]


class WorkerTurn(TypedDict):
    """One round of the call: the caller's message, the steps before the agent's reply, the reply."""

    turn: int | None
    request: TurnSummary | None
    response: TurnSummary | None


class WorkerSteps(TypedDict):
    steps: list[WorkerStep]
    turns: list[WorkerTurn]
    depth: int
    parallel: int
    overlap_unknown: bool
    worker_depths: dict[str, int]


class Coordinator(TypedDict):
    name: str


class WorkerTimeline(TypedDict):
    """A call's record under `call_metadata["harness_workers"]`."""

    workers: list[HarnessWorker]
    events: list[WorkerEvent]
    events_omitted: int
    coordinator: Coordinator | None
    spans_received: bool
    started_at: str | None
    # The caller's and the agent's messages on the steps' clock, so the steps can be grouped by turn.
    messages: NotRequired[list[CallTurn]]
    collection: NotRequired[
        Literal["complete", "partial", "unavailable", "no_evidence"]
    ]
    # How many of the call's messages were left out past the first ones kept.
    messages_omitted: NotRequired[int]
    collected_at: NotRequired[str]
    # False when the read lacked a trace's top-level span or stopped at the span bound; that read is read again.
    tree_complete: NotRequired[bool]
