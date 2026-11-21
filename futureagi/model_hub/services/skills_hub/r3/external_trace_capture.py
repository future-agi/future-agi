"""Verified immutable migration lookups and explicit external lineage, never name joins."""

from __future__ import annotations

from uuid import UUID

from model_hub.models.skills_hub_r3 import HubSkillMigrationReceipt
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.safety import scoped_version
from tracer.utils.adapters.langfuse_skills import (
    external_trace_link,
    hub_runtime_metadata,
)

SOURCE_FIELDS = {"origin", "project_id", "resource_id", "revision", "digest"}


def mapped_external_version(
    *, principal: SkillPrincipal, source: dict, resource_id: UUID
) -> dict | None:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=False)
    if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
        raise SkillsHubError(
            "capture_unsafe",
            "An immutable external source identity is required.",
            status=422,
        )
    rows = HubSkillMigrationReceipt.objects.select_related(
        "destination_version__skill"
    ).filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        destination_id=resource_id,
        destination__organization_id=principal.scope.organization_id,
        destination__workspace_id=principal.scope.workspace_id,
        schema=1,
        relation="identical",
        source_origin=source["origin"],
        source_project=source["project_id"],
        source_resource=source["resource_id"],
        source_revision=source["revision"],
        source_digest=source["digest"],
    )
    receipt = rows.first()
    if receipt is None or receipt.destination_version is None:
        return None
    version = receipt.destination_version
    if version.skill_id != resource_id or version.digest != receipt.destination_digest:
        return None
    return {
        "version_id": str(version.pk),
        "digest": version.digest,
        "migration_receipt_id": str(receipt.pk),
    }


def runtime_trace_metadata(
    *, principal: SkillPrincipal, version_id: UUID, hub_origin: str
) -> dict:
    require_scope_access(principal=principal, write=False)
    version = scoped_version(scope=principal.scope, version_id=version_id)
    return hub_runtime_metadata(
        kind=version.skill.kind,
        resource_id=version.skill_id,
        version_id=version.pk,
        version=version.number,
        digest=version.digest,
        hub_origin=hub_origin,
    )


def retained_external_lineage(
    *, origin: str, project_id: str, trace_id: str, observation_id: str
) -> dict:
    """Connector calls this with its own configured origin and returned immutable IDs."""
    return {
        "provider": "langfuse",
        "origin": origin.rstrip("/"),
        "project_id": project_id,
        "trace_id": trace_id,
        "observation_id": observation_id,
        "url": external_trace_link(
            origin=origin,
            project_id=project_id,
            trace_id=trace_id,
            observation_id=observation_id,
        ),
        "trust": "connector_recorded",
    }
