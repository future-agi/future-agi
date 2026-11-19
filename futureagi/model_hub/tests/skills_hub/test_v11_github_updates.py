"""Private continuation, update CAS, durable completion and source-adoption tests."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill, HubSkillDraft
from model_hub.models.skills_hub_v11 import (
    HubSkillGitHubPreview,
    HubSkillGitHubSource,
    HubSkillJob,
)
from model_hub.services.skills_hub import compare as compare_service
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.v11 import github
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.tests.skills_hub.test_v11_github import SHA, SKILL, archive
from model_hub.tests.skills_hub.test_v11_github import offline as offline

pytestmark = pytest.mark.django_db


@pytest.fixture
def principal(organization, workspace, user, settings):
    settings.SKILLS_GITHUB_ENABLED = True
    return SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )


@pytest.fixture
def dispatch(monkeypatch):
    from model_hub.services.skills_hub.v11 import jobs

    mock = Mock()
    monkeypatch.setattr(jobs, "dispatch_job", mock)
    return mock


def make_preview(
    principal, *, skill=None, outcome="ready", operation="import", bundle=None, **values
):
    job = HubSkillJob.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        owner_user_id=principal.user_id,
        principal_kind=principal.kind,
        actor_api_key_id=principal.api_key_id,
        kind="github",
        skill=skill,
        idempotency_key=uuid4(),
        request_digest="synthetic",
        workflow_id="skills-job-" + str(uuid4()),
        status="completed",
        deadline_at=timezone.now() + timedelta(seconds=60),
        reservation_released_at=timezone.now(),
    )
    return HubSkillGitHubPreview.objects.create(
        job=job,
        operation=operation,
        repo="example/synthetic",
        repository_id=10,
        path="",
        ref="refs/heads/main",
        ref_kind="branch",
        commit_sha=SHA,
        expires_at=timezone.now() + timedelta(minutes=15),
        outcome=outcome,
        bundle=bundle,
        license={
            "status": "identified",
            "files": [],
            "declared": "MIT",
            "requires_admin_review": False,
        },
        **values,
    )


def test_continuation_inherits_sha_and_tracked_ref_without_reresolution(
    principal, dispatch, monkeypatch
):
    parent = make_preview(principal, outcome="needs_path", roots=["one", "two"])
    key = uuid4()
    request = {"parent_preview_id": str(parent.pk), "path": "two"}
    child = github.create_github_preview(
        principal=principal, idempotency_key=key, request=request
    )
    assert (
        child.commit_sha,
        child.ref,
        child.ref_kind,
        child.repository_id,
        child.parent_preview_id,
    ) == (SHA, "refs/heads/main", "branch", 10, parent.pk)
    monkeypatch.setattr(
        github.github_public,
        "repository_metadata",
        Mock(
            side_effect=AssertionError("No mutable metadata resolution on continuation")
        ),
    )
    monkeypatch.setattr(
        github.github_public,
        "resolve_sha",
        Mock(side_effect=AssertionError("Do not re-resolve branch")),
    )
    download = Mock(
        return_value=archive([("one/SKILL.md", SKILL), ("two/SKILL.md", SKILL)])
    )
    monkeypatch.setattr(github.github_public, "download_archive", download)
    result = github.execute_github_preview(scope=principal.scope, preview_id=child.pk)
    assert result["status"] == "completed"
    child.refresh_from_db()
    assert child.path == "two" and child.outcome == "ready"
    assert download.call_args.kwargs["sha"] == SHA
    # Idempotency is checked before the expired parent, and never starts new work.
    parent.expires_at = timezone.now() - timedelta(seconds=1)
    parent.save(update_fields=["expires_at"])
    assert (
        github.create_github_preview(
            principal=principal, idempotency_key=key, request=request
        ).pk
        == child.pk
    )
    assert (
        github.execute_github_preview(scope=principal.scope, preview_id=child.pk)[
            "status"
        ]
        == "completed"
    )
    assert download.call_count == 1


@pytest.mark.parametrize(
    "change,code",
    [
        ({"path": "undiscovered"}, "github_source_invalid"),
        ({"url": "https://github.com/evil/repo"}, "github_source_invalid"),
    ],
)
def test_continuation_cannot_change_identity_or_select_undiscovered_root(
    principal, dispatch, change, code
):
    parent = make_preview(principal, outcome="needs_path", roots=["one", "two"])
    with pytest.raises(SkillsHubError) as exc:
        github.create_github_preview(
            principal=principal,
            idempotency_key=uuid4(),
            request={"parent_preview_id": str(parent.pk), "path": "one", **change},
        )
    assert exc.value.code == code
    assert HubSkillJob.objects.count() == 1


def test_foreign_owner_and_scope_previews_are_not_found(
    principal, dispatch, hub_other_user, hub_other_workspace
):
    parent = make_preview(principal, outcome="needs_path", roots=["one", "two"])
    foreign = replace(principal, user_id=hub_other_user.pk)
    # Direct owner filter is independently verified without granting mutation authority.
    with pytest.raises(SkillsHubError) as exc:
        github._owned(foreign, parent.pk)
    assert exc.value.code == "resource_not_found"
    wrong_scope = replace(
        principal,
        scope=SkillScope(principal.scope.organization_id, hub_other_workspace.pk),
    )
    with pytest.raises(SkillsHubError) as exc:
        github._owned(wrong_scope, parent.pk)
    assert exc.value.code == "resource_not_found"


def test_expired_continuation_and_changed_idempotency_request(principal, dispatch):
    parent = make_preview(principal, outcome="needs_path", roots=["one", "two"])
    key = uuid4()
    github.create_github_preview(
        principal=principal,
        idempotency_key=key,
        request={"parent_preview_id": str(parent.pk), "path": "one"},
    )
    with pytest.raises(SkillsHubError) as exc:
        github.create_github_preview(
            principal=principal,
            idempotency_key=key,
            request={"parent_preview_id": str(parent.pk), "path": "two"},
        )
    assert exc.value.code == "idempotency_conflict"
    # Release the child capacity so this assertion reaches parent expiry validation.
    HubSkillJob.objects.exclude(pk=parent.pk).update(
        reservation_released_at=timezone.now()
    )
    parent.expires_at = timezone.now() - timedelta(seconds=1)
    parent.save()
    with pytest.raises(SkillsHubError) as exc:
        github.create_github_preview(
            principal=principal,
            idempotency_key=uuid4(),
            request={"parent_preview_id": str(parent.pk), "path": "two"},
        )
    assert exc.value.code == "github_preview_expired"


def test_disabled_rollout_blocks_new_jobs_but_keeps_idempotency_receipts(
    principal, dispatch, settings
):
    key = uuid4()
    request = {"url": "https://github.com/example/synthetic", "path": None, "ref": None}
    original = github.create_github_preview(
        principal=principal, idempotency_key=key, request=request
    )
    settings.SKILLS_GITHUB_ENABLED = False
    assert (
        github.create_github_preview(
            principal=principal, idempotency_key=key, request=request
        ).pk
        == original.pk
    )
    with pytest.raises(SkillsHubError) as exc:
        github.create_github_preview(
            principal=principal, idempotency_key=uuid4(), request=request
        )
    assert exc.value.code == "skills_unavailable"
    assert HubSkillJob.objects.count() == 1


def test_system_key_cannot_inherit_private_owner(principal):
    system = replace(principal, kind="system_key")
    with pytest.raises(SkillsHubError) as exc:
        github.stage_github_preview(principal=system, preview_id=uuid4(), request={})
    assert exc.value.code == "principal_not_supported"


def adopted_source(principal, skill, version, **values):
    return HubSkillGitHubSource.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        skill=skill,
        repo="example/synthetic",
        repository_id=10,
        path="",
        ref="refs/heads/main",
        ref_kind=values.pop("ref_kind", "branch"),
        imported_sha=SHA,
        upstream_digest=version.digest,
        associated_version=version,
        **values,
    )


def test_pinned_update_uses_no_transport_and_updates_checked_revision(
    principal, dispatch, hub_skill, publish_skill, monkeypatch
):
    version = publish_skill(hub_skill).version
    source = adopted_source(principal, hub_skill, version, ref_kind="commit")
    preview = github.create_github_preview(
        principal=principal,
        idempotency_key=uuid4(),
        request={"expected_source_revision": source.revision},
        skill_id=hub_skill.pk,
    )
    monkeypatch.setattr(
        github.github_public,
        "repository_metadata",
        Mock(side_effect=AssertionError("Pinned has no network")),
    )
    assert (
        github.execute_github_preview(scope=principal.scope, preview_id=preview.pk)[
            "status"
        ]
        == "completed"
    )
    source.refresh_from_db()
    preview.refresh_from_db()
    assert (
        preview.outcome == "pinned"
        and source.revision == 2
        and preview.source_revision == 2
    )
    assert source.imported_sha == SHA and source.checked_sha == SHA
    assert hub_skill.versions.count() == 1 and hub_skill.labels.count() == 0


def test_source_revision_race_never_overwrites_tracking(
    principal, dispatch, hub_skill, publish_skill
):
    version = publish_skill(hub_skill).version
    source = adopted_source(principal, hub_skill, version)
    preview = github.create_github_preview(
        principal=principal,
        idempotency_key=uuid4(),
        request={"expected_source_revision": 1},
        skill_id=hub_skill.pk,
    )
    source.revision = 2
    source.save()
    result = github.execute_github_preview(scope=principal.scope, preview_id=preview.pk)
    assert result["status"] == "failed"
    preview.job.refresh_from_db()
    source.refresh_from_db()
    assert preview.job.failure_code == "source_conflict"
    assert source.revision == 2 and source.checked_at is None


def test_unchanged_sha_does_not_download_or_publish(
    principal, dispatch, hub_skill, publish_skill, monkeypatch
):
    version = publish_skill(hub_skill).version
    adopted_source(principal, hub_skill, version)
    preview = github.create_github_preview(
        principal=principal,
        idempotency_key=uuid4(),
        request={"expected_source_revision": 1},
        skill_id=hub_skill.pk,
    )
    monkeypatch.setattr(
        github.github_public,
        "repository_metadata",
        lambda **kwargs: {
            "repo": "example/synthetic",
            "repository_id": 10,
            "default_branch": "main",
        },
    )
    monkeypatch.setattr(
        github.github_public, "resolve_sha", lambda **kwargs: {"sha": SHA}
    )
    monkeypatch.setattr(
        github.github_public,
        "download_archive",
        Mock(side_effect=AssertionError("Unchanged SHA needs no archive")),
    )
    github.execute_github_preview(scope=principal.scope, preview_id=preview.pk)
    preview.refresh_from_db()
    version.refresh_from_db()
    assert preview.outcome == "unchanged_commit" and preview.source_revision == 2
    assert version.source == {"type": "api"}
    assert hub_skill.versions.count() == 1


def test_new_sha_same_files_is_not_a_publication(
    principal, dispatch, hub_skill, publish_skill, monkeypatch
):
    files = [{"path": "SKILL.md", "content": SKILL}]
    version = publish_skill(hub_skill, files=files).version
    source = adopted_source(principal, hub_skill, version)
    preview = github.create_github_preview(
        principal=principal,
        idempotency_key=uuid4(),
        request={"expected_source_revision": 1},
        skill_id=hub_skill.pk,
    )
    monkeypatch.setattr(
        github.github_public,
        "repository_metadata",
        lambda **kwargs: {
            "repo": "example/synthetic",
            "repository_id": 10,
            "default_branch": "main",
        },
    )
    monkeypatch.setattr(
        github.github_public, "resolve_sha", lambda **kwargs: {"sha": "b" * 40}
    )
    monkeypatch.setattr(
        github.github_public,
        "download_archive",
        lambda **kwargs: archive([("SKILL.md", SKILL)]),
    )
    # Integration owns extraction of this v1 pure comparison seam.
    comparison = Mock(
        return_value={
            "from": None,
            "to": None,
            "files": [],
            "summary": {"added": 0, "removed": 0, "modified": 0, "unchanged": 1},
        }
    )
    monkeypatch.setattr(compare_service, "compare_file_sets", comparison, raising=False)
    github.execute_github_preview(scope=principal.scope, preview_id=preview.pk)
    preview.refresh_from_db()
    source.refresh_from_db()
    version.refresh_from_db()
    assert preview.outcome == "unchanged_content"
    assert source.imported_sha == SHA and source.checked_sha == "b" * 40
    assert source.upstream_digest == version.digest
    assert version.source == {"type": "api"} and hub_skill.versions.count() == 1
    assert comparison.call_args.args == (files, files)


def test_stage_existing_draft_rejected_without_overwriting(
    principal, hub_skill, publish_skill, make_skill_files
):
    version = publish_skill(hub_skill).version
    source = adopted_source(principal, hub_skill, version)
    files = make_skill_files()
    preview = make_preview(
        principal,
        skill=hub_skill,
        operation="update",
        outcome="changed",
        source_revision=source.revision,
        expected_latest_version=version.number,
        expected_latest_digest=version.digest,
        bundle={
            "name": hub_skill.name,
            "files": files,
            "digest": manifest_digest(files),
        },
    )
    original = HubSkillDraft.objects.get(skill=hub_skill, user_id=principal.user_id)
    with pytest.raises(SkillsHubError) as exc:
        github.stage_github_preview(
            principal=principal,
            preview_id=preview.pk,
            request={
                "revision": 0,
                "expected_source_revision": 1,
                "expected_latest_version": version.number,
                "expected_latest_digest": version.digest,
                "accept_replacement": True,
                "rights": {
                    "confirmed": True,
                    "review_note": "Reviewed synthetic license",
                },
            },
        )
    assert exc.value.code == "draft_conflict"
    original.refresh_from_db()
    assert original.revision == 1


def test_receipt_replay_after_expiry_does_not_save_again(principal, hub_skill):
    preview = make_preview(
        principal, consumed_skill=hub_skill, consumed_draft_revision=4
    )
    preview.expires_at = timezone.now() - timedelta(days=1)
    preview.save()
    result = github.stage_github_preview(
        principal=principal, preview_id=preview.pk, request={}
    )
    assert result["replayed"] and result["consumed"] == {
        "skill_id": str(hub_skill.pk),
        "draft_revision": 4,
    }
    assert (
        HubSkillDraft.objects.get(skill=hub_skill, user_id=principal.user_id).revision
        == 1
    )


def test_source_adoption_requires_publication_transaction_and_verified_receipt(
    principal, hub_skill, publish_skill
):
    version = publish_skill(hub_skill).version
    draft = HubSkillDraft.objects.get(skill=hub_skill, user_id=principal.user_id)
    draft.source = {"provider": "github", "preview_id": str(uuid4()), "commit_sha": SHA}
    with transaction.atomic(), pytest.raises(SkillsHubError) as exc:
        github.adopt_draft_source_locked(skill=hub_skill, draft=draft, version=version)
    assert exc.value.code == "source_conflict"
    assert not HubSkillGitHubSource.objects.filter(skill=hub_skill).exists()


def test_adoption_can_bind_an_existing_unchanged_version_without_mutating_it(
    principal, hub_skill, publish_skill
):
    version = publish_skill(hub_skill).version
    draft = HubSkillDraft.objects.get(skill=hub_skill, user_id=principal.user_id)
    preview = make_preview(
        principal,
        consumed_skill=hub_skill,
        consumed_draft_revision=draft.revision,
        bundle={"digest": version.digest},
    )
    draft.source = github._provenance(preview)
    with transaction.atomic():
        github.adopt_draft_source_locked(skill=hub_skill, draft=draft, version=version)
        github.adopt_draft_source_locked(skill=hub_skill, draft=draft, version=version)
    source = HubSkillGitHubSource.objects.get(skill=hub_skill)
    assert source.associated_version_id == version.pk
    assert source.imported_sha == SHA and source.revision == 1
    assert source.organization_id == principal.scope.organization_id
    assert source.workspace_id == principal.scope.workspace_id
    version.refresh_from_db()
    assert version.source == {"type": "api"} and hub_skill.versions.count() == 1


def test_native_start_claim_still_executes_exactly_once(
    principal, dispatch, monkeypatch
):
    from model_hub.services.skills_hub.v11.jobs import start_job

    parent = make_preview(principal, outcome="needs_path", roots=["one", "two"])
    preview = github.create_github_preview(
        principal=principal,
        idempotency_key=uuid4(),
        request={"parent_preview_id": str(parent.pk), "path": "one"},
    )
    start_job(scope=principal.scope, job_id=preview.pk)
    download = Mock(return_value=archive([("one/SKILL.md", SKILL)]))
    monkeypatch.setattr(github.github_public, "download_archive", download)
    assert (
        github.execute_github_preview(scope=principal.scope, preview_id=preview.pk)[
            "status"
        ]
        == "completed"
    )
    assert (
        github.execute_github_preview(scope=principal.scope, preview_id=preview.pk)[
            "status"
        ]
        == "completed"
    )
    assert download.call_count == 1


def test_staging_source_migration_is_atomic_prerequisite(principal, make_skill_files):
    files = make_skill_files("new-source")
    preview = make_preview(
        principal,
        bundle={"name": "new-source", "files": files, "digest": manifest_digest(files)},
    )
    request = {"rights": {"confirmed": True, "review_note": "Synthetic fixture rights"}}
    if not any(field.name == "source" for field in HubSkillDraft._meta.fields):
        with pytest.raises(SkillsHubError) as exc:
            github.stage_github_preview(
                principal=principal, preview_id=preview.pk, request=request
            )
        assert exc.value.code == "skills_unavailable"
        assert not HubSkill.no_workspace_objects.filter(name="new-source").exists()
        preview.refresh_from_db()
        assert preview.consumed_skill_id is None
    else:
        result = github.stage_github_preview(
            principal=principal, preview_id=preview.pk, request=request
        )
        assert result["published"] is False
        assert result["draft"].source["commit_sha"] == SHA
        assert (
            result["skill"].versions.count() == 0
            and result["skill"].labels.count() == 0
        )
        again = github.stage_github_preview(
            principal=principal, preview_id=preview.pk, request=request
        )
        assert (
            again["replayed"]
            and again["consumed"]["draft_revision"] == result["draft"].revision
        )


def test_missing_tracked_root_does_not_adopt_nested_same_name(
    principal, dispatch, hub_skill, publish_skill, monkeypatch
):
    version = publish_skill(hub_skill).version
    source = adopted_source(principal, hub_skill, version)
    preview = github.create_github_preview(
        principal=principal,
        idempotency_key=uuid4(),
        request={"expected_source_revision": 1},
        skill_id=hub_skill.pk,
    )
    monkeypatch.setattr(
        github.github_public,
        "repository_metadata",
        lambda **kwargs: {
            "repo": "example/synthetic",
            "repository_id": 10,
            "default_branch": "main",
        },
    )
    monkeypatch.setattr(
        github.github_public, "resolve_sha", lambda **kwargs: {"sha": "b" * 40}
    )
    monkeypatch.setattr(
        github.github_public,
        "download_archive",
        lambda **kwargs: archive(
            [
                ("nested/SKILL.md", SKILL),
            ]
        ),
    )
    github.execute_github_preview(scope=principal.scope, preview_id=preview.pk)
    preview.refresh_from_db()
    source.refresh_from_db()
    assert preview.outcome == "source_missing" and preview.bundle is None
    assert preview.path == source.path == ""
    assert source.imported_sha == SHA and source.checked_sha == "b" * 40


def test_cancellation_during_download_discards_payload(
    principal, dispatch, monkeypatch
):
    parent = make_preview(principal, outcome="needs_path", roots=["one", "two"])
    preview = github.create_github_preview(
        principal=principal,
        idempotency_key=uuid4(),
        request={"parent_preview_id": str(parent.pk), "path": "one"},
    )

    def download(**kwargs):
        HubSkillJob.objects.filter(pk=preview.pk).update(
            status="cancelling", cancel_requested_at=timezone.now()
        )
        return archive([("one/SKILL.md", SKILL)])

    monkeypatch.setattr(github.github_public, "download_archive", download)
    assert (
        github.execute_github_preview(scope=principal.scope, preview_id=preview.pk)[
            "status"
        ]
        == "cancelled"
    )
    preview.refresh_from_db()
    assert preview.bundle is None


def test_staged_source_publish_edit_restore_and_unchanged_adoption(
    principal, make_skill_files
):
    from accounts.models.user import User
    from model_hub.services.skills_hub.drafts import publish_draft, save_draft
    from model_hub.services.skills_hub.types import Actor
    from model_hub.services.skills_hub.versions import restore_version

    user = User.objects.get(pk=principal.user_id)
    files = make_skill_files("source-lifecycle")
    preview = make_preview(
        principal,
        bundle={
            "name": "source-lifecycle",
            "files": files,
            "digest": manifest_digest(files),
        },
    )
    staged = github.stage_github_preview(
        principal=principal,
        preview_id=preview.pk,
        request={
            "rights": {"confirmed": True, "review_note": "Synthetic fixture rights"}
        },
    )
    skill, draft = staged["skill"], staged["draft"]
    assert not HubSkillGitHubSource.objects.filter(skill=skill).exists()
    first = publish_draft(
        skill=skill,
        user=user,
        revision=draft.revision,
        labels=[],
        acknowledge_warnings=True,
    )
    first_source = dict(first.version.source)
    assert (
        first_source["provider"] == "github"
        and first_source["local_modifications"] is False
    )
    source = HubSkillGitHubSource.objects.get(skill=skill)
    assert source.imported_sha == SHA and source.associated_version == first.version
    changed = make_skill_files(
        "source-lifecycle", body="An explicitly reviewed local change."
    )
    draft = save_draft(
        skill=skill, user=user, files=changed, note="", base_version=1, revision=0
    )
    assert draft.source == first_source
    second = publish_draft(
        skill=skill,
        user=user,
        revision=draft.revision,
        labels=[],
        acknowledge_warnings=True,
    )
    assert second.version.source["local_modifications"] is True
    assert second.version.source["commit_sha"] == SHA
    restored = restore_version(
        skill=skill, actor=Actor(user=user, api_key=None), number=1
    )
    assert restored.version.source["operation"] == "restore"
    assert restored.version.source["local_modifications"] is False
    first.version.refresh_from_db()
    assert first.version.source == first_source
    source.refresh_from_db()
    assert source.associated_version_id == first.version.pk

    # A reviewed newer upstream commit with identical files advances the mutable
    # binding, not an immutable version or its recorded original provenance.
    update = make_preview(
        principal,
        skill=skill,
        operation="update",
        outcome="unchanged_content",
        source_revision=source.revision,
        expected_latest_version=restored.version.number,
        expected_latest_digest=restored.version.digest,
        bundle={"name": skill.name, "files": files, "digest": manifest_digest(files)},
    )
    update.commit_sha = "b" * 40
    update.save(update_fields=["commit_sha"])
    staged = github.stage_github_preview(
        principal=principal,
        preview_id=update.pk,
        request={
            "revision": 0,
            "expected_source_revision": source.revision,
            "expected_latest_version": restored.version.number,
            "expected_latest_digest": restored.version.digest,
            "accept_replacement": False,
            "rights": {"confirmed": True, "review_note": "Reviewed newer commit"},
        },
    )
    preserved_source = dict(restored.version.source)
    repeated = publish_draft(
        skill=skill,
        user=user,
        revision=staged["draft"].revision,
        labels=[],
        acknowledge_warnings=True,
    )
    assert repeated.unchanged and repeated.version.pk == restored.version.pk
    restored.version.refresh_from_db()
    source.refresh_from_db()
    assert restored.version.source == preserved_source
    assert source.imported_sha == "b" * 40
    assert source.associated_version_id == restored.version.pk
    assert skill.versions.count() == 3
    assert not skill.drafts.filter(user=user).exists()


def test_github_staging_and_publication_preserve_original_metadata(
    principal, hub_actor, user
):
    from model_hub.services.skills_hub import drafts
    from model_hub.services.skills_hub.v11.repository_zip import parse_repository_zip

    name = "sk-" + "a" * 24
    description = "Use when reviewing the synthetic token sk-" + "b" * 24
    content = f"---\nname: {name}\ndescription: {description}\n---\nFollow the supplied policy.\n"
    parsed = parse_repository_zip(archive([("SKILL.md", content)]), path="")
    assert parsed["bundle"]["name"] == name
    assert parsed["bundle"]["checks"]["frontmatter"]["name"] != name
    preview = make_preview(principal, bundle=parsed["bundle"])
    staged = github.stage_github_preview(
        principal=principal,
        preview_id=preview.pk,
        request={"rights": {"confirmed": True, "review_note": "Synthetic fixture"}},
    )
    skill, draft = staged["skill"], staged["draft"]
    assert skill.name == name and skill.description == description
    result = drafts.publish_draft(
        skill=skill,
        user=user,
        actor=hub_actor,
        revision=draft.revision,
        labels=[],
        acknowledge_warnings=True,
    )
    assert result.version.frontmatter["name"] == name
    assert result.version.frontmatter["description"] == description
    assert result.version.source["provider"] == "github"
    assert not skill.drafts.exists()
    assert HubSkillDraft.all_objects.get(pk=draft.pk).source == {}
