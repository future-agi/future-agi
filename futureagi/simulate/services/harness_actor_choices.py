"""What an actor edit adds that must exist here: a channel this agent can be reached through, files and records the
actor may read, a known model and actions, and a name no other actor has. Only what the edit adds is checked, so an
actor written elsewhere can still be edited."""

from __future__ import annotations

import json
from typing import Any

from simulate.services.harness_actor_rules import (
    ACTORS_FILE,
    ActorRecord,
    ActorRules,
    actor_models,
    knowledge_sources,
    readable_file,
    speaks,
    value_at,
    world_actions,
)
from simulate.services.harness_actor_validation import Problem, problem
from simulate.services.harness_versioning.versions import suite_documents


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
