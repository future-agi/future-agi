from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import close_old_connections
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_r3 import HubSkillIntakePlan
from model_hub.models.skills_hub_v12 import HubSkillContextQuarantine
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.push_plans import (
    accept_push,
    preview_push,
    restore_skill_version,
)

pytestmark = pytest.mark.django_db


def accept(principal, plan, **overrides):
    item = plan.inventory["items"][0]
    return accept_push(
        **{
            **{
                "principal": principal,
                "plan_id": plan.pk,
                "expected_fingerprint": plan.fingerprint,
                "mode": item["mode"],
                "expected_head_version": item["expected_head_version"],
                "expected_head_digest": item["expected_head_digest"],
                "acknowledge_warnings": True,
                "idempotency_key": uuid4(),
            },
            **overrides,
        }
    )


def test_omitted_tags_preserve_empty_clears_and_patch_is_not_snapshot(
    principal, hub_skill, publish_skill, make_skill_files
):
    publish_skill(
        hub_skill,
        files=make_skill_files(
            extra=[{"path": "references/keep.md", "content": "Keep this"}]
        ),
    )
    plan = preview_push(
        principal=principal,
        resource_id=hub_skill.pk,
        files=make_skill_files(body="Patch"),
        mode="patch",
    )
    result = accept(principal, plan)
    hub_skill.refresh_from_db()
    assert hub_skill.tags == ["support"]
    assert (
        hub_skill.versions.get(pk=result["version_id"])
        .files.filter(path="references/keep.md")
        .exists()
    )
    second = preview_push(
        principal=principal,
        resource_id=hub_skill.pk,
        files=make_skill_files(body="Snapshot"),
        mode="snapshot",
        tags=[],
    )
    assert second.inventory["items"][0]["changes"]["delete_paths"] == [
        "references/keep.md"
    ]
    result = accept(principal, second)
    hub_skill.refresh_from_db()
    assert hub_skill.tags == [] and hub_skill.metadata_revision == 1
    assert (
        not hub_skill.versions.get(pk=result["version_id"])
        .files.filter(path="references/keep.md")
        .exists()
    )
    assert (
        hub_skill.drafts.count() == 1
    )  # A push never consumes an unrelated personal draft.
    assert hub_skill.labels.count() == 0


def test_patch_explicit_deletion_and_invalid_overlap(
    principal, hub_skill, publish_skill, make_skill_files
):
    publish_skill(
        hub_skill,
        files=make_skill_files(
            extra=[{"path": "references/delete.md", "content": "Old"}]
        ),
    )
    plan = preview_push(
        principal=principal,
        resource_id=hub_skill.pk,
        files=[],
        mode="patch",
        delete_paths=["references/delete.md"],
    )
    result = accept(principal, plan)
    assert hub_skill.versions.get(pk=result["version_id"]).file_count == 1
    for paths in [["missing.md"], ["SKILL.md"]]:
        with pytest.raises(SkillsHubError):
            preview_push(
                principal=principal,
                resource_id=hub_skill.pk,
                files=make_skill_files(),
                mode="patch",
                delete_paths=paths,
            )


def test_stale_plan_cannot_clobber_another_writer(
    principal, second_principal, hub_skill, publish_skill, make_skill_files
):
    publish_skill(hub_skill)
    first = preview_push(
        principal=principal,
        resource_id=hub_skill.pk,
        files=make_skill_files(body="Author one"),
        mode="snapshot",
    )
    second = preview_push(
        principal=second_principal,
        resource_id=hub_skill.pk,
        files=make_skill_files(body="Author two"),
        mode="snapshot",
    )
    result = accept(principal, first)
    with pytest.raises(SkillsHubError) as error:
        accept(second_principal, second)
    assert error.value.code == "stale_base"
    hub_skill.refresh_from_db()
    assert hub_skill.latest_version_number == 2
    assert (
        hub_skill.versions.get(pk=result["version_id"])
        .files.get(path="SKILL.md")
        .blob.content.endswith("Author one\n")
    )


def test_push_replay_is_original_receipt_after_head_and_expiry(
    principal, hub_skill, publish_skill, make_skill_files
):
    publish_skill(hub_skill)
    plan = preview_push(
        principal=principal,
        resource_id=hub_skill.pk,
        files=make_skill_files(body="Reviewed"),
        mode="snapshot",
    )
    key = uuid4()
    result = accept(principal, plan, idempotency_key=key)
    publish_skill(hub_skill, files=make_skill_files(body="Later head"))
    HubSkillIntakePlan.objects.filter(pk=plan.pk).update(
        payload=None, expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert accept(principal, plan, idempotency_key=key) == result
    assert hub_skill.versions.count() == 3
    with pytest.raises(SkillsHubError) as error:
        accept(principal, plan, idempotency_key=key, acknowledge_warnings=False)
    assert error.value.code == "idempotency_conflict"


def test_same_plan_different_key_cannot_allocate_twice(principal, make_skill_files):
    plan = preview_push(principal=principal, files=make_skill_files(), mode="snapshot")
    result = accept(principal, plan)
    assert accept(principal, plan) == result
    assert HubSkill._base_manager.get(pk=result["resource_id"]).versions.count() == 1


def test_explicit_mode_and_metadata_concurrency(
    principal, hub_skill, publish_skill, make_skill_files
):
    publish_skill(hub_skill)
    plan = preview_push(
        principal=principal,
        resource_id=hub_skill.pk,
        files=make_skill_files(),
        mode="snapshot",
        tags=["approved"],
    )
    with pytest.raises(SkillsHubError) as error:
        accept(principal, plan, mode="patch")
    assert error.value.code == "stale_base"
    hub_skill.tags = ["concurrent"]
    hub_skill.metadata_revision += 1
    hub_skill.save(update_fields=["tags", "metadata_revision"])
    with pytest.raises(SkillsHubError) as error:
        accept(principal, plan)
    assert error.value.code == "stale_base"


def test_new_push_cannot_hijack_existing_name(principal, hub_skill, make_skill_files):
    plan = preview_push(principal=principal, files=make_skill_files(), mode="snapshot")
    with pytest.raises(SkillsHubError) as error:
        accept(principal, plan)
    assert error.value.code == "name_taken"
    assert hub_skill.versions.count() == 0


def test_private_push_plan_denies_system_and_wrong_kind(
    principal, hub_skill, make_skill_files
):
    with pytest.raises(SkillsHubError) as error:
        preview_push(
            principal=replace(principal, kind="system_key", user_id=None),
            files=make_skill_files(),
            mode="snapshot",
        )
    assert error.value.code == "principal_not_supported"
    hub_skill.kind = "agent_context"
    hub_skill.save(update_fields=["kind"])
    with pytest.raises(SkillsHubError) as error:
        preview_push(
            principal=principal,
            resource_id=hub_skill.pk,
            files=make_skill_files(),
            mode="snapshot",
        )
    assert error.value.status == 404


def test_restore_always_new_exact_origin_and_operation_replay(
    principal, hub_skill, publish_skill, make_skill_files
):
    first = publish_skill(hub_skill).version
    publish_skill(hub_skill, files=make_skill_files(body="Second"))
    request = {
        "principal": principal,
        "resource_id": hub_skill.pk,
        "number": 1,
        "expected_head_version": 2,
        "expected_digest": first.digest,
        "note": "Restore first",
        "idempotency_key": uuid4(),
    }
    result = restore_skill_version(**request)
    version = hub_skill.versions.get(pk=result["version_id"])
    assert (
        version.digest == first.digest
        and version.parent_number == 2
        and version.origin_number == 1
    )
    assert restore_skill_version(**request) == result
    second_restore = restore_skill_version(
        **{**request, "expected_head_version": 3, "idempotency_key": uuid4()}
    )
    assert second_restore["version"] == 4 and second_restore["unchanged"] is False
    assert hub_skill.labels.count() == 0


def test_quarantine_blocks_restore_and_push_preview(
    principal, hub_skill, publish_skill, make_skill_files
):
    first = publish_skill(hub_skill).version
    HubSkillContextQuarantine.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        version=first,
        revision=1,
        active=True,
        reason="Synthetic quarantine",
        actor_id=principal.user_id,
    )
    with pytest.raises(SkillsHubError) as error:
        restore_skill_version(
            principal=principal,
            resource_id=hub_skill.pk,
            number=1,
            expected_head_version=1,
            expected_digest=first.digest,
            note="",
            idempotency_key=uuid4(),
        )
    assert error.value.code == "context_quarantined"
    with pytest.raises(SkillsHubError) as error:
        preview_push(
            principal=principal,
            resource_id=hub_skill.pk,
            files=make_skill_files(),
            mode="patch",
        )
    assert error.value.code == "context_quarantined"


@pytest.mark.django_db(transaction=True)
def test_concurrent_pushes_have_exactly_one_winner(
    principal, second_principal, hub_skill, publish_skill, make_skill_files
):
    publish_skill(hub_skill)
    plans = [
        preview_push(
            principal=actor,
            resource_id=hub_skill.pk,
            mode="snapshot",
            files=make_skill_files(body=f"Writer {index}"),
        )
        for index, actor in enumerate((principal, second_principal))
    ]

    def worker(pair):
        actor, plan = pair
        close_old_connections()
        try:
            return accept(actor, plan)["version"]
        except SkillsHubError as error:
            return error.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(worker, zip((principal, second_principal), plans, strict=False))
        )
    assert sorted(map(str, outcomes)) == ["2", "stale_base"]
    assert hub_skill.versions.count() == 2
