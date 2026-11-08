"""Credential-bound immutable continuation; label movement never retargets pages."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from model_hub.models.skills_hub import HubSkillVersion
from model_hub.models.skills_hub_v12 import (
    HubSkillContextDependency,
    HubSkillContextQuarantine,
    HubSkillContextResolution,
)
from model_hub.selectors.skills_hub_v12 import version_in_scope
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.types import SkillScope
from model_hub.services.skills_hub.v12.credentials import require_active_credential
from model_hub.services.skills_hub.v12.limits import RESOLUTION_SECONDS
from model_hub.services.skills_hub.v12.types import (
    ContextCredentialPrincipal,
    ResourceKind,
)


def check_quarantine_closure(*, scope: SkillScope, version: HubSkillVersion) -> None:
    """Retirement is not quarantine. Inspect all retained children, including retired."""
    if (
        version.skill.organization_id != scope.organization_id
        or version.skill.workspace_id != scope.workspace_id
    ):
        raise SkillsHubError("resource_not_found", "Resource not found.", status=404)
    dependencies = list(
        HubSkillContextDependency.objects.filter(parent_version=version).select_related(
            "child_version__skill"
        )
    )
    for dependency in dependencies:
        child = dependency.child_version.skill
        if (
            dependency.organization_id != scope.organization_id
            or dependency.workspace_id != scope.workspace_id
            or child.organization_id != scope.organization_id
            or child.workspace_id != scope.workspace_id
            or child.kind != "skill"
            or dependency.child_skill_id != child.pk
        ):
            raise SkillsHubError(
                "dependency_unavailable",
                "A retained dependency is unavailable.",
                status=404,
            )
    ids = [version.pk, *(dependency.child_version_id for dependency in dependencies)]
    events = (
        HubSkillContextQuarantine.objects.filter(version_id__in=ids)
        .order_by("version_id", "-revision")
        .distinct("version_id")
    )
    if any(event.active for event in events):
        raise SkillsHubError(
            "context_quarantined",
            "This release or a retained dependency is quarantined.",
            status=422,
        )


def require_distribution_eligibility(version: HubSkillVersion) -> None:
    """Security check for an already scope-authorized legacy or context version."""
    check_quarantine_closure(
        scope=SkillScope(version.skill.organization_id, version.skill.workspace_id),
        version=version,
    )


def authorize_grant(
    *, principal: ContextCredentialPrincipal, version: HubSkillVersion, requested: dict
) -> dict:
    credential = require_active_credential(principal=principal, purpose="consume")
    skill = version.skill
    if (
        skill.organization_id != principal.scope.organization_id
        or skill.workspace_id != principal.scope.workspace_id
        or skill.deleted
    ):
        raise SkillsHubError("resource_not_found", "Resource not found.", status=404)
    for grant in credential.grants:
        if grant["kind"] != skill.kind or grant["resource_id"] != str(skill.pk):
            continue
        if (
            set(requested) == {"version"}
            and type(requested["version"]) is int
            and requested["version"] == version.number
            and str(version.pk) in grant["version_ids"]
        ):
            return dict(grant)
        if set(requested) == {"label"} and requested["label"] in grant["labels"]:
            label = requested["label"]
            current = (
                skill.latest_version_number == version.number
                if label == "latest"
                else skill.labels.filter(name=label, version=version).exists()
            )
            if current:
                return dict(grant)
    raise SkillsHubError(
        "credential_operation_forbidden",
        "The credential does not grant this selector.",
        status=403,
    )


@transaction.atomic
def issue_resolution(
    *, principal: ContextCredentialPrincipal, version: HubSkillVersion, requested: dict
) -> HubSkillContextResolution:
    credential = require_active_credential(principal=principal, purpose="consume")
    # Serialize against revocation. Selectors are checked again in this transaction.
    type(credential).objects.select_for_update().get(pk=credential.pk)
    credential = require_active_credential(principal=principal, purpose="consume")
    grant = authorize_grant(principal=principal, version=version, requested=requested)
    check_quarantine_closure(scope=principal.scope, version=version)
    return HubSkillContextResolution.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        credential=credential,
        version=version,
        resource_kind=version.skill.kind,
        digest=version.digest,
        requested=requested,
        grant=grant,
        expires_at=min(
            timezone.now() + timedelta(seconds=RESOLUTION_SECONDS),
            credential.expires_at,
        ),
    )


def validate_resolution(
    *,
    principal: ContextCredentialPrincipal,
    resolution_id: UUID | None,
    resource_id: UUID,
    kind: ResourceKind,
    number: int,
) -> HubSkillVersion:
    credential = require_active_credential(principal=principal, purpose="consume")
    if resolution_id is None:
        raise SkillsHubError(
            "context_precondition_required",
            "X-Context-Resolution is required for immutable reads.",
            status=428,
        )
    resolution = HubSkillContextResolution.objects.filter(
        pk=resolution_id,
        credential=credential,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        resource_kind=kind,
        version__skill_id=resource_id,
        version__number=number,
    ).first()
    if resolution is None:
        raise SkillsHubError("resource_not_found", "Resolution not found.", status=404)
    if resolution.expires_at <= timezone.now():
        raise SkillsHubError(
            "resolution_expired",
            "Resolve again and replan; this resolution expired.",
            status=410,
        )
    if resolution.grant not in credential.grants:
        raise SkillsHubError(
            "credential_operation_forbidden",
            "The original grant is no longer available.",
            status=403,
        )
    version = version_in_scope(
        scope=principal.scope, kind=kind, version_id=resolution.version_id
    )
    if version.digest != resolution.digest:
        raise SkillsHubError(
            "candidate_changed", "The immutable identity no longer matches.", status=409
        )
    check_quarantine_closure(scope=principal.scope, version=version)
    return version
