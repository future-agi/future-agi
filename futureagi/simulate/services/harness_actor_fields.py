"""An actor's record as a read shows it: each field typed, with the value a field the record leaves out reads as."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict, cast

from simulate.services.harness_actor_labels import Side, side_of, trigger_of
from simulate.services.harness_actor_rules import (
    ACTORS_FILE,
    ActorRecord,
    ActorRules,
    RecordSource,
    mapping_of,
    real_of,
    text_of,
    texts_of,
    whole_of,
)
from simulate.types import CallActorTrigger

# What an actor may agree to, keeps to itself, hands over on and is done at, as its record's `constraints` holds them.
CONSTRAINT_FIELDS = ("may_agree", "private", "escalate_when", "stop_when")


# The persona fields the editor reads, each as text, but for the languages it speaks and whether it switches.
PERSONA_TEXT_FIELDS = (
    "name",
    "gender",
    "age_group",
    "accent",
    "occupation",
    "location",
    "personality",
    "communication_style",
    "initial_message",
)


TRIGGER_FIELDS = (
    "kind",
    "seconds",
    "tool",
    "on_call",
    "p",
    "turn",
    "words",
    "collection",
    "where",
)


class ActorPersona(TypedDict, total=False):
    name: str
    gender: str
    age_group: str
    accent: str
    occupation: str
    location: str
    personality: str
    communication_style: str
    initial_message: str
    languages: list[str]
    multilingual: bool


class ActorConstraints(TypedDict):
    may_agree: list[str]
    private: list[str]
    escalate_when: list[str]
    stop_when: list[str]


class ResponseTiming(TypedDict):
    kind: Literal["immediate", "delayed"]
    seconds: float


class ActorBehaviour(TypedDict):
    style: str
    response_timing: ResponseTiming


class ActorActivation(TypedDict):
    trigger: CallActorTrigger
    timeout_seconds: float
    fallback: str
    # A world action's own arguments, shaped by that action; absent when the fallback takes none.
    fallback_arguments: NotRequired[dict[str, object]]


class ActorProvenance(TypedDict):
    origin: str
    by: str | None
    fields: dict[str, str]


class KnowledgeEntry(TypedDict, total=False):
    """One thing an actor is given to read: written facts, files, or a record collection (with a filter)."""

    kind: str
    text: str
    paths: list[str]
    store: str
    collection: str
    where: dict[str, object]


class RecordDefaults(TypedDict):
    """What a field the record leaves out reads as, from the sealed record schema's defaults."""

    kind: str
    play: str
    surface: str
    fallback: str


def record_defaults(rules: ActorRules) -> RecordDefaults:
    """The defaults the sealed record schema declares for the fields a read fills in."""
    schema = rules["records"][ACTORS_FILE]
    properties = schema.get("properties") or {}
    activation = ((schema.get("$defs") or {}).get("Activation") or {}).get(
        "properties"
    ) or {}

    def default(block: dict[str, Any], name: str, fallback: str) -> str:
        return text_of((block.get(name) or {}).get("default")) or fallback

    return {
        "kind": default(properties, "kind", "person"),
        "play": default(properties, "play", "mocked"),
        "surface": default(properties, "surface", ""),
        "fallback": default(activation, "fallback", "silent"),
    }


def typed_knowledge(actor: ActorRecord) -> list[KnowledgeEntry]:
    """The stored knowledge entries in order, each with the keys a read types; any other key stays in the record."""
    return [
        typed_entry(one)
        for one in actor.get("knowledge") or []
        if isinstance(one, dict)
    ]


def typed_entry(entry: dict[str, Any]) -> KnowledgeEntry:
    """One knowledge entry with the keys a read types."""
    one: dict[str, object] = {
        name: entry[name]
        for name in ("kind", "text", "store", "collection")
        if isinstance(entry.get(name), str)
    }
    if isinstance(entry.get("paths"), list):
        one["paths"] = texts_of(entry["paths"])
    if isinstance(entry.get("where"), dict):
        one["where"] = entry["where"]
    return cast(KnowledgeEntry, one)


class ActorFields(TypedDict):
    """An actor's record fields as a read shows them: typed, with the defaults a field it does not set reads as."""

    id: str
    name: str
    role: str
    kind: str
    goal: str
    play: str
    prompt: str
    surface: str
    model: str | None
    side: Side
    channel: str | None
    parent: str | None
    on_behalf_of: str | None
    identities: dict[str, str]
    persona: ActorPersona | None
    loudness_ratio: float | None
    constraints: ActorConstraints
    behaviour: ActorBehaviour
    permitted_actions: list[str]
    activation: ActorActivation
    max_turns: int | None
    max_lines: int | None
    provenance: ActorProvenance | None
    knowledge: list[KnowledgeEntry]


def written_fact(entry: object) -> bool:
    """A fact the editor shows: one with text. Any other knowledge entry is kept where it is, untouched."""
    text = mapping_of(entry).get("text")
    return (
        mapping_of(entry).get("kind") == "facts"
        and isinstance(text, str)
        and bool(text.strip())
    )


def record_key(entry: dict[str, Any]) -> str:
    return f"{entry.get('store') or ''}.{entry.get('collection') or ''}"


def _persona(value: object) -> ActorPersona | None:
    if not isinstance(value, dict):
        return None
    persona: dict[str, object] = {
        name: value[name]
        for name in PERSONA_TEXT_FIELDS
        if isinstance(value.get(name), str)
    }
    if isinstance(value.get("languages"), list):
        persona["languages"] = texts_of(value["languages"])
    if isinstance(value.get("multilingual"), bool):
        persona["multilingual"] = value["multilingual"]
    return cast(ActorPersona, persona)


def _trigger(value: object) -> CallActorTrigger:
    """The stored trigger with only the keys a trigger has: a missing key stays missing."""
    stored = mapping_of(value)
    return cast(
        CallActorTrigger, {key: stored[key] for key in TRIGGER_FIELDS if key in stored}
    ) or {"kind": "always"}


def _activation(actor: ActorRecord, defaults: RecordDefaults) -> ActorActivation:
    stored = mapping_of(actor.get("activation"))
    activation: ActorActivation = {
        "trigger": _trigger(trigger_of(actor)),
        "timeout_seconds": real_of(stored.get("timeout_seconds")) or 0.0,
        "fallback": text_of(stored.get("fallback")) or defaults["fallback"],
    }
    if isinstance(stored.get("fallback_arguments"), dict):
        activation["fallback_arguments"] = stored["fallback_arguments"]
    return activation


def _behaviour(actor: ActorRecord) -> ActorBehaviour:
    stored = mapping_of(actor.get("behaviour"))
    timing = mapping_of(stored.get("response_timing"))
    return {
        "style": text_of(stored.get("style")),
        "response_timing": {
            "kind": "delayed" if timing.get("kind") == "delayed" else "immediate",
            "seconds": real_of(timing.get("seconds")) or 0.0,
        },
    }


def _provenance(value: object) -> ActorProvenance | None:
    if not isinstance(value, dict):
        return None
    fields = mapping_of(value.get("fields"))
    return {
        "origin": text_of(value.get("origin")),
        "by": text_of(value.get("by")) or None,
        "fields": {str(name): str(source) for name, source in fields.items()},
    }


def knowledge_of(actor: ActorRecord) -> tuple[list[str], list[str], list[RecordSource]]:
    """The written facts, the files and the record collections an actor's knowledge names, in order."""
    knowledge = [one for one in actor.get("knowledge") or [] if isinstance(one, dict)]
    facts = [str(one["text"]) for one in knowledge if written_fact(one)]
    files = [
        str(path)
        for one in knowledge
        if one.get("kind") == "files"
        for path in texts_of(one.get("paths"))
    ]
    records: list[RecordSource] = [
        {
            "store": text_of(one.get("store")),
            "collection": text_of(one.get("collection")),
        }
        for one in knowledge
        if one.get("kind") == "records"
    ]
    return facts, files, records


def typed_fields(actor: ActorRecord, defaults: RecordDefaults) -> ActorFields:
    """The record fields as a read shows them; a write compares what it is sent with these."""
    constraints = mapping_of(actor.get("constraints"))
    return {
        "id": str(actor.get("id") or ""),
        "name": text_of(actor.get("name")),
        "role": text_of(actor.get("role")),
        "kind": text_of(actor.get("kind")) or defaults["kind"],
        "goal": text_of(actor.get("goal")),
        "play": text_of(actor.get("play")) or defaults["play"],
        "prompt": text_of(actor.get("prompt")),
        "surface": text_of(actor.get("surface")) or defaults["surface"],
        "model": text_of(actor.get("model")) or None,
        "side": side_of(actor),
        "channel": text_of(actor.get("channel")) or None,
        "parent": text_of(actor.get("parent")) or None,
        "on_behalf_of": text_of(actor.get("on_behalf_of")) or None,
        "identities": {
            str(key): str(value)
            for key, value in mapping_of(actor.get("identities")).items()
        },
        "persona": _persona(actor.get("persona")),
        "loudness_ratio": real_of(actor.get("loudness_ratio")),
        "constraints": {
            "may_agree": texts_of(constraints.get("may_agree")),
            "private": texts_of(constraints.get("private")),
            "escalate_when": texts_of(constraints.get("escalate_when")),
            "stop_when": texts_of(constraints.get("stop_when")),
        },
        "behaviour": _behaviour(actor),
        "permitted_actions": texts_of(actor.get("permitted_actions")),
        "activation": _activation(actor, defaults),
        "knowledge": typed_knowledge(actor),
        "max_turns": whole_of(actor.get("max_turns")),
        "max_lines": whole_of(actor.get("max_lines")),
        "provenance": _provenance(actor.get("provenance")),
    }
