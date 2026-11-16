"""Typed shapes of the simulate app's JSON payloads: worker records and actors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, Required, TypedDict

WorkerEventType = Literal["delegated", "transferred", "tool_call", "returned"]
WorkerStatus = Literal["ok", "error"]
WorkerRelationship = Literal["observed", "unknown"]
WorkerAttribution = Literal["reported", "unattributed"]
WorkerCollection = Literal["complete", "partial", "unavailable", "no_evidence"]
WorkerStepKind = Literal["handoff", "tool_call"]
# How a hand-off ended: a return decides it; a control pass that never returns is `passed` unless a tool it ran failed.
HandOffEnd = Literal["finished", "failed", "passed", "no_return", "cut_off"]
# `code`: the text is data (JSON, shown indented, or a printed dict or list); `text`: prose or markdown.
TextFormat = Literal["code", "text"]


@dataclass(frozen=True)
class CallWorkersRead:
    """The worker-record activity's payload; `attempt` is the call's last reset when the read was scheduled."""

    call_execution_id: str
    second_read: bool = False
    attempt: str | None = None

    @classmethod
    def parse(cls, payload: Mapping[str, object]) -> CallWorkersRead:
        attempt = payload.get("attempt")
        return cls(
            call_execution_id=str(payload["call_execution_id"]),
            second_read=bool(payload.get("second_read")),
            attempt=None if attempt is None else str(attempt),
        )


class WorkerSpan(TypedDict):
    span_id: str
    parent_span_id: str | None
    name: str
    observation_type: str
    start_time: str | None
    end_time: str | None
    status: str
    input: NotRequired[object]
    output: NotRequired[object]
    attributes: dict[str, str]
    trace_id: NotRequired[str]
    model: NotRequired[str]
    tokens: NotRequired[int]


class SealedToolCall(TypedDict, total=False):
    """One row of `provider_call_data.livekit.tool_calls` as the sandbox sealed it; times are epoch seconds."""

    name: str
    arguments: object
    result: object
    error: object
    ok: bool
    refused: bool
    at: float
    ended_at: float
    start_time_ms: float
    worker_id: str
    worker_label: str
    delegated_to: str
    kind: str
    relationship: str
    parent_span_id: str
    span_id: str
    runtime_ids: dict[str, str]
    task_label: str
    task_id: str
    input_omitted: bool
    output_omitted: bool


class DeclaredWorker(TypedDict):
    id: str
    name: NotRequired[str]


class CallTurn(TypedDict):
    role: Literal["user", "agent"]
    text: str | None
    at: str


class ShownText(TypedDict):
    format: TextFormat
    text: str


class TurnSummary(TypedDict):
    text: str | None
    at: str | None
    shown: NotRequired[ShownText | None]


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
    type: Required[WorkerEventType]
    worker_id: Required[str | None]
    by: str | None
    delegation_id: str
    task: str | None
    result: str | None
    task_shown: ShownText | None
    result_shown: ShownText | None
    tool: str
    span_id: str
    trace_id: str
    status: WorkerStatus
    input: object
    output: object
    input_omitted: bool
    output_omitted: bool
    relationship: WorkerRelationship
    parent_delegation_id: str
    runtime_event_id: str
    runtime_parent_event_id: str
    # Internal: the sealed row this event came from or that a span showed, to join reads; never sent to the client.
    row: int
    at: Required[str | None]
    attribution: Required[WorkerAttribution]


class WorkerStep(TypedDict, total=False):
    """One row of the Participants tab: a hand-off (with its return) or a tool call, under its parent hand-off."""

    kind: Required[WorkerStepKind]
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
    end: HandOffEnd
    # The keys of the steps directly under this hand-off, in reading order.
    children: list[int]
    call: WorkerEvent
    # None when the record does not say which turn the step was in.
    turn: int | None
    order: int
    relationship: WorkerRelationship


class WorkerTurn(TypedDict):
    """One round of the call: the caller's message, the steps before the agent's reply, the reply."""

    turn: int | None
    request: TurnSummary | None
    response: TurnSummary | None
    # The keys of the top-level steps in this turn, in reading order.
    children: list[int]
    calls_outside: int


class HandOffCounts(TypedDict):
    hand_offs: int
    workers: int
    finished: int
    did_not_finish: int
    passed: int
    cut_off: int


class WorkerSteps(TypedDict):
    steps: list[WorkerStep]
    turns: list[WorkerTurn]
    depth: int
    parallel: int
    overlap_unknown: bool
    worker_depths: dict[str, int]
    counts: HandOffCounts
    cut_off: bool
    beyond_boundary: bool


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
    collection: NotRequired[WorkerCollection]
    # How many of the call's messages were left out past the first ones kept.
    messages_omitted: NotRequired[int]
    collected_at: NotRequired[str]
    # False when the read lacked a trace's top-level span or stopped at the span bound; that read is read again.
    tree_complete: NotRequired[bool]
    # When the later read is due; absent once no later read is scheduled.
    next_read_at: NotRequired[str]


class CallWorkersRecord(WorkerTimeline, WorkerSteps):
    """The `/workers/` response: the kept record with its steps; `pending` while a read is still due."""

    pending: bool


class CallActorTrigger(TypedDict, total=False):
    """When an actor enters a call; the harness writes only the keys its kind uses."""

    kind: str
    seconds: float
    turn: int
    tool: str
    on_call: int
    words: list[str]
    p: float
    collection: str
    where: dict[str, object]


class ScenarioCallActor(TypedDict):
    """An actor a scenario includes, as `HostedHarnessScenario.call_actors` stores it."""

    id: str
    instruction: str
    # Missing: the actor's own activation applies.
    trigger: NotRequired[CallActorTrigger]
    knowledge: NotRequired[list[dict[str, object]]]
    actions: NotRequired[list[dict[str, object]]]


class CallActor(TypedDict):
    """One entry of the environment's actor list."""

    id: str
    name: str
    role: str
    channel: str
    # The caller persona's fields, any the runtime keeps included.
    persona: dict[str, object]
    loudness_ratio: NotRequired[float | None]


class ContractCallActor(CallActor):
    """A list entry on the contract, with how many scenarios include it."""

    scenarios_count: int


class CallActorTookPart(TypedDict, total=False):
    """Whether one actor was triggered in a call and how many lines reached the agent."""

    actor: str
    name: str | None
    triggered: bool
    delivered: int
    drawn: bool
    dropped: list[str]


# An actor's part in one call: heard by the agent, triggered with nothing heard, never
# triggered, or left out by the call's draw.
CallActorDelivery = Literal["delivered", "dropped", "not_triggered", "not_drawn"]


class CallActorFacetValue(TypedDict):
    """A run's calls one actor was planned for, by how far they got; or the calls with none."""

    value: str
    label: str
    count: int
    delivered: int
    dropped: int
    not_triggered: int


class CallActorEvent(TypedDict, total=False):
    """One step of an actor's part in a call."""

    actor: str
    name: str | None
    channel: str | None
    kind: str
    text: str
    reason: str
    at: float
    ended_at: float
    asked_at: float | None


class CallCallActors(TypedDict, total=False):
    """`call_metadata["harness_call_actors"]`: who else was in the call, who took part and when."""

    resolved: list[CallActor]
    summary: list[CallActorTookPart]
    events: list[CallActorEvent]
