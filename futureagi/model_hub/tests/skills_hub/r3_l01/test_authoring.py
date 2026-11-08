"""Historical origin and independently reviewed head cannot silently converge."""

import pytest
from django.db import transaction

from model_hub.models.skills_hub import HubSkill
from model_hub.services.skills_hub.authoring_foundation import (
    acknowledge_head_locked,
    head_review,
    require_reviewed_head_locked,
    save_owned_draft,
    start_selected_draft,
)
from model_hub.services.skills_hub.drafts import discard_draft
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.versions import create_skill, publish_by_name

pytestmark = pytest.mark.django_db


def test_origin_and_latest_are_independent_and_acknowledgment_is_digest_bound(
    hub_skill, hub_actor, publish_skill, make_skill_files
):
    origin = publish_skill(hub_skill).version
    head = publish_skill(
        hub_skill, files=make_skill_files(body="Unrelated newer policy")
    ).version
    principal = SkillPrincipal(
        SkillScope(hub_skill.organization_id, hub_skill.workspace_id),
        hub_actor.user.pk,
        None,
        "human",
    )
    discard_draft(skill=hub_skill, user=hub_actor.user)
    draft = start_selected_draft(
        principal=principal,
        resource_id=hub_skill.pk,
        kind="skill",
        revision=0,
        from_version=origin.number,
    )
    assert manifest_digest(draft.files) == origin.digest
    assert draft.base_version == origin.number
    assert draft.expected_head_version == head.number
    revision = draft.revision
    with transaction.atomic():
        locked = HubSkill.no_workspace_objects.select_for_update().get(pk=hub_skill.pk)
        preview = head_review(skill=locked, draft=draft, candidate_digest=origin.digest)
        draft = acknowledge_head_locked(
            skill=locked,
            draft=draft,
            candidate_digest=origin.digest,
            expected_head_version=head.number,
            review_fingerprint=preview["review_fingerprint"],
        )
        require_reviewed_head_locked(
            skill=locked,
            draft=draft,
            candidate_digest=origin.digest,
            review_fingerprint=draft.head_review_fingerprint,
        )
    assert draft.revision == revision + 1
    assert draft.base_version == origin.number
    assert manifest_digest(draft.files) == origin.digest
    publish_skill(hub_skill, files=make_skill_files(body="Another newer policy"))
    with transaction.atomic(), pytest.raises(SkillsHubError) as error:
        locked = HubSkill.no_workspace_objects.select_for_update().get(pk=hub_skill.pk)
        require_reviewed_head_locked(
            skill=locked,
            draft=draft,
            candidate_digest=origin.digest,
            review_fingerprint=draft.head_review_fingerprint,
        )
    assert error.value.code == "stale_base"


def test_start_refuses_existing_draft_and_saves_cannot_change_origin(
    hub_skill, hub_actor, publish_skill
):
    version = publish_skill(hub_skill).version
    principal = SkillPrincipal(
        SkillScope(hub_skill.organization_id, hub_skill.workspace_id),
        hub_actor.user.pk,
        None,
        "human",
    )
    with pytest.raises(SkillsHubError) as error:
        start_selected_draft(
            principal=principal,
            resource_id=hub_skill.pk,
            kind="skill",
            revision=0,
            from_version=version.number,
        )
    assert error.value.code == "draft_conflict"
    original = hub_skill.drafts.get()
    with pytest.raises(SkillsHubError) as error:
        save_owned_draft(
            principal=principal,
            resource_id=hub_skill.pk,
            kind="skill",
            revision=original.revision,
            base_version=version.number,
            files=original.files,
            note="",
        )
    assert error.value.code == "stale_base"


def test_context_namespace_cannot_be_overwritten_by_legacy_create_or_push(
    organization, workspace, hub_actor, make_skill_files
):
    context = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="same-name",
        description="Context purpose",
        kind="agent_context",
    )
    with pytest.raises(SkillsHubError) as error:
        create_skill(
            organization=organization,
            workspace=workspace,
            actor=hub_actor,
            name=context.name,
            description="A leaf purpose",
            tags=[],
        )
    assert error.value.code == "name_taken"
    with pytest.raises(SkillsHubError) as error:
        publish_by_name(
            organization=organization,
            workspace=workspace,
            actor=hub_actor,
            files=make_skill_files(name=context.name),
            note="",
            labels=[],
            tags=[],
            acknowledge_warnings=True,
        )
    assert error.value.code == "name_taken"
    context.refresh_from_db()
    assert context.kind == "agent_context" and context.latest_version_number == 0


def test_context_save_preserves_accepted_lock_and_invalidates_head_review(
    organization, workspace, hub_actor
):
    context = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="context-draft",
        description="Context purpose",
        kind="agent_context",
    )
    principal = SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), hub_actor.user.pk, None, "human"
    )
    draft = start_selected_draft(
        principal=principal,
        resource_id=context.pk,
        kind="agent_context",
        revision=0,
        from_version=None,
    )
    draft.resolved_lock = {"schema": 1, "links": [{"version": 8}]}
    draft.head_review_fingerprint = "sha256:" + "f" * 64
    draft.save()
    saved = save_owned_draft(
        principal=principal,
        resource_id=context.pk,
        kind="agent_context",
        revision=draft.revision,
        base_version=None,
        files=draft.files,
        note="Edit during review",
        description="New purpose",
    )
    assert saved.resolved_lock == draft.resolved_lock
    assert saved.context_spec["description"] == "New purpose"
    assert saved.head_review_fingerprint == ""
    assert saved.revision == draft.revision + 1
