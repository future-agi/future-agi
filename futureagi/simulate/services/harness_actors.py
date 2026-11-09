"""An environment's actors, read and edited as one list in its draft, checked for structure only."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict

import jsonschema
from django.db import transaction

from simulate.models import HostedHarnessJob
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.versions import suite_documents
from simulate.services.hosted_harness import HostedHarnessError

if TYPE_CHECKING:
    from accounts.models.user import User

ACTORS_FILE = "actors.json"
# What ALK decides about actors, sealed into each bundle by the release that built it; never kept here.
ACTOR_RULES_FILE = "actor_rules.json"
# The first sealing release's rules, for bundles sealed before any release did; generated once, never edited.
_RULES_BEFORE_DECLARED = Path(__file__).with_name(
    "harness_actor_rules_before_declared.json"
)
# The environment's caller, an actor like any other; the one actor given the caller's whole situation.
CALLER = "caller"
# Fields the platform writes on a record, beside the ones its rules declare.
_PLATFORM_FIELDS = ("provenance",)


class ActorRecord(TypedDict, total=False):
    """One record as its file holds it; fields a later release declares pass through unchanged."""

    id: str
    name: str
    role: str
    kind: str
    side: str
    channel: str
    play: str
    parent: str | None
    permitted_actions: list[str]
    activation: dict[str, Any] | None
    provenance: dict[str, Any]
    scenarios: int


class ActorRules(TypedDict, total=False):
    """The sealed `actor_rules.json`; `records` holds each actor file's JSON Schema."""

    records: dict[str, dict[str, Any]]
    reserved_ids: list[str]
    references: dict[str, list[str]]
    acyclic: list[str]
    world_actions: dict[str, Any]
    loudness: dict[str, Any]
    distinct_from_caller: list[str]
    channels: dict[str, str]
    channel_modalities: dict[str, list[str]]
    equal_when_set: dict[str, str]
    formats: dict[str, str]
    brief_inputs: list[list[str]]
    situation_before: str
    evaluator_only: dict[str, list[str]]
    private_to_actor: list[str]
    actor_models: list[str]


class WorldAction(TypedDict):
    action: str
    tool: str
    description: str


class RecordSource(TypedDict):
    store: str
    collection: str


class KnowledgeSources(TypedDict):
    """What an actor may be given to read here: the environment's own files and its world's record collections."""

    files: list[str]
    records: list[RecordSource]


class Cast(TypedDict):
    actors: list[ActorRecord]
    source: Literal["draft", "saved"]
    revision: int
    record_schemas: dict[str, dict[str, Any]]
    actions: list[WorldAction]
    channels: dict[str, str]
    loudness: dict[str, Any]
    reserved_ids: list[str]
    knowledge_sources: KnowledgeSources
    # The environment's actor limits; a key set to null is no limit, a missing key the channel's default.
    limits: dict[str, Any]
    # The models an actor may be played on; the first is the default.
    actor_models: list[str]


class Seen(TypedDict):
    input: str
    value: Any
    source: str


class NeverSees(TypedDict):
    scenario_fields: list[str]
    files: list[str]
    other_actors: list[str]


class ActorView(TypedDict):
    actor_id: str
    permitted_actions: list[str]
    sees: list[Seen]
    never_sees: NeverSees
    played_from_view: bool
    unused_reason: str | None


def _not_found(actor_id: str) -> HostedHarnessError:
    return HostedHarnessError(
        "actor_not_found",
        f"{actor_id} is not an actor of this environment",
        status_code=404,
    )


def _read_json(files: dict[str, bytes], name: str) -> Any:
    raw = files.get(name)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise _unreadable(name) from exc


def _unreadable(name: str) -> HostedHarnessError:
    return HostedHarnessError(
        "actors_unreadable",
        f"This environment's {name} cannot be read; ask the builder chat to fix it",
        status_code=409,
    )


def _entries(files: dict[str, bytes], name: str, key: str) -> list[ActorRecord]:
    body = _read_json(files, name)
    if body is None:
        return []
    entries = (body.get(key) or []) if isinstance(body, dict) else None
    if not isinstance(entries, list) or not all(
        isinstance(one, dict) for one in entries
    ):
        raise _unreadable(name)
    return entries


def _speaks(actor: ActorRecord) -> bool:
    """Whether the actor speaks in the conversation: it has a channel. Every other field is the same for all."""
    return bool(actor.get("channel"))


def _cast(files: dict[str, bytes]) -> list[ActorRecord]:
    return _entries(files, ACTORS_FILE, "actors")


@lru_cache(maxsize=1)
def _rules_before_declared() -> ActorRules:
    return json.loads(_RULES_BEFORE_DECLARED.read_text(encoding="utf-8"))


def actor_rules(files: dict[str, bytes]) -> ActorRules:
    """The actor rules of the release that sealed these files."""
    return _read_json(files, ACTOR_RULES_FILE) or _rules_before_declared()


def _value_at(record: ActorRecord, path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def world_actions(files: dict[str, bytes], rules: ActorRules) -> list[WorldAction]:
    """Each tool entrypoint's route an actor can send to, named as the sealing release names it."""
    naming = rules.get("world_actions") or {}
    contract = _read_json(files, "contract.json")
    contract = contract if isinstance(contract, dict) else {}
    about = {
        str(one.get("name")): str(one.get("description") or "")
        for one in contract.get("tools") or []
        if isinstance(one, dict)
    }
    actions = {}
    for entry in contract.get(naming.get("from", "tool_entrypoints")) or []:
        if not isinstance(entry, dict):
            continue
        route = str(entry.get(naming.get("route", "endpoint")) or "").strip("/")
        method = str(entry.get(naming.get("method", "method")) or "POST").upper()
        # Rules that name the methods and path placeholders keep only routes an actor's action can be sent to.
        if (
            not route
            or ("methods" in naming and method not in naming["methods"])
            or ("placeholder" in naming and naming["placeholder"] in route)
        ):
            continue
        tool = str(entry.get(naming.get("tool", "tool")) or "")
        actions[route.replace("/", naming.get("separator", "."))] = {
            "tool": tool,
            "description": about.get(tool, ""),
        }
    actions.update(_record_changes(files, rules))
    return [{"action": name, **one} for name, one in sorted(actions.items())]


def _record_changes(
    files: dict[str, bytes], rules: ActorRules
) -> dict[str, dict[str, Any]]:
    """The record changes an actor may be given on the world's own collections, as the sealing release names them:
    none for a release that seals no such rule."""
    naming = rules.get("record_changes") or {}
    if not naming:
        return {}
    model = _read_json(files, str(naming.get("from") or ""))
    tables = (
        model.get(naming.get("tables", "tables")) if isinstance(model, dict) else None
    )
    never = set(naming.get("never") or [])
    prefix = str(naming.get("never_prefix") or "")
    out: dict[str, dict[str, Any]] = {}
    for table in tables or []:
        name = (
            str((table or {}).get(naming.get("name", "name")) or "")
            if isinstance(table, dict)
            else ""
        )
        if not name or name in never or (prefix and name.lower().startswith(prefix)):
            continue
        for verb in naming.get("verbs") or []:
            out[f"{name}.{verb}"] = {
                "tool": "",
                "description": f"A record change on {name}",
            }
    return out


def _readable_file(path: str, rules: ActorRules) -> bool:
    """Whether an actor may be given this file, by the sealed rules: never what only the evaluator reads, never the
    harness's own files."""
    name = path.strip().lstrip("/")
    hidden = rules.get("not_knowledge") or {}
    return (
        bool(name)
        and ".." not in name.split("/")
        and (
            name.rsplit("/", 1)[-1]
            not in (rules.get("evaluator_only") or {}).get("files", [])
            and name not in hidden.get("files", [])
            and not any(
                name.startswith(prefix) for prefix in hidden.get("prefixes", [])
            )
        )
    )


def knowledge_sources(
    environment_files: dict[str, bytes], rules: ActorRules, stores: Any
) -> KnowledgeSources:
    """The files and record collections an actor may be given to read in this environment."""
    records: list[RecordSource] = [
        {
            "store": str(store.get("capability") or ""),
            "collection": str(table.get("name") or ""),
        }
        for store in (stores if isinstance(stores, list) else [])
        if isinstance(store, dict)
        for table in store.get("tables") or []
        if isinstance(table, dict) and table.get("name")
    ]
    return {
        "files": sorted(
            name for name in environment_files if _readable_file(name, rules)
        ),
        "records": records,
    }


def _stores(environment: HostedHarnessJob) -> Any:
    from simulate.services.harness_environment import _stage_output

    return _stage_output(environment, "stores")


def _cast_view(
    environment_files: dict[str, bytes],
    suite_files: dict[str, bytes],
    draft: Any,
    stores: Any = None,
) -> Cast:
    rules = actor_rules(environment_files)
    documents = suite_documents(suite_files)
    return {
        "actors": [
            {
                **one,
                "scenarios": len(_scenarios_using(documents, str(one.get("id") or ""))),
            }
            for one in _cast(environment_files)
        ],
        "source": "draft" if draft is not None else "saved",
        "revision": draft.revision if draft is not None else 0,
        "record_schemas": rules["records"],
        "actions": world_actions(environment_files, rules),
        "channels": rules.get("channels") or {},
        "loudness": rules.get("loudness") or {},
        "reserved_ids": list(rules.get("reserved_ids") or []),
        "knowledge_sources": knowledge_sources(environment_files, rules, stores),
        "limits": _limits(environment_files),
        "actor_models": actor_models(rules),
    }


# The models an actor may name, for a bundle sealed before its release listed them; a newer one seals its own.
ACTOR_MODELS = ("gemini-3.8-flash", "gemini-3.7-flash")


def actor_models(rules: ActorRules) -> list[str]:
    """The models an actor may name, as the release that sealed the environment lists them."""
    sealed = rules.get("actor_models")
    return (
        [str(one) for one in sealed]
        if isinstance(sealed, list) and sealed
        else list(ACTOR_MODELS)
    )


LIMIT_FIELDS = {
    "max_active": int,
    "caller_takes_back_after_turns": int,
    "actor_seconds_per_call": float,
    "max_messages": int,
    "live_turns_per_actor": int,
    "live_turns_per_trial": int,
    "model": str,
}


def _limits(environment_files: dict[str, bytes]) -> dict[str, Any]:
    body = (
        _read_json(environment_files, ACTORS_FILE)
        if ACTORS_FILE in environment_files
        else {}
    )
    limits = body.get("limits") if isinstance(body, dict) else None
    return dict(limits) if isinstance(limits, dict) else {}


def write_actor_limits(
    environment: HostedHarnessJob,
    *,
    limits: dict[str, Any],
    user: User | None,
    expected_revision: int | None,
) -> Cast:
    """Set the environment's actor limits in the draft, as one whole block: each a positive number or null for no
    limit, `model` one of the models its sealed rules list; a field left out of the block takes the channel's default.
    Refuses `limits_invalid` (400)."""
    draft, environment_files, suite_files = _draft_read(environment, expected_revision)
    rules = actor_rules(environment_files)
    models = actor_models(rules)
    problems = []
    clean: dict[str, Any] = {}
    for key, value in (limits or {}).items():
        kind = LIMIT_FIELDS.get(key)
        if kind is None:
            problems.append(f"{key} is not an actor limit")
        elif value is None:
            clean[key] = None
        elif kind is str:
            if value not in models:
                problems.append(f"model is one of {', '.join(models)}")
            else:
                clean[key] = value
        elif (
            isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
        ):
            problems.append(f"{key} is a positive number, or null for no limit")
        else:
            clean[key] = kind(value)
    if problems:
        raise HostedHarnessError("limits_invalid", "; ".join(problems), status_code=400)
    return _written(
        environment,
        _with_cast(environment_files, _cast(environment_files), rules, limits=clean),
        suite_files,
        draft,
        user,
    )


def read_actors(environment: HostedHarnessJob) -> Cast:
    """The cast the next run would use (the draft's, else the saved version's) and what an actor can be given.

    Raises `actors_unreadable` (409) when an actor file is not valid JSON.
    """
    sides = drafts.edit_sides(environment)
    return _cast_view(
        sides["environment"],
        sides["suite"],
        drafts.fresh_draft(environment, "environment"),
        _stores(environment),
    )


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


def _modality(files: dict[str, bytes]) -> str | None:
    """The agent's modality as authoring reported it; unknown before then, so no channel is refused for it."""
    contract = _read_json(files, "contract.json")
    modality = contract.get("modality") if isinstance(contract, dict) else None
    return str(modality).strip().lower() or None if modality else None


def _new_choices(
    before: ActorRecord | None,
    after: ActorRecord,
    files: dict[str, bytes],
    rules: ActorRules,
    suite_files: dict[str, bytes] | None = None,
    modality: str | None = None,
    stores: Any = None,
) -> list[str]:
    """Only what this edit adds must exist here, so an actor written elsewhere can still be edited."""
    before = before or {}
    actor_id = str(after.get("id") or "")
    problems = []
    if _speaks(after):
        channels = rules.get("channels") or {}
        channel = after.get("channel")
        listed = _channel_schema(rules)
        reaches = (rules.get("channel_modalities") or {}).get(channel)
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
    known = [one["action"] for one in world_actions(files, rules)]
    old = set(before.get("permitted_actions") or [])
    added = [
        one
        for one in after.get("permitted_actions") or []
        if one not in old and one not in known
    ]
    tool = ((after.get("activation") or {}).get("trigger") or {}).get("tool")
    old_tool = ((before.get("activation") or {}).get("trigger") or {}).get("tool")
    if tool and tool != old_tool and tool not in known:
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
    stores: Any,
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
    refused = [path for path in added_files if not _readable_file(path, rules)]
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
    return broken.message


def _record_schema(rules: ActorRules) -> dict[str, Any]:
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
) -> list[str]:
    actor_id = str(actor.get("id") or "")
    schema = _record_schema(rules)
    record = dict(actor)
    problems = []
    extra = sorted(set(record) - set(schema["properties"]) - set(_PLATFORM_FIELDS))
    if extra:
        problems.append(
            f"{actor_id} has no {', '.join(extra)} in {ACTORS_FILE}; remove {'it' if len(extra) == 1 else 'them'}."
        )
    checked = {
        name: value
        for name, value in record.items()
        if name not in extra and name not in _PLATFORM_FIELDS
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
            else f"{actor_id}'s {where}:" if where else f"{actor_id}:"
        )
        problems.append(f"{subject} {_reason(error)}.")
    for path, field in (rules.get("equal_when_set") or {}).items():
        named = _value_at(actor, path)
        if (
            isinstance(named, str)
            and named.strip()
            and named.strip() != actor.get(field)
        ):
            problems.append(
                f"{actor_id}'s {path} is {named.strip()!r} but its {field} is {actor.get(field)!r}; make them the same."
            )
    if new_id and actor_id in rules.get("reserved_ids", []):
        problems.append(f"{actor_id!r} is reserved; give the actor another id.")
    return problems


def permission_conflicts(
    before: list[ActorRecord], edited: ActorRecord
) -> list[dict[str, str]]:
    """Each action this edit gives the actor that exactly one other actor holds today: that one's alone, so giving it
    to a second is a conflict to show before saving. An action two or more already hold is shared, not reserved.
    """
    actor_id = str(edited.get("id") or "")
    own = next((one for one in before if one.get("id") == actor_id), None) or {}
    had = set(own.get("permitted_actions") or [])
    conflicts = []
    for action in edited.get("permitted_actions") or []:
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


def _conflict_error(conflicts: list[dict[str, str]]) -> HostedHarnessError:
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


def _broken_references(
    actor: ActorRecord, known: set[str], rules: ActorRules
) -> list[tuple[str, Any]]:
    actor_id = str(actor.get("id") or "")
    # Fields that may name one of the actor's own actions instead (a fallback taken for it); every release has
    # allowed that for the fallback, so rules sealed before it was declared get the same.
    own = set(rules.get("own_actions", ["activation.fallback"]))
    actions = set(actor.get("permitted_actions") or [])
    return [
        (path, other)
        for path, also in (rules.get("references") or {}).items()
        if (other := _value_at(actor, path)) is not None
        and other not in also
        and other not in known - {actor_id}
        and not (path in own and other in actions)
    ]


def _in_a_cycle(actor_id: str, actors: list[ActorRecord], path: str) -> bool:
    links = {str(one.get("id") or ""): _value_at(one, path) for one in actors}
    seen, at = set(), links.get(actor_id)
    while at is not None and at in links and at not in seen:
        if at == actor_id:
            return True
        seen.add(at)
        at = links[at]
    return False


def actor_problems(
    actors: list[ActorRecord], actor_id: str, rules: ActorRules, *, new_id: bool
) -> list[str]:
    """The edited record checked against the sealing release's rules, one sentence each with its fix.

    Other records are taken as they are, so a value written elsewhere never blocks an edit; what an actor should do
    is never judged.
    """
    known = {str(one.get("id") or "") for one in actors}
    actor = next(one for one in actors if one.get("id") == actor_id)
    problems = _record_problems(actor, rules, new_id=new_id)
    problems += [
        f"{actor_id}'s {path} {other!r} is not another listed actor."
        for path, other in _broken_references(actor, known, rules)
    ]
    problems += [
        f"{actor_id} is in a cycle of {path} links; break it."
        for path in rules.get("acyclic", [])
        if _in_a_cycle(actor_id, actors, path)
    ]
    return problems


def _removal_problems(
    actors: list[ActorRecord], actor_id: str, rules: ActorRules
) -> list[str]:
    """Another actor naming the removed one would point at nobody."""
    known = {str(one.get("id") or "") for one in actors}
    return [
        f"{one.get('id')}'s {path} is {actor_id}; change it first."
        for one in actors
        for path, other in _broken_references(one, known, rules)
        if other == actor_id
    ]


def _scenarios_using(documents: list[dict[str, Any]], actor_id: str) -> list[str]:
    return sorted(
        str(scenario.get("scenario_key") or scenario.get("name") or "")
        for scenario in documents
        if _takes_part(scenario, actor_id)
    )


def _takes_part(scenario: dict[str, Any], actor_id: str) -> bool:
    """Whether an actor is in a scenario: listed, sending its start, or writing a pre-run session.

    The caller is the person in every scenario a person starts, so only one started by a task or an event has none.
    """
    start = scenario.get("starts_with")
    if actor_id == CALLER:
        return not start
    if isinstance(start, dict) and start.get("actor") == actor_id:
        return True
    if any(
        isinstance(one, dict) and one.get("id") == actor_id
        for one in scenario.get("actors") or []
    ):
        return True
    return any(
        isinstance(session, dict) and actor_id in (session.get("actors") or [])
        for session in scenario.get("pre_run") or []
    )


def _edited(
    actors: list[ActorRecord],
    *,
    op: str,
    actor_id: str,
    body: ActorRecord,
    user: User | None,
    suite_files: dict[str, bytes],
) -> list[ActorRecord]:
    """The cast after one create, replace or delete; refused with the reason when the change cannot apply."""
    index = next((i for i, one in enumerate(actors) if one.get("id") == actor_id), None)
    actors = list(actors)
    if op == "create" and index is not None:
        raise HostedHarnessError(
            "actor_exists",
            f"The id {actor_id!r} is already used by {actors[index].get('name') or actor_id}; choose another id",
            status_code=409,
        )
    if op == "create":
        return [*actors, _stamped({**body, "id": actor_id}, user)]
    if op not in ("replace", "delete"):
        raise HostedHarnessError(
            "actor_op_unknown", f"unknown change {op!r}", status_code=400
        )
    if index is None:
        raise _not_found(actor_id)
    if op == "replace":
        after = _stamped({**body, "id": actor_id}, user, before=actors[index])
        if after.get("channel") != actors[index].get("channel"):
            using = _scenarios_using(suite_documents(suite_files), actor_id)
            if using:
                # A scenario's trigger and level were written for the way the actor joins.
                raise HostedHarnessError(
                    "actor_in_use",
                    f"{actors[index].get('name') or actor_id} joins {', '.join(using)} as it does now; take it out "
                    "of those scenarios before changing how it joins",
                    status_code=409,
                )
        actors[index] = after
        return actors
    using = _scenarios_using(suite_documents(suite_files), actor_id)
    if using:
        many = len(using) != 1
        raise HostedHarnessError(
            "actor_in_use",
            f"{actors[index].get('name') or actor_id} is in {len(using)} scenario{'s' if many else ''} "
            f"({', '.join(using)}); take them out of {'those' if many else 'it'} first",
            status_code=409,
        )
    actors.pop(index)
    return actors


def _with_cast(
    environment_files: dict[str, bytes],
    actors: list[ActorRecord],
    rules: ActorRules,
    *,
    limits: dict[str, Any] | None = None,
) -> dict[str, bytes]:
    """The environment's files with this cast, keeping its limits (or these new ones)."""
    kept = _limits(environment_files) if limits is None else limits
    files = dict(environment_files)
    # No actors and no limits means no file, so removing the only actor leaves nothing to review.
    files.pop(ACTORS_FILE, None)
    if actors or kept:
        body: dict[str, Any] = {
            "schema": rules["formats"][ACTORS_FILE],
            "actors": actors,
        }
        if kept:
            body["limits"] = kept
        files[ACTORS_FILE] = (json.dumps(body, indent=2) + "\n").encode("utf-8")
    return files


def write_actor_edit(
    environment: HostedHarnessJob,
    *,
    op: str,
    actor_id: str | None,
    body: ActorRecord | None,
    user: User | None,
    expected_revision: int | None,
    share_actions: list[str] | None = None,
) -> Cast:
    """Create, replace or delete one actor in the environment draft and return the cast after it.

    `expected_revision` is the draft revision the caller read; an edit made elsewhere since is refused with
    `draft_changed` (409). Also refuses `actor_exists`, `actor_in_use` (409), `actor_not_found` (404) and
    `actor_invalid` (400), `actor_id_missing` and `actor_id_mismatch` (400), `permission_conflict` (409, with each
    conflict) when the edit gives an actor an action only one other actor holds and `share_actions` does not confirm
    it, and retires the builder chat's runtime once the draft is written. `actor_id` is the id in the address, if any; a create may give it in the body instead.
    """
    # The scenario count is read-side only; it is never written into a record.
    body = {name: value for name, value in (body or {}).items() if name != "scenarios"}
    given = body.pop("id", None) or actor_id
    if op != "delete" and not given:
        raise HostedHarnessError(
            "actor_id_missing", "give the actor an id", status_code=400
        )
    if op != "delete" and actor_id is not None and given != actor_id:
        raise HostedHarnessError(
            "actor_id_mismatch", "an actor's id never changes", status_code=400
        )
    actor_id = given
    draft, environment_files, suite_files = _draft_read(environment, expected_revision)
    before = _cast(environment_files)
    actors = _edited(
        before, op=op, actor_id=actor_id, body=body, user=user, suite_files=suite_files
    )
    rules = actor_rules(environment_files)
    if op == "delete":
        problems = _removal_problems(actors, actor_id, rules)
    else:
        problems = actor_problems(
            actors, actor_id, rules, new_id=op == "create"
        ) + _new_choices(
            next((one for one in before if one.get("id") == actor_id), None),
            next(one for one in actors if one.get("id") == actor_id),
            environment_files,
            rules,
            suite_files,
            _modality(environment_files),
            _stores(environment),
        )
    if problems:
        raise HostedHarnessError("actor_invalid", " ".join(problems), status_code=400)
    if op != "delete":
        confirmed = set(share_actions or [])
        conflicts = [
            one
            for one in permission_conflicts(
                before, next(one for one in actors if one.get("id") == actor_id)
            )
            if one["action"] not in confirmed
        ]
        if conflicts:
            raise _conflict_error(conflicts)
    return _written(
        environment,
        _with_cast(environment_files, actors, rules),
        suite_files,
        draft,
        user,
    )


def _draft_read(
    environment: HostedHarnessJob, expected_revision: int | None
) -> tuple[Any, dict[str, bytes], dict[str, bytes]]:
    """The environment draft and both sides' files, refusing `draft_changed` (409) when the caller read an older
    revision. The draft is read before its files, so an edit landing in between fails the write's revision check.
    """
    draft = drafts.fresh_draft(environment, "environment")
    sides = drafts.edit_sides(environment)
    if expected_revision is not None and expected_revision != (
        draft.revision if draft is not None else 0
    ):
        raise HostedHarnessError(
            "draft_changed",
            "the actors were edited elsewhere since you read them; read them again",
            status_code=409,
        )
    return draft, sides["environment"], sides["suite"]


def _written(
    environment: HostedHarnessJob,
    environment_files: dict[str, bytes],
    suite_files: dict[str, bytes],
    draft: Any,
    user: User | None,
) -> Cast:
    """Record the environment side of the draft and return the cast as it now reads."""
    expected = {
        "environment": (
            drafts.DraftRef(str(draft.id), draft.revision)
            if draft is not None
            else drafts.NO_DRAFT
        )
    }
    written = drafts.record_draft(
        environment,
        archives.pack_archive(archives.compose(environment_files, suite_files)),
        origin="manual",
        user=user,
        expected=expected,
        pieces={"environment"},
    )
    if written is None:
        raise HostedHarnessError(
            "drafts_unavailable",
            "this environment does not keep drafts",
            status_code=409,
        )
    transaction.on_commit(lambda: drafts.retire_chat_runtime(environment))
    return _cast_view(
        environment_files, suite_files, written.environment, _stores(environment)
    )


# What a person may upload into an environment draft, by kind: the folder it lands in and the formats it may be. A
# later kind (seed data, recordings) adds one entry here and a reader where it is used, nothing else.
UPLOAD_KINDS: dict[str, dict[str, Any]] = {
    # Documents an actor may be given to read (a policy, a price list), picked per actor like any environment file.
    "knowledge": {"folder": "knowledge/", "formats": (".md", ".txt", ".json", ".csv")},
}
# An upload's size bound: an actor is given at most the start of a file, so a larger one only costs storage.
UPLOAD_MAX_BYTES = 1024 * 1024
_UPLOAD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,119}$")


def upload_environment_file(
    environment: HostedHarnessJob,
    *,
    kind: str,
    name: str,
    content: bytes,
    user: User | None,
    expected_revision: int | None,
) -> Cast:
    """Put one uploaded file into the environment draft under its kind's folder and return the cast, whose knowledge
    sources then list it. Refuses `upload_kind_unknown`, `upload_invalid` (400) and `draft_changed` (409); a file of
    the same name is replaced."""
    spec = UPLOAD_KINDS.get(kind)
    if spec is None:
        raise HostedHarnessError(
            "upload_kind_unknown",
            f"{kind!r} is not a kind of upload; one of {', '.join(sorted(UPLOAD_KINDS))}",
            status_code=400,
        )
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    problems = []
    if not _UPLOAD_NAME.match(base):
        problems.append(
            "name it with letters, digits, spaces, dots, dashes or underscores, up to 120 characters"
        )
    if not base.lower().endswith(spec["formats"]):
        problems.append(f"a {kind} file is one of {', '.join(spec['formats'])}")
    if not content:
        problems.append("the file is empty")
    if len(content) > UPLOAD_MAX_BYTES:
        problems.append(f"the file is larger than {UPLOAD_MAX_BYTES // 1024} KB")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        problems.append("the file is not UTF-8 text")
        text = ""
    if text and base.lower().endswith(".json"):
        try:
            json.loads(text)
        except ValueError:
            problems.append("the file is not valid JSON")
    if problems:
        raise HostedHarnessError(
            "upload_invalid",
            f"{base or 'The file'}: " + "; ".join(problems),
            status_code=400,
        )
    draft, environment_files, suite_files = _draft_read(environment, expected_revision)
    path = spec["folder"] + base
    if kind == "knowledge" and not _readable_file(path, actor_rules(environment_files)):
        raise HostedHarnessError(
            "upload_invalid",
            f"{base}: an actor may not be given a file by this name; rename it",
            status_code=400,
        )
    return _written(
        environment, {**environment_files, path: content}, suite_files, draft, user
    )


def _stamped(
    actor: ActorRecord, user: User | None, *, before: ActorRecord | None = None
) -> ActorRecord:
    """Who set the record and each field this edit changed: set here, never taken from a client."""
    old = before or {}
    skipped = {"id", "provenance"}
    fields = {
        **((old.get("provenance") or {}).get("fields") or {}),
        **{
            name: "editor"
            for name, value in actor.items()
            if name not in skipped and (before is None or old.get(name) != value)
        },
    }
    return {
        **actor,
        "provenance": {
            "origin": "editor",
            "by": str(getattr(user, "id", "") or "") or None,
            "fields": fields,
        },
    }


def actor_view(
    environment: HostedHarnessJob, actor_id: str, *, scenario_key: str | None = None
) -> ActorView:
    """What one actor is given, with each value's source, what it may do, and what it never sees; optionally in one
    scenario. Raises `actor_not_found` or `scenario_not_found` (404)."""
    sides = drafts.edit_sides(environment)
    environment_files, suite_files = sides["environment"], sides["suite"]
    actors = _cast(environment_files)
    actor = next((one for one in actors if one.get("id") == actor_id), None)
    if actor is None:
        raise _not_found(actor_id)
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
        sees.append({"input": f"{source}.{field}", "value": value, "source": source})
    hidden = rules["evaluator_only"]
    others = [
        f"{one.get('name') or one.get('id')}.{name}"
        for one in actors
        if one.get("id") != actor_id
        for name in rules["private_to_actor"]
    ]
    # Played from this view when it speaks, is the caller, or decides its own world actions (live or both).
    played = (
        _speaks(actor) or actor_id == CALLER or actor.get("play") in ("live", "both")
    )
    return {
        "actor_id": actor_id,
        "permitted_actions": list(actor.get("permitted_actions") or []),
        "sees": sees,
        "never_sees": {
            "scenario_fields": list(hidden["scenario_fields"]),
            "files": list(hidden["files"]),
            "other_actors": others,
        },
        # Whether a run plays this actor from what it sees. One that only acts in the world takes its listed actions
        # at their triggers and is given none of it.
        "played_from_view": played,
        "unused_reason": (
            None
            if played
            else "This actor only takes the actions its scenarios give it, so nothing here is given to it during a run. "
            "Set it to decide its own actions, or to speak in the conversation, to have it play from this."
        ),
    }
