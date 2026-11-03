"""An environment's actors, read and edited as data in its draft.

The cast lives in the environment's `actors.json`, and the sub-actors who speak on a call in its `sub_actors.json`, which
the call runtime reads; both are one cast here. An edit writes the environment draft and nothing else, so a saved
version is unchanged until the draft is saved, and the edit shows as a proposed change for review. Checks here are
structural only (ids, references, who brings whom in); what an actor should do is never judged.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from django.db import transaction

from simulate.models import HostedHarnessJob
from simulate.services.harness_versioning import archives, drafts
from simulate.services.hosted_harness import HostedHarnessError

ACTORS_FILE = "actors.json"
SUB_ACTORS_FILE = "sub_actors.json"
# What ALK decides about actors (record fields and limits, who is reserved, what an actor is given and never sees),
# sealed into each bundle by the release that built it. Read from the environment's own files, never kept here.
ACTOR_RULES_FILE = "actor_rules.json"
# The rules of the first release that sealed them, for bundles sealed before any release did. Generated from that
# release once and never edited: every later bundle carries its own.
_RULES_BEFORE_DECLARED = Path(__file__).with_name(
    "harness_actor_rules_before_declared.json"
)
# Fields the platform writes on a record, beside the ones its rules declare.
_PLATFORM_FIELDS = ("provenance",)
# A sub-actor is a person on the caller's side; the cast shows it so.
_SUB_ACTOR_CAST = {"kind": "person", "side": "caller"}


def _error(code: str, message: str, status_code: int) -> HostedHarnessError:
    return HostedHarnessError(code, message, status_code=status_code)


def _sides(
    environment: HostedHarnessJob,
) -> tuple[dict[str, bytes], dict[str, bytes], Any]:
    """The environment and suite files the next edit builds on, and the open environment draft, if any."""
    sides = drafts.edit_sides(environment)
    return (
        sides["environment"],
        sides["suite"],
        drafts.fresh_draft(environment, "environment"),
    )


def _listing(files: dict[str, bytes]) -> list[dict[str, Any]]:
    raw = files.get(ACTORS_FILE)
    if not raw:
        return []
    body = json.loads(raw.decode("utf-8"))
    return list(body.get("actors") or []) if isinstance(body, dict) else []


def _sub_actor_listing(files: dict[str, bytes]) -> list[dict[str, Any]]:
    raw = files.get(SUB_ACTORS_FILE)
    if not raw:
        return []
    body = json.loads(raw.decode("utf-8"))
    entries = list(body.get("sub_actors") or []) if isinstance(body, dict) else []
    return [
        {**entry, **_SUB_ACTOR_CAST} for entry in entries if isinstance(entry, dict)
    ]


def _is_sub_actor(actor: dict[str, Any]) -> bool:
    """A sub-actor speaks on a call channel; a world actor has none."""
    return "channel" in actor


def _sub_actor_entry(actor: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in actor.items() if name not in _SUB_ACTOR_CAST}


def _cast(files: dict[str, bytes]) -> list[dict[str, Any]]:
    return _listing(files) + _sub_actor_listing(files)


@lru_cache(maxsize=1)
def _rules_before_declared() -> dict[str, Any]:
    return json.loads(_RULES_BEFORE_DECLARED.read_text(encoding="utf-8"))


def actor_rules(files: dict[str, bytes]) -> dict[str, Any]:
    """The actor rules of the release that sealed these files."""
    raw = files.get(ACTOR_RULES_FILE)
    return json.loads(raw.decode("utf-8")) if raw else _rules_before_declared()


def _value_at(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def world_actions(
    files: dict[str, bytes], rules: dict[str, Any]
) -> list[dict[str, str]]:
    """The actions the world lets an actor take, named as the sealing release names them: each tool entrypoint's
    route, with the tool and what it does. A tool that runs inside the agent has no route and is no world action.
    """
    naming = rules.get("world_actions") or {}
    raw = files.get("contract.json")
    contract = json.loads(raw.decode("utf-8")) if raw else {}
    about = {
        str(one.get("name")): str(one.get("description") or "")
        for one in contract.get("tools") or []
    }
    actions = {}
    for entry in contract.get(naming.get("from", "tool_entrypoints")) or []:
        route = str(entry.get(naming.get("route", "endpoint")) or "").strip("/")
        if not route:
            continue
        tool = str(entry.get(naming.get("tool", "tool")) or "")
        actions[route.replace("/", naming.get("separator", "."))] = {
            "tool": tool,
            "description": about.get(tool, ""),
        }
    return [{"action": name, **one} for name, one in sorted(actions.items())]


def read_actors(environment: HostedHarnessJob) -> dict[str, Any]:
    """The cast the next run would use (the draft's when there is one, else the saved version's), with what an actor
    can be given: the record schemas, the world's actions and the call channels."""
    environment_files, suite_files, draft = _sides(environment)
    rules = actor_rules(environment_files)
    return {
        "actors": [
            {
                **one,
                "scenarios": len(
                    _scenarios_using(suite_files, str(one.get("id") or ""))
                ),
            }
            for one in _cast(environment_files)
        ],
        "source": "draft" if draft is not None else "saved",
        "revision": draft.revision if draft is not None else 0,
        "record_schemas": rules["records"],
        "actions": world_actions(environment_files, rules),
        "channels": rules.get("channels") or {},
        "loudness": rules.get("loudness") or {},
    }


def _sharing_the_caller(
    before: dict[str, Any],
    after: dict[str, Any],
    suite_files: dict[str, bytes],
    rules: dict[str, Any],
) -> list[str]:
    """A sub-actor field the rules keep distinct from the caller's must not equal the caller's in any scenario that
    includes the sub-actor; two speakers of one name make a transcript unreadable."""
    from simulate.services.harness_versioning.versions import suite_documents

    actor_id = str(after.get("id") or "")
    problems = []
    for field in rules.get("distinct_from_caller") or []:
        value = str(after.get(field) or "").strip().lower()
        if not value or value == str(before.get(field) or "").strip().lower():
            continue
        for scenario in suite_documents(suite_files):
            included = any(
                isinstance(one, dict) and one.get("id") == actor_id
                for one in scenario.get("sub_actors") or []
            )
            caller = str((scenario.get("persona") or {}).get(field) or "").strip()
            if included and caller.lower() == value:
                key = scenario.get("scenario_key") or scenario.get("name")
                problems.append(
                    f"{actor_id}'s {field} is the caller's in {key} ({caller}); choose another."
                )
    return problems


def _new_choices(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    files: dict[str, bytes],
    rules: dict[str, Any],
    suite_files: dict[str, bytes] | None = None,
) -> list[str]:
    """An action, trigger or channel this edit adds must be one the environment has. Values already on the record are
    kept as they are, so an actor written elsewhere can still be edited."""
    before = before or {}
    actor_id = str(after.get("id") or "")
    problems = []
    if _is_sub_actor(after):
        channels = rules.get("channels") or {}
        if (
            after.get("channel") != before.get("channel")
            and channels
            and after.get("channel") not in channels
        ):
            problems.append(
                f"{actor_id}'s channel {after.get('channel')!r} is not one of: {', '.join(channels)}."
            )
        return problems + _sharing_the_caller(before, after, suite_files or {}, rules)
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


def _reason(error: jsonschema.ValidationError) -> str:
    """The rule a value broke, in the words its schema gives. An optional field's schema allows null as well, so its
    own error only says no branch matched; the branch that is not null names the limit.
    """
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
    return broken.message


def actor_problems(actors: list[dict[str, Any]], rules: dict[str, Any]) -> list[str]:
    """Each record checked against the rules of the release that sealed the environment; one sentence each, with
    the fix. What an actor should do is never judged."""
    problems = []
    ids = [str(one.get("id") or "") for one in actors]
    known = set(ids)
    for one in actors:
        actor_id = str(one.get("id") or "")
        sub_actor = _is_sub_actor(one)
        file = SUB_ACTORS_FILE if sub_actor else ACTORS_FILE
        schema = rules["records"][file]
        record = _sub_actor_entry(one) if sub_actor else dict(one)
        if sub_actor and one.get("side", "caller") != "caller":
            problems.append(
                f"{actor_id} speaks on the call ({one['channel']}), so it is on the caller's side."
            )
        extra = sorted(
            set(record) - set(schema.get("properties") or {}) - set(_PLATFORM_FIELDS)
        )
        if extra:
            problems.append(
                f"{actor_id} has no {', '.join(extra)} in {file}; remove {'it' if len(extra) == 1 else 'them'}."
            )
        checked = {
            name: value
            for name, value in record.items()
            if name not in extra and name not in _PLATFORM_FIELDS
        }
        for error in jsonschema.Draft202012Validator(schema).iter_errors(checked):
            where = ".".join(str(part) for part in error.absolute_path) or "the record"
            # A bad id cannot name its own actor, so the sentence names the field instead.
            subject = "The id" if where == "id" else f"{actor_id}'s {where}:"
            problems.append(f"{subject} {_reason(error)}.")
        if actor_id in rules.get("reserved_ids", []):
            problems.append(f"{actor_id!r} is reserved; give the actor another id.")
        for path, also in (rules.get("references") or {}).items():
            other = _value_at(one, path)
            if (
                other is not None
                and other not in also
                and other not in known - {actor_id}
            ):
                problems.append(
                    f"{actor_id}'s {path} {other!r} is not another listed actor."
                )
    if len(ids) != len(known):
        problems.append("each actor id appears once; rename the repeated one.")
    for path in rules.get("acyclic", []):
        links = {str(one.get("id") or ""): _value_at(one, path) for one in actors}
        for actor_id in links:
            seen, at = {actor_id}, links[actor_id]
            while at is not None and at in links:
                if at in seen:
                    problems.append(
                        f"{actor_id} is in a cycle of {path} links; break it."
                    )
                    break
                seen.add(at)
                at = links[at]
    return problems


def _scenarios_using(suite_files: dict[str, bytes], actor_id: str) -> list[str]:
    from simulate.services.harness_versioning.versions import suite_documents

    return sorted(
        str(scenario.get("scenario_key") or scenario.get("name") or "")
        for scenario in suite_documents(suite_files)
        if any(
            isinstance(one, dict) and one.get("id") == actor_id
            for one in [
                *(scenario.get("actors") or []),
                *(scenario.get("sub_actors") or []),
            ]
        )
    )


def write_actor_edit(
    environment: HostedHarnessJob,
    *,
    op: str,
    actor_id: str,
    body: dict[str, Any] | None,
    user: Any,
    expected_revision: int | None,
) -> dict[str, Any]:
    """Create, replace or delete one actor in the environment draft. `expected_revision` is the draft revision the
    caller read; an edit made elsewhere since is refused rather than overwritten."""
    environment_files, suite_files, draft = _sides(environment)
    current_revision = draft.revision if draft is not None else 0
    if expected_revision is not None and expected_revision != current_revision:
        raise _error(
            "draft_changed",
            "the actors were edited elsewhere since you read them; read them again",
            409,
        )
    actors = _cast(environment_files)
    index = next((i for i, one in enumerate(actors) if one.get("id") == actor_id), None)
    before = actors[index] if index is not None else None
    # The scenario count is read-side only; it is never written into a record.
    body = {name: value for name, value in (body or {}).items() if name != "scenarios"}
    if op == "create":
        if index is not None:
            raise _error(
                "actor_exists",
                f"The id {actor_id!r} is already used by {actors[index].get('name') or actor_id}; choose another id",
                409,
            )
        actors.append(_stamped({**(body or {}), "id": actor_id}, user))
    elif op == "replace":
        if index is None:
            raise _error(
                "actor_not_found",
                f"{actor_id} is not an actor of this environment",
                404,
            )
        actors[index] = _stamped({**(body or {}), "id": actor_id}, user)
    elif op == "delete":
        if index is None:
            raise _error(
                "actor_not_found",
                f"{actor_id} is not an actor of this environment",
                404,
            )
        using = _scenarios_using(suite_files, actor_id)
        if using:
            raise _error(
                "actor_in_use",
                f"{actor_id} takes part in {', '.join(using)}; take it out of them first",
                409,
            )
        actors.pop(index)
    else:
        raise _error("actor_op_unknown", f"unknown change {op!r}", 400)
    rules = actor_rules(environment_files)
    problems = actor_problems(actors, rules)
    if op != "delete":
        problems += _new_choices(
            before,
            next(one for one in actors if one.get("id") == actor_id),
            environment_files,
            rules,
            suite_files,
        )
    if problems:
        raise _error("actor_invalid", " ".join(problems), 400)
    world = [one for one in actors if not _is_sub_actor(one)]
    speaking = [_sub_actor_entry(one) for one in actors if _is_sub_actor(one)]
    environment_files = dict(environment_files)
    # No world actors means no file, so removing the only actor leaves nothing to review.
    environment_files.pop(ACTORS_FILE, None)
    if world:
        environment_files[ACTORS_FILE] = (
            json.dumps(
                {"schema": rules["formats"][ACTORS_FILE], "actors": world}, indent=2
            )
            + "\n"
        ).encode("utf-8")
    if speaking or SUB_ACTORS_FILE in environment_files:
        environment_files[SUB_ACTORS_FILE] = (
            json.dumps({"sub_actors": speaking}, indent=2) + "\n"
        ).encode("utf-8")
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
        raise _error("drafts_unavailable", "this environment does not keep drafts", 409)
    transaction.on_commit(lambda: drafts.retire_chat_runtime(environment))
    return read_actors(environment)


def _stamped(actor: dict[str, Any], user: Any) -> dict[str, Any]:
    """Who set the record: set here, never taken from a client."""
    fields = {name: "editor" for name in actor if name not in ("id", "provenance")}
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
) -> dict[str, Any]:
    """What one actor is given (each input, its value and source), what it may do in the world, and what it never
    sees; optionally in one scenario."""
    from simulate.services.harness_versioning.versions import suite_documents

    environment_files, suite_files, _ = _sides(environment)
    actors = _cast(environment_files)
    actor = next((one for one in actors if one.get("id") == actor_id), None)
    if actor is None:
        raise _error(
            "actor_not_found", f"{actor_id} is not an actor of this environment", 404
        )
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
            raise _error(
                "scenario_not_found",
                f"{scenario_key} is not a scenario of this environment",
                404,
            )
    entry = next(
        (
            one
            for one in [
                *((scenario or {}).get("actors") or []),
                *((scenario or {}).get("sub_actors") or []),
            ]
            if isinstance(one, dict) and one.get("id") == actor_id
        ),
        None,
    )
    rules = actor_rules(environment_files)
    sees = []
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
                # The caller has its whole situation already; anyone else, a sub-actor beside it included, is given
                # the part before its details.
                if scenario is not None
                and (actor.get("side") != "caller" or _is_sub_actor(actor))
                else None
            )
        if value in (None, "", [], {}):
            continue
        sees.append({"input": f"{source}.{field}", "value": value, "source": source})
    hidden = rules["evaluator_only"]
    never = [f"scenario.{name}" for name in hidden["scenario_fields"]] + [
        f"file.{name}" for name in hidden["files"]
    ]
    never += [
        f"{one.get('id')}.{name}"
        for one in actors
        if one.get("id") != actor_id
        for name in rules["private_to_actor"]
    ]
    return {
        "actor_id": actor_id,
        "permitted_actions": list(actor.get("permitted_actions") or []),
        "sees": sees,
        "never_sees": never,
    }
