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
    value_at,
)
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


# What the editor asks for when a field the record needs is missing or empty, said on that field.
_ASK_FOR = {"name": "Give them a name.", "id": "Give them an id."}


def _missing(
    error: jsonschema.ValidationError, where: str
) -> list[tuple[str, Problem]]:
    """Each field a `required` error names, as a problem on that field in the editor's words."""
    present = error.instance if isinstance(error.instance, dict) else {}
    paths = [
        f"{where}.{name}" if where else str(name)
        for name in error.validator_value
        if name not in present
    ]
    return [
        (path, problem(field_at(path), _ASK_FOR.get(path, f"Fill in {path}.")))
        for path in paths
    ]


def _branch(error: jsonschema.ValidationError) -> jsonschema.ValidationError:
    """An optional field allows null too, so what is wrong is the branch that is not null."""
    branches = [
        one
        for one in error.context or ()
        if not (one.validator == "type" and one.validator_value == "null")
    ]
    return jsonschema.exceptions.best_match(branches) if branches else error


def _reason(error: jsonschema.ValidationError) -> str:
    """What is wrong, named by the branch that is not null."""
    broken = _branch(error)
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
    """Each problem with the record, with the record path it sits at ("" for the record as a whole)."""
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
        if error.validator == "required":
            problems.extend(_missing(error, where))
            continue
        if where in _ASK_FOR and _branch(error).validator == "minLength":
            problems.append((where, problem(field_at(where), _ASK_FOR[where])))
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
                # Where inside the field it is wrong, so a problem in a part the edit leaves alone stays apart.
                ".".join(str(part) for part in _branch(error).absolute_path) or where,
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

    A problem `before` already had at a record path outside `touched` (the paths the edit writes, such as
    `activation.timeout_seconds`) is not this edit's, so it never blocks the edit."""
    known = {str(one.get("id") or "") for one in actors}
    actor = next(one for one in actors if one.get("id") == actor_id)
    had = (
        [
            found
            for where, found in _record_problems(
                {**before, "id": actor_id}, rules, new_id=False
            )
            if touched is None or not _within(where, touched)
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


def _within(where: str, touched: set[str]) -> bool:
    """Whether a record path is one the edit writes: the path itself, inside it, or a block holding it."""
    return bool(where) and any(
        where == path or where.startswith(f"{path}.") or path.startswith(f"{where}.")
        for path in touched
    )


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
