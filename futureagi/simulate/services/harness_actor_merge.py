"""How an edit becomes the stored record: a field-level patch laid over it, or a whole record sent back as it was
read, and the id a new actor is given."""

from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from typing import Any, Literal, NotRequired, TypedDict, cast

from simulate.services.harness_actor_fields import (
    CONSTRAINT_FIELDS,
    PERSONA_TEXT_FIELDS,
    RecordDefaults,
    knowledge_of,
    record_key,
    typed_entry,
    typed_fields,
    written_fact,
)
from simulate.services.harness_actor_labels import cap_field
from simulate.services.harness_actor_rules import (
    ACTORS_FILE,
    ActorRecord,
    ActorRules,
    RecordSource,
    mapping_of,
    text_of,
)
from simulate.types import CallActorTrigger


class ResponseTimingChange(TypedDict):
    kind: Literal["immediate", "delayed"]
    # Null or left out on a delay: it answers straight away.
    seconds: NotRequired[float | None]


class ActorChanges(TypedDict, total=False):
    """A field-level patch to one actor: only the fields sent change, every other field of the stored record stays."""

    name: str
    role: str
    # Whose side the actor is on; given on a create.
    side: Literal["caller", "world"]
    channel: str | None
    gender: str
    age_group: str
    accent: str
    loudness_ratio: float | None
    kind: str
    parent: str | None
    goal: str
    may_agree: list[str]
    private: list[str]
    escalate_when: list[str]
    stop_when: list[str]
    facts: list[str]
    files: list[str]
    records: list[RecordSource]
    style: str
    response_timing: ResponseTimingChange
    permitted_actions: list[str]
    play: str
    trigger: CallActorTrigger
    # Null: it never waits.
    timeout_seconds: float | None
    fallback: str
    fallback_arguments: dict[str, object] | None
    model: str | None
    max_turns: int | None
    max_lines: int | None


# The persona fields an actor's voice is picked from.
VOICE_FIELDS = ("gender", "age_group", "accent")


# The patch fields that live in the record's `activation`.
ACTIVATION_FIELDS = ("trigger", "timeout_seconds", "fallback", "fallback_arguments")


# The record path each patch field is written into.
_RECORD_FIELD = {
    **{name: f"persona.{name}" for name in VOICE_FIELDS},
    **{name: f"constraints.{name}" for name in CONSTRAINT_FIELDS},
    **dict.fromkeys(("facts", "files", "records"), "knowledge"),
    **{name: f"behaviour.{name}" for name in ("style", "response_timing")},
    **{name: f"activation.{name}" for name in ACTIVATION_FIELDS},
}


def touched_fields(changes: ActorChanges) -> set[str]:
    """The record paths a patch writes (`activation.timeout_seconds`, `name`): what a check of the edit is about."""
    return {_RECORD_FIELD.get(name, name) for name in changes}


# An id made from a name keeps room for a `_<n>` that makes it unique.
_ID_BASE_CHARS = 60
# How much of a name's hash an id made from a name in another script carries.
_ID_HASH_CHARS = 8


def id_from(name: str, taken: set[str]) -> str:
    """An id made from a name, unique among `taken` (`sam_lee`, then `sam_lee_2`): lower case, accents dropped, every
    run of other characters one underscore. A name with no letter an id can hold (one in another script) is `actor_`
    and a short hash of the name, so it is the same each time and two such names differ."""
    folded = unicodedata.normalize("NFKD", (name or "").strip().lower())
    plain = "".join(
        one for one in folded if not unicodedata.category(one).startswith("M")
    )
    base = re.sub(r"[^a-z0-9]+", "_", plain).strip("_")[:_ID_BASE_CHARS]
    if not base:
        named = (name or "").strip()
        base = (
            f"actor_{hashlib.sha256(named.encode('utf-8')).hexdigest()[:_ID_HASH_CHARS]}"
            if named
            else "actor"
        )
    made, count = base, 2
    while made in taken:
        made, count = f"{base}_{count}", count + 1
    return made


def _lines(values: list[str]) -> list[str]:
    """Each line typed, trimmed, without the empty ones."""
    return [
        line.strip() for value in values for line in value.split("\n") if line.strip()
    ]


def _with_facts(
    knowledge: list[dict[str, Any]], typed: list[str]
) -> list[dict[str, Any]]:
    """The knowledge with these lines as its written facts, in the places facts held (any extra after the rest), so
    other knowledge keeps its order. A fact whose text is still there, over as many lines as it spans, stays the same
    entry with its other keys; the longest are matched first."""
    unused = sorted(
        (one for one in knowledge if written_fact(one)),
        key=lambda one: len(_lines([one["text"]])),
        reverse=True,
    )
    entries: list[dict[str, Any]] = []
    at = 0
    while at < len(typed):
        same = next(
            (
                one
                for one in unused
                if all(
                    at + n < len(typed) and typed[at + n] == line
                    for n, line in enumerate(_lines([one["text"]]))
                )
            ),
            None,
        )
        if same is not None:
            unused.remove(same)
            entries.append(same)
            at += len(_lines([same["text"]]))
        else:
            entries.append({"kind": "facts", "text": typed[at]})
            at += 1
    placed = []
    for one in knowledge:
        if not written_fact(one):
            placed.append(one)
        elif entries:
            placed.append(entries.pop(0))
    return [*placed, *entries]


def _with_readable(
    knowledge: list[dict[str, Any]], files: list[str], records: list[RecordSource]
) -> list[dict[str, Any]]:
    """The knowledge with these files and record collections in place of those it named; facts and any other entry
    stay where they were. The files are one entry, keeping the first stored files entry's other keys; a record
    collection still picked keeps its entry (its `where`)."""
    before = {record_key(one): one for one in knowledge if one.get("kind") == "records"}
    first_files = next((one for one in knowledge if one.get("kind") == "files"), {})
    return [
        *(one for one in knowledge if one.get("kind") not in ("files", "records")),
        *([{**first_files, "kind": "files", "paths": files}] if files else []),
        *(
            before.get(record_key(dict(one)))
            or {
                "kind": "records",
                "store": one["store"],
                "collection": one["collection"],
            }
            for one in records
        ),
    ]


# What a created actor holds for each field the patch leaves out.
CREATE_DEFAULTS: dict[str, object] = {
    "kind": "person",
    "play": "mocked",
    "goal": "",
    "constraints": {
        "may_agree": [],
        "private": [],
        "escalate_when": [],
        "stop_when": [],
    },
    "knowledge": [],
    "behaviour": {"style": "", "response_timing": {"kind": "immediate", "seconds": 0}},
    "permitted_actions": [],
    "activation": {
        "trigger": {"kind": "always"},
        "timeout_seconds": 0,
        "fallback": "silent",
    },
}


def created_defaults(rules: ActorRules) -> ActorRecord:
    """The defaults of a created actor, each only where the sealed record schema declares the field."""
    declared = (rules["records"][ACTORS_FILE].get("properties") or {}).keys()
    return cast(
        ActorRecord,
        copy.deepcopy(
            {key: value for key, value in CREATE_DEFAULTS.items() if key in declared}
        ),
    )


# What a read adds to each actor beside its record; a whole record sent back with them never stores them.
RESPONSE_ONLY_FIELDS = (
    "removable",
    "removal_blocked_by",
    "locked_fields",
    "reaches",
    "fallback",
    "permissions",
    "editable_trigger",
    "when_label",
    "timeout_label",
    "scenarios",
    "facts",
    "files",
    "records",
)
# The keys a read shows inside these blocks; any other stored key in them is kept when the block is sent edited.
_SHOWN_IN = {
    "persona": (*PERSONA_TEXT_FIELDS, "languages", "multilingual"),
    "activation": ("trigger", "timeout_seconds", "fallback", "fallback_arguments"),
}


def _sent_knowledge(sent: list[object], stored: list[object]) -> list[object]:
    """Knowledge sent back: each entry that reads exactly as a stored entry does is that stored entry, its other keys
    kept; a new or changed entry is the client's."""
    unused = [one for one in stored if isinstance(one, dict)]
    out: list[object] = []
    for entry in sent:
        same = next(
            (
                one
                for one in unused
                if isinstance(entry, dict) and typed_entry(one) == entry
            ),
            None,
        )
        if same is None:
            out.append(entry)
        else:
            unused.remove(same)
            out.append(same)
    return out


def replaced_record(
    sent: ActorRecord, stored: ActorRecord, defaults: RecordDefaults
) -> ActorRecord:
    """A whole record sent to replace the stored one, kept lossless for a client that sends back what it read.

    A field sent back exactly as read keeps its stored value; stored fields a read never shows are kept."""
    shown: dict[str, object] = dict(typed_fields(stored, defaults))
    kept: dict[str, Any] = dict(stored)
    record: dict[str, Any] = {}
    for key, value in sent.items():
        if key in RESPONSE_ONLY_FIELDS:
            continue
        if key in shown and value == shown[key]:
            if key in kept:
                record[key] = kept[key]
            continue
        if key == "knowledge" and isinstance(value, list):
            value = _sent_knowledge(value, list(kept.get("knowledge") or []))
        hidden = _SHOWN_IN.get(key)
        if (
            hidden is not None
            and isinstance(value, dict)
            and isinstance(kept.get(key), dict)
        ):
            value = {
                **{name: one for name, one in kept[key].items() if name not in hidden},
                **value,
            }
        record[key] = value
    for key, value in kept.items():
        if key not in sent and (key not in shown or key == "knowledge"):
            record[key] = value
    return cast(ActorRecord, record)


def _merge_who(
    record: dict[str, Any], stored: ActorRecord, sent: dict[str, Any], loud: list[str]
) -> None:
    """Name, role, side and the way the actor joins; a persona name set on the record follows the name, and only a
    channel with a loudness keeps one."""
    if "name" in sent:
        record["name"] = sent["name"].strip()
        if str(mapping_of(stored.get("persona")).get("name") or "").strip():
            record["persona"] = {
                **mapping_of(stored.get("persona")),
                "name": record["name"],
            }
    if "role" in sent:
        record["role"] = sent["role"].strip()
    for name in ("side", "kind", "goal"):
        if name in sent:
            record[name] = sent[name]
    if "parent" in sent:
        record["parent"] = sent["parent"] or None
    if "channel" in sent:
        if sent["channel"]:
            record["channel"] = sent["channel"]
        else:
            record.pop("channel", None)
    _move_cap(record, stored, sent)
    if ("channel" in sent or "loudness_ratio" in sent) and (
        record.get("channel") or stored.get("loudness_ratio") is not None
    ):
        record["loudness_ratio"] = (
            sent.get("loudness_ratio", stored.get("loudness_ratio"))
            if record.get("channel") in loud
            else None
        )


def _move_cap(
    record: dict[str, Any], stored: ActorRecord, sent: dict[str, Any]
) -> None:
    """A change of the way the actor joins moves its own cap to the field that counts there (lines for one who speaks,
    turns for one in the world), so the cap is never kept where nothing reads or shows it. A cap sent in this edit is
    the edit's."""
    before = cap_field(text_of(stored.get("channel")) or None)
    after = cap_field(text_of(record.get("channel")) or None)
    if "channel" not in sent or before == after or before in sent:
        return
    value = record.pop(before, None)
    if value is not None and after not in sent:
        record[after] = value


def _merge_voice(
    record: dict[str, Any], stored: ActorRecord, sent: dict[str, Any]
) -> None:
    """The persona fields the voice is picked from; one sent empty is removed."""
    if not any(name in sent for name in VOICE_FIELDS):
        return
    before = mapping_of(stored.get("persona"))
    persona = dict(mapping_of(record.get("persona")))
    for name in VOICE_FIELDS:
        value = sent.get(name, before.get(name) or "")
        if value:
            persona[name] = value
        else:
            persona.pop(name, None)
    if record.get("persona") is not None or persona:
        record["persona"] = persona


def _merge_knowledge(
    record: dict[str, Any], stored: ActorRecord, sent: dict[str, Any]
) -> None:
    """Constraints, facts, files and record collections."""
    sent_constraints = [name for name in CONSTRAINT_FIELDS if name in sent]
    if sent_constraints:
        constraints = dict(mapping_of(stored.get("constraints")))
        for name in sent_constraints:
            constraints[name] = _lines(sent[name])
        record["constraints"] = constraints
    knowledge = [one for one in stored.get("knowledge") or [] if isinstance(one, dict)]
    if "facts" in sent:
        knowledge = _with_facts(knowledge, _lines(sent["facts"]))
        record["knowledge"] = knowledge
    if "files" in sent or "records" in sent:
        _, files, records = knowledge_of(stored)
        record["knowledge"] = _with_readable(
            knowledge, sent.get("files", files), sent.get("records", records)
        )


def _merge_behaviour(
    record: dict[str, Any], stored: ActorRecord, sent: dict[str, Any]
) -> None:
    """How the actor talks and how soon it answers."""
    if "style" not in sent and "response_timing" not in sent:
        return
    behaviour = dict(mapping_of(stored.get("behaviour")))
    if "style" in sent:
        behaviour["style"] = sent["style"]
    if "response_timing" in sent:
        timing = sent["response_timing"]
        # A delay left empty is none: it answers straight away.
        behaviour["response_timing"] = (
            {"kind": "delayed", "seconds": float(timing["seconds"])}
            if timing["kind"] == "delayed" and timing.get("seconds") is not None
            else {"kind": "immediate", "seconds": 0}
        )
    record["behaviour"] = behaviour


def _merge_activation(
    record: dict[str, Any], stored: ActorRecord, sent: dict[str, Any]
) -> None:
    """When it comes in, how long it waits, and its fallback; a new fallback drops the old one's arguments unless
    they are sent too."""
    if not any(name in sent for name in ACTIVATION_FIELDS):
        return
    before = mapping_of(stored.get("activation"))
    activation = dict(before)
    if "trigger" in sent:
        activation["trigger"] = dict(sent["trigger"])
    if "timeout_seconds" in sent:
        activation["timeout_seconds"] = float(sent["timeout_seconds"] or 0)
    if "fallback" in sent:
        activation["fallback"] = sent["fallback"]
        if (
            sent["fallback"] != (before.get("fallback") or "silent")
            and "fallback_arguments" not in sent
        ):
            activation.pop("fallback_arguments", None)
    if "fallback_arguments" in sent:
        if sent["fallback_arguments"]:
            activation["fallback_arguments"] = sent["fallback_arguments"]
        else:
            activation.pop("fallback_arguments", None)
    record["activation"] = activation


def _merge_play(record: dict[str, Any], sent: dict[str, Any]) -> None:
    """What it may do, how it is played, its model and its own limits; null or empty clears the model and a limit."""
    if "permitted_actions" in sent:
        record["permitted_actions"] = list(sent["permitted_actions"])
    if "play" in sent:
        record["play"] = sent["play"]
    for name in ("model", "max_turns", "max_lines"):
        if name not in sent:
            continue
        value = sent[name]
        if value and (name == "model" or value > 0):
            record[name] = value
        else:
            record.pop(name, None)


def merged_record(
    stored: ActorRecord, changes: ActorChanges, *, loud_channels: list[str]
) -> ActorRecord:
    """The stored record with this patch laid over it: only the fields sent change, and every field the patch does
    not name (a later release's included) stays as stored."""
    record: dict[str, Any] = copy.deepcopy(dict(stored))
    sent: dict[str, Any] = dict(changes)
    _merge_who(record, stored, sent, loud_channels)
    _merge_voice(record, stored, sent)
    _merge_knowledge(record, stored, sent)
    _merge_behaviour(record, stored, sent)
    _merge_activation(record, stored, sent)
    _merge_play(record, sent)
    return cast(ActorRecord, record)
