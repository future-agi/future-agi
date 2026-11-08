"""Purpose-limited immutable consumption facade for both resource kinds."""

from __future__ import annotations

from uuid import UUID

from django.utils import timezone

from model_hub.models.skills_hub_v12 import HubSkillContextResolution
from model_hub.selectors.skills_hub_v12 import resources_for_scope
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.resolve import read_version_file_chunk
from model_hub.services.skills_hub.snapshots import immutable_manifest
from model_hub.services.skills_hub.v12.credentials import require_active_credential
from model_hub.services.skills_hub.v12.resolution_leases import (
    issue_resolution,
    validate_resolution,
)
from model_hub.services.skills_hub.v12.types import (
    ContextCredentialPrincipal,
    ResourceKind,
)


def resolve_consumption(
    *,
    principal: ContextCredentialPrincipal,
    kind: ResourceKind,
    name: str,
    label: str | None = None,
    version: int | None = None,
) -> dict:
    credential = require_active_credential(principal=principal, purpose="consume")
    if label is not None and version is not None:
        raise SkillsHubError("invalid_selector", "Choose one selector.", status=400)
    if version is not None and (type(version) is not int or version < 1):
        raise SkillsHubError(
            "invalid_selector", "Choose a positive version number.", status=400
        )
    grants = [grant for grant in credential.grants if grant["kind"] == kind]
    resource = (
        resources_for_scope(scope=principal.scope, kind=kind)
        .filter(name=name, pk__in=[grant["resource_id"] for grant in grants])
        .first()
    )
    if resource is None:
        raise SkillsHubError("resource_not_found", "Resource not found.", status=404)
    grant = next(grant for grant in grants if grant["resource_id"] == str(resource.pk))
    requested: dict
    if version is not None:
        requested = {"version": version}
        target = resource.versions.filter(
            number=version, pk__in=grant["version_ids"]
        ).first()
    else:
        label = label or "production"
        requested = {"label": label}
        if label not in grant["labels"]:
            raise SkillsHubError(
                "credential_operation_forbidden",
                "The credential does not grant this selector.",
                status=403,
            )
        target = (
            resource.versions.filter(number=resource.latest_version_number).first()
            if label == "latest"
            else resource.versions.filter(
                labels__skill=resource, labels__name=label
            ).first()
        )
    if target is None:
        raise SkillsHubError(
            "version_not_found" if version is not None else "label_not_found",
            "Requested release not found.",
            status=404,
        )
    resolution = issue_resolution(
        principal=principal, version=target, requested=requested
    )
    return {
        **immutable_manifest(version=target, requested=requested),
        "resolution_id": str(resolution.pk),
        "resolution_expires_at": resolution.expires_at,
    }


def read_consumption_manifest(
    *,
    principal: ContextCredentialPrincipal,
    kind: ResourceKind,
    resource_id: UUID,
    number: int,
    resolution_id: UUID | None,
    file_offset: int = 0,
    file_limit: int = 25,
) -> dict:
    version = validate_resolution(
        principal=principal,
        resolution_id=resolution_id,
        resource_id=resource_id,
        kind=kind,
        number=number,
    )
    assert resolution_id is not None  # validate_resolution rejects missing leases.
    resolution = HubSkillContextResolution.objects.get(
        pk=resolution_id, credential_id=principal.credential_id
    )
    return {
        **immutable_manifest(
            version=version,
            requested=resolution.requested,
            file_offset=file_offset,
            file_limit=file_limit,
        ),
        "resolution_id": str(resolution.pk),
        "resolution_expires_at": resolution.expires_at,
    }


def read_consumption_file(
    *,
    principal: ContextCredentialPrincipal,
    kind: ResourceKind,
    resource_id: UUID,
    number: int,
    resolution_id: UUID | None,
    path: str,
    offset: int = 0,
    max_bytes: int = 32768,
) -> dict:
    version = validate_resolution(
        principal=principal,
        resolution_id=resolution_id,
        resource_id=resource_id,
        kind=kind,
        number=number,
    )
    return {
        **read_version_file_chunk(version, path, offset, max_bytes),
        "resource_id": str(resource_id),
        "version_id": str(version.pk),
        "digest": version.digest,
    }


def check_consumption_access(
    *,
    principal: ContextCredentialPrincipal,
    kind: ResourceKind,
    resource_id: UUID,
    number: int,
    resolution_id: UUID | None,
) -> dict:
    version = validate_resolution(
        principal=principal,
        resolution_id=resolution_id,
        resource_id=resource_id,
        kind=kind,
        number=number,
    )
    return {
        "allowed": True,
        "version_id": str(version.pk),
        "digest": version.digest,
        "checked_at": timezone.now(),
    }
