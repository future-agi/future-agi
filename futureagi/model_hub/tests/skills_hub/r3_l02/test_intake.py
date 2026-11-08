import io
import zipfile
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill, HubSkillDraft
from model_hub.models.skills_hub_r3 import HubSkillIntakePlan, HubSkillMigrationReceipt
from model_hub.services.skills_hub.drafts import discard_draft
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest, parse_upload
from model_hub.services.skills_hub.r3.import_plans import (
    accept_import,
    get_import_plan,
    preview_import,
    purge_expired_intake,
)
from model_hub.services.skills_hub.r3.migration_receipts import (
    bind_migration_publication_locked,
    export_migrated_version,
    lookup_migration,
    preview_langfuse_export,
)
from model_hub.services.skills_hub.v12.imports import (
    accept_context_import,
    preview_context_import,
)

pytestmark = pytest.mark.django_db


def uploads(files):
    return [(file["path"], file["content"].encode("utf-8")) for file in files]


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zip_file:
        for path, raw in files:
            zip_file.writestr(path, raw)
    return [("library.zip", output.getvalue())]


def source_plan(principal, files, revision="1", project="project-one"):
    raw = uploads(files)
    parsed = parse_upload(raw)
    identity = {
        "origin": "langfuse.example.test",
        "project": project,
        "resource": "immutable-resource",
        "revision": revision,
        "digest": manifest_digest(files),
    }
    plan = preview_import(
        principal=principal,
        uploads=raw,
        source_identities={parsed["bundles"][0]["key"]: identity},
    )
    return plan, identity


def accept(principal, plan, **overrides):
    item = plan.inventory["items"][0]
    return accept_import(
        **{
            **{
                "principal": principal,
                "plan_id": plan.pk,
                "item_key": item["key"],
                "expected_fingerprint": plan.fingerprint,
                "expected_head_version": item["expected_head_version"],
                "expected_head_digest": item["expected_head_digest"],
                "rights_confirmed": True,
                "idempotency_key": uuid4(),
            },
            **overrides,
        }
    )


def bind_publish(principal, result, publish_skill):
    skill = HubSkill._base_manager.get(pk=result["resource_id"])
    draft = skill.drafts.get(user_id=principal.user_id)
    version = publish_skill(skill, files=draft.files).version
    with transaction.atomic():
        bind_migration_publication_locked(skill=skill, draft=draft, version=version)
    return skill, draft, version


def test_accepts_retained_bytes_no_labels_and_replays_after_expiry(
    principal, make_skill_files
):
    files = make_skill_files(body="Reviewed exact café\r\n")
    plan = preview_import(principal=principal, uploads=uploads(files))
    key = uuid4()
    result = accept(principal, plan, idempotency_key=key)
    skill = HubSkill._base_manager.get(pk=result["resource_id"])
    assert skill.latest_version_number == 0 and skill.labels.count() == 0
    assert skill.drafts.get(user_id=principal.user_id).files == files
    HubSkillIntakePlan.objects.filter(pk=plan.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert purge_expired_intake() == 1
    assert accept(principal, plan, idempotency_key=key) == result
    assert HubSkill._base_manager.filter(name="help-desk").count() == 1
    with pytest.raises(SkillsHubError) as error:
        get_import_plan(principal=principal, plan_id=plan.pk)
    assert error.value.code == "import_preview_expired"


def test_changed_upload_fingerprint_and_rights_refused(principal, make_skill_files):
    first = preview_import(
        principal=principal, uploads=uploads(make_skill_files(body="First"))
    )
    second = preview_import(
        principal=principal, uploads=uploads(make_skill_files(body="Second"))
    )
    with pytest.raises(SkillsHubError) as error:
        accept(principal, second, expected_fingerprint=first.fingerprint)
    assert error.value.code == "candidate_changed"
    with pytest.raises(SkillsHubError) as error:
        accept(principal, first, rights_confirmed=False)
    assert error.value.code == "license_review_required"
    assert HubSkill._base_manager.count() == 0


@pytest.mark.parametrize(
    "path,raw",
    [
        ("image.bin", b"\x00bad"),
        ("nested.zip", b"PK\x03\x04bad"),
        ("../escape", b"bad"),
        ("sub/SKILL.md", b"nested"),
    ],
)
def test_rejected_member_blocks_surviving_bundle(
    principal, make_skill_files, path, raw
):
    plan = preview_import(
        principal=principal,
        uploads=archive(uploads(make_skill_files()) + [(path, raw)]),
    )
    assert plan.inventory["rejected"]
    with pytest.raises(SkillsHubError) as error:
        accept(principal, plan)
    assert error.value.code == "import_invalid"
    assert HubSkill._base_manager.count() == 0


def test_private_owner_scope_and_wrong_kind_plans(
    principal, second_principal, make_skill_files
):
    plan = preview_import(principal=principal, uploads=uploads(make_skill_files()))
    with pytest.raises(SkillsHubError) as error:
        get_import_plan(principal=second_principal, plan_id=plan.pk)
    assert error.value.status == 404
    with pytest.raises(SkillsHubError) as error:
        preview_import(
            principal=replace(principal, kind="system_key", user_id=None),
            uploads=uploads(make_skill_files()),
        )
    assert error.value.code == "principal_not_supported"
    with pytest.raises(SkillsHubError) as error:
        accept_context_import(
            principal=principal,
            preview_id=plan.pk,
            root_key=plan.inventory["items"][0]["key"],
            expected_fingerprint=plan.fingerprint,
            name="context",
            description="Purpose",
            tags=[],
            rights_confirmed=True,
            idempotency_key=uuid4(),
        )
    assert error.value.status == 404


def test_name_is_not_migration_identity(principal, make_skill_files):
    first, _ = source_plan(principal, make_skill_files())
    accept(principal, first)
    other, _ = source_plan(principal, make_skill_files(), project="other-project")
    assert other.inventory["items"][0]["destination_id"] is None
    with pytest.raises(SkillsHubError) as error:
        accept(principal, other)
    assert error.value.code == "name_taken"


def test_second_import_three_columns_preserves_draft_then_stages(
    principal, make_skill_files, publish_skill, hub_actor
):
    first, identity = source_plan(principal, make_skill_files(body="Source one"))
    initial = accept(principal, first, tags=["retained"])
    skill, draft, version = bind_publish(principal, initial, publish_skill)
    publish_skill(skill, files=make_skill_files(body="Hub divergence"))
    second, _ = source_plan(
        principal, make_skill_files(body="Source two"), revision="2"
    )
    item = second.inventory["items"][0]
    assert item["destination_id"] == str(skill.pk)
    assert item["migration"]["hub_diverged"] is True
    columns = item["migration"]["files"][0]
    assert (
        len({columns["base_sha256"], columns["source_sha256"], columns["hub_sha256"]})
        == 3
    )
    with pytest.raises(SkillsHubError) as error:
        accept(principal, second)
    assert error.value.code == "draft_conflict"
    assert HubSkillMigrationReceipt.objects.count() == 1
    discard_draft(skill=skill, user=hub_actor.user)
    updated = accept(principal, second)
    new_draft = skill.drafts.get(user_id=principal.user_id)
    assert new_draft.revision > draft.revision
    assert "Source two" in new_draft.files[0]["content"]
    skill.refresh_from_db()
    assert skill.tags == ["retained"] and skill.latest_version_number == 2
    assert updated["published"] is False
    assert lookup_migration(
        principal=principal,
        **{f"source_{key}": value for key, value in identity.items()},
    )["version_id"] == str(version.pk)


def test_keep_source_update_does_not_replace_draft(
    principal, make_skill_files, publish_skill
):
    first, _ = source_plan(principal, make_skill_files())
    skill, draft, _ = bind_publish(principal, accept(principal, first), publish_skill)
    second, _ = source_plan(
        principal, make_skill_files(body="Source update"), revision="2"
    )
    result = accept(principal, second, decision="keep")
    draft.refresh_from_db()
    assert result["decision"] == "keep" and draft.revision == 1
    assert HubSkillMigrationReceipt.objects.count() == 1
    assert skill.drafts.count() == 1


def test_source_revision_cannot_change_bytes(principal, make_skill_files):
    plan, _ = source_plan(principal, make_skill_files())
    accept(principal, plan)
    with pytest.raises(SkillsHubError) as error:
        source_plan(principal, make_skill_files(body="Different bytes same revision"))
    assert error.value.code == "source_conflict"


def test_source_head_movement_rejects_acceptance(
    principal, make_skill_files, publish_skill, hub_actor
):
    plan, _ = source_plan(principal, make_skill_files())
    skill, _, _ = bind_publish(principal, accept(principal, plan), publish_skill)
    discard_draft(skill=skill, user=hub_actor.user)
    second, _ = source_plan(
        principal, make_skill_files(body="New source"), revision="2"
    )
    publish_skill(skill, files=make_skill_files(body="Concurrent release"))
    with pytest.raises(SkillsHubError) as error:
        accept(principal, second)
    assert error.value.code == "stale_base"
    assert skill.drafts.count() == 0


def test_export_round_trip_and_derived_receipt(
    principal, make_skill_files, publish_skill
):
    files = make_skill_files(
        body="Original café\r\n",
        extra=[{"path": "references/notice.txt", "content": "Synthetic NOTICE\r\n"}],
    )
    plan, identity = source_plan(principal, files)
    skill, draft, version = bind_publish(
        principal, accept(principal, plan), publish_skill
    )
    exported = export_migrated_version(
        principal=principal, resource_id=skill.pk, number=version.number
    )
    with zipfile.ZipFile(io.BytesIO(exported["archive"])) as archive_file:
        for file in files:
            assert archive_file.read(f"{skill.name}/{file['path']}") == file[
                "content"
            ].encode("utf-8")
    assert exported["mapping_receipts"][0]["source_revision"] == "1"
    assert exported["exit"]["credentials_revoked"] is False
    changed = publish_skill(skill, files=make_skill_files(body="Hub derived")).version
    with transaction.atomic():
        bind_migration_publication_locked(skill=skill, draft=draft, version=changed)
    mapped = lookup_migration(
        principal=principal,
        **{f"source_{key}": value for key, value in identity.items()},
    )
    assert (
        mapped["version_id"] == str(version.pk) and mapped["digest"] != changed.digest
    )


def test_langfuse_exchange_15_packages_and_no_source_cutover(
    principal, make_skill_files
):
    skills = []
    for index in range(15):
        files = make_skill_files(name=f"policy-{index}")
        skills.append(
            {
                "resource": f"source-{index}",
                "revision": "1",
                "digest": manifest_digest(files),
                "files": files,
            }
        )
    plan = preview_langfuse_export(
        principal=principal,
        export={
            "format": "langfuse-skills-v1",
            "origin": "langfuse.example.test",
            "project": "source-project",
            "skills": skills,
        },
    )
    assert len(plan.inventory["items"]) == 15
    for item in plan.inventory["items"]:
        accept(principal, plan, item_key=item["key"])
    assert HubSkill._base_manager.count() == 15
    assert HubSkillMigrationReceipt.objects.count() == 15
    assert (
        sum(skill.latest_version_number for skill in HubSkill._base_manager.all()) == 0
    )


@pytest.mark.parametrize(
    "path,code",
    [
        ("nested/AGENTS.md", "unsupported_instruction_scope"),
        ("skills/leaf/SKILL.md", "embedded_skill_unregistered"),
        (".claude/settings.json", "destination_forbidden"),
        ("CLAUDE.md", "unsupported_instruction_scope"),
    ],
)
def test_context_inventory_rejects_embedded_and_control_members(principal, path, code):
    plan = preview_context_import(
        principal=principal,
        uploads=[("AGENTS.md", b"Standing policy"), (path, b"unmanaged")],
    )
    item = plan.inventory["items"][0]
    assert any(blocker["reason"] == code for blocker in item["blockers"])
    with pytest.raises(SkillsHubError):
        accept_context_import(
            principal=principal,
            preview_id=plan.pk,
            root_key=item["key"],
            expected_fingerprint=plan.fingerprint,
            name="context",
            description="Purpose",
            tags=[],
            rights_confirmed=True,
            idempotency_key=uuid4(),
        )
    assert HubSkill._base_manager.count() == 0


def test_context_root_preserves_bytes_and_monotonic_replay(principal):
    raw = [
        ("project/AGENTS.md", "# Standing café\r\n".encode()),
        ("project/references/policy.md", b"Reference"),
    ]
    plan = preview_context_import(principal=principal, uploads=raw)
    item = plan.inventory["items"][0]
    request = {
        "principal": principal,
        "preview_id": plan.pk,
        "root_key": item["key"],
        "expected_fingerprint": plan.fingerprint,
        "name": "context",
        "description": "Purpose",
        "tags": ["pilot"],
        "rights_confirmed": True,
        "idempotency_key": uuid4(),
    }
    result = accept_context_import(**request)
    assert accept_context_import(**request) == result
    skill = HubSkill._base_manager.get(pk=result["resource_id"])
    draft = HubSkillDraft.objects.get(skill=skill)
    assert skill.kind == "agent_context" and skill.latest_version_number == 0
    assert draft.files[0]["content"].encode() == raw[0][1]
    assert draft.context_spec == {"schema": 1, "description": "Purpose", "links": []}
    assert draft.resolved_lock == {"schema": 1, "links": []}


def test_context_descriptor_counts_against_200_file_closure(principal):
    raw = [("AGENTS.md", b"Root")] + [
        (f"references/{index}.txt", b"text") for index in range(199)
    ]
    plan = preview_context_import(principal=principal, uploads=raw)
    assert {
        "path": ".futureagi/context.json",
        "reason": "context_closure_too_large",
    } in plan.inventory["items"][0]["blockers"]
