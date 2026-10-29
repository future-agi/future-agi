"""Versions of a connected agent, made from the Agents page: Sync, a new upload, a
prompt edit or a credentials change, each through the one version engine; which
version is production; and whether an environment follows it or is pinned."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from django.db import transaction

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
)
from simulate.services.harness_agent_versions import (
    POLICY_FOLLOW,
    POLICY_KEY,
    POLICY_PINNED,
    activate_agent_version,
    create_agent_version,
    discard_agent_version,
    environment_policy,
    hand_move,
    make_production,
    retry_preflight,
)
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

Trigger = HarnessAgentVersionRuntime.Trigger
SourceKind = HarnessAgentVersionRuntime.SourceKind
Status = HarnessAgentVersionRuntime.PreflightStatus

KIND_SYNC = "sync"
KIND_UPLOAD = "upload"
KIND_CREDENTIALS = "credentials"
KIND_MANUAL = "manual"
KINDS = (KIND_SYNC, KIND_UPLOAD, KIND_CREDENTIALS, KIND_MANUAL)


@dataclass(frozen=True)
class VersionChange:
    version: AgentVersion
    created: bool


def production_version(definition: AgentDefinition) -> AgentVersion | None:
    return definition.active_version


def _runtime(version: AgentVersion | None) -> HarnessAgentVersionRuntime | None:
    if version is None:
        return None
    return (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=version)
        .defer("credential_blob", "diff")
        .first()
    )


def _parent(definition: AgentDefinition) -> AgentVersion:
    parent = production_version(definition) or definition.latest_version
    if parent is None:
        raise HostedHarnessError(
            "agent_has_no_version",
            "This agent has no version to change yet",
            status_code=409,
        )
    return parent


def _github_source(runtime: HarnessAgentVersionRuntime | None) -> dict[str, Any]:
    if runtime is None or runtime.source_kind != SourceKind.GITHUB:
        raise HostedHarnessError(
            "sync_unsupported",
            "Only an agent connected from a GitHub repository can be synced",
            status_code=422,
        )
    source = dict(runtime.source or {})
    access = (runtime.agent_payload or {}).get("source_access") or {}
    return {
        "kind": "github",
        "repository": source.get("repository"),
        "ref": source.get("ref"),
        "commit_sha": source.get("commit_sha"),
        "subfolder": source.get("subfolder"),
        "visibility": access.get("visibility") or "public",
        "installation_id": access.get("installation_id"),
        "repository_id": access.get("repository_id"),
    }


def sync_version(definition: AgentDefinition, *, user: Any = None) -> VersionChange:
    """A version at the branch's current head, or the parent when it is at the head."""
    from simulate.services.harness_agent_changes import github_head

    parent = _parent(definition)
    source = _github_source(_runtime(parent))
    head = github_head(
        str(source["repository"]),
        source.get("ref"),
        purpose="create",
        repository_id=source.get("repository_id"),
    )
    if head.status != "ok" or not head.sha:
        raise HostedHarnessError(
            "source_unreachable",
            "The repository's branch could not be read; try again",
            status_code=503,
            retryable=True,
            details={"status": head.status},
        )
    if head.sha.lower() == str(source.get("commit_sha") or "").lower():
        return VersionChange(parent, False)
    version = create_agent_version(
        definition,
        parent=parent,
        env=None,
        source={
            key: value
            for key, value in {**source, "commit_sha": head.sha}.items()
            if key != "repository_id" and value
        },
        trigger=Trigger.SYNC,
        user=user,
    )
    return VersionChange(version, version.id != parent.id)


def upload_version(
    definition: AgentDefinition, *, archive_artifact_id: UUID, user: Any = None
) -> VersionChange:
    from simulate.services.harness_versioning.archives import archive_exists
    from simulate.services.hosted_harness_gateway import _source_object_key

    parent = _parent(definition)
    runtime = _runtime(parent)
    if runtime is not None and runtime.source_kind not in (
        SourceKind.ARCHIVE,
        SourceKind.GITHUB,
    ):
        raise HostedHarnessError(
            "upload_unsupported",
            "Only a code agent takes a new upload",
            status_code=422,
        )
    if runtime is not None and str(archive_artifact_id) == str(
        (runtime.source or {}).get("archive_artifact_id") or ""
    ):
        return VersionChange(parent, False)
    if not archive_exists(
        _source_object_key(definition.organization_id, str(archive_artifact_id))
    ):
        raise HostedHarnessError(
            "archive_source_not_found",
            "The upload expired; upload the folder again",
            status_code=422,
        )
    version = create_agent_version(
        definition,
        parent=parent,
        env=None,
        source={"kind": "archive", "archive_artifact_id": str(archive_artifact_id)},
        trigger=Trigger.UPLOAD,
        user=user,
    )
    return VersionChange(version, version.id != parent.id)


def credentials_version(
    definition: AgentDefinition, *, secret_values: Mapping[str, str], user: Any = None
) -> VersionChange:
    parent = _parent(definition)
    version = create_agent_version(
        definition,
        parent=parent,
        env=None,
        secret_values=dict(secret_values),
        trigger=Trigger.CREDENTIALS,
        user=user,
    )
    return VersionChange(version, version.id != parent.id)


def manual_version(
    definition: AgentDefinition, *, prompt: str, user: Any = None
) -> VersionChange:
    parent = _parent(definition)
    runtime = _runtime(parent)
    if runtime is not None and runtime.source_kind in (
        SourceKind.GITHUB,
        SourceKind.ARCHIVE,
    ):
        raise HostedHarnessError(
            "prompt_in_source",
            "A code agent's prompt lives in its source; sync or upload it instead",
            status_code=422,
        )
    if prompt == (parent.description or ""):
        return VersionChange(parent, False)
    version = create_agent_version(
        definition,
        parent=parent,
        env=None,
        prompt=prompt,
        trigger=Trigger.PROMPT,
        user=user,
    )
    return VersionChange(version, version.id != parent.id)


def change_version(
    definition: AgentDefinition, *, kind: str, user: Any = None, **body: Any
) -> VersionChange:
    """One change of ``kind`` to the agent, through the version engine."""
    if kind == KIND_SYNC:
        return sync_version(definition, user=user)
    if kind == KIND_UPLOAD:
        return upload_version(
            definition, archive_artifact_id=body["archive_artifact_id"], user=user
        )
    if kind == KIND_CREDENTIALS:
        return credentials_version(
            definition, secret_values=body["secret_values"], user=user
        )
    if kind == KIND_MANUAL:
        return manual_version(definition, prompt=body["prompt"], user=user)
    raise HostedHarnessError("kind_unknown", f"unknown kind {kind!r}", status_code=400)


def set_production(version: AgentVersion) -> list[Any]:
    """Make ``version`` the agent's production version; a checked version must have
    passed. Returns the environments moved to it."""
    runtime = _runtime(version)
    if runtime is not None and runtime.preflight_status not in (
        Status.PASSED,
        Status.LEGACY,
    ):
        raise HostedHarnessError(
            "version_not_ready",
            "This version has not passed its check yet",
            status_code=409,
            details={"preflight_status": runtime.preflight_status},
        )
    if runtime is None:
        make_production(version)
        return []
    make_production(version)
    return activate_agent_version(version)


def retry_version(version: AgentVersion) -> None:
    if _runtime(version) is None:
        raise HostedHarnessError(
            "version_not_checked",
            "This version has no check to retry",
            status_code=409,
        )
    retry_preflight(version)


def discard_version(version: AgentVersion) -> None:
    if version.is_active:
        raise HostedHarnessError(
            "version_in_production",
            "The production version can't be discarded; make another production first",
            status_code=409,
        )
    discard_agent_version(version)


def set_environment_policy(
    environment: HostedHarnessJob,
    *,
    policy: str,
    version_id: Any = None,
    user: Any = None,
) -> HostedHarnessJob:
    """Make the environment follow the agent's production version, or pin it to one;
    a pin names the version, a follow moves to production now."""
    from simulate.services.harness_versioning.versions import move_agent
    from simulate.services.hosted_harness import agent_version_pickable

    with transaction.atomic():
        locked = (
            HostedHarnessJob.no_workspace_objects.select_for_update()
            .filter(id=environment.id, environment__isnull=True)
            .first()
        )
        if locked is None:
            raise HostedHarnessError(
                "environment_not_found", "environment not found", status_code=404
            )
        definition_id = (
            AgentVersion._base_manager.filter(id=locked.agent_version_id)
            .values_list("agent_definition_id", flat=True)
            .first()
        )
        target = None
        if policy == POLICY_PINNED and version_id is not None:
            target = AgentVersion.no_workspace_objects.filter(
                id=version_id, agent_definition_id=definition_id
            ).first()
            if target is None:
                raise HostedHarnessError(
                    "version_not_found",
                    "that version is not one of this agent's",
                    status_code=404,
                )
        elif policy == POLICY_FOLLOW and definition_id is not None:
            target = (
                AgentVersion.no_workspace_objects.filter(
                    agent_definition_id=definition_id, status="active"
                )
                .order_by("-version_number")
                .first()
            )
        payload = dict(locked.payload or {})
        payload["metadata"] = {**(payload.get("metadata") or {}), POLICY_KEY: policy}
        locked.payload = payload
        locked.save(update_fields=["payload", "updated_at"])
        if target is not None and locked.agent_version_id != target.id:
            if not agent_version_pickable(locked, target.id):
                raise HostedHarnessError(
                    "version_not_pickable",
                    "that version can't be this environment's default yet",
                    status_code=409,
                )
            move_agent(locked, target, moved=hand_move(user, target.id))
        logger.info(
            "harness_environment_policy_set",
            environment_id=str(locked.id),
            policy=environment_policy(locked),
            agent_version_id=str(locked.agent_version_id),
        )
        return locked
