"""Versions of a connected agent, made from the Agents page: Sync, a new upload, a
prompt edit or a credentials change, each through the one version engine; which
version is production; and whether an environment follows it or is pinned."""

from __future__ import annotations

import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from django.db import transaction
from django.utils import timezone
from minio.error import S3Error
from urllib3.exceptions import HTTPError

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
    next_version_number,
    promote_to_production,
    record_hand_move,
    retry_preflight,
    stamp_requested_at,
)
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

Trigger = HarnessAgentVersionRuntime.Trigger
SourceKind = HarnessAgentVersionRuntime.SourceKind
Status = HarnessAgentVersionRuntime.PreflightStatus

# The provider a connector reads its agent from, when the two names differ.
_SYNC_PROVIDERS = {"retell_chat": "retell"}
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
    """A version at the branch's current head, or the parent when it is at the head.
    A Vapi or Retell agent syncs from its provider instead."""
    parent = _parent(definition)
    runtime = _runtime(parent)
    if runtime is not None and runtime.source_kind == SourceKind.PROVIDER:
        return provider_sync(definition, parent, runtime, user=user)
    head_sha = _branch_head(definition, _github_source(runtime))
    version, _created = version_at_head(
        definition, head_sha, trigger=Trigger.SYNC, user=user
    )
    return version


def live_version_at(definition_id: Any, commit_sha: str) -> AgentVersion | None:
    """The newest version of the agent made at ``commit_sha`` that still stands for
    it: a failed check or a discarded version does not."""
    runtime = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_definition_id=definition_id,
            source__commit_sha__iexact=commit_sha,
        )
        .exclude(preflight_status__in=(Status.FAILED, Status.DISCARDED))
        .select_related("agent_version")
        .defer("credential_blob", "diff")
        .order_by("-created_at")
        .first()
    )
    return runtime.agent_version if runtime is not None else None


def _restart_stalled_check(version: AgentVersion) -> None:
    """Start the check of a pending version whose check never began or has run past
    the wait limit, so a version whose workflow failed to start is not reported as
    up to date forever. Starting it again is safe: a check already running is
    joined, not repeated."""
    from simulate.services.harness_agent_versions import _WAIT_LIMIT, queue_check

    runtime = _runtime(version)
    if runtime is None or runtime.preflight_status != Status.PENDING:
        return
    started = runtime.check_started_at
    if started is not None and started > timezone.now() - _WAIT_LIMIT:
        return
    logger.info("agent_version_check_restarted", version_id=str(version.id))
    queue_check(version)


def version_at_head(
    definition: AgentDefinition,
    head_sha: str,
    *,
    trigger: str,
    user: Any = None,
) -> tuple[AgentVersion, bool]:
    """The agent's version at ``head_sha`` of its branch, and whether this call made
    it: the parent when it is already there, a version Sync or the poller already
    made at it, else a new one. Sync and the poller both come here, so a commit gets
    one version whichever looks first."""
    parent = _parent(definition)
    source = _github_source(_runtime(parent))
    if head_sha.lower() == str(source.get("commit_sha") or "").lower():
        return parent, False
    existing = live_version_at(definition.id, head_sha)
    if existing is not None:
        _restart_stalled_check(existing)
        return existing, False
    first_new = next_version_number(definition.id)
    version = create_agent_version(
        definition,
        parent=parent,
        env=None,
        source={
            key: value
            for key, value in {**source, "commit_sha": head_sha}.items()
            if value
        },
        trigger=trigger,
        user=user,
    )
    return version, version.version_number >= first_new


def provider_sync(
    definition: AgentDefinition,
    parent: AgentVersion,
    runtime: HarnessAgentVersionRuntime,
    *,
    user: Any = None,
) -> AgentVersion:
    """Read the agent as its provider holds it now. The first Sync records what it
    found on the parent; a later one that finds a change makes a version carrying
    the new document, and the parent stands when nothing changed."""
    from simulate.models.agent_setup import AgentVersionSetup
    from simulate.services.agent_definition import resolve_api_key_for_version
    from simulate.services.agent_setup import (
        AgentStoreError,
        store_agent,
        stored_agent,
    )
    from simulate.services.provider_profile import (
        PLATFORM_SDK_VERSION,
        PROVIDERS,
        profile_digest,
        profile_document,
        read_profile,
    )

    source = dict(runtime.source or {})
    connector = str(source.get("provider") or definition.provider or "")
    provider = _SYNC_PROVIDERS.get(connector, connector)
    if provider not in PROVIDERS:
        raise HostedHarnessError(
            "sync_unsupported",
            "Only an agent connected from GitHub, Vapi or Retell can be synced",
            status_code=422,
        )
    assistant_id = str(source.get("assistant_id") or definition.assistant_id or "")
    modality = (
        "chat"
        if connector == "retell_chat"
        or definition.agent_type != AgentDefinition.AgentTypeChoices.VOICE
        else "voice"
    )
    profile = read_profile(
        provider,
        assistant_id=assistant_id,
        api_key=resolve_api_key_for_version(parent) or "",
        modality=modality,
    )
    digest = profile_digest(profile)
    document = profile_document(provider, assistant_id, profile, modality=modality)
    known = source.get("profile_digest")
    current = stored_agent(parent.id, include_stale=True)
    if known is None and current is None:
        # The first look at this agent: what it holds now is what the parent is.
        try:
            store_agent(
                agent_version_id=parent.id,
                agent=document,
                sdk_version=PLATFORM_SDK_VERSION,
                origin=AgentVersionSetup.Origin.READING,
            )
        except AgentStoreError as exc:
            logger.warning(
                "provider_sync_not_stored",
                agent_version_id=str(parent.id),
                code=exc.code,
            )
        return parent
    changed = (
        known != digest
        if known is not None
        else (current or {}).get("setup_hash") != document["setup_hash"]
    )
    if not changed:
        return parent
    prompt = "\n\n".join(
        str(unit.get("instructions") or "")
        for unit in document["behaviour"]
        if unit.get("instructions")
    )
    version = create_agent_version(
        definition,
        parent=parent,
        env=None,
        source={"kind": "provider", "profile_digest": digest},
        prompt=prompt or None,
        trigger=Trigger.PROVIDER_SYNC,
        user=user,
    )
    try:
        store_agent(
            agent_version_id=version.id,
            agent=document,
            sdk_version=PLATFORM_SDK_VERSION,
            origin=AgentVersionSetup.Origin.READING,
        )
    except AgentStoreError as exc:
        if exc.code != "setup_outcome_already_stored":
            logger.warning(
                "provider_sync_not_stored",
                agent_version_id=str(version.id),
                code=exc.code,
            )
    logger.info(
        "provider_sync_version",
        agent_definition_id=str(definition.id),
        version_id=str(version.id),
        provider=provider,
        tools=len(document["tools"]),
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
    existing = _same_upload_version(definition, archive_artifact_id)
    if existing is not None:
        return existing
    version = create_agent_version(
        definition,
        parent=parent,
        env=None,
        source={"kind": "archive", "archive_artifact_id": str(archive_artifact_id)},
        trigger=Trigger.UPLOAD,
        user=user,
    )
    return version


_UPLOAD_COMPARE_LIMIT = 5


def _same_upload_version(
    definition: AgentDefinition, archive_artifact_id: Any
) -> AgentVersion | None:
    """The newest live upload version of the agent with the same files as this
    upload, so the same folder again makes no version."""
    rows = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_definition_id=definition.id, source_kind=SourceKind.ARCHIVE
        )
        .exclude(preflight_status__in=(Status.FAILED, Status.DISCARDED))
        .select_related("agent_version")
        .defer("credential_blob", "diff")
        .order_by("-created_at")[:_UPLOAD_COMPARE_LIMIT]
    )
    rows = list(rows)
    if not rows:
        return None
    uploaded = _upload_digests(definition.organization_id, archive_artifact_id)
    if uploaded is None:
        return None
    for runtime in rows:
        current = _upload_digests(
            definition.organization_id,
            (runtime.source or {}).get("archive_artifact_id"),
        )
        if current is not None and current == uploaded:
            return runtime.agent_version
    return None


def _upload_digests(
    organization_id: Any, artifact_id: Any
) -> dict[str, tuple[str, int]] | None:
    """The per-file digests of an upload, so two uploads compare by content even
    though each is packed on its own; an upload that can't be read compares as
    different."""
    from simulate.services.harness_versioning.archives import (
        get_archive,
        member_digests,
    )
    from simulate.services.hosted_harness_gateway import _source_object_key

    if not artifact_id:
        return None
    try:
        return member_digests(
            get_archive(_source_object_key(organization_id, str(artifact_id)))
        )
    except (S3Error, HTTPError, tarfile.TarError, OSError, EOFError) as exc:
        logger.warning(
            "agent_upload_compare_failed",
            archive=str(artifact_id),
            error=type(exc).__name__,
        )
        return None


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
    definition: AgentDefinition,
    *,
    prompt: str | None = None,
    settings: Mapping[str, Any] | None = None,
    user: Any = None,
) -> AgentVersion:
    """A version from an edited prompt or settings, or the parent when they are the
    ones it already has."""
    from simulate.services.harness_agent_changes import edit_agent_config

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
    if runtime is None:
        if settings:
            raise HostedHarnessError(
                "agent_change_unsupported",
                "This agent's source doesn't take that change",
                status_code=422,
                details={"field": "settings"},
            )
        if prompt is None or prompt == (parent.description or ""):
            return parent
        return create_agent_version(
            definition,
            parent=parent,
            env=None,
            prompt=prompt,
            trigger=Trigger.PROMPT,
            user=user,
        )
    current = dict(runtime.agent_payload or {})
    edit = edit_agent_config(
        runtime.source_kind,
        current,
        description=parent.description or "",
        prompt=prompt,
        settings=settings,
        settings_field="settings",
    )
    if not edit.changed:
        return parent
    changes: dict[str, Any] = {}
    if edit.config is not None:
        changes["agent_payload"] = {**current, "config": edit.config}
        changes["source"] = dict(runtime.source or {})
    return create_agent_version(
        definition,
        parent=parent,
        env=None,
        prompt=edit.prompt,
        trigger=edit.trigger,
        user=user,
        **changes,
    )


def change_version(
    definition: AgentDefinition,
    *,
    kind: str,
    user: Any = None,
    archive_artifact_id: UUID | None = None,
    secret_values: Mapping[str, str] | None = None,
    prompt: str | None = None,
    settings: Mapping[str, Any] | None = None,
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
        version = manual_version(
            definition, prompt=prompt, settings=settings, user=user
        )
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
        if policy == POLICY_FOLLOW and target is not None:
            stamp_requested_at(target.id, locked.id)
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
            workspace_id=definition.workspace_id,
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
