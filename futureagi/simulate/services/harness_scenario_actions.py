"""Lifecycle actions on one environment's scenarios: validate, archive, restore, revisions.

The views stay thin: each action resolves its environment, then calls one function here. Errors are
``HostedHarnessError`` with a code and a status the views pass through.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, NotRequired, Required, TypedDict

import structlog
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from simulate.models import (
    AgentVersion,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.services.canonical_json import canonical_digest
from simulate.services.harness_scenario_sources import file_path
from simulate.services.hosted_harness import HostedHarnessError, not_pickable

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from simulate.services.harness_scenario_lifecycle import LifecycleView

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


def _environment_piece(
    piece: str, model: Any, environment: HostedHarnessJob, value: Any, default: Any
) -> Any:
    if value in (None, ""):
        return (
            model._base_manager.filter(id=default, job_id=environment.id).first()
            if default
            else None
        )
    try:
        found = model._base_manager.filter(
            id=value, job_id=environment.id, deleted=False
        ).first()
    except (ValueError, DjangoValidationError):
        found = None
    if found is None:
        raise not_pickable(piece)
    return found


def resolve_versions(
    environment: HostedHarnessJob, query: Mapping[str, Any]
) -> tuple[AgentVersion | None, Any, Any]:
    """The agent, environment and suite versions a request names, each the environment's default when not named.
    An agent version is this environment's only by the rule readiness and runs use (its workspace, organization,
    agent and, for a shared agent, its own lineage); an environment or suite version only when this environment
    holds it, saved or not. Refuses as a run's pick does (422 `version_not_pickable`, naming the piece), and for an
    id that is not one."""
    from simulate.services.harness_readiness.report import require_environments_agent

    agent_id = query.get("agent_version_id") or None
    if agent_id is not None:
        try:
            require_environments_agent(environment, agent_id)
        except (HostedHarnessError, ValueError, DjangoValidationError) as exc:
            raise not_pickable("agent") from exc
    agent_id = agent_id or environment.agent_version_id
    agent = (
        AgentVersion.no_workspace_objects.filter(id=agent_id).first()
        if agent_id
        else None
    )
    return (
        agent,
        _environment_piece(
            "environment",
            HarnessEnvironmentVersion,
            environment,
            query.get("environment_version_id"),
            environment.environment_version_id,
        ),
        _environment_piece(
            "suite",
            HarnessSuiteVersion,
            environment,
            query.get("suite_version_id"),
            environment.suite_version_id,
        ),
    )


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


class ValidateAnswer(TypedDict, total=False):
    """Validate scenarios' answer: `validating` with its job, `ready` when proofs are fresh, or `unavailable`."""

    state: Required[Literal["validating", "ready", "unavailable"]]
    job_id: str | None
    deadline_at: str | None
    finished_at: str
    message: str


class ValidationStatus(TypedDict):
    state: Literal["validating", "finished", "failed", "unavailable"]
    job_id: str
    deadline_at: str
    finished_at: str | None
    proofs_recorded: bool
    message: str | None
    # How many of the pair's cases hold a proof made with this agent version on these versions, of how many.
    proved: int
    total: int


# A case held for one of these has no proof of its own on the pair yet: never proved, not reached, or proved with
# another agent version. Validate proves these; a case whose proof ran keeps it until its content or versions change.
UNPROVED_REASONS = frozenset(
    {
        "not_validated",
        "validation_incomplete",
        "proved_on_other_agent",
        "tools_unread",
        "validation_failed",
        "validation_ended_on_our_side",
        "proofs_unread",
        "validation_unavailable",
    }
)
# How long a validation's progress counts are kept between polls of a large suite.
PROGRESS_CACHE_SECONDS = 10


def cases_to_prove(
    environment: HostedHarnessJob,
    agent: Any,
    environment_version: Any,
    suite_version: Any,
) -> tuple[list[str], int]:
    """The pair's cases without a proof of their own for this agent version, and how many cases it holds (archived
    ones aside). A case proved only the older way (a legacy proof, or one that is not format 2) counts as not proved,
    so a Validate brings it up to date; it keeps running as it does until then."""
    from simulate.models import HarnessSuiteValidation
    from simulate.services.harness_scenario_lifecycle import scenario_lifecycle

    views = scenario_lifecycle(
        environment,
        suite_version=suite_version,
        environment_version=environment_version,
        agent_version_id=getattr(agent, "id", agent),
    )
    proofs = (
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=suite_version, environment_version=environment_version
        )
        .values_list("proofs", flat=True)
        .first()
    ) or {}
    live = {key: view for key, view in views.items() if view["state"] != "archived"}
    needed = sorted(
        key
        for key, view in live.items()
        if (view["state"] == "draft" and view["reason"] in UNPROVED_REASONS)
        or view.get("legacy")
        or (isinstance(proofs.get(key), dict) and proofs[key].get("format") != 2)
    )
    return needed, len(live)


class LifecycleAnswer(TypedDict):
    """An archive's or a restore's answer: the case's lifecycle after it, and on restore whether it runs at once."""

    scenario_key: str
    lifecycle: LifecycleView
    live: NotRequired[bool]


class ScenarioRevision(TypedDict):
    revision: int
    digest: str
    # A version number is null only for a version saved before versions were numbered.
    suite_versions: list[int | None]
    first_suite_version: int | None
    changed_fields: list[str]
    lifecycle_on_default_environment: NotRequired[LifecycleView | None]


class ScenarioRevisions(TypedDict):
    scenario_key: str
    revisions: list[ScenarioRevision]
    truncated: bool


class NotActiveCase(TypedDict):
    scenario_key: str
    reason: str | None
    reason_text: str | None


class SourceChange(TypedDict):
    """One cited rule or file that changed, its text before and after, and the cases that cite it."""

    kind: str
    ref: str
    before: str | None
    after: str | None
    cited_by: list[str]


class SourceChanges(TypedDict):
    from_version: int | None
    to_version: int | None
    changes: list[SourceChange]
    truncated: bool


def validate_scenarios(
    environment: HostedHarnessJob, body: Mapping[str, Any]
) -> tuple[ValidateAnswer, int]:
    """Start proving every scenario of the pair; ``(body, status)``. A live job is reported, never
    refused; "proofs already exist" never skips one, unless the caller asks with ``if_fresh``."""
    from simulate.services.harness_versioning import validation

    keys = body.get("keys")
    # The person named the cases (or all of them): those are proved again, proof or not, and the progress counts them.
    asked = keys is not None or bool(body.get("reprove_all"))
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
    agent, environment_version, suite_version = resolve_versions(environment, body)
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
            return ValidateAnswer(
                state="ready",
                job_id=str(newest.id),
                finished_at=newest.updated_at.isoformat(),
            ), 200
    if keys is None and not body.get("reprove_all"):
        # Only what has no proof of its own yet: a check the window stopped carries on where it stopped.
        needed, total = cases_to_prove(
            environment, agent, environment_version, suite_version
        )
        if not needed:
            return ValidateAnswer(state="ready"), 200
        keys = needed if len(needed) < total else None
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
        return ValidateAnswer(
            state="unavailable", message=validation.UNAVAILABLE_REASON
        ), 200
    job = started.job
    if asked and job is not None:
        _mark_asked(job, keys)
    deadline = validation.check_deadline(job) if job is not None else None
    return ValidateAnswer(
        state="validating",
        job_id=str(job.id) if job is not None else None,
        deadline_at=deadline.isoformat() if deadline is not None else None,
    ), 202


# On a validation the person scoped: its progress counts the cases it was asked to prove, as this job reaches them.
PROVING_ASKED = "proving_asked"


def _mark_asked(job: HostedHarnessJob, keys: list[str] | None) -> None:
    """Mark the job as proving what was asked, when it proves exactly that (a check already running for another
    scope keeps its own count)."""
    metadata = (job.payload or {}).get("metadata") or {}
    if (metadata.get("scenario_keys") or None) != (list(keys) if keys else None):
        return
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        payload={**job.payload, "metadata": {**metadata, PROVING_ASKED: True}}
    )
    job.payload = {**job.payload, "metadata": {**metadata, PROVING_ASKED: True}}


def _asked_progress(
    environment: HostedHarnessJob, job: HostedHarnessJob
) -> tuple[int, int]:
    """How many of the cases a scoped validation was asked to prove it has reached, of how many: its keys, else every
    case of the pair not archived. A case counts once its proof for this job's agent version was made by this job."""
    from simulate.models import HarnessSuiteValidation
    from simulate.services.harness_versioning.versions import OTHER_AGENTS, suite_cases

    metadata = (job.payload or {}).get("metadata") or {}
    scope = list(dict.fromkeys(metadata.get("scenario_keys") or []))
    if not scope:
        archived = set(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment, archived_at__isnull=False
            ).values_list("scenario_key", flat=True)
        )
        scope = [
            key for key, _, _ in suite_cases(job.suite_version) if key not in archived
        ]
    proofs = (
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version_id=job.suite_version_id,
            environment_version_id=job.environment_version_id,
        )
        .values_list("proofs", flat=True)
        .first()
    ) or {}

    def reached(entry: object) -> bool:
        if not isinstance(entry, Mapping):
            return False
        own = entry
        if str(entry.get("agent_version_id")) != str(job.agent_version_id):
            own = (entry.get(OTHER_AGENTS) or {}).get(str(job.agent_version_id)) or {}
        return (
            str(own.get("job_id")) == str(job.id)
            and own.get("blocked_reason") != "validation_incomplete"
        )

    return sum(1 for key in scope if reached(proofs.get(key))), len(scope)


def check_ending(job: HostedHarnessJob) -> tuple[Literal["unavailable", "failed"], str]:
    """How a validation that neither runs nor completed ended: on our side, or failed by the pairing; and why."""
    from simulate.services.harness_versioning import validation

    if validation.ended_on_our_side(job):
        return "unavailable", validation.UNAVAILABLE_REASON
    return "failed", validation.failed_reason(job.failure or {})


def validation_status(environment: HostedHarnessJob, job_id: Any) -> ValidationStatus:
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
    state: Literal["validating", "finished", "failed", "unavailable"]
    if validation.is_live(job):
        state, message, finished = "validating", None, None
        recorded = False
    elif job.state == HostedHarnessJob.State.COMPLETED:
        state, finished = "finished", job.updated_at.isoformat()
        message = None if recorded else NO_RESULTS
    else:
        (state, message), finished = check_ending(job), job.updated_at.isoformat()
    # Polled every few seconds while a large suite is proved: counted at most once per interval while it runs, and
    # afresh once it ends.
    counted = (
        cache.get(f"harness-validation-progress:{job.id}")
        if state == "validating"
        else None
    )
    if counted is None and metadata.get(PROVING_ASKED):
        counted = _asked_progress(environment, job)
    if counted is None:
        needed, total = cases_to_prove(
            environment,
            job.agent_version_id,
            job.environment_version,
            job.suite_version,
        )
        counted = (total - len(needed), total)
        cache.set(
            f"harness-validation-progress:{job.id}", counted, PROGRESS_CACHE_SECONDS
        )
    return ValidationStatus(
        state=state,
        job_id=str(job.id),
        deadline_at=validation.check_deadline(job).isoformat(),
        finished_at=finished,
        proofs_recorded=recorded,
        message=message,
        proved=counted[0],
        total=counted[1],
    )


def _row(environment: HostedHarnessJob, key: str) -> HostedHarnessScenario | None:
    from simulate.services.harness_starting_records import scenario_token

    token = scenario_token(key)
    return next(
        (
            row
            for row in HostedHarnessScenario.no_workspace_objects.filter(
                job=environment, deleted=False
            )
            if scenario_token(row.scenario_key) == token
        ),
        None,
    )


def _suite_now(environment: HostedHarnessJob) -> list[dict[str, Any]]:
    from simulate.services.harness_versioning.drafts import edit_sides
    from simulate.services.harness_versioning.versions import suite_documents

    return [dict(one) for one in suite_documents(edit_sides(environment)["suite"])]


def _lifecycle_of(environment: HostedHarnessJob, key: str) -> LifecycleView:
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
) -> LifecycleAnswer:
    """Take a case out of future selection, keeping its identity, runs and evidence."""
    from simulate.services.harness_starting_records import scenario_token
    from simulate.services.harness_versioning import read as version_reads
    from simulate.services.harness_versioning.drafts import draft_rows
    from simulate.services.harness_versioning.versions import lock_environment

    version_reads.require_versioning()
    with transaction.atomic():
        locked = lock_environment(environment)
        row = _row(locked, key)
        drafted = draft_rows(locked)
        if row is None:
            added = any(
                scenario_token(one.scenario_key) == scenario_token(key)
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
                if scenario_token(one.get("scenario_key"))
                == scenario_token(row.scenario_key)
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
    return LifecycleAnswer(
        scenario_key=row.scenario_key,
        lifecycle=_lifecycle_of(environment, row.scenario_key),
    )


def restore_scenario(
    environment: HostedHarnessJob, key: str, *, user: Any
) -> LifecycleAnswer:
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
    return LifecycleAnswer(
        scenario_key=row.scenario_key,
        live=live,
        lifecycle=_lifecycle_of(environment, row.scenario_key)
        if live
        else in_draft_view(),
    )


def scenario_revisions(environment: HostedHarnessJob, key: str) -> ScenarioRevisions:
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
    revisions: list[ScenarioRevision] = []
    # The suite version each revision first appeared in, read back below.
    first_suites: list[Any] = []
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
            ScenarioRevision(
                revision=len(revisions) + 1,
                digest=digest,
                suite_versions=[number],
                first_suite_version=number,
                changed_fields=changed,
            )
        )
        first_suites.append(suite_id)
        previous = content
    environment_version = environment.environment_version
    for one, suite_id in zip(revisions, first_suites, strict=True):
        suite = HarnessSuiteVersion._base_manager.filter(id=suite_id).first()
        views = scenario_lifecycle(
            environment,
            suite_version=suite,
            environment_version=environment_version,
            keys=[key],
        )
        one["lifecycle_on_default_environment"] = views.get(key)
    revisions.reverse()
    return ScenarioRevisions(
        scenario_key=key,
        revisions=revisions[:REVISIONS_MAX],
        truncated=len(revisions) > REVISIONS_MAX,
    )


def not_active(environment: HostedHarnessJob) -> list[NotActiveCase]:
    """The cases of the default suite that are not Active on the new defaults, for the Save response."""
    from simulate.services.harness_scenario_lifecycle import scenario_lifecycle

    environment.refresh_from_db()
    views = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    )
    return [
        NotActiveCase(
            scenario_key=key, reason=view["reason"], reason_text=view["reason_text"]
        )
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


def _replaced_by(
    before: str | None,
    new_rules: list[str],
    documents: Mapping[str, Any],
    parent_documents: Mapping[str, Any],
) -> str | None:
    """The rule the harness recorded as replacing ``before`` (`amendment_records`: the new record's `previous`, or the
    old record's `replaced_by`, by rule id), as this version holds it; None when it recorded none."""
    from simulate.services.harness_versioning.review_items import rule_key

    if not before:
        return None
    was = f"rule-{rule_key(before)}"
    records = [
        one
        for side in (documents, parent_documents)
        for one in ((side.get("contract") or {}).get("amendment_records") or [])
        if isinstance(one, Mapping) and one.get("kind") == "rule"
    ]
    now = next(
        (
            str(one.get("key"))
            for one in records
            if one.get("previous") == was and one.get("key")
        ),
        None,
    ) or next(
        (
            str(one.get("replaced_by"))
            for one in records
            if one.get("key") == was and one.get("replaced_by")
        ),
        None,
    )
    return next((rule for rule in new_rules if f"rule-{rule_key(rule)}" == now), None)


def source_changes(environment: HostedHarnessJob, version_id: Any) -> SourceChanges:
    """What changed between an environment version and its parent in the rules and files the
    default suite cites, each with the cases that cite it."""
    from simulate.services.harness_scenario_lifecycle import (
        changed_refs,
        matched_rule,
        parent_version_documents,
        rules_of,
    )

    version = HarnessEnvironmentVersion._base_manager.filter(
        id=version_id, job_id=environment.id, deleted=False
    ).first()
    if version is None:
        raise _error("version_not_found", "no such version in this environment", 404)
    parent_documents = parent_version_documents(version) or {}
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
        rules_of(version.documents or {}),
        rules_of(parent_documents),
    )
    by_change: dict[tuple[str, str], SourceChange] = {}
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
                SourceChange(kind=kind, ref=ref, before=None, after=None, cited_by=[]),
            )
            if key not in entry["cited_by"]:
                entry["cited_by"].append(key)
            if kind == "rule":
                quote = str(source.get("quote") or "")
                entry["before"] = matched_rule(quote, old_rules)
                # A cited rule this version no longer holds reads its replacement only as the harness declared it.
                entry["after"] = matched_rule(quote, new_rules) or _replaced_by(
                    entry["before"],
                    new_rules,
                    version.documents or {},
                    parent_documents,
                )
            else:
                files.add(file_path(ref))
    before_texts = _file_texts(parent, files) if parent else {}
    after_texts = _file_texts(version, files)
    for (kind, ref), entry in by_change.items():
        if kind == "file":
            path = file_path(ref)
            entry["before"] = before_texts.get(path)
            entry["after"] = after_texts.get(path)
    changes = sorted(by_change.values(), key=lambda one: (one["kind"], one["ref"]))
    return SourceChanges(
        from_version=getattr(parent, "version_number", None),
        to_version=version.version_number,
        changes=changes[:SOURCE_CHANGES_MAX],
        truncated=len(changes) > SOURCE_CHANGES_MAX,
    )
