"""Both-kind quarantine and explicitly human forensic reads, independent of flags."""

from __future__ import annotations

from uuid import UUID

from django.db import transaction

from model_hub.models.skills_hub import HubSkill, HubSkillVersion
from model_hub.models.skills_hub_v12 import (
    HubSkillContextDependency,
    HubSkillContextQuarantine,
)
from model_hub.selectors.skills_hub_v12 import version_in_scope
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.resolve import read_version_file_chunk
from model_hub.services.skills_hub.snapshots import (
    immutable_manifest,
    version_definition,
)
from model_hub.services.skills_hub.v11.principals import (
    effective_scope_level,
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.resolution_leases import (
    authorize_grant,
    check_quarantine_closure,
)
from model_hub.services.skills_hub.v12.types import (
    ContextCredentialPrincipal,
    ContextPrincipal,
    ResourceKind,
)
from tfc.constants.levels import Level


def scoped_version(
    *, scope: SkillScope, version_id: UUID, include_retired: bool = False
) -> HubSkillVersion:
    rows = HubSkillVersion.objects.select_related("skill").filter(
        pk=version_id,
        skill__organization_id=scope.organization_id,
        skill__workspace_id=scope.workspace_id,
        skill__workspace__organization_id=scope.organization_id,
        skill__kind__in=["skill", "agent_context"],
    )
    if not include_retired:
        rows = rows.filter(skill__deleted=False)
    version = rows.first()
    if version is None:
        raise SkillsHubError("resource_not_found", "Version not found.", status=404)
    return version


def require_safety_admin(*, principal: SkillPrincipal) -> None:
    if isinstance(principal, ContextCredentialPrincipal):
        raise SkillsHubError(
            "credential_operation_forbidden",
            "A Context credential cannot administer safety.",
            status=403,
        )
    if effective_scope_level(principal=principal) < Level.WORKSPACE_ADMIN:
        raise SkillsHubError(
            "quarantine_forbidden",
            "Current administrator access is required.",
            status=403,
        )


def get_version_safety(*, principal: SkillPrincipal, version_id: UUID) -> dict:
    require_scope_access(principal=principal, write=False)
    version = scoped_version(
        scope=principal.scope, version_id=version_id, include_retired=True
    )
    if version.skill.deleted:
        require_safety_admin(principal=principal)
    event = (
        HubSkillContextQuarantine.objects.filter(version=version)
        .order_by("-revision")
        .first()
    )
    return {
        "revision": event.revision if event else 0,
        "quarantined": event.active if event else False,
        "reason": event.reason if event else "",
        "replacement_version_id": (
            str(event.replacement_version_id)
            if event and event.replacement_version_id
            else None
        ),
        "affected_context_count": HubSkillContextDependency.objects.filter(
            child_version=version,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            parent_version__skill__organization_id=principal.scope.organization_id,
            parent_version__skill__workspace_id=principal.scope.workspace_id,
            parent_version__skill__kind="agent_context",
            parent_version__skill__deleted=False,
        )
        .values("parent_version__skill_id")
        .distinct()
        .count(),
    }


def set_quarantine(
    *,
    principal: SkillPrincipal,
    version_id: UUID,
    revision: int,
    active: bool,
    reason: str,
    replacement_version_id: UUID | None,
) -> dict:
    require_safety_admin(principal=principal)
    if (
        type(revision) is not int
        or revision < 0
        or type(active) is not bool
        or not isinstance(reason, str)
        or not 1 <= len(reason.strip()) <= 2000
    ):
        raise SkillsHubError(
            "invalid_context_definition",
            "Supply a revision, state and bounded reason.",
            status=400,
        )
    version = scoped_version(
        scope=principal.scope, version_id=version_id, include_retired=True
    )
    # Alternatives belong to this stable identity; never redirect an old UUID by name.
    replacement = None
    if replacement_version_id is not None:
        replacement = scoped_version(
            scope=principal.scope,
            version_id=replacement_version_id,
            include_retired=True,
        )
        if replacement.skill_id != version.skill_id or replacement.pk == version.pk:
            raise SkillsHubError(
                "invalid_context_definition",
                "Choose another version of this resource.",
                status=400,
            )
    versions = [version.pk, *([replacement.pk] if replacement else [])]
    resource_ids = {
        version.skill_id,
        *HubSkillContextDependency.objects.filter(
            parent_version_id__in=versions,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
        ).values_list("child_skill_id", flat=True),
    }
    with transaction.atomic():
        # Publication/release take this same UUID-ordered graph lock. In particular,
        # replacement eligibility cannot race a quarantine of one of its children.
        list(
            HubSkill._base_manager.filter(
                pk__in=resource_ids,
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
            )
            .order_by("id")
            .select_for_update(of=("self",))
        )
        require_safety_admin(principal=principal)
        current = get_version_safety(principal=principal, version_id=version.pk)
        if current["revision"] != revision:
            raise SkillsHubError(
                "release_conflict",
                "Safety state changed. Review again.",
                status=409,
                detail={"revision": current["revision"]},
            )
        if replacement:
            check_quarantine_closure(scope=principal.scope, version=replacement)
        HubSkillContextQuarantine.objects.create(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            version=version,
            revision=revision + 1,
            active=active,
            reason=reason.strip(),
            replacement_version=replacement,
            actor_id=principal.user_id,
        )
        from django.db.models import F

        HubSkill._base_manager.filter(pk=version.skill_id).update(
            security_revision=F("security_revision") + 1
        )
        return get_version_safety(principal=principal, version_id=version.pk)


def check_distribution_access(
    *, principal: ContextPrincipal, version_id: UUID, session_id: UUID | None = None
) -> None:
    if session_id is not None:
        from model_hub.services.skills_hub.v12.consumers import check_session_access

        result = check_session_access(principal=principal, session_id=session_id)
        if result["version_id"] != str(version_id):
            raise SkillsHubError(
                "session_context_conflict",
                "The session has a different immutable identity.",
                status=409,
            )
        return
    if isinstance(principal, ContextCredentialPrincipal):
        version = scoped_version(scope=principal.scope, version_id=version_id)
        authorize_grant(
            principal=principal, version=version, requested={"version": version.number}
        )
    else:
        require_scope_access(principal=principal, write=False)
        version = scoped_version(scope=principal.scope, version_id=version_id)
    check_quarantine_closure(scope=principal.scope, version=version)


def _inspection_version(
    *, principal: SkillPrincipal, context_id: UUID, number: int, kind: ResourceKind
) -> HubSkillVersion:
    if isinstance(principal, ContextCredentialPrincipal):
        raise SkillsHubError(
            "credential_operation_forbidden",
            "Forensic inspection requires a real human principal.",
            status=403,
        )
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=False)
    row = HubSkillVersion.objects.filter(
        skill_id=context_id,
        number=number,
        skill__organization_id=principal.scope.organization_id,
        skill__workspace_id=principal.scope.workspace_id,
        skill__kind=kind,
    ).first()
    if row is None:
        raise SkillsHubError("resource_not_found", "Version not found.", status=404)
    version = version_in_scope(
        scope=principal.scope, kind=kind, version_id=row.pk, include_retired=True
    )
    if version.skill.deleted:
        require_safety_admin(principal=principal)
    return version


def inspect_version(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    number: int,
    kind: ResourceKind = "agent_context",
    file_offset: int = 0,
    file_limit: int = 25,
) -> dict:
    version = _inspection_version(
        principal=principal, context_id=context_id, number=number, kind=kind
    )
    blocked = None
    try:
        check_quarantine_closure(scope=principal.scope, version=version)
    except SkillsHubError as exc:
        if exc.code not in {"context_quarantined", "dependency_unavailable"}:
            raise
        blocked = exc.code
    return {
        "descriptor": version_definition(version),
        "manifest": immutable_manifest(
            version=version,
            requested={"version": number},
            file_offset=file_offset,
            file_limit=file_limit,
        ),
        "distribution_blocked": bool(blocked) or version.skill.deleted,
        "reason": blocked or ("resource_archived" if version.skill.deleted else None),
    }


def inspect_version_file(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    number: int,
    path: str,
    offset: int = 0,
    max_bytes: int = 32768,
    kind: ResourceKind = "agent_context",
) -> dict:
    version = _inspection_version(
        principal=principal, context_id=context_id, number=number, kind=kind
    )
    return {
        **read_version_file_chunk(version, path, offset, max_bytes),
        "resource_id": str(version.skill_id),
        "version_id": str(version.pk),
        "digest": version.digest,
    }
