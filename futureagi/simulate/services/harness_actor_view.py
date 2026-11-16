"""What one actor is given, what it may do, and what it never sees, optionally in one scenario."""

from __future__ import annotations

import re
from typing import Any, TypedDict

from simulate.models import HostedHarnessJob
from simulate.models.persona import Persona
from simulate.services.harness_actor_rules import (
    CALLER,
    actor_not_found,
    actor_rules,
    cast_records,
    speaks,
)
from simulate.services.harness_versioning import drafts
from simulate.services.harness_versioning.versions import suite_documents
from simulate.services.hosted_harness import HostedHarnessError


class Seen(TypedDict):
    input: str
    value: Any
    source: str
    # The value as one line a person reads: lists joined, fields as "field: value", never raw JSON.
    text: str
    # What the input is, in words; its own name when it has none here.
    label: str


# Each input an actor's brief is built from, in words.
SEEN_LABELS = {
    "actor.name": "Name",
    "actor.role": "Role",
    "actor.persona": "Persona",
    "actor.goal": "Aim in this conversation",
    "actor.constraints": "What they may agree to and keep to themselves",
    "actor.behaviour": "How they behave",
    "actor.prompt": "Their prompt",
    "actor.surface": "How they reach the agent",
    "actor.knowledge": "What they know",
    "scenario.instruction": "What they do in this scenario",
    "scenario.knowledge": "What they know in this scenario",
    "caller.situation": "The caller's situation, without the caller's own details",
}


def readable(value: object) -> str:
    """A view value as a person reads it: lists joined, a typed entry as its content (a fact's text), a `kind` tag as
    its value alone, other fields as "field: value"."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(one for one in (readable(item) for item in value) if one)
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
        pairs = [(str(key), readable(one)) for key, one in value.items()]
        return " · ".join(
            text if key == "kind" else f"{key.replace('_', ' ')}: {text}"
            for key, text in pairs
            if text
        )
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def seen_text(field: str, value: object) -> str:
    """One seen value as its line; a persona's voice fields read as values, its label already saying what they are."""
    if field != "persona" or not isinstance(value, dict):
        return readable(value)
    rest = {key: one for key, one in value.items() if key not in _PERSONA_LEAD}
    gender = str(value.get("gender") or "")
    lead = [
        str(value.get("name") or ""),
        _GENDER_LABELS.get(gender, gender),
        str(value.get("age_group") or ""),
        str(value.get("accent") or ""),
    ]
    return " · ".join(one for one in [*lead, readable(rest)] if one)


# The persona fields a seen persona leads with, as values.
_PERSONA_LEAD = ("name", "gender", "age_group", "accent")
_GENDER_LABELS = {
    str(value): str(label) for value, label in Persona.GenderChoices.choices
}


class OthersPrivate(TypedDict):
    """The fields a number of other actors each keep to themselves, as words."""

    fields: list[str]
    count: int


class NeverSees(TypedDict):
    scenario_fields: list[str]
    files: list[str]
    other_actors: list[str]
    # The grader's scenario fields and files as words, once each.
    grader: list[str]
    # Other actors' private fields, one line per set of fields.
    others: list[OthersPrivate]


class ActorView(TypedDict):
    actor_id: str
    permitted_actions: list[str]
    sees: list[Seen]
    never_sees: NeverSees
    played_from_view: bool
    unused_reason: str | None


def never_sees(
    scenario_fields: list[str], files: list[str], other_actors: list[str]
) -> NeverSees:
    """What an actor never sees, as sent and grouped to read: the grader's material as words, once each (a file
    without its extension, so `sub_goals.json` and `sub_goals` are one line), and one line per set of fields the other
    actors each keep to themselves."""
    grader = dict.fromkeys(
        [name.replace("_", " ") for name in scenario_fields]
        + [re.sub(r"[-_]", " ", re.sub(r"\.[^.]+$", "", name)) for name in files]
    )
    by_actor: dict[str, list[str]] = {}
    for one in other_actors:
        owner, _, field = one.rpartition(".")
        by_actor.setdefault(owner, []).append(field.replace("_", " "))
    groups: dict[tuple[str, ...], int] = {}
    for fields in by_actor.values():
        groups[tuple(fields)] = groups.get(tuple(fields), 0) + 1
    return {
        "scenario_fields": scenario_fields,
        "files": files,
        "other_actors": other_actors,
        "grader": list(grader),
        "others": [
            {"fields": list(fields), "count": count} for fields, count in groups.items()
        ],
    }


def actor_view(
    environment: HostedHarnessJob, actor_id: str, *, scenario_key: str | None = None
) -> ActorView:
    """What one actor is given, with each value's source, what it may do, and what it never sees; optionally in one
    scenario. Raises `actor_not_found` or `scenario_not_found` (404)."""
    sides = drafts.edit_sides(environment)
    environment_files, suite_files = sides["environment"], sides["suite"]
    actors = cast_records(environment_files)
    actor = next((one for one in actors if one.get("id") == actor_id), None)
    if actor is None:
        raise actor_not_found(actor_id)
    scenario = None
    if scenario_key is not None:
        scenario = next(
            (
                one
                for one in suite_documents(suite_files)
                if (one.get("scenario_key") or one.get("name")) == scenario_key
            ),
            None,
        )
        if scenario is None:
            raise HostedHarnessError(
                "scenario_not_found",
                f"{scenario_key} is not a scenario of this environment",
                status_code=404,
            )
    entry = next(
        (
            one
            for one in (scenario or {}).get("actors") or []
            if isinstance(one, dict) and one.get("id") == actor_id
        ),
        None,
    )
    rules = actor_rules(environment_files)
    sees: list[Seen] = []
    # The same rules the runtime builds the actor's brief from (ALK `actor_brief`), sealed with the bundle.
    for source, field in rules["brief_inputs"]:
        if source == "actor":
            value = actor.get(field)
        elif source == "scenario":
            value = (entry or {}).get(field) if entry is not None else None
        else:
            value = (
                str(scenario.get("instruction") or "")
                .split(rules["situation_before"])[0]
                .strip()
                # The caller has its whole situation already; anyone else is given the part before its details.
                if scenario is not None and actor_id != CALLER
                else None
            )
        if value in (None, "", [], {}):
            continue
        sees.append(
            {
                "input": f"{source}.{field}",
                "value": value,
                "source": source,
                "text": seen_text(field, value),
                "label": SEEN_LABELS.get(
                    f"{source}.{field}", field.replace("_", " ").capitalize()
                ),
            }
        )
    hidden = rules["evaluator_only"]
    others = [
        f"{one.get('name') or one.get('id')}.{name}"
        for one in actors
        if one.get("id") != actor_id
        for name in rules["private_to_actor"]
    ]
    # Played from this view when it speaks, is the caller, or decides its own world actions (live or both).
    played = (
        speaks(actor) or actor_id == CALLER or actor.get("play") in ("live", "both")
    )
    return {
        "actor_id": actor_id,
        "permitted_actions": list(actor.get("permitted_actions") or []),
        "sees": sees,
        "never_sees": never_sees(
            list(hidden["scenario_fields"]), list(hidden["files"]), others
        ),
        # Whether a run plays this actor from what it sees. One that only acts in the world takes its listed actions
        # at their triggers and is given none of it.
        "played_from_view": played,
        "unused_reason": (
            None
            if played
            else "This actor only takes the actions its scenarios give it, "
            "so nothing here is given to it during a run. "
            "Set it to decide its own actions, or to speak in the conversation, to have it play from this."
        ),
    }
