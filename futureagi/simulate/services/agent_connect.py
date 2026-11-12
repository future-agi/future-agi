"""Connecting a code agent on its own, with no environment: find the agents in a
source, make the agent and its first version from the one picked, and confirm what
the first reading found before anything is built from it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from simulate.models import (
    AgentDefinition,
    AgentScan,
    AgentVersion,
    HarnessAgentVersionRuntime,
)
from simulate.services.harness_agent_versions import (
    CONNECTED_OWNER,
    advisory_xact_lock,
    create_agent_version,
)
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

Trigger = HarnessAgentVersionRuntime.Trigger
SourceKind = HarnessAgentVersionRuntime.SourceKind
SOURCE_KEYS = (
    "kind",
    "repository",
    "ref",
    "commit_sha",
    "subfolder",
    "visibility",
    "installation_id",
    "repository_id",
    "archive_artifact_id",
)


@dataclass(frozen=True)
class Connected:
    definition: AgentDefinition
    version: AgentVersion
    created: bool


def _clean_source(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: source[name] for name in SOURCE_KEYS if source.get(name) not in (None, "")
    }


def _check_source(organization_id: Any, workspace_id: Any, source: Mapping) -> None:
    if source.get("subfolder") and not getattr(
        settings, "HARNESS_SOURCE_SUBFOLDER_ENABLED", False
    ):
        raise HostedHarnessError(
            "subfolder_unsupported",
            "Choosing a folder isn't supported yet; connect the repository root",
            status_code=422,
        )
    if source.get("kind") == "github" and source.get("visibility") == "private":
        from integrations.services.github_app import installation_connected

        if not installation_connected(
            organization_id=organization_id,
            workspace_id=workspace_id,
            installation_id=str(source.get("installation_id") or ""),
        ):
            raise HostedHarnessError(
                "github_installation_not_connected",
                "Connect GitHub in Settings > Integrations before connecting a"
                " private repository",
                status_code=422,
            )
    if source.get("kind") == "archive":
        from simulate.services.harness_versioning.archives import archive_exists
        from simulate.services.hosted_harness_gateway import _source_object_key

        if not archive_exists(
            _source_object_key(organization_id, str(source.get("archive_artifact_id")))
        ):
            raise HostedHarnessError(
                "archive_source_not_found",
                "The upload expired; upload the folder again",
                status_code=422,
            )


def _require_reading() -> None:
    from simulate.services.agent_version_steps import reading_enabled

    if not reading_enabled():
        raise HostedHarnessError(
            "reading_unavailable",
            "Agent reading is turned off on this installation",
            status_code=422,
        )


def start_scan(
    *, organization, workspace, user, source: Mapping[str, Any]
) -> AgentScan:
    """Record a scan for the agents in a source and start it; the drawer polls it."""
    from simulate.tasks.agent_scans import schedule_agent_scan

    _require_reading()

    cleaned = _clean_source(source)
    _check_source(organization.id, getattr(workspace, "id", None), cleaned)
    scan = AgentScan.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user if getattr(user, "pk", None) else None,
        source=cleaned,
    )
    transaction.on_commit(lambda: schedule_agent_scan(scan.id))
    logger.info(
        "agent_scan_started",
        scan_id=str(scan.id),
        kind=cleaned.get("kind"),
        repository=cleaned.get("repository"),
    )
    return scan


def connect_lock_name(
    organization_id: Any, workspace_id: Any, source: Mapping[str, Any]
) -> str:
    """One name per agent a source connects, so two connects of it queue."""
    if source.get("kind") == "archive":
        identity = f"archive:{source.get('archive_artifact_id')}"
    else:
        identity = (
            f"github:{str(source.get('repository') or '').lower()}"
            f":{source.get('subfolder') or ''}"
        )
    return f"agent-connect:{organization_id}:{workspace_id}:{identity}"


def existing_connected_agent(
    organization_id: Any, workspace_id: Any, source: Mapping[str, Any]
) -> tuple[AgentDefinition, AgentVersion] | None:
    """The agent already connected from this repository and folder (or upload) in
    the workspace, with its first version."""
    rows = HarnessAgentVersionRuntime.no_workspace_objects.filter(
        organization_id=organization_id,
        workspace_id=workspace_id,
        trigger=Trigger.CONNECT,
        agent_payload__definition_owner=CONNECTED_OWNER,
        agent_definition__deleted=False,
    ).select_related("agent_definition", "agent_version")
    if source.get("kind") == "archive":
        rows = rows.filter(
            source_kind=SourceKind.ARCHIVE,
            source__archive_artifact_id=str(source.get("archive_artifact_id")),
        )
        match = rows.order_by("created_at").first()
    else:
        wanted = str(source.get("subfolder") or "")
        match = next(
            (
                row
                for row in rows.filter(
                    source_kind=SourceKind.GITHUB,
                    source__repository__iexact=str(source.get("repository") or ""),
                ).order_by("created_at")
                if str((row.source or {}).get("subfolder") or "") == wanted
            ),
            None,
        )
    if match is None:
        return None
    return match.agent_definition, match.agent_version


def connect_agent(
    *,
    organization,
    workspace,
    user,
    source: Mapping[str, Any],
    name: str,
    env_values: Mapping[str, str] | None = None,
) -> Connected:
    """Make the agent and its first version from a source, and start the version's
    reading. The same repository and folder connected again answers the agent that
    already stands for it."""
    from simulate.services.agent_versions import branch_head_of

    _require_reading()
    workspace_id = getattr(workspace, "id", None)
    cleaned = _clean_source(source)
    _check_source(organization.id, workspace_id, cleaned)
    found = existing_connected_agent(organization.id, workspace_id, cleaned)
    if found is not None:
        definition, version = found
        logger.info(
            "agent_connect_exists",
            agent_definition_id=str(definition.id),
            version_id=str(version.id),
        )
        return Connected(definition, version, created=False)
    if cleaned.get("kind") == "github" and not cleaned.get("commit_sha"):
        cleaned["commit_sha"] = branch_head_of(organization.id, workspace_id, cleaned)
    with transaction.atomic():
        advisory_xact_lock(connect_lock_name(organization.id, workspace_id, cleaned))
        found = existing_connected_agent(organization.id, workspace_id, cleaned)
        if found is not None:
            return Connected(*found, created=False)
        definition = AgentDefinition.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            agent_name=str(name).strip()[:255],
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            description="",
        )
        version = create_agent_version(
            definition,
            parent=None,
            env=None,
            source=cleaned,
            agent_payload={
                "connector": "auto",
                "config": {},
                "definition_owner": CONNECTED_OWNER,
            },
            secret_values=dict(env_values or {}) or None,
            trigger=Trigger.CONNECT,
            user=user if getattr(user, "pk", None) else None,
        )
    logger.info(
        "agent_connect_created",
        agent_definition_id=str(definition.id),
        version_id=str(version.id),
        kind=cleaned.get("kind"),
        subfolder=cleaned.get("subfolder") or "",
    )
    return Connected(definition, version, created=True)


def found_check_required(definition: AgentDefinition) -> bool:
    """An agent connected on its own is built or run only once someone confirmed
    what its first reading found."""
    if definition.found_checked_at is not None:
        return False
    return HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_definition_id=definition.id, trigger=Trigger.CONNECT
    ).exists()


def require_found_check(definition: AgentDefinition) -> None:
    if found_check_required(definition):
        raise HostedHarnessError(
            "found_check_required",
            "Check what we found about this agent before building from it",
            status_code=409,
        )


def found_check(definition: AgentDefinition, user=None) -> AgentDefinition:
    """Record that what the first reading found was confirmed; a second confirmation
    changes nothing."""
    now = timezone.now()
    AgentDefinition.no_workspace_objects.filter(
        id=definition.id, found_checked_at__isnull=True
    ).update(found_checked_at=now, updated_at=now)
    definition.refresh_from_db(fields=["found_checked_at"])
    logger.info(
        "agent_found_checked",
        agent_definition_id=str(definition.id),
        user_id=str(getattr(user, "pk", "") or ""),
    )
    return definition
