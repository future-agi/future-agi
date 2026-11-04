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
    create_agent_version,
    discard_agent_version,
    environment_owned,
    environment_policy,
    lock_agent_production,
    promote_to_production,
    record_hand_move,
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


def environment_of(
    definition: AgentDefinition, environment_id: UUID
) -> HostedHarnessJob:
    """A live environment of the agent's organization, or ``environment_not_found``."""
    environment = HostedHarnessJob.objects.filter(
        id=environment_id,
        organization_id=definition.organization_id,
        environment__isnull=True,
        deleted=False,
    ).first()
    if environment is None:
        raise HostedHarnessError(
            "environment_not_found", "environment not found", status_code=404
        )
    return environment


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


def _source_unreachable(status: str, *, retryable: bool = True) -> HostedHarnessError:
    return HostedHarnessError(
        "source_unreachable",
        "The repository's branch could not be read; try again",
        status_code=503,
        retryable=retryable,
        details={"status": status},
    )


def _source_gone() -> HostedHarnessError:
    return HostedHarnessError(
        "source_not_found",
        "The repository or branch no longer exists on GitHub",
        status_code=422,
    )


def _branch_head(definition: AgentDefinition, source: dict[str, Any]) -> str:
    return branch_head_of(definition.organization_id, definition.workspace_id, source)


def branch_head_of(organization_id: Any, workspace_id: Any, source: dict[str, Any]):
    """The commit a source's branch points at now; a private repository is read
    through the workspace's GitHub installation."""
    from simulate.services.harness_agent_changes import github_head

    installation_id = source.get("installation_id")
    if source.get("visibility") == "private" and installation_id:
        from integrations.services.github_app import (
            GitHubAppError,
            branch_head,
            installation_connected,
        )

        if not installation_connected(
            organization_id=organization_id,
            workspace_id=workspace_id,
            installation_id=str(installation_id),
        ):
            raise HostedHarnessError(
                "github_installation_not_connected",
                "Connect GitHub in Settings > Integrations before syncing a"
                " private repository",
                status_code=422,
            )
        try:
            sha = branch_head(
                str(installation_id),
                repository=str(source["repository"]),
                repository_id=source.get("repository_id"),
                ref=source.get("ref"),
            )
        except GitHubAppError as exc:
            raise _source_unreachable(exc.code, retryable=exc.retryable) from exc
        if sha is None:
            raise _source_gone()
        return sha
    head = github_head(
        str(source["repository"]),
        source.get("ref"),
        purpose="create",
        repository_id=source.get("repository_id"),
    )
    if head.status == "not_found":
        raise _source_gone()
    if head.status not in ("ok", "not_modified") or not head.sha:
        raise _source_unreachable(head.status)
    return head.sha


def sync_version(definition: AgentDefinition, *, user: Any = None) -> AgentVersion:
    """A version at the branch's current head, or the parent when it is at the head."""
    parent = _parent(definition)
    source = _github_source(_runtime(parent))
    head_sha = _branch_head(definition, source)
    if head_sha.lower() == str(source.get("commit_sha") or "").lower():
        return parent
    version = create_agent_version(
        definition,
        parent=parent,
        env=None,
        source={
            key: value
            for key, value in {**source, "commit_sha": head_sha}.items()
            if key != "repository_id" and value
        },
        trigger=Trigger.SYNC,
        user=user,
    )
    return version


def upload_version(
    definition: AgentDefinition, *, archive_artifact_id: UUID, user: Any = None
) -> AgentVersion:
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
        return parent
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
    return version


def credentials_version(
    definition: AgentDefinition, *, secret_values: Mapping[str, str], user: Any = None
) -> AgentVersion:
    parent = _parent(definition)
    version = create_agent_version(
        definition,
        parent=parent,
        env=None,
        secret_values=dict(secret_values),
        trigger=Trigger.CREDENTIALS,
        user=user,
    )
    return version


def manual_version(
    definition: AgentDefinition, *, prompt: str, user: Any = None
) -> AgentVersion:
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
        return parent
    version = create_agent_version(
        definition,
        parent=parent,
        env=None,
        prompt=prompt,
        trigger=Trigger.PROMPT,
        user=user,
    )
    return version


def change_version(
    definition: AgentDefinition,
    *,
    kind: str,
    user: Any = None,
    archive_artifact_id: UUID | None = None,
    secret_values: Mapping[str, str] | None = None,
    prompt: str | None = None,
) -> VersionChange:
    """One change of ``kind`` to the agent, through the version engine. ``created``
    is whether the call made a version: the same content again, a draft included,
    returns the version that already stands for it."""
    before = set(
        AgentVersion.no_workspace_objects.filter(
            agent_definition=definition
        ).values_list("id", flat=True)
    )
    if kind == KIND_SYNC:
        version = sync_version(definition, user=user)
    elif kind == KIND_UPLOAD:
        version = upload_version(
            definition, archive_artifact_id=archive_artifact_id, user=user
        )
    elif kind == KIND_CREDENTIALS:
        version = credentials_version(
            definition, secret_values=secret_values or {}, user=user
        )
    elif kind == KIND_MANUAL:
        version = manual_version(definition, prompt=prompt or "", user=user)
    else:
        raise HostedHarnessError(
            "kind_unknown", f"unknown kind {kind!r}", status_code=400
        )
    return VersionChange(version, version.id not in before)


def set_production(version: AgentVersion) -> list[Any]:
    """Make ``version`` the agent's production version; a checked version must have
    passed. Returns the environments moved to it. An agent an environment made has
    no production here: its environment decides which version is in use."""
    if environment_owned(version.agent_definition_id):
        raise HostedHarnessError(
            "managed_by_environment",
            "An environment manages this agent; change its version from there",
            status_code=409,
        )
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
    return promote_to_production(version)


def retry_version(version: AgentVersion) -> None:
    if _runtime(version) is None:
        raise HostedHarnessError(
            "version_not_checked",
            "This version has no check to retry",
            status_code=409,
        )
    retry_preflight(version)


def discard_version(version: AgentVersion) -> None:
    from simulate.temporal.client import cancel_agent_version_workflow

    with transaction.atomic():
        lock_agent_production(version.agent_definition_id)
        if AgentVersion._base_manager.filter(
            id=version.id, status=AgentVersion.StatusChoices.ACTIVE
        ).exists():
            raise HostedHarnessError(
                "version_in_production",
                "The production version can't be discarded; make another production"
                " first",
                status_code=409,
            )
        discard_agent_version(version)
        transaction.on_commit(lambda: cancel_agent_version_workflow(str(version.id)))


def set_environment_policy(
    environment: HostedHarnessJob,
    *,
    policy: str,
    version_id: Any = None,
    user: Any = None,
) -> HostedHarnessJob:
    """Make the environment follow the agent's production version, or pin it to one;
    a pin names the version, a follow moves to production now."""
    from simulate.services.harness_versioning.versions import move_default
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
            locked = move_default(
                locked,
                "agent",
                target.id,
                expected_default_id=locked.agent_version_id,
                user=user,
            )
        elif policy == POLICY_PINNED:
            record_hand_move(locked, user, locked.agent_version_id)
        logger.info(
            "harness_environment_policy_set",
            environment_id=str(locked.id),
            policy=environment_policy(locked),
            agent_version_id=str(locked.agent_version_id),
        )
        return locked


def build_preflight(
    definition: AgentDefinition, version: AgentVersion | None
) -> dict[str, Any]:
    """What a build from this agent would find: the version to build from, its
    check, its stored code and the credential names it answers for."""
    from simulate.models import CodeArchive
    from simulate.services.harness_agent_versions import version_credential_names
    from simulate.services.harness_versioning.archives import archive_exists
    from simulate.services.hosted_harness_gateway import _source_object_key

    version = version or production_version(definition) or definition.latest_version
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, message: str) -> None:
        checks.append(
            {"name": name, "status": "ok" if ok else "blocked", "message": message}
        )

    if version is None:
        check("version", False, "This agent has no version to build from yet")
        return {"ok": False, "version_id": None, "checks": checks}
    runtime = _runtime(version)
    ready = runtime is None or runtime.preflight_status in (
        Status.PASSED,
        Status.LEGACY,
    )
    check(
        "version",
        ready,
        f"v{version.version_number} "
        + ("is ready" if ready else f"is {runtime.preflight_status}"),
    )
    if runtime is not None and runtime.source_kind == SourceKind.GITHUB:
        source = runtime.source or {}
        stored = CodeArchive.no_workspace_objects.filter(
            organization_id=definition.organization_id,
            repository=str(source.get("repository") or "").lower(),
            commit_sha=str(source.get("commit_sha") or "").lower(),
        ).exists()
        check(
            "code",
            stored or bool(source.get("commit_sha")),
            (
                "the commit's code is stored"
                if stored
                else "the commit is pinned; its code is fetched on the first build"
            ),
        )
    elif runtime is not None and runtime.source_kind == SourceKind.ARCHIVE:
        artifact = str((runtime.source or {}).get("archive_artifact_id") or "")
        present = bool(artifact) and archive_exists(
            _source_object_key(definition.organization_id, artifact)
        )
        check(
            "code",
            present,
            "the upload is stored" if present else "the upload expired; upload again",
        )
    names = version_credential_names(runtime) if runtime is not None else []
    check(
        "credentials",
        True,
        f"{len(names)} credential name{'s' if len(names) != 1 else ''} come from the version",
    )
    from simulate.services.agent_connect import found_check_required

    if found_check_required(definition):
        check(
            "found_check",
            False,
            "check what we found about this agent first",
        )
    return {
        "ok": all(one["status"] == "ok" for one in checks),
        "version_id": str(version.id),
        "checks": checks,
    }
