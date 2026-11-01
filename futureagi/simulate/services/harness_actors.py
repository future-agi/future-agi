"""An environment's actors, read and edited as data in its draft.

The cast lives in the environment's `actors.json`. An edit writes the environment draft and nothing else, so a saved
version is unchanged until the draft is saved, and the edit shows as a proposed change for review. Checks here are
structural only (ids, references, who brings whom in); what an actor should do is never judged.
"""

from __future__ import annotations

import json
import re
from typing import Any

from django.db import transaction

from simulate.models import HostedHarnessJob
from simulate.services.harness_versioning import archives, drafts
from simulate.services.hosted_harness import HostedHarnessError

ACTORS_FILE = "actors.json"
ACTORS_SCHEMA = "futureagi.actors.v1"
_ID = re.compile(r"^[a-z0-9_]{1,64}$")
# The agent under test in the action log; no actor may take its id.
_AGENT = "agent"
# What only the grader reads, and what an actor's brief is built from: the same declarations ALK seals
# (`fi.alk.harness.actors.EVALUATOR_ONLY` and `BRIEF_INPUTS`), so the view shows what the actor is given.
EVALUATOR_ONLY_FIELDS = ("solution", "alternatives", "sub_goals", "tests", "setup_code", "ready_code", "coverage")
EVALUATOR_ONLY_FILES = ("sub_goals.json", "certification.json", "runtime-validation.json")
BRIEF_INPUTS = (
    ("actor", "name"),
    ("actor", "role"),
    ("actor", "persona"),
    ("actor", "goal"),
    ("actor", "constraints"),
    ("actor", "behaviour"),
    ("actor", "prompt"),
    ("actor", "surface"),
    ("actor", "knowledge"),
    ("scenario", "instruction"),
    ("scenario", "knowledge"),
    ("caller", "situation"),
)
_OTHERS_PRIVATE = ("goal", "prompt", "instruction", "actions")


def _error(code: str, message: str, status_code: int) -> HostedHarnessError:
    return HostedHarnessError(code, message, status_code=status_code)


def _head(environment: HostedHarnessJob) -> HostedHarnessJob:
    return HostedHarnessJob.no_workspace_objects.select_related("environment_version", "suite_version").get(
        id=environment.id
    )


def _sides(head: HostedHarnessJob) -> tuple[dict[str, bytes], dict[str, bytes], Any]:
    """The environment and suite files the next edit builds on (each piece's draft, else its saved version), and
    the environment draft, if any."""
    draft = drafts.fresh_draft(head, "environment")
    environment_files = drafts._piece_files(head, "environment", draft or head.environment_version)
    suite_files = drafts._piece_files(
        head, "suite", drafts.fresh_draft(head, "suite") or head.suite_version
    )
    return environment_files, suite_files, draft


def _listing(files: dict[str, bytes]) -> list[dict[str, Any]]:
    raw = files.get(ACTORS_FILE)
    if not raw:
        return []
    body = json.loads(raw.decode("utf-8"))
    return list(body.get("actors") or []) if isinstance(body, dict) else []


def read_actors(environment: HostedHarnessJob) -> dict[str, Any]:
    """The cast the next run would use: the draft's when there is one, else the saved version's."""
    head = _head(environment)
    environment_files, _, draft = _sides(head)
    return {
        "actors": _listing(environment_files),
        "source": "draft" if draft is not None else "saved",
        "revision": draft.revision if draft is not None else 0,
    }


def actor_problems(actors: list[dict[str, Any]]) -> list[str]:
    """Structural problems only, each one sentence with the fix."""
    problems = []
    ids = [str(one.get("id") or "") for one in actors]
    known = set(ids)
    for one in actors:
        actor_id = str(one.get("id") or "")
        if not _ID.match(actor_id):
            problems.append(f"{actor_id!r} is not a valid id; use 1 to 64 lowercase letters, digits or underscores.")
        if actor_id == _AGENT:
            problems.append("'agent' is the agent under test; give the actor another id.")
        if not str(one.get("name") or "").strip():
            problems.append(f"{actor_id} has no name; say what others call them.")
        for field in ("parent", "on_behalf_of"):
            other = one.get(field)
            if other is not None and other not in known - {actor_id}:
                problems.append(f"{actor_id}'s {field} {other!r} is not another listed actor.")
        fallback = ((one.get("activation") or {}).get("fallback")) or "silent"
        if fallback not in ("silent", "decline") and fallback not in known - {actor_id}:
            problems.append(f"{actor_id} falls back to {fallback!r}, which is not another listed actor.")
    if len(ids) != len(known):
        problems.append("each actor id appears once; rename the repeated one.")
    parents = {str(one.get("id") or ""): one.get("parent") for one in actors}
    for actor_id in parents:
        seen, at = {actor_id}, parents[actor_id]
        while at is not None and at in parents:
            if at in seen:
                problems.append(f"{actor_id} is in a cycle of actors bringing each other in; break it.")
                break
            seen.add(at)
            at = parents[at]
    return problems


def _scenarios_using(suite_files: dict[str, bytes], actor_id: str) -> list[str]:
    from simulate.services.harness_versioning.versions import suite_documents

    return sorted(
        str(scenario.get("scenario_key") or scenario.get("name") or "")
        for scenario in suite_documents(suite_files)
        if any(isinstance(one, dict) and one.get("id") == actor_id for one in scenario.get("actors") or [])
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
    head = _head(environment)
    environment_files, suite_files, draft = _sides(head)
    current_revision = draft.revision if draft is not None else 0
    if expected_revision is not None and expected_revision != current_revision:
        raise _error("draft_changed", "the actors were edited elsewhere since you read them; read them again", 409)
    actors = _listing(environment_files)
    index = next((i for i, one in enumerate(actors) if one.get("id") == actor_id), None)
    if op == "create":
        if index is not None:
            raise _error("actor_exists", f"{actor_id} is already an actor; replace it instead", 409)
        actors.append(_stamped({**(body or {}), "id": actor_id}, user))
    elif op == "replace":
        if index is None:
            raise _error("actor_not_found", f"{actor_id} is not an actor of this environment", 404)
        actors[index] = _stamped({**(body or {}), "id": actor_id}, user)
    elif op == "delete":
        if index is None:
            raise _error("actor_not_found", f"{actor_id} is not an actor of this environment", 404)
        using = _scenarios_using(suite_files, actor_id)
        if using:
            raise _error(
                "actor_in_use", f"{actor_id} takes part in {', '.join(using)}; take it out of them first", 409
            )
        actors.pop(index)
    else:
        raise _error("actor_op_unknown", f"unknown change {op!r}", 400)
    problems = actor_problems(actors)
    if problems:
        raise _error("actor_invalid", " ".join(problems), 400)
    environment_files = {
        **environment_files,
        ACTORS_FILE: (json.dumps({"schema": ACTORS_SCHEMA, "actors": actors}, indent=2) + "\n").encode("utf-8"),
    }
    expected = {
        "environment": drafts.DraftRef(str(draft.id), draft.revision) if draft is not None else drafts.NO_DRAFT
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
    return {**actor, "provenance": {"origin": "editor", "by": str(getattr(user, "id", "") or "") or None, "fields": fields}}


def actor_view(
    environment: HostedHarnessJob, actor_id: str, *, scenario_key: str | None = None
) -> dict[str, Any]:
    """What one actor is given (each input, its value and source), what it may do in the world, and what it never
    sees; optionally in one scenario."""
    from simulate.services.harness_versioning.versions import suite_documents

    head = _head(environment)
    environment_files, suite_files, _ = _sides(head)
    actors = _listing(environment_files)
    actor = next((one for one in actors if one.get("id") == actor_id), None)
    if actor is None:
        raise _error("actor_not_found", f"{actor_id} is not an actor of this environment", 404)
    scenario = None
    if scenario_key is not None:
        scenario = next(
            (one for one in suite_documents(suite_files) if (one.get("scenario_key") or one.get("name")) == scenario_key),
            None,
        )
        if scenario is None:
            raise _error("scenario_not_found", f"{scenario_key} is not a scenario of this environment", 404)
    entry = next(
        (one for one in (scenario or {}).get("actors") or [] if isinstance(one, dict) and one.get("id") == actor_id),
        None,
    )
    sees = []
    for source, field in BRIEF_INPUTS:
        if source == "actor":
            value = actor.get(field)
        elif source == "scenario":
            value = (entry or {}).get(field) if entry is not None else None
        else:
            value = (
                str(scenario.get("instruction") or "").split("Your details:")[0].strip()
                if scenario is not None and actor.get("side") != "caller"
                else None
            )
        if value in (None, "", [], {}):
            continue
        sees.append({"input": f"{source}.{field}", "value": value, "source": source})
    never = [f"scenario.{name}" for name in EVALUATOR_ONLY_FIELDS] + [f"file.{name}" for name in EVALUATOR_ONLY_FILES]
    never += [f"{one.get('id')}.{name}" for one in actors if one.get("id") != actor_id for name in _OTHERS_PRIVATE]
    return {
        "actor_id": actor_id,
        "permitted_actions": list(actor.get("permitted_actions") or []),
        "sees": sees,
        "never_sees": never,
    }
