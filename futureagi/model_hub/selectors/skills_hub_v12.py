"""Explicit-kind selectors. Authorization precedes lookups and counts."""

from __future__ import annotations

from uuid import UUID

from django.db.models import QuerySet

from model_hub.models.skills_hub import HubSkill, HubSkillVersion
from model_hub.models.skills_hub_v12 import HubSkillContextCredential
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.principals import require_scope_access
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.types import ResourceKind


def resources_for_scope(
    *, scope: SkillScope, kind: ResourceKind, archived: bool = False
) -> QuerySet[HubSkill]:
    if kind not in {"skill", "agent_context"}:
        raise SkillsHubError("resource_not_found", "Resource not found.", status=404)
    # all_objects still applies ambient workspace filtering. Recovery and isolated
    # credentials must instead use only the explicitly authorized scope below.
    return HubSkill._base_manager.filter(
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        workspace__organization_id=scope.organization_id,
        kind=kind,
        deleted=archived,
    )


def get_resource(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    lock: bool = False,
    archived: bool = False,
) -> HubSkill:
    require_scope_access(principal=principal, write=lock)
    if archived:
        from model_hub.services.skills_hub.v11.principals import effective_scope_level
        from tfc.constants.levels import Level

        if effective_scope_level(principal=principal) < Level.WORKSPACE_ADMIN:
            raise SkillsHubError(
                "permission_denied", "Administrator access is required.", status=403
            )
    rows = resources_for_scope(scope=principal.scope, kind=kind, archived=archived)
    if lock:
        rows = rows.select_for_update(of=("self",))
    resource = rows.filter(pk=resource_id).first()
    if resource is None:
        raise SkillsHubError("resource_not_found", "Resource not found.", status=404)
    return resource


def version_in_scope(
    *,
    scope: SkillScope,
    kind: ResourceKind,
    version_id: UUID,
    include_retired: bool = False,
) -> HubSkillVersion:
    rows = HubSkillVersion.objects.filter(
        pk=version_id,
        skill__organization_id=scope.organization_id,
        skill__workspace_id=scope.workspace_id,
        skill__workspace__organization_id=scope.organization_id,
        skill__kind=kind,
    )
    if not include_retired:
        rows = rows.filter(skill__deleted=False)
    version = rows.select_related("skill").first()
    if version is None:
        raise SkillsHubError("resource_not_found", "Resource not found.", status=404)
    return version


def credentials_for_scope(*, scope: SkillScope) -> QuerySet[HubSkillContextCredential]:
    return (
        HubSkillContextCredential.objects.filter(
            organization_id=scope.organization_id, workspace_id=scope.workspace_id
        )
        .defer("secret_hash")
        .order_by("-created_at", "id")
    )
