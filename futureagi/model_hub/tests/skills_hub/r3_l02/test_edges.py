from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.skills_hub_r3 import HubSkillIntakePlan, HubSkillMigrationReceipt
from model_hub.models.skills_hub_v12 import HubSkillContextQuarantine
from model_hub.services.skills_hub.drafts import discard_draft
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.r3.import_plans import (
    list_import_receipts,
    preview_import,
    preview_manual_reconciliation,
)
from model_hub.services.skills_hub.r3.migration_receipts import (
    export_migrated_version,
    read_import_comparison,
)
from model_hub.services.skills_hub.r3.push_plans import preview_push
from model_hub.services.skills_hub.v12.imports import preview_context_import
from model_hub.services.skills_hub.versions import publish_files

from .test_intake import accept, archive, bind_publish, source_plan, uploads
from .test_push import accept as accept_push_plan

pytestmark = pytest.mark.django_db


def test_context_preview_reuses_file_checks_without_a_fake_skill_root(principal):
    plan = preview_context_import(
        principal=principal,
        uploads=[("AGENTS.md", b"Use [policy](references/missing.md).")],
    )
    checks = plan.inventory["items"][0]["checks"]
    assert checks["summary"]["errors"] == 0
    assert checks["summary"]["warnings"] >= 1
    assert "link_broken" in {item["code"] for item in checks["items"]}
    assert "root_missing" not in {item["code"] for item in checks["items"]}
    assert checks["tokens"]["skill_md"] > 0
    assert checks["validation_scope"] == "authored_files"


def test_retained_payload_cannot_change_under_a_reviewed_fingerprint(
    principal, make_skill_files
):
    plan = preview_import(
        principal=principal, uploads=uploads(make_skill_files(body="Reviewed"))
    )
    payload = plan.payload
    assert payload is not None
    payload["items"][0]["files"][0]["content"] += "Altered after preview"
    HubSkillIntakePlan.objects.filter(pk=plan.pk).update(payload=payload)
    with pytest.raises(SkillsHubError) as error:
        accept(principal, plan)
    assert error.value.code == "candidate_changed"
    assert list_import_receipts(principal=principal, plan_id=plan.pk)["count"] == 0


def test_changed_export_retains_mapping_as_derivation(
    principal, make_skill_files, publish_skill, hub_actor
):
    initial, _ = source_plan(principal, make_skill_files())
    skill, draft, original = bind_publish(
        principal, accept(principal, initial), publish_skill
    )
    changed = publish_files(
        skill=skill,
        actor=hub_actor,
        files=make_skill_files(body="Changed in Hub"),
        note="",
        base_version=1,
        labels=[],
        acknowledge_warnings=True,
        source=draft.source,
    ).version
    exported = export_migrated_version(
        principal=principal, resource_id=skill.pk, number=changed.number
    )
    mapping = exported["mapping_receipts"][0]
    assert mapping["relation"] == "derived"
    assert mapping["source_digest"] == original.digest
    assert mapping["destination_digest"] == changed.digest
    assert mapping["destination_version_id"] == str(changed.pk)
    assert (
        HubSkillMigrationReceipt.objects.get(destination=skill).relation == "identical"
    )


def test_three_way_text_review_uses_frozen_head_not_later_release(
    principal, make_skill_files, publish_skill
):
    first, _ = source_plan(principal, make_skill_files(body="Original source"))
    skill, _, _ = bind_publish(principal, accept(principal, first), publish_skill)
    publish_skill(skill, files=make_skill_files(body="Hub reviewed head"))
    update, _ = source_plan(
        principal, make_skill_files(body="New source"), revision="2"
    )
    publish_skill(skill, files=make_skill_files(body="Unrelated later head"))
    comparison = read_import_comparison(
        principal=principal,
        plan_id=update.pk,
        item_key=update.inventory["items"][0]["key"],
        expected_fingerprint=update.fingerprint,
    )
    assert comparison["basis_payload_available"] is True
    assert comparison["expected_head_version"] == 2
    assert "Original source" in comparison["base_source"]["files"][0]["from_text"]
    assert "New source" in comparison["base_source"]["files"][0]["to_text"]
    assert "Hub reviewed head" in comparison["source_hub"]["files"][0]["to_text"]
    assert "Unrelated later head" not in str(comparison)


def test_unsupported_member_blocks_affected_package_not_other_roots(
    principal, make_skill_files
):
    raw = [(f"good/{path}", text) for path, text in uploads(make_skill_files("good"))]
    raw += [(f"bad/{path}", text) for path, text in uploads(make_skill_files("bad"))]
    raw.append(("bad/image.bin", b"\x00binary"))
    plan = preview_import(principal=principal, uploads=archive(raw))
    items = {item["name"]: item for item in plan.inventory["items"]}
    assert len(items) == 2
    assert items["bad"]["blockers"] and not items["good"]["blockers"]
    result = accept(principal, plan, item_key=items["good"]["key"])
    assert result["published"] is False
    with pytest.raises(SkillsHubError):
        accept(principal, plan, item_key=items["bad"]["key"])
    receipts = list_import_receipts(principal=principal, plan_id=plan.pk)
    assert (
        receipts["count"] == 1
        and receipts["results"][0]["item_key"] == items["good"]["key"]
    )


@pytest.mark.parametrize("context", [False, True])
def test_orphan_notice_is_visible_blocker_not_silent_omission(
    principal, make_skill_files, context
):
    if context:
        plan = preview_context_import(
            principal=principal,
            uploads=[("project/AGENTS.md", b"Root"), ("NOTICE", b"Retain me")],
        )
    else:
        plan = preview_import(
            principal=principal,
            uploads=[
                (f"project/{path}", raw) for path, raw in uploads(make_skill_files())
            ]
            + [("NOTICE", b"Retain me")],
        )
    assert any(
        row["reason"] == "unassigned_member" for row in plan.inventory["rejected"]
    )
    assert plan.inventory["items"][0]["blockers"]


def test_receipt_list_remains_private_after_payload_expiry(
    principal, second_principal, make_skill_files
):
    plan = preview_import(principal=principal, uploads=uploads(make_skill_files()))
    result = accept(principal, plan)
    HubSkillIntakePlan.objects.filter(pk=plan.pk).update(
        payload=None, expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert (
        list_import_receipts(principal=principal, plan_id=plan.pk)["results"][0][
            "result"
        ]
        == result
    )
    with pytest.raises(SkillsHubError) as error:
        list_import_receipts(principal=second_principal, plan_id=plan.pk)
    assert error.value.status == 404


def test_manual_reconciliation_has_reviewed_plan_and_derived_mapping(
    principal, make_skill_files, publish_skill, hub_actor
):
    initial, _ = source_plan(principal, make_skill_files())
    skill, _, _ = bind_publish(principal, accept(principal, initial), publish_skill)
    discard_draft(skill=skill, user=hub_actor.user)
    source_files = make_skill_files(body="Source two")
    update, _ = source_plan(principal, source_files, revision="2")
    manual_files = make_skill_files(
        body="Explicit manual reconciliation of source and Hub"
    )
    item = update.inventory["items"][0]
    manual = preview_manual_reconciliation(
        principal=principal,
        plan_id=update.pk,
        item_key=item["key"],
        expected_fingerprint=update.fingerprint,
        files=manual_files,
    )
    with pytest.raises(SkillsHubError):
        accept(principal, update, decision="manual")
    with pytest.raises(SkillsHubError):
        accept(principal, manual, decision="replace")
    result = accept(principal, manual, decision="manual", tags=[])
    assert result["decision"] == "manual"
    draft = skill.drafts.get(user_id=principal.user_id)
    assert draft.files == manual_files
    receipt = HubSkillMigrationReceipt.objects.get(source_revision="2")
    assert receipt.source_digest == manifest_digest(source_files)
    assert receipt.destination_digest == manifest_digest(manual_files)
    assert receipt.relation == "derived"


def test_quarantine_after_push_preview_blocks_new_publication(
    principal, hub_skill, publish_skill, make_skill_files
):
    version = publish_skill(hub_skill).version
    plan = preview_push(
        principal=principal,
        resource_id=hub_skill.pk,
        mode="patch",
        files=make_skill_files(body="Reviewed edit"),
    )
    HubSkillContextQuarantine.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        version=version,
        revision=1,
        active=True,
        reason="Synthetic quarantine",
        actor_id=principal.user_id,
    )
    with pytest.raises(SkillsHubError) as error:
        accept_push_plan(principal, plan)
    assert error.value.code == "context_quarantined"
    assert hub_skill.versions.count() == 1


def test_import_rejects_patch_mode(principal, make_skill_files):
    plan = preview_import(principal=principal, uploads=uploads(make_skill_files()))
    with pytest.raises(SkillsHubError) as error:
        accept(principal, plan, mode="patch", idempotency_key=uuid4())
    assert error.value.code == "import_invalid"
