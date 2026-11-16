"""The checks an actor edit passes before it is written: the record against the sealed schema, the references between
actors, the choices this edit adds, and the actions it would share."""

from __future__ import annotations

import json
from typing import Any, TypedDict

import jsonschema

from simulate.services.harness_actor_rules import (
    ACTORS_FILE,
    PLATFORM_FIELDS,
    ActorRecord,
    ActorRules,
    actor_models,
    knowledge_sources,
    readable_file,
    speaks,
    value_at,
    world_actions,
)
from simulate.services.harness_versioning.versions import suite_documents
from simulate.services.hosted_harness import HostedHarnessError


class Problem(TypedDict):
    """One reason an edit cannot be written; `field` is the editor's field it is about, none when it is the record."""

    field: str | None
    message: str


class PermissionConflict(TypedDict):
    """An action an edit gives that exactly one other actor holds today."""

    actor: str
    action: str
    holder: str
    holder_name: str


def problem(field: str | None, message: str) -> Problem:
    return {"field": field, "message": message}


# Where a record path lives in the editor's fields, when it sits inside a block: the trigger, the fallback, the style.
_FIELD_IN_BLOCK = {
    "activation": {"trigger", "timeout_seconds", "fallback", "fallback_arguments"},
    "behaviour": {"style", "response_timing"},
    "constraints": {"may_agree", "private", "escalate_when", "stop_when"},
    "persona": {"name", "gender", "age_group", "accent"},
}


def field_at(path: str) -> str | None:
    """The editor's field a record path belongs to: `activation.trigger.tool` is the trigger, `persona.name` the name;
    none for the record as a whole."""
    parts = [one for one in path.split(".") if one]
    if not parts:
        return None
    if len(parts) > 1 and parts[1] in _FIELD_IN_BLOCK.get(parts[0], ()):
        return parts[1]
    return parts[0]


def _sharing_the_caller(
    before: ActorRecord,
    after: ActorRecord,
    suite_files: dict[str, bytes],
    rules: ActorRules,
) -> list[str]:
    """Two speakers of one name make a transcript unreadable, so an actor never shares its caller's."""
    actor_id = str(after.get("id") or "")
    problems = []
    for field in rules.get("distinct_from_caller") or []:
        value = str(after.get(field) or "").strip().lower()
        if not value or value == str(before.get(field) or "").strip().lower():
            continue
        for scenario in suite_documents(suite_files):
            included = any(
                isinstance(one, dict) and one.get("id") == actor_id
                for one in scenario.get("actors") or []
            )
            caller = str((scenario.get("persona") or {}).get(field) or "").strip()
            if included and caller.lower() == value:
                key = scenario.get("scenario_key") or scenario.get("name")
                problems.append(
                    f"{actor_id}'s {field} is the caller's in {key} ({caller}); choose another."
                )
    return problems


def new_choice_problems(
    before: ActorRecord | None,
    after: ActorRecord,
    files: dict[str, bytes],
    rules: ActorRules,
    suite_files: dict[str, bytes] | None = None,
    modality: str | None = None,
    stores: object = None,
) -> list[str]:
    """Only what this edit adds must exist here, so an actor written elsewhere can still be edited."""
    before = before or {}
    actor_id = str(after.get("id") or "")
    problems = []
    if speaks(after):
        channels = rules.get("channels") or {}
        channel = after.get("channel")
        listed = _channel_schema(rules)
        reaches = (rules.get("channel_modalities") or {}).get(str(channel))
        if channel != before.get("channel"):
            # A schema that lists the channels already names an unknown one.
            if (
                channels
                and channel not in channels
                and "enum" not in json.dumps(listed)
            ):
                problems.append(
                    f"{actor_id}'s channel {channel!r} is not one of: {', '.join(channels)}."
                )
            if modality and reaches is not None and modality not in reaches:
                problems.append(
                    f"{actor_id} cannot reach a {modality} agent through {channel}; choose another channel."
                )
        problems += _sharing_the_caller(before, after, suite_files or {}, rules)
    problems += _knowledge_problems(before, after, files, rules, stores)
    model = after.get("model")
    if model and model != before.get("model") and model not in actor_models(rules):
        problems.append(
            f"{actor_id}'s model {model!r} is not one of: {', '.join(actor_models(rules))}."
        )
    known = [one["action"] for one in world_actions(files, rules)]
    old = {
        str(one)
        for one in before.get("permitted_actions") or []
        if isinstance(one, (str, int))
    }
    given = after.get("permitted_actions")
    added = [
        str(one)
        for one in (given if isinstance(given, list) else [])
        if one not in old and one not in known
    ]
    # Read by path, so a record whose activation is not of the right shape (written elsewhere) has no tool.
    tool = value_at(after, "activation.trigger.tool")
    old_tool = value_at(before, "activation.trigger.tool")
    if isinstance(tool, str) and tool and tool != old_tool and tool not in known:
        added.append(tool)
    if added and not known:
        problems.append(
            f"{actor_id} cannot be given {', '.join(added)}: this environment has no world actions, because its tools "
            "run inside the agent. Leave them empty."
        )
    elif added:
        problems.append(
            f"{', '.join(added)} is not an action of this environment; choose from {', '.join(known)}."
        )
    return problems


def _knowledge_problems(
    before: ActorRecord,
    after: ActorRecord,
    files: dict[str, bytes],
    rules: ActorRules,
    stores: object,
) -> list[str]:
    """Files and records this edit adds to an actor's knowledge must be ones it may read here."""
    actor_id = str(after.get("id") or "")
    offered = knowledge_sources(files, rules, stores)

    def named(record: ActorRecord, kind: str) -> set[str]:
        out = set()
        for one in record.get("knowledge") or []:
            if isinstance(one, dict) and one.get("kind") == kind == "files":
                out |= {str(path) for path in one.get("paths") or []}
            elif isinstance(one, dict) and one.get("kind") == kind == "records":
                out.add(f"{one.get('store') or ''}.{one.get('collection') or ''}")
        return out

    problems = []
    added_files = sorted(named(after, "files") - named(before, "files"))
    refused = [path for path in added_files if not readable_file(path, rules)]
    if refused:
        problems.append(
            f"{actor_id} may never read {', '.join(refused)}: it is the evaluator's or the harness's own."
        )
    missing = [
        path
        for path in added_files
        if path not in refused and path not in offered["files"]
    ]
    if missing:
        problems.append(f"{', '.join(missing)} is not a file of this environment.")
    collections = {f"{one['store']}.{one['collection']}" for one in offered["records"]}
    unknown = [
        one
        for one in sorted(named(after, "records") - named(before, "records"))
        if one not in collections
    ]
    if unknown:
        problems.append(
            f"{', '.join(unknown)} is not a record collection of this environment."
        )
    return problems


def _channel_schema(rules: ActorRules) -> dict[str, Any]:
    return (rules["records"][ACTORS_FILE].get("properties") or {}).get("channel") or {}


def _reason(error: jsonschema.ValidationError) -> str:
    """An optional field allows null too, so the limit is named by the branch that is not null."""
    branches = [
        one
        for one in error.context or ()
        if not (one.validator == "type" and one.validator_value == "null")
    ]
    broken = jsonschema.exceptions.best_match(branches) if branches else error
    described = (
        broken.schema.get("description") if isinstance(broken.schema, dict) else None
    )
    if broken.validator == "pattern" and described:
        return f"{broken.instance!r} is not valid; use {described}"
    if broken.validator == "maxLength":
        return f"use at most {broken.validator_value} characters"
    if broken.validator == "enum":
        return f"{broken.instance!r} is not one of: {', '.join(map(str, broken.validator_value))}"
    if broken.validator == "minLength":
        return "it cannot be empty"
    return str(broken.message)


def record_schema(rules: ActorRules) -> dict[str, Any]:
    """The one actor record's schema, with a name required."""
    schema = rules["records"][ACTORS_FILE]
    properties = dict(schema.get("properties") or {})
    properties["name"] = {"minLength": 1, **(properties.get("name") or {})}
    return {
        **schema,
        "properties": properties,
        "required": sorted({*(schema.get("required") or []), "id", "name"}),
    }


def _record_problems(
    actor: ActorRecord, rules: ActorRules, *, new_id: bool
) -> list[tuple[str, Problem]]:
    """Each problem with the record, with the record field it sits in ("" for the record as a whole)."""
    actor_id = str(actor.get("id") or "")
    schema = record_schema(rules)
    record = dict(actor)
    problems: list[tuple[str, Problem]] = []
    extra = sorted(set(record) - set(schema["properties"]) - set(PLATFORM_FIELDS))
    if extra:
        problems.append(
            (
                "",
                problem(
                    None,
                    f"{actor_id} has no {', '.join(extra)} in {ACTORS_FILE}; remove "
                    f"{'it' if len(extra) == 1 else 'them'}.",
                ),
            )
        )
    checked = {
        name: value
        for name, value in record.items()
        if name not in extra and name not in PLATFORM_FIELDS
    }
    for error in jsonschema.Draft202012Validator(schema).iter_errors(checked):
        where = ".".join(str(part) for part in error.absolute_path)
        # An id never changes, so one already in the cast is kept as it is.
        if where == "id" and not new_id:
            continue
        # A bad id cannot name its own actor, so the sentence names the field instead.
        subject = (
            "The id"
            if where == "id"
            else f"{actor_id}'s {where}:"
            if where
            else f"{actor_id}:"
        )
        problems.append(
            (
                where.split(".")[0],
                problem(field_at(where), f"{subject} {_reason(error)}."),
            )
        )
    for path, field in (rules.get("equal_when_set") or {}).items():
        named = value_at(actor, path)
        if (
            isinstance(named, str)
            and named.strip()
            and named.strip() != actor.get(field)
        ):
            problems.append(
                (
                    field,
                    problem(
                        field,
                        f"{actor_id}'s {path} is {named.strip()!r} but its {field} is {actor.get(field)!r}; make "
                        "them the same.",
                    ),
                )
            )
    if new_id and actor_id in rules.get("reserved_ids", []):
        problems.append(
            (
                "id",
                problem("id", f"{actor_id!r} is reserved; give the actor another id."),
            )
        )
    return problems


def _names(value: object) -> list[str]:
    """The action names a record lists; anything else in the list is a shape its own check names."""
    return (
        [one for one in value if isinstance(one, str)]
        if isinstance(value, list)
        else []
    )


def permission_conflicts(
    before: list[ActorRecord], edited: ActorRecord
) -> list[PermissionConflict]:
    """Each action this edit gives the actor that exactly one other actor holds today: that one's alone, so giving it
    to a second is a conflict to show before saving. An action two or more already hold is shared, not reserved.
    """
    actor_id = str(edited.get("id") or "")
    own = next((one for one in before if one.get("id") == actor_id), None) or {}
    had = set(_names(own.get("permitted_actions")))
    conflicts: list[PermissionConflict] = []
    for action in _names(edited.get("permitted_actions")):
        if action in had:
            continue
        holders = [
            one
            for one in before
            if one.get("id") != actor_id
            and action in (one.get("permitted_actions") or [])
        ]
        if len(holders) == 1:
            holder = holders[0]
            conflicts.append(
                {
                    "actor": actor_id,
                    "action": action,
                    "holder": str(holder.get("id") or ""),
                    "holder_name": str(holder.get("name") or holder.get("id") or ""),
                }
            )
    return conflicts


def actor_invalid(problems: list[Problem]) -> HostedHarnessError:
    """An edit that cannot be written, each problem about its editor field as the check names it."""
    return HostedHarnessError(
        "actor_invalid",
        " ".join(one["message"] for one in problems),
        status_code=400,
        details={"problems": problems},
    )


def limits_invalid(problems: list[Problem]) -> HostedHarnessError:
    """Limits that cannot be written, each problem about the limit it names."""
    return HostedHarnessError(
        "limits_invalid",
        "Fix the limits marked before saving.",
        status_code=400,
        details={"problems": problems},
    )


def conflict_error(conflicts: list[PermissionConflict]) -> HostedHarnessError:
    sentences = [
        f"{one['action']} is held only by {one['holder_name']} today; giving it to {one['actor']} lets both take it."
        for one in conflicts
    ]
    return HostedHarnessError(
        "permission_conflict",
        " ".join(sentences)
        + " Remove it, or confirm both should have it by listing it in share_actions.",
        status_code=409,
        details={"conflicts": conflicts},
    )


def broken_references(
    actor: ActorRecord, known: set[str], rules: ActorRules
) -> list[tuple[str, Any]]:
    actor_id = str(actor.get("id") or "")
    # Fields that may name one of the actor's own actions instead (a fallback taken for it); every release has
    # allowed that for the fallback, so rules sealed before it was declared get the same.
    own = set(rules.get("own_actions", ["activation.fallback"]))
    actions = set(_names(actor.get("permitted_actions")))
    # A link is an id; anything else is a shape the record's own check names.
    return [
        (path, other)
        for path, also in (rules.get("references") or {}).items()
        if isinstance(other := value_at(actor, path), str)
        and other not in also
        and other not in known - {actor_id}
        and not (path in own and other in actions)
    ]


def _in_a_cycle(actor_id: str, actors: list[ActorRecord], path: str) -> bool:
    links = {
        str(one.get("id") or ""): link
        if isinstance(link := value_at(one, path), str)
        else None
        for one in actors
    }
    seen, at = set(), links.get(actor_id)
    while at is not None and at in links and at not in seen:
        if at == actor_id:
            return True
        seen.add(at)
        at = links[at]
    return False


def actor_problems(
    actors: list[ActorRecord],
    actor_id: str,
    rules: ActorRules,
    *,
    new_id: bool,
    before: ActorRecord | None = None,
    touched: set[str] | None = None,
) -> list[Problem]:
    """The edited record's problems against the sealing release's rules, each with its fix and field.

    A problem `before` already had in a field outside `touched` is not this edit's, so it never blocks the edit."""
    known = {str(one.get("id") or "") for one in actors}
    actor = next(one for one in actors if one.get("id") == actor_id)
    had = (
        [
            found
            for where, found in _record_problems(
                {**before, "id": actor_id}, rules, new_id=False
            )
            if touched is None or where not in touched
        ]
        if before is not None
        else []
    )
    problems = [
        found
        for _, found in _record_problems(actor, rules, new_id=new_id)
        if found not in had
    ]
    if problems:
        # The links are read only from a record of the right shape.
        return problems
    problems += [
        problem(
            field_at(path),
            f"{actor_id}'s {path} {other!r} is not another listed actor.",
        )
        for path, other in broken_references(actor, known, rules)
    ]
    problems += [
        problem(field_at(path), f"{actor_id} is in a cycle of {path} links; break it.")
        for path in rules.get("acyclic", [])
        if _in_a_cycle(actor_id, actors, path)
    ]
    return problems


def same_name_problems(
    actors: list[ActorRecord], actor_id: str, before: ActorRecord | None
) -> list[Problem]:
    """Two actors of one name read as one person to the agent, in a transcript and in every list that names them, so
    a name this edit gives is one no other actor has (trimmed, in any case)."""
    actor = next(one for one in actors if one.get("id") == actor_id)
    name = str(actor.get("name") or "").strip()
    if not name or (
        before is not None
        and name.casefold() == str(before.get("name") or "").strip().casefold()
    ):
        return []
    holder = next(
        (
            one
            for one in actors
            if one.get("id") != actor_id
            and str(one.get("name") or "").strip().casefold() == name.casefold()
        ),
        None,
    )
    if holder is None:
        return []
    return [
        problem(
            "name",
            f"Another actor is already called {str(holder.get('name') or '').strip()}; give this one another name.",
        )
    ]


def removal_problems(
    actors: list[ActorRecord],
    actor_id: str,
    rules: ActorRules,
    before: list[ActorRecord],
) -> list[str]:
    """Another actor naming the removed one would point at nobody."""
    known = {str(one.get("id") or "") for one in actors}
    names = {
        str(one.get("id") or ""): str(one.get("name") or one.get("id"))
        for one in before
    }
    removed = names.get(actor_id, actor_id)
    return [
        f"{_naming(path, names[str(one.get('id') or '')], removed)}; change that on "
        f"{names[str(one.get('id') or '')]} before removing {removed}."
        for one in actors
        for path, other in broken_references(one, known, rules)
        if other == actor_id
    ]


def _naming(path: str, holder: str, named: str) -> str:
    """How one actor's record names another, in the editor's words."""
    if path == "parent":
        return f"{holder} is brought in by {named}"
    if path == "activation.fallback":
        return f"{holder} has {named} answer instead when {holder} does not"
    if path == "on_behalf_of":
        return f"{holder} acts on behalf of {named}"
    return f"{holder}'s {path} names {named}"


# Bounds every field of one actor record together, as compact JSON; each field's own limit comes from the sealed rules.
ACTOR_RECORD_MAX_CHARS = 65_536


def record_over_by(record: ActorRecord) -> int:
    """How many characters one record is over the bound, counted as compact JSON; 0 when within it."""
    size = len(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return max(0, size - ACTOR_RECORD_MAX_CHARS)


def in_use_problem(name: str, using: list[str]) -> str:
    """Why an actor cannot be removed while scenarios include it."""
    many = len(using) != 1
    return (
        f"{name} is in {len(using)} scenario{'s' if many else ''} ({', '.join(using)}); take them out of "
        f"{'those' if many else 'it'} first"
    )


def joins_fixed_problem(name: str, using: list[str]) -> str:
    """Why an actor's way of joining cannot change while scenarios include it: their triggers were written for it."""
    return (
        f"{name} joins {', '.join(using)} as it does now; "
        "take it out of those scenarios before changing how it joins"
    )


def required_fields(rules: ActorRules, channel: str | None) -> list[str]:
    """The fields an actor joining this way must have, as the sealed record schema requires them, its conditional
    rules included; the id is not one, since the server makes one from the name."""
    schema = record_schema(rules)
    probe = {"channel": channel} if channel else {}
    required = list(schema.get("required") or [])
    for part in [schema, *(schema.get("allOf") or [])]:
        condition = part.get("if") if isinstance(part, dict) else None
        if isinstance(condition, dict) and jsonschema.Draft202012Validator(
            condition
        ).is_valid(probe):
            required += (part.get("then") or {}).get("required") or []
            continue
        if isinstance(condition, dict):
            required += (part.get("else") or {}).get("required") or []
    return [field for field in dict.fromkeys(required) if field != "id"]
