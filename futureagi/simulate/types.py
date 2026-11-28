"""Typed shapes of the simulate app's JSON payloads: worker records and actors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, Required, TypedDict

WorkerEventType = Literal["delegated", "transferred", "tool_call", "returned"]
WorkerStatus = Literal["ok", "error"]
WorkerRelationship = Literal["observed", "unknown"]
WorkerAttribution = Literal["reported", "unattributed"]
# `infrastructure_lost`: nothing was recorded because the attempt was lost to an infrastructure failure, never the app.
WorkerCollection = Literal[
    "complete", "partial", "unavailable", "no_evidence", "infrastructure_lost"
]
# What the `/workers/` answer holds: a kept record, an earlier attempt kept before records were, or a failed read.
WorkersState = Literal["recorded", "not_kept", "unreadable"]
WorkerStepKind = Literal["handoff", "tool_call"]
# How a hand-off ended: a return decides it; a control pass that never returns is `passed` unless a tool it ran failed.
HandOffEnd = Literal["finished", "failed", "passed", "no_return", "cut_off"]
# `code`: the text is data (JSON, shown indented, or a printed dict or list); `text`: prose or markdown.
TextFormat = Literal["code", "text"]
# How a worker ended in one call.
WorkerState = Literal[
    "completed",
    "failed",
    "no_return",
    "deadline_passed",
    "cancel_requested",
    "cancelled",
    "infrastructure",
    "not_observed",
    # It took part (its tool calls, or control handed to it), but no return or end was recorded, so none is claimed.
    "observed_no_end",
    # Not seen in an attempt lost to an infrastructure failure: whether it ran is not known.
    "unknown",
]
# How a call's trial ended, which a worker still out when it ended takes.
TrialEnding = Literal["ended", "infrastructure", "cancel_requested", "cancelled"]
# Whose check a sub-goal is: the task's, or one of the agent's workers'.
SubjectKind = Literal["task", "worker"]
PlacementStatus = Literal["failed", "inconclusive"]
# Why a failed check is on no step: a task check names no moment; a worker check's worker had several hand-offs or none.
Untied = Literal["no_moment", "several_hand_offs", "no_hand_off"]
# A worker's own check or world change is named `worker:<id>`; the task's and the agent's are not.
WORKER_SUBJECT_PREFIX = "worker:"


def worker_subject_id(subject: object) -> str | None:
    """The worker id a `worker:<id>` subject names; None for any other subject."""
    text = str(subject or "")
    return (
        text[len(WORKER_SUBJECT_PREFIX) :]
        if text.startswith(WORKER_SUBJECT_PREFIX)
        else None
    )


def is_worker_subject(subject: object) -> bool:
    return worker_subject_id(subject) is not None


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
    # None when the span reported no token count.
    tokens: NotRequired[int | None]


class SealedWorkerEvent(TypedDict, total=False):
    """One worker event the harness sealed on a tool-call row; `worker_id` names the runtime's agent, times are epoch."""

    type: str
    id: str
    worker_id: str | None
    by: str | None
    parent_id: str
    relationship: str
    tool: str
    at: float
    ended_at: float | None
    status: str


class SealedToolCall(TypedDict, total=False):
    """One row of `provider_call_data.livekit.tool_calls` as the sandbox sealed it; times are epoch seconds."""

    name: str
    arguments: object
    result: object
    error: object
    ok: bool
    refused: bool
    # Why the world refused it, as its action log coded it.
    refusal_code: str
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
    worker_events: list[SealedWorkerEvent]


# How a declared worker reaches the world; `not_declared` when its contract entry does not say.
WorkerBinding = Literal["separate", "shared", "not_declared"]


# How the world held a worker to its limits: on its own channel, not at all, bypassed in a trial, or not known.
WorkerEnforcement = Literal["held_at_world", "not_enforced", "bypassed", "unknown"]


def worker_enforcement(
    binding: object,
    *,
    trial: Mapping[str, Any] | None = None,
    bypassed: bool = False,
    unheld: bool = False,
    at_door: bool = False,
) -> WorkerEnforcement:
    """How the world held a worker, from what the harness sealed for the trial (`trial`): held only on positive
    evidence, its own calls at the door in this trial (`at_door`) with a door that held each separate worker on its own
    channel (`worker_channels`). Bypassed when the trial says so; not enforced when it shares the app's address, the
    trial says its limits were not held (`unheld`) or no door saw its tools; otherwise, and with no trial, not known."""
    if bypassed:
        return "bypassed"
    if binding != "separate" or unheld:
        return "not_enforced"
    if trial is None or trial.get("stopped_workers_unreadable") is True:
        # With no trial, or whether the harness stopped its workers could not be read: not known, never held.
        return "unknown"
    if trial.get("door") is True and trial.get("worker_channels") is True:
        return "held_at_world" if at_door else "unknown"
    if trial.get("recorded") is False:
        return "not_enforced"
    return "unknown"


class ContractWorker(TypedDict):
    """A worker the app's contract declares, as the Workers and Participants cards show it."""

    id: str
    name: NotRequired[str]
    # What the cards call it: its name, else its id.
    display_name: str
    tools: list[str]
    # The tools it may use when narrower than what its code holds; empty means all of `tools`.
    allowed_tools: list[str]
    # The tools the cards list: its allowed tools when narrower, else every tool its code holds.
    tools_shown: list[str]
    binding: WorkerBinding
    enforcement: WorkerEnforcement
    enforcement_label: str
    address_env: NotRequired[str]
    # The process that runs it alone, when the app serves it as its own service.
    process: NotRequired[str]


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


# A sub-goal with no pass or fail: why it has none.
SubGoalUnscored = Literal["not_applicable", "inconclusive", "unmeasured"]


class SubGoalEntry(TypedDict):
    """One sub-goal of a call as the results list it; `worker_id` names the worker whose own check it is."""

    name: str
    passed: bool | None
    result: NotRequired[SubGoalUnscored]
    reason: NotRequired[str]
    evidence: NotRequired[list[str]]
    subject: NotRequired[str]
    subject_kind: SubjectKind
    worker_id: NotRequired[str]
    # What the results show: its name, with the worker's name when it is one worker's own check.
    display_name: str


# `stopped`: the harness ended the worker's own process (a worker the app serves alone) when its deadline passed.
WorkerCleanupStatus = Literal[
    "stopped", "stopped_with_sandbox", "still_running", "unknown"
]


class StoppedWorker(TypedDict):
    """A worker whose own process the harness ended past its deadline, as the trial records it."""

    worker: str
    process: str
    # Seconds since the epoch.
    at: float
    reason: str


# Which bound cut a shared record: its own actions, the trial's list of records, or a trial sealed before the
# harness said which.
SharedRecordTruncation = Literal["actions", "records", "unknown"]


class SharedRecordAction(TypedDict):
    who: str
    name: str
    tool: str
    outcome: str
    # False for a read, true for a write, None when nothing says.
    write: bool | None


class SharedRecord(TypedDict):
    """A record two or more participants named in one trial, as argument=value."""

    key: str
    argument: str
    value: str
    actions: list[SharedRecordAction]
    committed_writes: int
    # As the harness sealed it: its actions or the list of records reached its bound.
    truncated: bool
    # As the harness sealed it: this record's own actions were cut; None for a trial sealed before the flag.
    actions_truncated: NotRequired[bool | None]
    # Which bound cut it, read from the two flags; None when nothing was cut.
    truncation: NotRequired[SharedRecordTruncation | None]


class WorkerCleanup(TypedDict):
    status: WorkerCleanupStatus
    at: str | None


class WorkerCheck(TypedDict):
    """One of a worker's own checks in a call: beside the call's verdict, never deciding it."""

    name: str
    passed: bool | None
    result: NotRequired[SubGoalUnscored]


class HarnessWorker(TypedDict):
    worker_id: str
    id: str
    name: str
    observed: bool
    undeclared: bool
    instance_ids: list[str]
    model: NotRequired[str]
    tokens: NotRequired[int]
    # How the worker ended in this call; a timeline read before end states were recorded has it filled when served.
    state: NotRequired[WorkerState]
    # When the harness ended this worker's own process past its deadline; absent for a worker it did not stop.
    stopped_at: NotRequired[float]
    # Served only: a cut-short worker's cleanup, and whether the trial's end stopped it before it returned.
    cleanup: NotRequired[WorkerCleanup | None]
    cut_short: NotRequired[bool]
    # Served only: the world refused its later calls past its action deadline; it was not stopped.
    past_deadline: NotRequired[bool]
    # Served only: it reached the world on the shared address instead of its own channel in this trial.
    channel_bypassed: NotRequired[bool]
    # Served only: how the world held it to its limits in this trial, and the words for it.
    enforcement: NotRequired[WorkerEnforcement]
    enforcement_label: NotRequired[str]
    # Served only: its own checks in this call.
    checks: NotRequired[list[WorkerCheck]]


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
    # A hand-off to a background job, placed under the tool call that launched it.
    launch: Literal["async"]
    refusal_code: str
    runtime_event_id: str
    runtime_parent_event_id: str
    # Internal: the sealed row this event came from or that a span showed, to join reads; never sent to the client.
    row: int
    at: Required[str | None]
    # When a tool call finished; absent when the record does not say.
    ended_at: str | None
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
    # The worker's end state, on a hand-off that did not come back and that the trial's end cut short.
    end_state: WorkerState
    # The hand-off failed or its worker never came back: counted in `counts.did_not_finish`.
    did_not_finish: bool
    # The worker's first hand-off in the call, where its model and tokens (its totals) are shown.
    first_of_worker: bool
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
    # The keys of every hand-off in this turn, nested ones included, each followed by the ones it made.
    handoffs: list[int]
    # False for work before the caller spoke or in no recorded turn: a missing request or reply there is not missed.
    expects_messages: bool


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
    asynchronous: int
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
    # The project the call's traces were read from; absent for a record read before it was kept.
    project_id: NotRequired[str | None]


class FailurePlacement(TypedDict):
    """A failed or unsure sub-goal: a worker's own check on that worker's one hand-off; `untied` says why it is on none."""

    name: str
    status: PlacementStatus
    # The hand-off step's key; null when the check is on no step.
    step: int | None
    # The record's `worker_id` of the worker whose own check it is; null for a task check, which names no worker.
    worker_id: str | None
    untied: Untied | None


class CallWorkersRecord(WorkerTimeline, WorkerSteps):
    """The `/workers/` response: the kept record with its steps; `pending` while a read is still due."""

    pending: bool
    placements: list[FailurePlacement]
    stopped_by_user: bool
    state: WorkersState


# A trial's route: compared when `recorded` (a whole record) or `no_hand_off` (a read that found none); never when the
# record is cut short or due a later read, the read is due, the call did not complete, or the read failed or never ran.
# `no_evidence`: the read ran but the app sent no traces and the sandbox recorded no hand-offs, so the route is not known.
TrialRoute = Literal[
    "recorded",
    "no_hand_off",
    "partial",
    "pending",
    "not_finished",
    "unreadable",
    "no_evidence",
]
# Who took a step's part: a worker, the coordinator, or unknown when the record does not say.
RouteParty = Literal["worker", "coordinator", "unknown"]
# A trial's step in a column: answered, failed, never answered, cut short with the record, taken more than once, or
# not taken.
RouteCellState = Literal["step", "failed", "no_answer", "cut_off", "repeated", "absent"]


class RouteColumn(TypedDict):
    """One who-asked-whom step, matched across trials by who and what: the worker asked, or the tool called."""

    key: str
    kind: WorkerStepKind
    from_kind: RouteParty
    # A worker's id within the run; null for the coordinator or an unknown party.
    from_id: str | None
    # Null when the record does not name it.
    from_name: str | None
    # Null for a tool-call column.
    to_kind: RouteParty | None
    to_id: str | None
    to_name: str | None
    tool: str | None


class RouteCell(TypedDict):
    state: RouteCellState
    # The step's key in the trial's worker record; the first one when it was taken more than once.
    step: int | None
    steps: list[int]
    count: int
    # Each occurrence's own state, in the order the trial took them.
    states: list[RouteCellState]


class RouteTrial(TypedDict):
    number: int
    call_execution_id: str
    result: str
    route: TrialRoute
    # One per column; null when the trial has no route to compare.
    cells: list[RouteCell] | None


class RouteSplitGroup(TypedDict):
    state: RouteCellState
    count: int
    states: list[RouteCellState]
    trials: list[int]


class RouteSplit(TypedDict):
    column: int
    key: str
    groups: list[RouteSplitGroup]


class TrialRoutes(TypedDict):
    """One scenario's trials in a run lined up by route."""

    scenario_key: str
    tool_calls: bool
    trials: list[RouteTrial]
    columns: list[RouteColumn]
    # Null when every trial took the same route.
    split: RouteSplit | None
    same_route: bool


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
CallActorDelivery = Literal[
    "delivered", "acted", "dropped", "not_triggered", "not_drawn"
]


class CallActorFacetValue(TypedDict):
    """A run's calls one actor was planned for, by how far they got; or the calls with none."""

    value: str
    label: str
    count: int
    delivered: int
    acted: int
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


# A scenario's lifecycle state on an agent, environment and suite pairing.
LifecycleState = Literal["draft", "validated", "active", "kept_as_written", "archived"]
# What a run does when every selected case is held back only for not being validated on the pairing.
UnprovedNext = Literal["validate_first", "run_unvalidated"]
