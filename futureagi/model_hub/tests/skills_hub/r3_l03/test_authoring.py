"""Durable accepted locks, publication origin/head and non-destructive retries."""

from copy import deepcopy
from uuid import uuid4

import pytest

from model_hub.models.skills_hub import HubSkillDraft
from model_hub.models.skills_hub_v12 import (
    HubSkillContextDependency,
    HubSkillContextQuarantine,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.labels import set_label
from model_hub.services.skills_hub.v12.authoring import (
    acknowledge_context_head,
    apply_dependencies,
    create_context,
    get_compiled_draft,
    preview_dependencies,
    publish_context_draft,
    restore_context,
    review_context_head,
    save_context_draft,
    start_context_draft,
)

pytestmark = pytest.mark.django_db


def test_accepted_v8_survives_upstream_v9_edits_and_publication(
    principal,
    context,
    hub_skill,
    hub_actor,
    publish_skill,
    make_skill_files,
    accept_links,
    publish_context,
):
    for number in range(1, 9):
        publish_skill(
            hub_skill,
            files=make_skill_files(body=f"Policy {number}"),
            labels=["production"],
        )
    links = [{"skill_id": str(hub_skill.pk), "selector": {"label": "production"}}]
    draft = accept_links(context, links)
    original_lock = deepcopy(draft.resolved_lock)
    publish_skill(
        hub_skill, files=make_skill_files(body="Policy 9"), labels=["production"]
    )
    saved = save_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        base_version=None,
        files=[{"path": "AGENTS.md", "content": "Changed root instructions"}],
        description="Edited purpose",
        note="Long review",
    )
    assert saved.resolved_lock == original_lock
    same = preview_dependencies(
        principal=principal,
        context_id=context.pk,
        revision=saved.revision,
        links=links,
        refresh_skill_ids=[],
    )
    assert same["resolved_lock"]["links"][0]["version"] == 8
    result = publish_context(context)
    assert result.version.frontmatter == {}
    assert result.version.origin_number is None and result.version.parent_number is None
    dependency = HubSkillContextDependency.objects.get(parent_version=result.version)
    assert dependency.child_version.number == 8
    assert not context.labels.exists()
    text = result.version.files.get(path="skills/help-desk/SKILL.md").blob.content
    assert "Policy 8" in text and "Policy 9" not in text
    tombstone = HubSkillDraft.all_objects.get(skill=context, user_id=principal.user_id)
    assert (
        tombstone.discarded_at
        and tombstone.context_spec == {}
        and tombstone.resolved_lock == {}
    )


def test_refresh_detects_label_aba_but_retained_links_ignore_upstream(
    principal,
    context,
    hub_skill,
    hub_actor,
    publish_skill,
    make_skill_files,
    accept_links,
):
    first = publish_skill(hub_skill, labels=["production"]).version
    second = publish_skill(
        hub_skill, files=make_skill_files(body="Second policy")
    ).version
    links = [{"skill_id": str(hub_skill.pk), "selector": {"label": "production"}}]
    draft = accept_links(context, links)
    keep = preview_dependencies(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        links=links,
        refresh_skill_ids=[],
    )
    refresh = preview_dependencies(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        links=links,
        refresh_skill_ids=[hub_skill.pk],
    )
    set_label(
        skill=hub_skill,
        actor=hub_actor,
        name="production",
        version_number=second.number,
    )
    set_label(
        skill=hub_skill, actor=hub_actor, name="production", version_number=first.number
    )
    with pytest.raises(SkillsHubError) as error:
        apply_dependencies(
            principal=principal,
            context_id=context.pk,
            request={
                "revision": draft.revision,
                "links": links,
                "refresh_skill_ids": [hub_skill.pk],
                "expected_fingerprint": refresh["fingerprint"],
            },
            idempotency_key=uuid4(),
        )
    assert error.value.code == "dependency_plan_changed"
    later = preview_dependencies(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        links=links,
        refresh_skill_ids=[],
    )
    assert later["fingerprint"] == keep["fingerprint"]


def test_dependency_replay_returns_original_receipt_without_overwriting_later_edits(
    principal, context, hub_skill, publish_skill
):
    version = publish_skill(hub_skill).version
    links = [{"skill_id": str(hub_skill.pk), "selector": {"version": version.number}}]
    draft = context.drafts.get()
    preview = preview_dependencies(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        links=links,
        refresh_skill_ids=[],
    )
    request = {
        "revision": draft.revision,
        "links": links,
        "refresh_skill_ids": [],
        "expected_fingerprint": preview["fingerprint"],
    }
    key = uuid4()
    first = apply_dependencies(
        principal=principal, context_id=context.pk, request=request, idempotency_key=key
    )
    draft.refresh_from_db()
    saved = save_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        base_version=None,
        files=[{"path": "AGENTS.md", "content": "Later work"}],
        description="Later purpose",
        note="",
    )
    assert (
        apply_dependencies(
            principal=principal,
            context_id=context.pk,
            request=request,
            idempotency_key=key,
        )
        == first
    )
    saved.refresh_from_db()
    assert saved.files[0]["content"] == "Later work"
    assert saved.revision > first["revision"]
    with pytest.raises(SkillsHubError) as error:
        apply_dependencies(
            principal=principal,
            context_id=context.pk,
            request={**request, "refresh_skill_ids": [hub_skill.pk]},
            idempotency_key=key,
        )
    assert error.value.code == "idempotency_conflict"


def test_historical_v4_repair_publishes_v7_against_acknowledged_v6(
    principal, context, publish_context
):
    versions = []
    for number in range(1, 7):
        if number > 1:
            start_context_draft(
                principal=principal,
                context_id=context.pk,
                revision=0,
                from_version=number - 1,
            )
        draft = context.drafts.get()
        save_context_draft(
            principal=principal,
            context_id=context.pk,
            revision=draft.revision,
            base_version=draft.base_version,
            files=[{"path": "AGENTS.md", "content": f"Policy {number}"}],
            description=f"Purpose {number}",
            note="",
        )
        versions.append(publish_context(context).version)
    draft = start_context_draft(
        principal=principal, context_id=context.pk, revision=0, from_version=4
    )
    assert draft.files == [{"path": "AGENTS.md", "content": "Policy 4"}]
    assert draft.context_spec["description"] == "Purpose 4"
    assert draft.expected_head_version == 6
    save_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        base_version=4,
        files=[{"path": "AGENTS.md", "content": "Policy 4 repaired"}],
        description="Purpose 4 repaired",
        note="",
    )
    result = publish_context(context)
    assert result.version.number == 7
    assert result.version.parent_number == 6
    assert result.version.origin_number == 4
    assert "Policy 6" not in result.version.files.get(path="AGENTS.md").blob.content


def test_head_acknowledgment_is_invalidated_by_another_publish(
    principal, context, publish_context, user, organization, workspace
):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from model_hub.services.skills_hub.v11.types import SkillPrincipal
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    first = publish_context(context).version
    draft = start_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=0,
        from_version=first.number,
    )
    reviewed = review_context_head(
        principal=principal, context_id=context.pk, revision=draft.revision
    )
    draft = acknowledge_context_head(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        expected_digest=reviewed["candidate_digest"],
        expected_head_version=1,
        review_fingerprint=reviewed["review_fingerprint"],
    )
    author = User.objects.create_user(
        email="context-other@example.test",
        name="Other author",
        organization=organization,
    )
    OrganizationMembership.no_workspace_objects.update_or_create(
        user=author,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    other = SkillPrincipal(principal.scope, author.pk, None, "human")
    their = start_context_draft(
        principal=other, context_id=context.pk, revision=0, from_version=1
    )
    their = save_context_draft(
        principal=other,
        context_id=context.pk,
        revision=their.revision,
        base_version=1,
        files=[{"path": "AGENTS.md", "content": "Unrelated new head"}],
        description="Other purpose",
        note="",
    )
    preview = review_context_head(
        principal=other, context_id=context.pk, revision=their.revision
    )
    their = acknowledge_context_head(
        principal=other,
        context_id=context.pk,
        revision=their.revision,
        expected_digest=preview["candidate_digest"],
        expected_head_version=1,
        review_fingerprint=preview["review_fingerprint"],
    )
    publish_context_draft(
        principal=other,
        context_id=context.pk,
        revision=their.revision,
        base_version=1,
        expected_digest=preview["candidate_digest"],
        expected_head_version=1,
        review_fingerprint=their.head_review_fingerprint,
        acknowledge_warnings=True,
        note="",
        idempotency_key=uuid4(),
    )
    with pytest.raises(SkillsHubError) as error:
        publish_context_draft(
            principal=principal,
            context_id=context.pk,
            revision=draft.revision,
            base_version=1,
            expected_digest=reviewed["candidate_digest"],
            expected_head_version=1,
            review_fingerprint=draft.head_review_fingerprint,
            acknowledge_warnings=True,
            note="",
            idempotency_key=uuid4(),
        )
    assert error.value.code == "stale_base"
    assert context.drafts.filter(user=user).exists()
    assert context.versions.count() == 2


def test_restore_retains_retired_child_and_always_allocates_once(
    principal, context, hub_skill, publish_skill, accept_links, publish_context
):
    leaf = publish_skill(hub_skill).version
    accept_links(
        context, [{"skill_id": str(hub_skill.pk), "selector": {"version": leaf.number}}]
    )
    original = publish_context(context).version
    hub_skill.deleted = True
    hub_skill.save(update_fields=["deleted"])
    args = {
        "principal": principal,
        "context_id": context.pk,
        "number": 1,
        "base_version": 1,
        "expected_digest": original.digest,
        "note": "",
        "idempotency_key": uuid4(),
    }
    restored = restore_context(**args)
    assert restored.version.number == 2 and not restored.unchanged
    assert restored.version.digest == original.digest
    assert restored.version.origin_number == 1 and restored.version.parent_number == 1
    assert (
        HubSkillContextDependency.objects.get(
            parent_version=restored.version
        ).child_version_id
        == leaf.pk
    )
    replay = restore_context(**args)
    assert replay.version.pk == restored.version.pk
    assert context.versions.count() == 2
    HubSkillContextQuarantine.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        version=leaf,
        revision=1,
        active=True,
        reason="Unsafe fixture",
        actor_id=principal.user_id,
    )
    with pytest.raises(SkillsHubError) as error:
        restore_context(**{**args, "base_version": 2, "idempotency_key": uuid4()})
    assert error.value.code == "context_quarantined"
    assert context.versions.count() == 2


def test_wrong_kind_foreign_leaf_pin_refresh_and_missing_label_are_not_accepted(
    principal, context, context_factory, hub_skill, publish_skill
):
    publish_skill(hub_skill)
    cases = [
        (
            context_factory("another-context").pk,
            {"label": "production"},
            [],
            "unsupported_dependency_kind",
        ),
        (uuid4(), {"version": 1}, [], "dependency_unavailable"),
        (hub_skill.pk, {"label": "missing"}, [], "label_not_found"),
        (hub_skill.pk, {"version": 1}, [hub_skill.pk], "invalid_dependency_selector"),
    ]
    for identity, selector, refresh, code in cases:
        with pytest.raises(SkillsHubError) as error:
            preview_dependencies(
                principal=principal,
                context_id=context.pk,
                revision=1,
                links=[{"skill_id": str(identity), "selector": selector}],
                refresh_skill_ids=refresh,
            )
        assert error.value.code == code
    assert context.drafts.get().resolved_lock == {"schema": 1, "links": []}


def test_create_idempotency_and_shared_namespace(principal, hub_skill):
    args = {
        "principal": principal,
        "name": "replay-context",
        "description": "Purpose",
        "tags": [],
        "files": None,
        "idempotency_key": uuid4(),
    }
    first = create_context(**args)
    assert create_context(**args) == first
    with pytest.raises(SkillsHubError) as error:
        create_context(**{**args, "name": hub_skill.name, "idempotency_key": uuid4()})
    assert error.value.code == "name_taken"


def test_start_refuses_existing_draft_and_save_preserves_origin(
    principal, context, publish_context
):
    with pytest.raises(SkillsHubError) as error:
        start_context_draft(
            principal=principal, context_id=context.pk, revision=0, from_version=None
        )
    assert error.value.code == "draft_conflict"
    version = publish_context(context).version
    draft = start_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=0,
        from_version=version.number,
    )
    with pytest.raises(SkillsHubError) as error:
        save_context_draft(
            principal=principal,
            context_id=context.pk,
            revision=draft.revision,
            base_version=None,
            files=draft.files,
            description="Changed",
            note="",
        )
    assert error.value.code == "stale_base"
    assert (
        get_compiled_draft(
            principal=principal, context_id=context.pk, revision=draft.revision
        ).digest
        == version.digest
    )


def test_publish_receipt_survives_tombstone_and_later_head(
    principal, context, publish_context
):
    draft = context.drafts.get()
    preview = review_context_head(
        principal=principal, context_id=context.pk, revision=draft.revision
    )
    draft = acknowledge_context_head(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        expected_digest=preview["candidate_digest"],
        expected_head_version=None,
        review_fingerprint=preview["review_fingerprint"],
    )
    args = {
        "principal": principal,
        "context_id": context.pk,
        "revision": draft.revision,
        "base_version": None,
        "expected_digest": preview["candidate_digest"],
        "expected_head_version": None,
        "review_fingerprint": draft.head_review_fingerprint,
        "acknowledge_warnings": True,
        "note": "",
        "idempotency_key": uuid4(),
    }
    first = publish_context_draft(**args)
    assert publish_context_draft(**args).version.pk == first.version.pk
    second_draft = start_context_draft(
        principal=principal, context_id=context.pk, revision=0, from_version=1
    )
    save_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=second_draft.revision,
        base_version=1,
        files=[{"path": "AGENTS.md", "content": "New"}],
        description="New",
        note="",
    )
    publish_context(context)
    assert publish_context_draft(**args).version.pk == first.version.pk
    assert context.versions.count() == 2
