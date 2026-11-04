"""Typed shapes of the sub-actor JSON that simulate stores and serves."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class SubActorTrigger(TypedDict, total=False):
    """When a sub-actor enters a call; the harness writes only the keys its kind uses."""

    kind: str
    turn: int
    tool: str
    on_call: int
    words: list[str]
    p: float


class ScenarioSubActor(TypedDict):
    """A sub-actor a scenario includes, as `HostedHarnessScenario.sub_actors` stores it."""

    id: str
    instruction: str
    trigger: SubActorTrigger


class SubActorPersona(TypedDict, total=False):
    """The caller persona fields a sub-actor's voice is chosen from."""

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


class SubActor(TypedDict):
    """One entry of the environment's sub-actor list."""

    id: str
    name: str
    role: str
    channel: str
    persona: SubActorPersona
    loudness_ratio: NotRequired[float]


class ContractSubActor(SubActor):
    """A list entry on the contract, with how many scenarios include it."""

    scenarios_count: int


class SubActorTookPart(TypedDict, total=False):
    """Whether one sub-actor was triggered in a call and how many lines reached the agent."""

    sub_actor: str
    name: str | None
    triggered: bool
    delivered: int
    drawn: bool
    dropped: list[str]


class SubActorEvent(TypedDict, total=False):
    """One step of a sub-actor's part in a call."""

    sub_actor: str
    name: str | None
    channel: str | None
    kind: str
    text: str
    reason: str
    at: float
    ended_at: float
    asked_at: float | None


class CallSubActors(TypedDict, total=False):
    """`call_metadata["harness_sub_actors"]`: who else was in the call, who took part and when."""

    resolved: list[SubActor]
    summary: list[SubActorTookPart]
    events: list[SubActorEvent]
