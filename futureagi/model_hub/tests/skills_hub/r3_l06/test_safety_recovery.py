from uuid import uuid4

import pytest

from accounts.models import OrgApiKey
from model_hub.models.skills_hub import HubSkill, HubSkillLabel
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.legacy_recovery import (
    list_archived_skills,
    review_unarchive,
    unarchive_resource,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.safety import (
    check_distribution_access,
    get_version_safety,
    inspect_version,
    inspect_version_file,
    set_quarantine,
)

pytestmark = pytest.mark.django_db


def test_retired_child_still_consumable_quarantine_blocks_parent(
    principal, context, context_version, hub_skill, publish_skill
):
    child = publish_skill(hub_skill).version
    parent = context_version(context, child=child)
    HubSkill._base_manager.filter(pk=hub_skill.pk).update(deleted=True)
    check_distribution_access(principal=principal, version_id=parent.pk)
    with pytest.raises(SkillsHubError):
        check_distribution_access(principal=principal, version_id=child.pk)
    result = set_quarantine(
        principal=principal,
        version_id=child.pk,
        revision=0,
        active=True,
        reason="Synthetic quarantine",
        replacement_version_id=None,
    )
    assert result["revision"] == 1
    assert result["affected_context_count"] == 1
    with pytest.raises(SkillsHubError) as exc:
        check_distribution_access(principal=principal, version_id=parent.pk)
    assert exc.value.code == "context_quarantined"
    inspection = inspect_version(
        principal=principal, context_id=context.pk, number=parent.number
    )
    assert inspection["distribution_blocked"]
    assert inspect_version_file(
        principal=principal,
        context_id=context.pk,
        number=parent.number,
        path="AGENTS.md",
    )["text"]
    with pytest.raises(SkillsHubError) as exc:
        set_quarantine(
            principal=principal,
            version_id=child.pk,
            revision=0,
            active=False,
            reason="Stale clear",
            replacement_version_id=None,
        )
    assert exc.value.code == "release_conflict"
    set_quarantine(
        principal=principal,
        version_id=child.pk,
        revision=1,
        active=False,
        reason="Cleared after review",
        replacement_version_id=None,
    )
    check_distribution_access(principal=principal, version_id=parent.pk)


def test_system_key_cannot_inspect_quarantine(
    principal, context, context_version, organization, workspace
):
    version = context_version(context)
    key = OrgApiKey.objects.create(
        name="Synthetic system",
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        type="system",
        organization=organization,
        workspace=workspace,
    )
    system = SkillPrincipal(principal.scope, None, key.pk, "system_key")
    with pytest.raises(SkillsHubError) as exc:
        inspect_version_file(
            principal=system,
            context_id=context.pk,
            number=version.number,
            path="AGENTS.md",
        )
    assert exc.value.code == "principal_not_supported"
    with pytest.raises(SkillsHubError):
        set_quarantine(
            principal=system,
            version_id=version.pk,
            revision=0,
            active=True,
            reason="Forbidden",
            replacement_version_id=None,
        )


@pytest.mark.parametrize("kind", ["skill", "agent_context"])
def test_original_uuid_recovery_collision_policy_cas_and_replay(
    principal, context, context_version, hub_skill, publish_skill, kind
):
    skill = context if kind == "agent_context" else hub_skill
    version = (
        context_version(skill)
        if kind == "agent_context"
        else publish_skill(skill).version
    )
    HubSkillLabel.objects.create(skill=skill, name="production", version=version)
    HubSkill._base_manager.filter(pk=skill.pk).update(
        deleted=True, protected_label_names=["production"]
    )
    review = review_unarchive(principal=principal, resource_id=skill.pk, kind=kind)
    assert review["retained_labels"][0]["is_protected"]
    assert list_archived_skills(principal=principal, kind=kind)["results"][0][
        "id"
    ] == str(skill.pk)
    duplicate = HubSkill._base_manager.create(
        organization_id=skill.organization_id,
        workspace_id=skill.workspace_id,
        name=skill.name,
        kind=kind,
        description="Same name, independent identity",
    )
    request = {
        "principal": principal,
        "resource_id": skill.pk,
        "kind": kind,
        "expected_release_revision": review["resource"]["release_revision"],
        "expected_review_fingerprint": review["review_fingerprint"],
        "note": "Recover retained identity",
        "idempotency_key": uuid4(),
    }
    with pytest.raises(SkillsHubError) as exc:
        unarchive_resource(**request)
    assert exc.value.code == "name_taken"
    HubSkill._base_manager.filter(pk=duplicate.pk).update(deleted=True)
    result = unarchive_resource(**request)
    assert result["id"] == str(skill.pk)
    assert result["release_revision"] == review["resource"]["release_revision"] + 1
    assert result["protected_label_names"] == ["production"]
    assert unarchive_resource(**request) == result
    assert skill.versions.get(pk=version.pk).digest == version.digest
    assert skill.labels.get(name="production").version_id == version.pk


def test_quarantine_replacement_must_be_same_identity(
    principal, context, context_version, hub_skill, publish_skill
):
    version = context_version(context)
    foreign = publish_skill(hub_skill).version
    with pytest.raises(SkillsHubError):
        set_quarantine(
            principal=principal,
            version_id=version.pk,
            revision=0,
            active=True,
            reason="Bad replacement",
            replacement_version_id=foreign.pk,
        )
    assert (
        get_version_safety(principal=principal, version_id=version.pk)["revision"] == 0
    )


def test_safety_foreign_ids_never_reveal_details(principal):
    with pytest.raises(SkillsHubError) as exc:
        get_version_safety(principal=principal, version_id=uuid4())
    assert exc.value.code == "resource_not_found"
    assert not exc.value.detail
