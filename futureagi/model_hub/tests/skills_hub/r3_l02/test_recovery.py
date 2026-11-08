import json
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.utils import timezone

from accounts.models.organization_membership import OrganizationMembership
from model_hub.models.skills_hub import HubSkillDraft
from model_hub.models.skills_hub_r3 import HubSkillDraftCheckpoint
from model_hub.services.skills_hub.authoring_foundation import (
    save_owned_draft,
    start_selected_draft,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.draft_recovery import (
    checkpoint_draft,
    compare_recovery_files,
    draft_recovery_digest,
    export_unsent_draft,
    get_checkpoint,
    list_checkpoints,
    purge_expired_checkpoints,
    restore_checkpoint,
)

pytestmark = pytest.mark.django_db


def save_checkpoint(principal, skill, **kwargs):
    draft = skill.drafts.get(user_id=principal.user_id)
    return checkpoint_draft(
        principal=principal,
        resource_id=skill.pk,
        kind=skill.kind,
        revision=draft.revision,
        expected_digest=draft_recovery_digest(draft),
        reason="suspension",
        suspend=kwargs.pop("suspend", True),
        idempotency_key=kwargs.pop("key", uuid4()),
        **kwargs,
    )


def test_suspend_historical_switch_restore_preserves_alternate(
    principal, hub_skill, publish_skill, make_skill_files
):
    first = publish_skill(hub_skill).version
    second = publish_skill(
        hub_skill, files=make_skill_files(body="Unrelated new policy")
    ).version
    draft = hub_skill.drafts.get(user_id=principal.user_id)
    original = draft.files
    saved = save_checkpoint(principal, hub_skill)
    tombstone = HubSkillDraft.all_objects.get(pk=draft.pk)
    assert tombstone.files == [] and tombstone.revision > draft.revision
    switched = start_selected_draft(
        principal=principal,
        resource_id=hub_skill.pk,
        kind="skill",
        revision=0,
        from_version=first.number,
    )
    assert (
        switched.base_version == first.number
        and switched.expected_head_version == second.number
    )
    assert switched.revision > tombstone.revision
    key = uuid4()
    request = {
        "principal": principal,
        "resource_id": hub_skill.pk,
        "kind": "skill",
        "checkpoint_id": UUID(saved["checkpoint"]["id"]),
        "revision": switched.revision,
        "expected_digest": saved["checkpoint"]["digest"],
        "idempotency_key": key,
    }
    restored = restore_checkpoint(**request)
    draft.refresh_from_db()
    assert draft.files == original and draft.revision > switched.revision
    assert draft.head_review_fingerprint == ""
    alternate = get_checkpoint(
        principal=principal,
        resource_id=hub_skill.pk,
        kind="skill",
        checkpoint_id=UUID(restored["preserved_checkpoint_id"]),
    )
    assert alternate.payload["base_version"] == first.number
    edited = save_owned_draft(
        principal=principal,
        resource_id=hub_skill.pk,
        kind="skill",
        revision=draft.revision,
        base_version=draft.base_version,
        files=make_skill_files(body="Later authored work"),
        note="Later",
    )
    assert restore_checkpoint(**request) == restored
    edited.refresh_from_db()
    assert edited.note == "Later"


def test_checkpoint_replay_before_expiry_and_current_revision(principal, hub_skill):
    draft = hub_skill.drafts.get(user_id=principal.user_id)
    request = {
        "principal": principal,
        "resource_id": hub_skill.pk,
        "kind": "skill",
        "revision": draft.revision,
        "expected_digest": draft_recovery_digest(draft),
        "reason": "suspension",
        "suspend": True,
        "idempotency_key": uuid4(),
    }
    first = checkpoint_draft(**request)
    HubSkillDraftCheckpoint.objects.update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert purge_expired_checkpoints() == 1
    assert checkpoint_draft(**request) == first
    with pytest.raises(SkillsHubError, match="different inputs"):
        checkpoint_draft(**{**request, "suspend": False})
    with pytest.raises(SkillsHubError) as error:
        get_checkpoint(
            principal=principal,
            resource_id=hub_skill.pk,
            kind="skill",
            checkpoint_id=UUID(first["checkpoint"]["id"]),
        )
    assert error.value.code == "evidence_expired"
    listed = list_checkpoints(
        principal=principal, resource_id=hub_skill.pk, kind="skill"
    )
    assert listed["results"][0]["payload_expired"] is True
    assert "payload" not in listed["results"][0]


def test_owner_kind_scope_and_system_isolation(
    principal, second_principal, hub_skill, hub_other_workspace
):
    checkpoint = save_checkpoint(principal, hub_skill)
    assert (
        list_checkpoints(
            principal=second_principal, resource_id=hub_skill.pk, kind="skill"
        )["count"]
        == 0
    )
    for actor, kind in [
        (second_principal, "skill"),
        (principal, "agent_context"),
        (
            replace(
                principal,
                scope=replace(principal.scope, workspace_id=hub_other_workspace.pk),
            ),
            "skill",
        ),
    ]:
        with pytest.raises(SkillsHubError) as error:
            get_checkpoint(
                principal=actor,
                resource_id=hub_skill.pk,
                kind=kind,
                checkpoint_id=UUID(checkpoint["checkpoint"]["id"]),
            )
        assert error.value.status == 404
    with pytest.raises(SkillsHubError) as error:
        list_checkpoints(
            principal=replace(principal, kind="system_key", user_id=None),
            resource_id=hub_skill.pk,
            kind="skill",
        )
    assert error.value.code == "principal_not_supported"


def test_revocation_denies_operation_replay(principal, hub_skill):
    draft = hub_skill.drafts.get(user_id=principal.user_id)
    request = {
        "principal": principal,
        "resource_id": hub_skill.pk,
        "kind": "skill",
        "revision": draft.revision,
        "expected_digest": draft_recovery_digest(draft),
        "reason": "manual",
        "suspend": False,
        "idempotency_key": uuid4(),
    }
    checkpoint_draft(**request)
    OrganizationMembership.no_workspace_objects.filter(
        user_id=principal.user_id
    ).update(is_active=False)
    with pytest.raises(SkillsHubError) as error:
        checkpoint_draft(**request)
    assert error.value.code == "access_revoked"


def test_bounded_checkpoints_do_not_silently_evict(principal, hub_skill):
    for _ in range(20):
        save_checkpoint(principal, hub_skill, suspend=False)
    with pytest.raises(SkillsHubError) as error:
        save_checkpoint(principal, hub_skill, suspend=False)
    assert error.value.code == "context_rate_limited"
    assert HubSkillDraftCheckpoint.objects.count() == 20
    assert hub_skill.drafts.count() == 1


def test_cas_and_digest_fail_without_checkpoints(principal, hub_skill):
    draft = hub_skill.drafts.get(user_id=principal.user_id)
    for revision, digest, code in [
        (draft.revision + 1, draft_recovery_digest(draft), "draft_conflict"),
        (draft.revision, "sha256:" + "0" * 64, "candidate_changed"),
    ]:
        with pytest.raises(SkillsHubError) as error:
            checkpoint_draft(
                principal=principal,
                resource_id=hub_skill.pk,
                kind="skill",
                revision=revision,
                expected_digest=digest,
                reason="manual",
                suspend=True,
                idempotency_key=uuid4(),
            )
        assert error.value.code == code
    assert HubSkillDraftCheckpoint.objects.count() == 0
    assert hub_skill.drafts.count() == 1


def test_recovery_export_is_lossless_and_comparison_has_three_columns(make_skill_files):
    base = make_skill_files(body="Base café\r\n")
    local = make_skill_files(body="Unsent 本文\r\n")
    server = make_skill_files(body="Server alternative")
    assert json.loads(export_unsent_draft(files=local, note="unsent"))["files"] == local
    comparison = compare_recovery_files(base=base, local=local, server=server)
    assert set(comparison) == {"base_local", "base_server", "local_server"}
    assert comparison["base_local"]["files"][0]["to_text"] == local[0]["content"]
    assert comparison["local_server"]["files"][0]["to_text"] == server[0]["content"]
