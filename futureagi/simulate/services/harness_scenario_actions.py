"""Lifecycle actions on one environment's scenarios: validate, archive, restore, revisions.

The views stay thin: each action resolves its environment, then calls one function here. Errors are
``HostedHarnessError`` with a code and a status the views pass through.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.services.canonical_json import canonical_digest
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

REVISIONS_MAX = 50
NO_RESULTS = "Validation finished but recorded no results; try again."
VALIDATE_KEYS_MAX = 50
_KEY_SHAPE = re.compile(r"[A-Za-z0-9_.:-]+")


def _error(code: str, message: str, status_code: int) -> HostedHarnessError:
    return HostedHarnessError(code, message, status_code=status_code)


def _write(
    locked: HostedHarnessJob, suite: list[dict[str, Any]], **kwargs: Any
) -> None:
    """Write the suite draft; a change the archive cannot hold is a 409, never a server error."""
    from simulate.services.harness_versioning.drafts import write_suite_edit
    from simulate.services.hosted_harness_gateway import AuthoringArchiveKept

    try:
        write_suite_edit(locked, suite, **kwargs)
    except AuthoringArchiveKept as exc:
        raise _error(
            "scenario_files_missing",
            "This change would leave a scenario without its files, so it was not made. "
            "Ask the builder chat to make it instead.",
            409,
        ) from exc


def _of_environment(
    model: Any, environment: HostedHarnessJob, value: Any, default: Any
) -> Any:
    if value in (None, ""):
        return (
            model._base_manager.filter(id=default, job_id=environment.id).first()
            if default
            else None
        )
    found = model._base_manager.filter(
        id=value, job_id=environment.id, deleted=False
    ).first()
    if found is None:
        raise _error(
            "versions_invalid", "those versions are not this environment's", 400
        )
    return found


def _pair(environment: HostedHarnessJob, body: Mapping[str, Any]):
    from simulate.models import AgentVersion

    agent_id = body.get("agent_version_id") or environment.agent_version_id
    agent = (
        AgentVersion.no_workspace_objects.filter(id=agent_id).first()
        if agent_id
        else None
    )
    if body.get("agent_version_id") and agent is None:
        raise _error(
            "versions_invalid", "that agent version is not this environment's", 400
        )
    environment_version = _of_environment(
        HarnessEnvironmentVersion,
        environment,
        body.get("environment_version_id"),
        environment.environment_version_id,
    )
    suite_version = _of_environment(
        HarnessSuiteVersion,
        environment,
        body.get("suite_version_id"),
        environment.suite_version_id,
    )
    return agent, environment_version, suite_version


def _last_change(
    environment: HostedHarnessJob, suite_version: Any, environment_version: Any
) -> Any:
    from django.db.models import Max

    proved = (
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=suite_version, environment_version=environment_version
        )
        .values_list("updated_at", flat=True)
        .first()
    )
    archived = HostedHarnessScenario.no_workspace_objects.filter(
        job=environment
    ).aggregate(newest=Max("archived_at"))["newest"]
    return max((one for one in (proved, archived) if one is not None), default=None)


def validate_scenarios(
    environment: HostedHarnessJob, body: Mapping[str, Any]
) -> tuple[dict[str, Any], int]:
    """Start proving every scenario of the pair; ``(body, status)``. A live job is reported, never
    refused; "proofs already exist" never skips one, unless the caller asks with ``if_fresh``."""
    from simulate.services.harness_versioning import validation

    keys = body.get("keys")
    if keys is not None and (
        not isinstance(keys, list)
        or not keys
        or len(keys) > VALIDATE_KEYS_MAX
        or not all(isinstance(one, str) and _KEY_SHAPE.fullmatch(one) for one in keys)
    ):
        raise _error(
            "keys_invalid",
            f"keys must be 1 to {VALIDATE_KEYS_MAX} scenario keys of letters, digits and ._:-",
            400,
        )
    agent, environment_version, suite_version = _pair(environment, body)
    if agent is None or environment_version is None or suite_version is None:
        raise _error(
            "cannot_validate",
            "This agent version can't be validated here: the environment has no versions",
            422,
        )
    if not validation.can_validate(environment, agent.id):
        raise _error(
            "cannot_validate",
            "This agent version can't be validated here: validation is not enabled for it",
            422,
        )
    if body.get("if_fresh") and keys is None:
        newest = (
            HostedHarnessJob.no_workspace_objects.filter(
                environment_id=environment.id,
                suite_version=suite_version,
                environment_version=environment_version,
                state=HostedHarnessJob.State.COMPLETED,
                payload__metadata__scenario_proving__in=["only", "with_certify"],
            )
            .order_by("-updated_at")
            .first()
        )
        changed = _last_change(environment, suite_version, environment_version)
        if newest is not None and (changed is None or newest.updated_at >= changed):
            return {
                "state": "ready",
                "job_id": str(newest.id),
                "finished_at": newest.updated_at.isoformat(),
            }, 200
    started = validation.start_validation(
        environment,
        agent,
        environment_version,
        suite_version,
        purpose="prove",
        scenario_keys=keys,
    )
    if started is None:
        raise _error(
            "cannot_validate",
            "This agent version can't be validated here: validation is not available",
            422,
        )
    if started.state == validation.CHECK_UNAVAILABLE:
        return {"state": "unavailable", "message": validation.UNAVAILABLE_REASON}, 200
    job = started.job
    deadline = validation.check_deadline(job) if job is not None else None
    return {
        "state": "validating",
        "job_id": str(job.id) if job is not None else None,
        "deadline_at": deadline.isoformat() if deadline is not None else None,
    }, 202


def validation_status(environment: HostedHarnessJob, job_id: Any) -> dict[str, Any]:
    from simulate.services.harness_versioning import validation

    job = HostedHarnessJob.no_workspace_objects.filter(
        id=job_id, environment_id=environment.id, payload__metadata__certify_only=True
    ).first()
    if job is None:
        raise _error(
            "job_not_found", "No validation of this environment has that id", 404
        )
    metadata = (job.payload or {}).get("metadata") or {}
    recorded = metadata.get("scenario_proofs_recorded") is True
    if job.state in validation._LIVE_STATES:
        state, message, finished = "validating", None, None
        recorded = False
    elif job.state == HostedHarnessJob.State.COMPLETED:
        state, finished = "finished", job.updated_at.isoformat()
        message = None if recorded else NO_RESULTS
    elif validation.ended_on_our_side(job):
        state, finished = "unavailable", job.updated_at.isoformat()
        message = validation.UNAVAILABLE_REASON
    else:
        state, finished = "failed", job.updated_at.isoformat()
        message = validation.failed_reason(job.failure or {})
    return {
        "state": state,
        "job_id": str(job.id),
        "deadline_at": validation.check_deadline(job).isoformat(),
        "finished_at": finished,
        "proofs_recorded": recorded,
        "message": message,
    }


def _row(environment: HostedHarnessJob, key: str) -> HostedHarnessScenario | None:
    from simulate.services.hosted_harness_gateway import _scenario_token

    token = _scenario_token(key)
    return next(
        (
            row
            for row in HostedHarnessScenario.no_workspace_objects.filter(
                job=environment, deleted=False
            )
            if _scenario_token(row.scenario_key) == token
        ),
        None,
    )


def _suite_now(environment: HostedHarnessJob) -> list[dict[str, Any]]:
    from simulate.services.harness_versioning.drafts import edit_sides
    from simulate.services.harness_versioning.versions import suite_documents

    return [dict(one) for one in suite_documents(edit_sides(environment)["suite"])]


def _lifecycle_of(environment: HostedHarnessJob, key: str) -> Any:
    from simulate.services.harness_scenario_lifecycle import (
        in_draft_view,
        scenario_lifecycle,
    )

    environment.refresh_from_db()
    views = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
        keys=[key],
    )
    return views.get(key) or in_draft_view()


def archive_scenario(
    environment: HostedHarnessJob, key: str, *, reason: str, user: Any
) -> dict[str, Any]:
    """Take a case out of future selection, keeping its identity, runs and evidence."""
    from simulate.services.harness_versioning import read as version_reads
    from simulate.services.harness_versioning.drafts import draft_rows
    from simulate.services.harness_versioning.versions import lock_environment
    from simulate.services.hosted_harness_gateway import _scenario_token

    version_reads.require_versioning()
    with transaction.atomic():
        locked = lock_environment(environment)
        row = _row(locked, key)
        drafted = draft_rows(locked)
        if row is None:
            added = any(
                _scenario_token(one.scenario_key) == _scenario_token(key)
                and one.draft_change == "added"
                for one in drafted or []
            )
            if added:
                raise _error(
                    "scenario_unsaved",
                    "This scenario isn't saved yet; remove it instead.",
                    409,
                )
            raise _error(
                "scenario_not_found", "No scenario of that key in this environment", 404
            )
        if row.archived_at is not None:
            raise _error("scenario_archived", "This scenario is already archived", 409)
        suite = _suite_now(locked)
        content = next(
            (
                one
                for one in suite
                if _scenario_token(one.get("scenario_key"))
                == _scenario_token(row.scenario_key)
            ),
            None,
        )
        if content is None:
            member = (
                HarnessSuiteMember.no_workspace_objects.filter(
                    suite_version_id=locked.suite_version_id,
                    scenario_key=row.scenario_key,
                )
                .values_list("content", flat=True)
                .first()
            )
            content = dict(member or {})
        # No draft change: selection holds an archived case back at once, and the next Save
        # leaves it out of the suite version it makes.
        row.archived_at = timezone.now()
        row.archived_by = user if getattr(user, "is_authenticated", False) else None
        row.archive_reason = (reason or "")[:255]
        row.archived_content = content
        row.save(
            update_fields=[
                "archived_at",
                "archived_by",
                "archive_reason",
                "archived_content",
                "updated_at",
            ]
        )
    logger.info(
        "harness_scenario_archived",
        environment_id=str(environment.id),
        scenario_key=row.scenario_key,
    )
    return {
        "scenario_key": row.scenario_key,
        "lifecycle": _lifecycle_of(environment, row.scenario_key),
    }


def restore_scenario(
    environment: HostedHarnessJob, key: str, *, user: Any
) -> dict[str, Any]:
    """Bring an archived case back. One still in the saved suite runs again at once with its own
    lifecycle; one a Save already left out goes back into the suite draft and reads Draft until saved."""
    from simulate.services.harness_scenario_lifecycle import in_draft_view
    from simulate.services.harness_versioning import read as version_reads
    from simulate.services.harness_versioning.drafts import archived_folder_files
    from simulate.services.harness_versioning.versions import lock_environment

    version_reads.require_versioning()
    with transaction.atomic():
        locked = lock_environment(environment)
        row = _row(locked, key)
        if row is None:
            raise _error(
                "scenario_not_found", "No scenario of that key in this environment", 404
            )
        if row.archived_at is None:
            raise _error("scenario_not_archived", "This scenario is not archived", 409)
        content = dict(row.archived_content or {})
        content["scenario_key"] = row.scenario_key
        # Cleared first: Save refuses a new scenario that takes an archived key.
        row.archived_at = None
        row.archived_by = None
        row.archive_reason = ""
        row.archived_content = None
        row.save(
            update_fields=[
                "archived_at",
                "archived_by",
                "archive_reason",
                "archived_content",
                "updated_at",
            ]
        )
        live = HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=locked.suite_version_id, scenario_key=row.scenario_key
        ).exists()
        if not live:
            _write(
                locked,
                [*_suite_now(locked), content],
                user=user,
                restored_files=archived_folder_files(locked, row.scenario_key),
            )
    logger.info(
        "harness_scenario_restored",
        environment_id=str(environment.id),
        scenario_key=row.scenario_key,
        live=live,
    )
    return {
        "scenario_key": row.scenario_key,
        "live": live,
        "lifecycle": _lifecycle_of(environment, row.scenario_key)
        if live
        else in_draft_view(),
    }


def scenario_revisions(environment: HostedHarnessJob, key: str) -> dict[str, Any]:
    """Each distinct content of a case across the environment's active suite versions, newest first."""
    from simulate.services.harness_scenario_lifecycle import scenario_lifecycle

    members = list(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version__job_id=environment.id,
            suite_version__status="active",
            suite_version__deleted=False,
            scenario_key=key,
        )
        .order_by("suite_version__version_number")
        .values_list("suite_version__version_number", "suite_version_id", "content")
    )
    if not members and _row(environment, key) is None:
        raise _error(
            "scenario_not_found", "No scenario of that key in this environment", 404
        )
    revisions: list[dict[str, Any]] = []
    previous: Mapping[str, Any] | None = None
    for number, suite_id, content in members:
        content = content if isinstance(content, Mapping) else {}
        digest = canonical_digest(content)
        if revisions and revisions[-1]["digest"] == digest:
            revisions[-1]["suite_versions"].append(number)
            continue
        changed = sorted(
            name
            for name in set(content) | set(previous or {})
            if previous is not None and content.get(name) != previous.get(name)
        )
        revisions.append(
            {
                "revision": len(revisions) + 1,
                "digest": digest,
                "suite_versions": [number],
                "first_suite_version": number,
                "changed_fields": changed,
                "_suite_id": suite_id,
            }
        )
        previous = content
    environment_version = environment.environment_version
    for one in revisions:
        suite = HarnessSuiteVersion._base_manager.filter(
            id=one.pop("_suite_id")
        ).first()
        views = scenario_lifecycle(
            environment,
            suite_version=suite,
            environment_version=environment_version,
            keys=[key],
        )
        one["lifecycle_on_default_environment"] = views.get(key)
    revisions.reverse()
    return {
        "scenario_key": key,
        "revisions": revisions[:REVISIONS_MAX],
        "truncated": len(revisions) > REVISIONS_MAX,
    }


def not_active(environment: HostedHarnessJob) -> list[dict[str, Any]]:
    """The cases of the default suite that are not Active on the new defaults, for the Save response."""
    from simulate.services.harness_scenario_lifecycle import scenario_lifecycle

    environment.refresh_from_db()
    views = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    )
    return [
        {
            "scenario_key": key,
            "reason": view["reason"],
            "reason_text": view["reason_text"],
        }
        for key, view in views.items()
        if view["state"] not in ("active", "archived")
    ]


SOURCE_CHANGES_MAX = 200
TEXT_PREVIEW = 2000


def _file_texts(version: Any, paths: set[str]) -> dict[str, str | None]:
    from simulate.services.harness_scenario_sources import file_bodies

    texts: dict[str, str | None] = {}
    for path, body in file_bodies(version, paths).items():
        try:
            texts[path] = body.decode("utf-8")[:TEXT_PREVIEW]
        except UnicodeDecodeError:
            texts[path] = None
    return texts


def _reworded(
    before: str | None, old_rules: list[str], new_rules: list[str]
) -> str | None:
    """The rule the version added that reads most like the cited one it dropped, so a reworded
    rule compares with its new wording rather than with nothing."""
    from difflib import SequenceMatcher

    if not before:
        return None
    added = [rule for rule in new_rules if rule not in old_rules]
    scored = [
        (SequenceMatcher(None, before.lower(), rule.lower()).ratio(), rule)
        for rule in added
    ]
    best = max(scored, default=(0.0, None))
    return best[1] if best[0] >= 0.6 else None


def source_changes(environment: HostedHarnessJob, version_id: Any) -> dict[str, Any]:
    """What changed between an environment version and its parent in the rules and files the
    default suite cites, each with the cases that cite it."""
    from simulate.services.harness_scenario_lifecycle import (
        _matched_rule,
        _parent_documents,
        _rules_of,
        changed_refs,
    )

    version = HarnessEnvironmentVersion._base_manager.filter(
        id=version_id, job_id=environment.id, deleted=False
    ).first()
    if version is None:
        raise _error("version_not_found", "no such version in this environment", 404)
    parent_documents = _parent_documents(version) or {}
    parent = (
        HarnessEnvironmentVersion._base_manager.filter(id=version.parent_id).first()
        if version.parent_id
        else None
    )
    contents = dict(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=environment.suite_version_id
        ).values_list("scenario_key", "content")
    )
    changed = (
        changed_refs(version, contents, parent_documents=parent_documents)
        if parent
        else {}
    )
    new_rules, old_rules = (
        _rules_of(version.documents or {}),
        _rules_of(parent_documents),
    )
    by_change: dict[tuple[str, str], dict[str, Any]] = {}
    files: set[str] = set()
    for key, refs in changed.items():
        for source in (contents.get(key) or {}).get("sources") or []:
            if (
                not isinstance(source, Mapping)
                or str(source.get("ref") or "") not in refs
            ):
                continue
            kind, ref = str(source.get("kind")), str(source.get("ref") or "")
            entry = by_change.setdefault(
                (kind, ref),
                {
                    "kind": kind,
                    "ref": ref,
                    "before": None,
                    "after": None,
                    "cited_by": [],
                },
            )
            if key not in entry["cited_by"]:
                entry["cited_by"].append(key)
            if kind == "rule":
                quote = str(source.get("quote") or "")
                entry["before"] = _matched_rule(quote, old_rules)
                entry["after"] = _matched_rule(quote, new_rules) or _reworded(
                    entry["before"], old_rules, new_rules
                )
            else:
                files.add(ref.removeprefix("file:").strip().lstrip("/"))
    before_texts = _file_texts(parent, files) if parent else {}
    after_texts = _file_texts(version, files)
    for (kind, ref), entry in by_change.items():
        if kind == "file":
            path = ref.removeprefix("file:").strip().lstrip("/")
            entry["before"] = before_texts.get(path)
            entry["after"] = after_texts.get(path)
    changes = sorted(by_change.values(), key=lambda one: (one["kind"], one["ref"]))
    return {
        "from_version": getattr(parent, "version_number", None),
        "to_version": version.version_number,
        "changes": changes[:SOURCE_CHANGES_MAX],
        "truncated": len(changes) > SOURCE_CHANGES_MAX,
    }
