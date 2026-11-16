"""An environment's actors, edited as one list in its draft and checked for structure only."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypedDict, cast

import structlog
from django.db import transaction

from simulate.models import HostedHarnessJob
from simulate.services.harness_actor_fields import record_defaults
from simulate.services.harness_actor_merge import (
    ActorChanges,
    created_defaults,
    id_from,
    merged_record,
    replaced_record,
)
from simulate.services.harness_actor_reads import Cast, cast_view
from simulate.services.harness_actor_rules import (
    ACTORS_FILE,
    LIMIT_FIELDS,
    ActorLimits,
    ActorRecord,
    ActorRules,
    UnresolvedPoint,
    actor_models,
    actor_not_found,
    actor_rules,
    agent_modality,
    cast_records,
    scenarios_using,
    stored_limits,
    unresolved_points,
    world_stores,
)
from simulate.services.harness_actor_validation import (
    ACTOR_RECORD_MAX_CHARS,
    PermissionConflict,
    Problem,
    actor_problems,
    conflict_error,
    in_use_problem,
    joins_fixed_problem,
    new_choice_problems,
    permission_conflicts,
    problem,
    record_over_by,
    removal_problems,
)
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.drafts import DraftRef
from simulate.services.harness_versioning.versions import suite_documents
from simulate.services.hosted_harness import HostedHarnessError

if TYPE_CHECKING:
    from accounts.models.user import User

logger = structlog.get_logger(__name__)


def accept_unresolved(
    environment: HostedHarnessJob,
    *,
    policy: str,
    user: User | None,
    expected_revision: int | None,
) -> Cast:
    """Accept the cast as written for one policy point the builder could not model: the point leaves the list the
    person is asked to decide. Refuses `unresolved_not_found` (404) and `draft_changed` (409)."""
    refs, environment_files, suite_files = draft_read(environment, expected_revision)
    points = unresolved_points(environment_files)
    kept = [one for one in points if one.get("policy") != policy]
    if len(kept) == len(points):
        raise HostedHarnessError(
            "unresolved_not_found",
            "that point is no longer waiting for a decision; read the actors again",
            status_code=404,
        )
    return written_cast(
        environment,
        _with_cast(
            environment_files,
            cast_records(environment_files),
            actor_rules(environment_files),
            unresolved=kept,
        ),
        suite_files,
        refs,
        user,
    )


def write_actor_limits(
    environment: HostedHarnessJob,
    *,
    limits: ActorLimits,
    user: User | None,
    expected_revision: int | None,
) -> Cast:
    """Set the environment's actor limits in the draft, as one whole block: each a positive number or null for no
    limit, `model` one of the models its sealed rules list; a field left out of the block takes the channel's default.
    Refuses `limits_invalid` (400)."""
    refs, environment_files, suite_files = draft_read(environment, expected_revision)
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
        elif kind is int and value != int(value):
            # Counts are whole: int(0.5) would store 0, which the harness refuses.
            problems.append(f"{key} is a whole number, or null for no limit")
        else:
            clean[key] = kind(value)
    if problems:
        raise HostedHarnessError("limits_invalid", "; ".join(problems), status_code=400)
    return written_cast(
        environment,
        _with_cast(
            environment_files, cast_records(environment_files), rules, limits=clean
        ),
        suite_files,
        refs,
        user,
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
        raise actor_not_found(actor_id)
    if op == "replace":
        after = _stamped({**body, "id": actor_id}, user, before=actors[index])
        if after.get("channel") != actors[index].get("channel"):
            using = scenarios_using(suite_documents(suite_files), actor_id)
            if using:
                # A scenario's trigger and level were written for the way the actor joins.
                raise HostedHarnessError(
                    "actor_in_use",
                    joins_fixed_problem(
                        str(actors[index].get("name") or actor_id), using
                    ),
                    status_code=409,
                )
        actors[index] = after
        return actors
    using = scenarios_using(suite_documents(suite_files), actor_id)
    if using:
        raise HostedHarnessError(
            "actor_in_use",
            in_use_problem(str(actors[index].get("name") or actor_id), using),
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
    unresolved: list[UnresolvedPoint] | None = None,
) -> dict[str, bytes]:
    """The environment's files with this cast, keeping its limits and unresolved points (or these new ones)."""
    kept = stored_limits(environment_files) if limits is None else limits
    points = unresolved_points(environment_files) if unresolved is None else unresolved
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
        if points:
            body["unresolved"] = points
        files[ACTORS_FILE] = (json.dumps(body, indent=2) + "\n").encode("utf-8")
    return files


class ActorValidation(TypedDict):
    """What a write would say, without writing: its problems, the actions it would share, the id a create gets."""

    problems: list[Problem]
    conflicts: list[PermissionConflict]
    suggested_id: str | None
    over_by: int


class _Verdict(TypedDict):
    refs: dict[str, DraftRef]
    environment_files: dict[str, bytes]
    suite_files: dict[str, bytes]
    actors: list[ActorRecord]
    actor_id: str
    refusal: HostedHarnessError | None
    problems: list[Problem]
    conflicts: list[PermissionConflict]
    over_by: int


def _verdict(
    environment: HostedHarnessJob,
    *,
    op: str,
    actor_id: str | None,
    body: ActorRecord | None,
    changes: ActorChanges | None,
    new_id: str | None,
    user: User | None,
    expected_revision: int | None,
    share_actions: list[str] | None,
) -> _Verdict:
    """Every check a write runs, without writing: the cast after the edit, and what refuses it."""
    # The scenario count is read-side only; it is never written into a record.
    record = cast(
        ActorRecord,
        {name: value for name, value in (body or {}).items() if name != "scenarios"},
    )
    sent_id = record.pop("id", None)
    if sent_id and new_id and sent_id != new_id:
        raise HostedHarnessError(
            "actor_id_mismatch", "an actor's id never changes", status_code=400
        )
    given = sent_id or new_id or actor_id
    if op != "delete" and changes is None and not given:
        raise HostedHarnessError(
            "actor_id_missing", "give the actor an id", status_code=400
        )
    if op != "delete" and actor_id is not None and given != actor_id:
        raise HostedHarnessError(
            "actor_id_mismatch", "an actor's id never changes", status_code=400
        )
    refs, environment_files, suite_files = draft_read(environment, expected_revision)
    before = cast_records(environment_files)
    rules = actor_rules(environment_files)
    stored_one = next((one for one in before if one.get("id") == given), None)
    if body is not None and op == "replace" and stored_one is not None:
        record = replaced_record(record, stored_one, record_defaults(rules))
    if changes is not None and op != "delete":
        stored = stored_one
        if op == "replace" and stored is None:
            raise actor_not_found(str(given))
        if not given:
            taken = {str(one.get("id") or "") for one in before}
            given = id_from(
                changes.get("name", ""), taken | set(rules.get("reserved_ids") or [])
            )
        loud = list((rules.get("loudness") or {}).get("channels") or [])
        record = merged_record(
            stored
            if op == "replace" and stored is not None
            else created_defaults(rules),
            changes,
            loud_channels=loud,
        )
        record.pop("id", None)
    actor_id = str(given or "")
    over_by = (
        record_over_by(cast(ActorRecord, {**record, "id": actor_id}))
        if op != "delete"
        else 0
    )
    verdict: _Verdict = {
        "refs": refs,
        "environment_files": environment_files,
        "suite_files": suite_files,
        "actors": before,
        "actor_id": actor_id,
        "refusal": None,
        "problems": [],
        "conflicts": [],
        "over_by": over_by,
    }
    confirmed = set(share_actions or [])
    edited = cast(ActorRecord, {**record, "id": actor_id})
    if op != "delete":
        verdict["conflicts"] = [
            one
            for one in permission_conflicts(before, edited)
            if one["action"] not in confirmed
        ]
    try:
        actors = _edited(
            before,
            op=op,
            actor_id=actor_id,
            body=record,
            user=user,
            suite_files=suite_files,
        )
    except HostedHarnessError as exc:
        if exc.code not in ("actor_exists", "actor_in_use"):
            raise
        verdict["refusal"] = exc
        return verdict
    verdict["actors"] = actors
    if op == "delete":
        verdict["problems"] = [
            problem(None, one)
            for one in removal_problems(actors, actor_id, rules, before)
        ]
        return verdict
    problems = actor_problems(actors, actor_id, rules, new_id=op == "create")
    # The choices are read only from a record of the right shape: a list of numbers is a type to fix first.
    problems = problems or [
        problem(None, one)
        for one in new_choice_problems(
            next((one for one in before if one.get("id") == actor_id), None),
            next(one for one in actors if one.get("id") == actor_id),
            environment_files,
            rules,
            suite_files,
            agent_modality(environment_files),
            world_stores(environment),
        )
    ]
    if over_by:
        problems.append(
            problem(None, f"Keep an actor under {ACTOR_RECORD_MAX_CHARS} characters.")
        )
    verdict["problems"] = problems
    return verdict


def _refusal_problem(refusal: HostedHarnessError, op: str) -> Problem:
    """A refusal that stops an edit before its record is checked, as a problem about its field."""
    field = (
        "id"
        if refusal.code == "actor_exists"
        else "channel"
        if op == "replace"
        else None
    )
    return problem(field, refusal.message)


def check_actor_edit(
    environment: HostedHarnessJob,
    *,
    op: str,
    actor_id: str | None,
    body: ActorRecord | None,
    user: User | None,
    expected_revision: int | None,
    share_actions: list[str] | None = None,
    changes: ActorChanges | None = None,
    new_id: str | None = None,
) -> ActorValidation:
    """What `write_actor_edit` would say about this edit, writing nothing: its problems (a refusal included), the
    actions it would share that `share_actions` does not confirm, the id a create would get and how far the record
    is over its bound. Refuses as a write does only when there is nothing to check: `actor_not_found` (404),
    `draft_changed` (409), `actor_id_missing` and `actor_id_mismatch` (400)."""
    verdict = _verdict(
        environment,
        op=op,
        actor_id=actor_id,
        body=body,
        changes=changes,
        new_id=new_id,
        user=user,
        expected_revision=expected_revision,
        share_actions=share_actions,
    )
    refusal = verdict["refusal"]
    problems = (
        [_refusal_problem(refusal, op)] if refusal is not None else []
    ) + verdict["problems"]
    logger.info(
        "harness_actor_edit_checked",
        environment_id=str(environment.id),
        op=op,
        problems=len(problems),
        conflicts=len(verdict["conflicts"]),
    )
    return {
        "problems": problems,
        "conflicts": verdict["conflicts"],
        "suggested_id": verdict["actor_id"] if op == "create" else None,
        "over_by": verdict["over_by"],
    }


def write_actor_edit(
    environment: HostedHarnessJob,
    *,
    op: str,
    actor_id: str | None,
    body: ActorRecord | None,
    user: User | None,
    expected_revision: int | None,
    share_actions: list[str] | None = None,
    changes: ActorChanges | None = None,
    new_id: str | None = None,
) -> Cast:
    """Create, replace or delete one actor in the environment draft and return the cast after it.

    The edit is a whole record (`body`), or a patch (`changes`) merged into the stored record; a create made from a
    patch without `new_id` gets an id made from its name. `expected_revision` is the draft revision the caller read;
    an edit made elsewhere since is refused with `draft_changed` (409). Also refuses `actor_exists`, `actor_in_use`
    (409), `actor_not_found` (404) and `actor_invalid` (400), `actor_id_missing` and `actor_id_mismatch` (400),
    `permission_conflict` (409, with each conflict) when the edit gives an actor an action only one other actor holds
    and `share_actions` does not confirm it, and retires the builder chat's runtime once the draft is written.
    `actor_id` is the id in the address, if any; a create may give it in the body instead.
    """
    verdict = _verdict(
        environment,
        op=op,
        actor_id=actor_id,
        body=body,
        changes=changes,
        new_id=new_id,
        user=user,
        expected_revision=expected_revision,
        share_actions=share_actions,
    )
    if verdict["refusal"] is not None:
        raise verdict["refusal"]
    if verdict["problems"]:
        raise HostedHarnessError(
            "actor_invalid",
            " ".join(one["message"] for one in verdict["problems"]),
            status_code=400,
        )
    if verdict["conflicts"]:
        raise conflict_error(verdict["conflicts"])
    return written_cast(
        environment,
        _with_cast(
            verdict["environment_files"],
            verdict["actors"],
            actor_rules(verdict["environment_files"]),
        ),
        verdict["suite_files"],
        verdict["refs"],
        user,
    )


def draft_read(
    environment: HostedHarnessJob, expected_revision: int | None
) -> tuple[dict[str, DraftRef], dict[str, bytes], dict[str, bytes]]:
    """Both pieces' draft refs and files, refusing `draft_changed` (409) when the caller read an older revision. The
    refs are read before the files, so an edit landing on either piece in between fails the write's check of both."""
    refs = drafts.draft_refs(environment)
    sides = drafts.edit_sides(environment)
    if (
        expected_revision is not None
        and expected_revision != refs["environment"].revision
    ):
        raise HostedHarnessError(
            "draft_changed",
            "the actors were edited elsewhere since you read them; read them again",
            status_code=409,
        )
    return refs, sides["environment"], sides["suite"]


def written_cast(
    environment: HostedHarnessJob,
    environment_files: dict[str, bytes],
    suite_files: dict[str, bytes],
    refs: dict[str, DraftRef],
    user: User | None,
) -> Cast:
    """Record the environment side of the draft and return the cast as it now reads. Both pieces' refs are checked:
    a scenario saved meanwhile may name an actor this edit removes."""
    try:
        written = drafts.record_draft(
            environment,
            archives.pack_archive(archives.compose(environment_files, suite_files)),
            origin="manual",
            user=user,
            expected=dict(refs),
            pieces={"environment"},
            # Validated against the scenarios: a scenario edit landing meanwhile refuses this one.
            checked=set(drafts.DRAFT_PIECES),
        )
    except HostedHarnessError as exc:
        if exc.code != "draft_changed":
            raise
        raise HostedHarnessError(
            "draft_changed",
            "the actors or scenarios were edited elsewhere since you read them; read them again",
            status_code=409,
        ) from exc
    if written is None:
        raise HostedHarnessError(
            "drafts_unavailable",
            "this environment does not keep drafts",
            status_code=409,
        )
    transaction.on_commit(lambda: drafts.retire_chat_runtime(environment))
    draft = written.environment
    return cast_view(
        environment_files,
        suite_files,
        draft.revision if draft is not None else None,
        world_stores(environment),
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
