"""An environment's actor records and what the release that sealed it decides about them: the record schema, the ids,
references and channels, the world actions and knowledge an actor may be given, and the limits beside the cast."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict, cast

from simulate.models import HostedHarnessJob
from simulate.selectors.stage_outputs import stage_output
from simulate.services.hosted_harness import HostedHarnessError

ACTORS_FILE = "actors.json"
# The environment's caller, an actor like any other; the one actor given the caller's whole situation.
CALLER = "caller"


# What ALK decides about actors, sealed into each bundle by the release that built it; never kept here.
ACTOR_RULES_FILE = "actor_rules.json"


# The first sealing release's rules, for bundles sealed before any release did; generated once, never edited.
_RULES_BEFORE_DECLARED = Path(__file__).with_name(
    "harness_actor_rules_before_declared.json"
)


# Fields the platform writes on a record, beside the ones its rules declare.
PLATFORM_FIELDS = ("provenance",)


class ActorRecord(TypedDict, total=False):
    """One record as its file holds it; fields a later release declares pass through unchanged."""

    id: str
    name: str
    role: str
    kind: str
    side: str
    channel: str | None
    play: str
    parent: str | None
    on_behalf_of: str | None
    goal: str
    prompt: str
    surface: str
    model: str
    max_turns: int
    max_lines: int
    loudness_ratio: float | None
    persona: dict[str, Any]
    constraints: dict[str, Any]
    behaviour: dict[str, Any]
    identities: dict[str, Any]
    permitted_actions: list[str]
    activation: dict[str, Any] | None
    knowledge: list[dict[str, Any]]
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
    not_knowledge: dict[str, list[str]]
    record_changes: dict[str, Any]
    own_actions: list[str]
    # What each actor limit left unset takes, by limit: `{default, by_modality: {modality: value}}`, `by_modality`
    # empty when every modality shares the default; a release that seals none leaves the defaults unsaid.
    limit_defaults: dict[str, dict[str, object]]


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


def text_of(value: object) -> str:
    return value if isinstance(value, str) else ""


def texts_of(value: object) -> list[str]:
    return (
        [one for one in value if isinstance(one, str)]
        if isinstance(value, list)
        else []
    )


def whole_of(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def real_of(value: object) -> float | None:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def mapping_of(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def is_the_caller(actor: ActorRecord) -> bool:
    """The environment's caller: the one actor with the caller's id."""
    return actor.get("id") == CALLER


def actor_not_found(actor_id: str) -> HostedHarnessError:
    return HostedHarnessError(
        "actor_not_found",
        f"{actor_id} is not an actor of this environment",
        status_code=404,
    )


def read_json(files: dict[str, bytes], name: str) -> Any:
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
    body = read_json(files, name)
    if body is None:
        return []
    entries = (body.get(key) or []) if isinstance(body, dict) else None
    if not isinstance(entries, list) or not all(
        isinstance(one, dict) for one in entries
    ):
        raise _unreadable(name)
    return entries


def speaks(actor: ActorRecord) -> bool:
    """Whether the actor speaks in the conversation: it has a channel. Every other field is the same for all."""
    return bool(actor.get("channel"))


def cast_records(files: dict[str, bytes]) -> list[ActorRecord]:
    return _entries(files, ACTORS_FILE, "actors")


@lru_cache(maxsize=1)
def _rules_before_declared() -> ActorRules:
    return cast(
        ActorRules, json.loads(_RULES_BEFORE_DECLARED.read_text(encoding="utf-8"))
    )


def actor_rules(files: dict[str, bytes]) -> ActorRules:
    """The actor rules of the release that sealed these files."""
    return read_json(files, ACTOR_RULES_FILE) or _rules_before_declared()


def value_at(record: ActorRecord, path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def world_actions(files: dict[str, bytes], rules: ActorRules) -> list[WorldAction]:
    """Each tool entrypoint's route an actor can send to, named as the sealing release names it."""
    naming = rules.get("world_actions") or {}
    contract = read_json(files, "contract.json")
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
    return [
        {
            "action": name,
            "tool": str(one["tool"]),
            "description": str(one["description"]),
        }
        for name, one in sorted(actions.items())
    ]


def _record_changes(
    files: dict[str, bytes], rules: ActorRules
) -> dict[str, dict[str, Any]]:
    """The record changes an actor may be given on the world's own collections, as the sealing release names them:
    none for a release that seals no such rule."""
    naming = rules.get("record_changes") or {}
    if not naming:
        return {}
    model = read_json(files, str(naming.get("from") or ""))
    tables = (
        model.get(naming.get("tables", "tables")) if isinstance(model, dict) else None
    )
    out: dict[str, dict[str, Any]] = {}
    for table in tables or []:
        name = (
            str((table or {}).get(naming.get("name", "name")) or "")
            if isinstance(table, dict)
            else ""
        )
        if not name or harness_table(name, rules):
            continue
        for verb in naming.get("verbs") or []:
            out[f"{name}.{verb}"] = {
                "tool": "",
                "description": f"A record change on {name}",
            }
    return out


def harness_table(name: str, rules: ActorRules) -> bool:
    """Whether a collection is the harness's own, as the sealing release names them: never a world record."""
    naming = rules.get("record_changes") or {}
    prefix = str(naming.get("never_prefix") or "")
    return name in (naming.get("never") or []) or bool(
        prefix and name.lower().startswith(prefix)
    )


def readable_file(path: str, rules: ActorRules) -> bool:
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
    environment_files: dict[str, bytes], rules: ActorRules, stores: object
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
        if isinstance(table, dict)
        and table.get("name")
        and not harness_table(str(table["name"]), rules)
    ]
    return {
        "files": sorted(
            name for name in environment_files if readable_file(name, rules)
        ),
        "records": records,
    }


def world_stores(environment: HostedHarnessJob) -> object:
    """The world's record stores as the build reported them; parsed where they are read."""
    return stage_output(environment, "stores")


def reachable_channels(rules: ActorRules, modality: str | None) -> dict[str, str]:
    """The channels an actor can join through to reach this environment's agent; all of them before authoring
    reports the agent's modality."""
    reaches = rules.get("channel_modalities") or {}
    return {
        channel: about
        for channel, about in (rules.get("channels") or {}).items()
        if not modality or channel not in reaches or modality in reaches[channel]
    }


def agent_modality(files: dict[str, bytes]) -> str | None:
    """The agent's modality as authoring reported it; unknown before then, so no channel is refused for it."""
    contract = read_json(files, "contract.json")
    modality = contract.get("modality") if isinstance(contract, dict) else None
    return str(modality).strip().lower() or None if modality else None


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


class UploadKind(TypedDict):
    # The folder an upload of this kind lands in, and the formats it may be.
    folder: str
    formats: tuple[str, ...]


# What a person may upload into an environment draft, by kind. A later kind (seed data, recordings) adds one entry here
# and a reader where it is used, nothing else.
UPLOAD_KINDS: dict[str, UploadKind] = {
    # Documents an actor may be given to read (a policy, a price list), picked per actor like any environment file.
    "knowledge": {"folder": "knowledge/", "formats": (".md", ".txt", ".json", ".csv")},
}


# An upload's size bound: an actor is given at most the start of a file, so a larger one only costs storage.
UPLOAD_MAX_BYTES = 1024 * 1024


LIMIT_FIELDS = {
    "max_active": int,
    "caller_takes_back_after_turns": int,
    "actor_seconds_per_call": float,
    "max_messages": int,
    "live_turns_per_actor": int,
    "live_turns_per_trial": int,
    "model": str,
}


def stored_limits(environment_files: dict[str, bytes]) -> dict[str, Any]:
    body = (
        read_json(environment_files, ACTORS_FILE)
        if ACTORS_FILE in environment_files
        else {}
    )
    limits = body.get("limits") if isinstance(body, dict) else None
    return dict(limits) if isinstance(limits, dict) else {}


class ActorLimits(TypedDict, total=False):
    """The environment's actor limits, each typed as `LIMIT_FIELDS` says; null is no limit."""

    max_active: int | None
    caller_takes_back_after_turns: int | None
    actor_seconds_per_call: float | None
    max_messages: int | None
    live_turns_per_actor: int | None
    live_turns_per_trial: int | None
    model: str | None


def typed_limits(limits: dict[str, Any]) -> ActorLimits:
    """The stored limits as their types say, for reading: a key no limit has, or a value of the wrong type, is left
    out (it is kept in the file as written)."""
    typed: dict[str, int | float | str | None] = {}
    for key, value in limits.items():
        kind = LIMIT_FIELDS.get(key)
        if kind is None:
            continue
        if value is None:
            typed[key] = None
        elif kind is str and isinstance(value, str):
            typed[key] = value
        elif (
            kind is not str
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (kind is float or value == int(value))
        ):
            typed[key] = kind(value)
    return cast(ActorLimits, typed)


class UnresolvedPoint(TypedDict, total=False):
    """A policy point the builder could not turn into an actor's permissions or knowledge."""

    policy: str
    why: str
    actor: str


def unresolved_points(environment_files: dict[str, bytes]) -> list[UnresolvedPoint]:
    """The policy points the builder left for the person, as `actors.json` keeps them beside the cast."""
    body = (
        read_json(environment_files, ACTORS_FILE)
        if ACTORS_FILE in environment_files
        else {}
    )
    points = body.get("unresolved") if isinstance(body, dict) else None
    return [
        cast(
            UnresolvedPoint,
            {
                key: one.get(key)
                for key in ("policy", "why", "actor")
                if one.get(key) is not None
            },
        )
        for one in points or []
        if isinstance(one, dict) and isinstance(one.get("policy"), str)
    ]


def scenarios_using(documents: list[dict[str, Any]], actor_id: str) -> list[str]:
    return sorted(
        str(scenario.get("scenario_key") or scenario.get("name") or "")
        for scenario in documents
        if _takes_part(scenario, actor_id)
    )


def _takes_part(scenario: dict[str, Any], actor_id: str) -> bool:
    """Whether an actor is in a scenario: listed, sending its start, or writing a pre-run session."""
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
