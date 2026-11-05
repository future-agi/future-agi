"""Typed shapes of the actor JSON that simulate stores and serves."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


class CallActorTrigger(TypedDict, total=False):
    """When an actor enters a call; the harness writes only the keys its kind uses."""

    kind: str
    turn: int
    tool: str
    on_call: int
    words: list[str]
    p: float


class ScenarioCallActor(TypedDict):
    """An actor a scenario includes, as `HostedHarnessScenario.call_actors` stores it."""

    id: str
    instruction: str
    trigger: CallActorTrigger
    actions: NotRequired[list[dict[str, object]]]


class CallActorPersona(TypedDict, total=False):
    """The caller persona fields an actor's voice is chosen from."""

    name: str
    gender: str
    age_group: str
    occupation: str
    location: str
    personality: str
    communication_style: str
    initial_message: str
    languages: list[str]
    accent: str


class CallActor(TypedDict):
    """One entry of the environment's actor list."""

    id: str
    name: str
    role: str
    channel: str
    persona: CallActorPersona
    loudness_ratio: NotRequired[float]


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
