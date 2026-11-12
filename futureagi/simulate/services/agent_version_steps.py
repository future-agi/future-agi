"""The steps a new code version of an Agents-page agent goes through, one per
activity of AgentVersionWorkflow: a newer version supersedes it, its code is
stored, it is read, then checked and activated as any version is. Each step
records what it did on the version's side row and can run again."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from simulate.models import AgentVersion, HarnessAgentVersionRuntime
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services.agent_contracts import (
    store_version_contract,
    stored_version_contract,
)
from simulate.services.agent_reading_sandbox import ReadOutcome, read_version
from simulate.services.agent_setup import AgentStoreError, store_agent, stored_agent
from simulate.services.harness_agent_versions import (
    CONNECTED_OWNER,
    _side_row,
    preflight_agent_version,
)
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

Status = HarnessAgentVersionRuntime.PreflightStatus
SourceKind = HarnessAgentVersionRuntime.SourceKind
CODE_KINDS = (SourceKind.GITHUB, SourceKind.ARCHIVE)

STEP_SNAPSHOT = "snapshot"
STEP_READING = "reading"
STEP_CHECK = "check"
STEP_DONE = "done"
# A reading that cannot be had after its retries; the version can be retried by hand.
UNAVAILABLE = "agent_reading_unavailable"
SUPERSEDED = "superseded"


@dataclass(frozen=True)
class StepOutcome:
    status: str
    code: str = ""
    message: str = ""
    retryable: bool = False


def reading_enabled() -> bool:
    return bool(getattr(settings, "HARNESS_AGENT_READING_ENABLED", False))


def workflow_applies(runtime: HarnessAgentVersionRuntime | None) -> bool:
    """Whether a version goes through the workflow: a code version of an Agents-page
    agent while readings are on. Every other version keeps the plain check."""
    return (
        reading_enabled()
        and runtime is not None
        and runtime.source_kind in CODE_KINDS
        and (runtime.agent_payload or {}).get("definition_owner") == CONNECTED_OWNER
    )


def workflow_applies_to(version_id: Any) -> bool:
    return workflow_applies(_side_row(version_id))


def document_reading_applies(runtime: HarnessAgentVersionRuntime | None) -> bool:
    """Whether a version is read only for its document: a later code version of an
    agent made with its environment, while readings are on. Its first version takes
    the document its environment's build writes."""
    return (
        reading_enabled()
        and runtime is not None
        and runtime.parent_id is not None
        and runtime.source_kind in CODE_KINDS
        and (runtime.agent_payload or {}).get("definition_owner") != CONNECTED_OWNER
    )


def document_reading_applies_to(version_id: Any) -> bool:
    return document_reading_applies(_side_row(version_id))


def _note(version_id: Any, **changes: Any) -> None:
    """Merge ``changes`` into the version's preflight report, under its lock."""
    with transaction.atomic():
        runtime = _side_row(version_id, lock=True)
        if runtime is None:
            return
        report = {**(runtime.preflight_report or {}), **changes}
        HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.id).update(
            preflight_report=report, updated_at=timezone.now()
        )


def supersede(version_id: Any) -> list[str]:
    """Set aside the agent's older versions still before their check: a burst of
    pushes reads only the newest. Returns the ids of the versions set aside."""
    runtime = _side_row(version_id)
    if runtime is None or not workflow_applies(runtime):
        return []
    older = list(
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_definition_id=runtime.agent_definition_id,
            preflight_status=Status.PENDING,
            source_kind__in=CODE_KINDS,
            agent_payload__definition_owner=CONNECTED_OWNER,
            agent_version__version_number__lt=runtime.agent_version.version_number,
        )
        .exclude(id=runtime.id)
        .values_list("agent_version_id", flat=True)
    )
    superseded = []
    for older_id in older:
        with transaction.atomic():
            locked = _side_row(older_id, lock=True)
            if locked is None or locked.preflight_status != Status.PENDING:
                continue
            payload = locked.agent_payload or {}
            # A version an environment asked for is its own to wait for.
            if payload.get("requested_by_environments") or payload.get(
                "created_for_environment"
            ):
                continue
            report = {
                **(locked.preflight_report or {}),
                "step": STEP_DONE,
                "reading": {
                    "status": "superseded",
                    "code": SUPERSEDED,
                    "message": "Superseded by a newer version",
                    "retryable": False,
                    "by_version_id": str(version_id),
                },
            }
            HarnessAgentVersionRuntime.no_workspace_objects.filter(id=locked.id).update(
                preflight_status=Status.DISCARDED,
                preflight_report=report,
                updated_at=timezone.now(),
            )
        superseded.append(str(older_id))
        logger.info(
            "agent_version_superseded",
            version_id=str(older_id),
            by_version_id=str(version_id),
        )
    return superseded


def snapshot(version_id: Any) -> StepOutcome:
    """Make sure the version's code is stored: a commit not yet archived is fetched
    once and kept, so every later read is from the archive."""
    from simulate.services.agent_reading_sandbox import version_source
    from simulate.services.harness_versioning.archives import archive_exists
    from simulate.services.hosted_harness_gateway import (
        _source_object_key,
        acquire_source_archive,
    )

    runtime = _side_row(version_id)
    if runtime is None or runtime.preflight_status != Status.PENDING:
        return StepOutcome("skipped", code="not_pending")
    _note(version_id, step=STEP_SNAPSHOT)
    source = version_source(runtime)
    try:
        if runtime.source_kind == SourceKind.ARCHIVE:
            key = _source_object_key(
                runtime.organization_id, str(source.get("archive_artifact_id") or "")
            )
            if not archive_exists(key):
                return StepOutcome(
                    "failed",
                    code="archive_source_not_found",
                    message="The upload expired; upload the folder again",
                )
            return StepOutcome("done")
        _body, commit = acquire_source_archive(
            runtime.organization_id, runtime.workspace_id, source, label=str(version_id)
        )
    except HostedHarnessError as exc:
        if exc.retryable:
            raise
        return StepOutcome("failed", code=exc.code, message=exc.message)
    if commit and commit != str(source.get("commit_sha") or "").lower():
        with transaction.atomic():
            locked = _side_row(version_id, lock=True)
            if locked is not None and not (locked.source or {}).get("commit_sha"):
                locked.source = {**(locked.source or {}), "commit_sha": commit}
                locked.save(update_fields=["source", "updated_at"])
    return StepOutcome("done")


def _same_code(
    runtime: HarnessAgentVersionRuntime, parent: HarnessAgentVersionRuntime
) -> bool:
    if runtime.source_kind != parent.source_kind:
        return False
    mine, theirs = runtime.source or {}, parent.source or {}
    if runtime.source_kind == SourceKind.ARCHIVE:
        return bool(mine.get("archive_artifact_id")) and mine.get(
            "archive_artifact_id"
        ) == theirs.get("archive_artifact_id")
    return (
        bool(mine.get("commit_sha"))
        and str(mine.get("commit_sha")).lower()
        == str(theirs.get("commit_sha") or "").lower()
        and (mine.get("subfolder") or "") == (theirs.get("subfolder") or "")
    )


def _reuse_parent_reading(
    runtime: HarnessAgentVersionRuntime, parent: HarnessAgentVersionRuntime
) -> bool:
    """A version on the same code as its parent takes the parent's document and
    contract; no reading runs."""
    document = stored_agent(
        parent.agent_version_id, with_raw_setup=True, include_stale=True
    )
    if document is None:
        return False
    setup = AgentVersionSetup.no_workspace_objects.filter(
        agent_version_id=parent.agent_version_id
    ).first()
    try:
        store_agent(
            agent_version_id=runtime.agent_version_id,
            agent=document,
            sdk_version=setup.sdk_version if setup else "",
            origin=setup.origin if setup else AgentVersionSetup.Origin.READING,
        )
    except AgentStoreError as exc:
        logger.warning(
            "agent_reading_not_reused",
            version_id=str(runtime.agent_version_id),
            parent_version_id=str(parent.agent_version_id),
            code=exc.code,
        )
        return False
    contract = stored_version_contract(parent.agent_version_id)
    if contract:
        store_version_contract(
            organization_id=runtime.organization_id,
            agent_version_id=runtime.agent_version_id,
            contract=contract,
        )
    return True


def read(version_id: Any) -> StepOutcome:
    """Read the version's agent, unless it already has a document or its parent's
    reading stands for the same code. A failure the next attempt may outlive is
    raised; any other is returned."""
    runtime = _side_row(version_id)
    if runtime is None or runtime.preflight_status != Status.PENDING:
        return StepOutcome("skipped", code="not_pending")
    if AgentVersionSetup.no_workspace_objects.filter(
        agent_version_id=version_id, status=AgentVersionSetup.Status.STORED
    ).exists():
        _note(version_id, reading={"status": "stored"})
        return StepOutcome("skipped", code="already_read")
    _note(version_id, step=STEP_READING)
    parent = _side_row(runtime.parent_id) if runtime.parent_id else None
    if parent is not None and _same_code(runtime, parent):
        if _reuse_parent_reading(runtime, parent):
            _note(
                version_id,
                reading={
                    "status": "reused",
                    "from_version_id": str(parent.agent_version_id),
                },
            )
            return StepOutcome("skipped", code="reused")
    outcome: ReadOutcome = read_version(version_id)
    if outcome.status == "stored":
        _note(version_id, reading={"status": "stored", "spend": outcome.spend})
        return StepOutcome("done")
    if outcome.status == "skipped":
        return StepOutcome("skipped", code=outcome.code)
    _note(
        version_id,
        reading={
            "status": "failed",
            "code": outcome.code,
            "message": outcome.message,
            "retryable": outcome.retryable,
            "spend": outcome.spend,
        },
    )
    return StepOutcome(
        "failed",
        code=outcome.code,
        message=outcome.message,
        retryable=outcome.retryable,
    )


def check(version_id: Any) -> str:
    """Today's check of the version's credentials, then its activation."""
    version = AgentVersion._base_manager.filter(id=version_id).first()
    if version is None:
        return "missing"
    _note(version_id, step=STEP_CHECK)
    status = preflight_agent_version(version).status
    _note(version_id, step=STEP_DONE)
    return str(status)


def fail(version_id: Any, *, code: str, message: str, retryable: bool) -> None:
    """End the version's check on a failure of its own steps; a retry starts it again."""
    with transaction.atomic():
        runtime = _side_row(version_id, lock=True)
        if runtime is None or runtime.preflight_status != Status.PENDING:
            return
        report = {
            **(runtime.preflight_report or {}),
            "step": STEP_DONE,
            "error": code,
            "reading": {
                **((runtime.preflight_report or {}).get("reading") or {}),
                "status": "failed",
                "code": code,
                "message": message[:2000],
                "retryable": retryable,
            },
        }
        now = timezone.now()
        HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.id).update(
            preflight_status=Status.FAILED,
            preflight_report=report,
            preflighted_at=now,
            updated_at=now,
        )
    logger.info("agent_version_failed", version_id=str(version_id), code=code)


def check_failure(runtime: HarnessAgentVersionRuntime | None) -> dict[str, Any] | None:
    """Why a version's check failed, for the page: its reading's failure when it
    has one, else the check's own error code."""
    if runtime is None or runtime.preflight_status not in (
        Status.FAILED,
        Status.DISCARDED,
    ):
        return None
    report = runtime.preflight_report or {}
    reading = report.get("reading") or {}
    if runtime.preflight_status == Status.DISCARDED:
        if reading.get("code") == SUPERSEDED:
            return {
                "code": SUPERSEDED,
                "message": str(reading.get("message") or ""),
                "retryable": False,
            }
        return None
    if reading.get("status") == "failed" and reading.get("code"):
        return {
            "code": str(reading["code"]),
            "message": str(reading.get("message") or ""),
            "retryable": bool(reading.get("retryable")),
        }
    if report.get("error"):
        return {"code": str(report["error"]), "message": "", "retryable": True}
    return None


def check_step(runtime: HarnessAgentVersionRuntime | None) -> str | None:
    """Where a pending version's check stands, for the page."""
    if runtime is None or runtime.preflight_status != Status.PENDING:
        return None
    step = (runtime.preflight_report or {}).get("step")
    if step in (STEP_SNAPSHOT, STEP_READING, STEP_CHECK):
        return str(step)
    return STEP_READING if workflow_applies(runtime) else STEP_CHECK
