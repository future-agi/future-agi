"""How an actor comes in, how long it waits and who answers for it, as the sentences the editor shows. They are built
here, beside the runtime rules they describe, so a change to a rule changes its sentence in one place."""

from __future__ import annotations

import json
import math
from typing import Literal, NotRequired, TypedDict, cast

from simulate.services.harness_actor_rules import (
    ActorRecord,
    ActorRules,
    is_the_caller,
    real_of,
    text_of,
    whole_of,
)
from simulate.types import CallActorTrigger, ScenarioCallActor

# Someone who takes the phone from the person: it waits until the person has it, never before the agent's second turn.
HANDOFF_CHANNEL = "audio_handoff"
TAKES_PHONE_FROM_TURN = 2
# A phone menu on the person's line: it answers the call the agent places, before anyone speaks.
IVR_CHANNEL = "ivr"
# The trigger kinds each runtime evaluates: someone who speaks runs on the agent's turns and words, someone acting
# through the world on its actions, contacts and time.
SPEAKING_TRIGGERS = ("always", "on_tool", "on_keyword", "mid_journey", "conditional")
WORLD_TRIGGERS = ("always", "on_tool", "contacted", "after_seconds", "conditional")
# What an actor that does not answer in time leaves behind, without naming an action or another actor.
FALLBACK_WORDS = {"silent": "Nothing happens", "decline": "They decline"}
# An actor's own cap on a trial: the lines one who speaks says, the turns one acting through the world takes.
CapField = Literal["max_turns", "max_lines"]

# Each way of joining in a word or two; null is acting through the world only.
CHANNEL_LABELS = {
    "audio_mix": "Heard nearby",
    "audio_handoff": "Takes the phone",
    "ivr": "Phone menu",
    "chat_message": "Writes in the chat",
}
# Each trigger kind as the editor offers it.
TRIGGER_KIND_LABELS = {
    "always": "Present from the start",
    "on_tool": "When the agent calls an action",
    "on_keyword": "When the agent says a word",
    "mid_journey": "From an agent turn",
    "contacted": "When the agent contacts them",
    "after_seconds": "At a set time",
    "conditional": "Once a record exists",
    "probability": "By chance",
}
# The keys each kind reads when the runtime evaluates it, as the editor makes them; `on_call` above 1 and a chance are
# kept, never made (`editable_trigger`).
TRIGGER_FIELDS: dict[str, list[str]] = {
    "always": [],
    "contacted": [],
    "on_tool": ["tool"],
    "on_keyword": ["words"],
    "mid_journey": ["turn"],
    "after_seconds": ["seconds"],
    "conditional": ["collection", "where"],
    "probability": [],
}


class TriggerOption(TypedDict):
    value: str
    label: str


def trigger_options(kinds: list[str]) -> list[TriggerOption]:
    """Each trigger kind with its label; a kind a later release adds reads by its name."""
    return [
        {"value": kind, "label": TRIGGER_KIND_LABELS.get(kind, _kind_words(kind))}
        for kind in kinds
    ]


def channel_label(channel: str | None) -> str | None:
    """A way of joining in a word or two; none for acting through the world only, whose description says it."""
    if channel is None:
        return None
    return CHANNEL_LABELS.get(channel, channel.replace("_", " ").capitalize())


Side = Literal["caller", "world"]
FallbackKind = Literal["silent", "decline", "action", "actor"]


class FallbackView(TypedDict):
    """An actor's fallback, classified: nothing, a refusal, one of its own actions, or another actor answering."""

    kind: FallbackKind
    value: str
    label: str


class ResolvedScenarioAction(TypedDict):
    """A world action an actor takes in a scenario, as stored, with when the world takes it in words."""

    action: str
    arguments: NotRequired[dict[str, object]]
    trigger: NotRequired[CallActorTrigger]
    when_label: str


class ResolvedScenarioActor(TypedDict):
    """A scenario's actor joined with the cast: who it is, and when it really comes in in that scenario."""

    id: str
    name: str
    role: str
    channel: str | None
    side: Side
    effective_trigger: CallActorTrigger
    when_label: str
    timeout_label: str | None
    instruction: str
    # Only when the scenario gives the actor actions.
    actions: NotRequired[list[ResolvedScenarioAction]]


# What each trigger key holds, as the sealed record schema types it; a stored trigger holding anything else is one
# the editor cannot make again, and reads by its kind alone.
_TRIGGER_TYPES: dict[str, type | tuple[type, ...]] = {
    "kind": str,
    "seconds": (int, float),
    "p": (int, float),
    "on_call": int,
    "turn": int,
    "tool": str,
    "collection": str,
    "words": list,
    "where": dict,
}


def _number(value: float) -> str:
    """A number as the editor writes it: 5, 2.5."""
    if not math.isfinite(value):
        return "0"
    return str(int(value)) if value == int(value) else repr(value)


def _percent(share: float) -> int:
    """A share in whole percent, a half rounded up."""
    return math.floor(share * 100 + 0.5)


def well_formed(trigger: CallActorTrigger) -> bool:
    """Whether each key the trigger holds has the type the sealed schema gives it."""
    for key, value in trigger.items():
        expected = _TRIGGER_TYPES.get(key)
        if expected is None:
            continue
        if isinstance(value, bool) or not isinstance(value, expected):
            return False
    words = trigger.get("words")
    return words is None or all(isinstance(one, str) for one in words)


def side_of(actor: ActorRecord) -> Side:
    """Whose side the actor is on: the caller and anyone the record puts on the caller's side; else the world's."""
    return (
        "caller" if is_the_caller(actor) or actor.get("side") == "caller" else "world"
    )


def trigger_of(actor: ActorRecord | None) -> CallActorTrigger:
    """The actor's own trigger; an actor without one is there from the start."""
    activation = (actor or {}).get("activation")
    trigger = activation.get("trigger") if isinstance(activation, dict) else None
    return (
        cast(CallActorTrigger, trigger)
        if isinstance(trigger, dict) and trigger
        else {"kind": "always"}
    )


def effective_trigger(
    entry: ScenarioCallActor, actor: ActorRecord | None
) -> CallActorTrigger:
    """The trigger that runs for an actor in a scenario: the scenario's own, else the actor's."""
    given = entry.get("trigger")
    return given if isinstance(given, dict) and given else trigger_of(actor)


def _kind_words(kind: object) -> str:
    return text_of(kind).replace("_", " ").capitalize() or "Its own trigger"


def cap_field(channel: str | None) -> CapField:
    """Which of an actor's own caps counts for the way it joins: lines for one who speaks, turns for one who acts
    through the world."""
    return "max_lines" if channel else "max_turns"


def when_label(
    trigger: CallActorTrigger, channel: str | None, *, scheduled: bool = False
) -> str:
    """When the actor comes in, in the words the editor shows, for the way it joins; `scheduled` says when the world
    takes one of a scenario's actions instead. A trigger whose keys are not the types the schema gives them reads by
    its kind alone."""
    kind = trigger.get("kind") or "always"
    if not well_formed(trigger):
        return _kind_words(kind)
    handoff = channel == HANDOFF_CHANNEL
    start = (
        "At the start"
        if scheduled
        else f"After the agent's {'second' if handoff else 'first'} turn"
        if channel
        else "Present from the start"
    )
    if kind == "mid_journey":
        turn = whole_of(trigger.get("turn")) or 0
        return f"After the agent's turn {max(turn, TAKES_PHONE_FROM_TURN) if handoff else turn}"
    if kind == "on_tool":
        on_call = whole_of(trigger.get("on_call")) or 1
        tool = text_of(trigger.get("tool"))
        return (
            f"On call {on_call} of {tool}"
            if on_call > 1
            else f"When the agent calls {tool}"
        )
    if kind == "on_keyword":
        return f"When the agent says {', '.join(trigger.get('words') or [])}"
    if kind == "contacted":
        return "When the agent contacts them"
    if kind == "after_seconds":
        return f"{_number(real_of(trigger.get('seconds')) or 0.0)} s after the start"
    if kind == "conditional":
        where = ", ".join(
            f"{field} {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
            for field, value in (trigger.get("where") or {}).items()
        )
        collection = text_of(trigger.get("collection"))
        return (
            f"Once a record{f' in {collection}' if collection else ''}"
            f"{f' with {where}' if where else ''} exists"
        )
    if kind == "probability":
        # A chance left out is the runtime's default, every call.
        share = real_of(trigger.get("p"))
        return f"In about {_percent(1.0 if share is None else share)}% of calls, {start[0].lower()}{start[1:]}"
    if kind != "always":
        # A kind a later release adds reads by its own name rather than as something it is not.
        return _kind_words(kind)
    return "Answers the call" if channel == IVR_CHANNEL and not scheduled else start


def fallback_of(
    actor: ActorRecord, names: dict[str, str], *, default: str = "silent"
) -> FallbackView:
    """What happens when the actor does not answer: one of its own actions is taken, or another actor answers.
    `default` is the fallback the sealed schema gives a record that sets none."""
    activation = actor.get("activation")
    value = (
        text_of(activation.get("fallback") if isinstance(activation, dict) else None)
        or default
    )
    if value == "silent":
        return {"kind": "silent", "value": value, "label": FALLBACK_WORDS[value]}
    if value == "decline":
        return {"kind": "decline", "value": value, "label": FALLBACK_WORDS[value]}
    if value in (actor.get("permitted_actions") or []) or value not in names:
        return {"kind": "action", "value": value, "label": f"{value} is taken for them"}
    return {"kind": "actor", "value": value, "label": f"{names[value]} answers instead"}


def timeout_label(
    actor: ActorRecord, names: dict[str, str], *, default_fallback: str = "silent"
) -> str | None:
    """How long the actor waits and what happens after; none when it never waits."""
    activation = actor.get("activation")
    seconds = real_of(
        activation.get("timeout_seconds") if isinstance(activation, dict) else None
    )
    if not seconds:
        return None
    fallback = fallback_of(actor, names, default=default_fallback)
    then = {
        "silent": "nothing is sent",
        "decline": "it declines",
        "actor": f"{names.get(fallback['value'], fallback['value'])} answers instead",
        "action": f"{fallback['value']} is taken for it",
    }[fallback["kind"]]
    return f"Waits {_number(seconds)} s, then {then}"


def trigger_kinds(rules: ActorRules, channel: str | None) -> list[str]:
    """The trigger kinds an actor joining this way can be given: what its runtime evaluates, of the kinds the sealed
    record schema allows (all of them when it lists none)."""
    schema = (rules.get("records") or {}).get("actors.json") or {}
    kind = (
        ((schema.get("$defs") or {}).get("Trigger") or {}).get("properties") or {}
    ).get("kind") or {}
    allowed = kind.get("enum")
    runs = SPEAKING_TRIGGERS if channel else WORLD_TRIGGERS
    return [one for one in runs if not isinstance(allowed, list) or one in allowed]


def editable_trigger(
    trigger: CallActorTrigger, channel: str | None, kinds: list[str]
) -> bool:
    """Whether the editor can make this trigger again: well formed, a kind it offers for the way the actor joins,
    and not a later call of a tool."""
    if not well_formed(trigger):
        return False
    kind = trigger.get("kind") or "always"
    return kind in kinds and not (
        kind == "on_tool" and (whole_of(trigger.get("on_call")) or 1) > 1
    )


def resolved_scenario_actors(
    entries: list[ScenarioCallActor], cast: list[ActorRecord]
) -> list[ResolvedScenarioActor]:
    """Each actor a scenario includes, resolved against the cast: its name, role and way of joining, the trigger that
    runs (the scenario's own beats the actor's) and its sentences, and the scenario's instruction as stored. An actor
    missing from the cast reads by its id."""
    by_id = {str(one.get("id") or ""): one for one in cast}
    names = {key: str(one.get("name") or key) for key, one in by_id.items()}
    resolved: list[ResolvedScenarioActor] = []
    for entry in entries:
        actor_id = str(entry.get("id") or "")
        actor = by_id.get(actor_id)
        record: ActorRecord = actor if actor is not None else {"id": actor_id}
        channel = text_of(record.get("channel")) or None
        trigger = effective_trigger(entry, actor)
        resolved.append(
            {
                "id": actor_id,
                "name": names.get(actor_id, actor_id),
                "role": text_of(record.get("role")),
                "channel": channel,
                "side": side_of(record),
                "effective_trigger": trigger,
                "when_label": when_label(trigger, channel),
                "timeout_label": timeout_label(record, names)
                if actor is not None
                else None,
                "instruction": text_of(entry.get("instruction")),
            }
        )
        actions = entry.get("actions")
        if isinstance(actions, list):
            resolved[-1]["actions"] = _resolved_actions(list(actions))
    return resolved


def _resolved_actions(actions: list[object]) -> list[ResolvedScenarioAction]:
    """Each action as stored, with when the world takes it: its own trigger, at the start when it has none. An entry
    that is not an action (written elsewhere) is left out."""
    out: list[ResolvedScenarioAction] = []
    for one in actions:
        if not isinstance(one, dict):
            continue
        trigger = one.get("trigger")
        typed: CallActorTrigger = (
            cast(CallActorTrigger, trigger)
            if isinstance(trigger, dict) and trigger
            else {"kind": "always"}
        )
        out.append(
            cast(
                ResolvedScenarioAction,
                {**one, "when_label": when_label(typed, None, scheduled=True)},
            )
        )
    return out
