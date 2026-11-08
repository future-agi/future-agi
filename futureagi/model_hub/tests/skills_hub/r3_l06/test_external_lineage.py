from uuid import uuid4

import pytest

from model_hub.models.skills_hub_r3 import HubSkillMigrationReceipt
from model_hub.services.skills_hub.r3.external_trace_capture import (
    mapped_external_version,
    runtime_trace_metadata,
)

pytestmark = pytest.mark.django_db


def test_external_mapping_matches_all_immutable_source_fields(
    principal, context, context_version
):
    version = context_version(context)
    source = {
        "origin": "https://traces.example.test",
        "project_id": "private-project-a",
        "resource_id": "immutable-context-id",
        "revision": "4",
        "digest": "sha256:" + "a" * 64,
    }
    HubSkillMigrationReceipt.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        source_origin=source["origin"],
        source_project=source["project_id"],
        source_resource=source["resource_id"],
        source_revision=source["revision"],
        source_digest=source["digest"],
        destination=context,
        destination_version=version,
        destination_digest=version.digest,
        relation="identical",
        mapping={},
    )
    assert mapped_external_version(
        principal=principal, source=source, resource_id=context.pk
    )["version_id"] == str(version.pk)
    for field in source:
        assert (
            mapped_external_version(
                principal=principal,
                source={**source, field: "other-identity"},
                resource_id=context.pk,
            )
            is None
        )
    assert (
        mapped_external_version(principal=principal, source=source, resource_id=uuid4())
        is None
    )
    HubSkillMigrationReceipt.objects.update(relation="derived")
    assert (
        mapped_external_version(
            principal=principal, source=source, resource_id=context.pk
        )
        is None
    )


def test_new_hub_release_keeps_new_identity_in_retained_tracing(
    principal, context, context_version
):
    historical = context_version(context)
    changed = context_version(
        context, body="Changed Hub policy, not the previous external revision."
    )
    result = runtime_trace_metadata(
        principal=principal,
        version_id=changed.pk,
        hub_origin="https://hub.example.test",
    )
    assert result["fi.context.version_id"] == str(changed.pk)
    assert result["fi.context.digest"] != historical.digest
    assert not any("langfuse" in key for key in result)
