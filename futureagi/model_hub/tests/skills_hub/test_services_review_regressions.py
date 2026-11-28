"""Publication safety regressions from the independent v1 review."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock, get_ident
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection
from django.db.models import QuerySet
from django.test.utils import CaptureQueriesContext

from model_hub.models.skills_hub import HubSkillBlob, HubSkillDraft
from model_hub.selectors.skills_hub import list_skills, workspace_stats
from model_hub.serializers.skills_hub import SkillDetail, SkillSummary
from model_hub.services.skills_hub import drafts
from model_hub.services.skills_hub.checks import parse_frontmatter
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256, parse_upload
from model_hub.services.skills_hub.v11.principals import principal_from_request
from model_hub.services.skills_hub.versions import (
    create_skill,
    publish_by_name,
    publish_files,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.mark.parametrize("ending", ["discard", "publish", "unchanged_publish"])
@pytest.mark.parametrize("stale_action", ["save", "publish"])
def test_draft_revision_never_repeats_after_recreation(
    hub_skill, user, publish_skill, make_skill_files, ending, stale_action
):
    original = drafts.get_draft(skill=hub_skill, user=user)
    if ending == "discard":
        drafts.discard_draft(skill=hub_skill, user=user)
    else:
        if ending == "unchanged_publish":
            publish_skill(hub_skill, files=original.files)
        result = drafts.publish_draft(
            skill=hub_skill,
            user=user,
            revision=original.revision,
            labels=[],
            acknowledge_warnings=True,
            force=True,
        )
        assert result.unchanged is (ending == "unchanged_publish")
    hub_skill.refresh_from_db()
    latest = hub_skill.latest_version_number or None
    saved = drafts.save_draft(
        skill=hub_skill,
        user=user,
        revision=0,
        base_version=latest,
        files=make_skill_files(body="New work from the other tab"),
        note="Keep this",
    )
    with pytest.raises(SkillsHubError) as error:
        if stale_action == "save":
            drafts.save_draft(
                skill=hub_skill,
                user=user,
                revision=original.revision,
                base_version=latest,
                files=original.files,
                note="Stale overwrite",
            )
        else:
            drafts.publish_draft(
                skill=hub_skill,
                user=user,
                revision=original.revision,
                labels=["production"],
                acknowledge_warnings=True,
                force=True,
            )
    assert (error.value.code, error.value.status) == ("draft_conflict", 409)
    assert error.value.detail["revision"] == saved.revision
    assert saved.revision == original.revision + 2
    current = drafts.get_draft(skill=hub_skill, user=user)
    assert (current.files, current.note, current.revision) == (
        saved.files,
        saved.note,
        saved.revision,
    )
    assert hub_skill.versions.count() == (latest or 0)
    assert not hub_skill.labels.exists() and not hub_skill.label_events.exists()


def test_tombstones_hide_content_from_reads_and_stats(
    hub_skill, user, organization, workspace
):
    original = drafts.get_draft(skill=hub_skill, user=user)
    drafts.discard_draft(skill=hub_skill, user=user)
    drafts.discard_draft(skill=hub_skill, user=user)
    tombstone = HubSkillDraft.all_objects.get(skill=hub_skill, user=user)
    assert tombstone.pk == original.pk and tombstone.revision == original.revision + 1
    assert tombstone.discarded_at is not None
    assert (tombstone.files, tombstone.note, tombstone.base_version) == ([], "", None)
    with pytest.raises(SkillsHubError) as error:
        drafts.get_draft(skill=hub_skill, user=user)
    assert (error.value.code, error.value.status) == ("draft_not_found", 404)
    context = {
        "request": SimpleNamespace(
            user=user, organization=organization, workspace=workspace
        ),
        "fetch_counts": {},
        "latest_versions": {},
    }
    detail = SkillDetail(hub_skill, context=context).data
    assert detail["drafts"] == [] and detail["has_draft"] is False
    assert workspace_stats(organization, workspace, user)["my_drafts"] == 0
    for revision in (original.revision, tombstone.revision):
        with pytest.raises(SkillsHubError) as error:
            drafts.save_draft(
                skill=hub_skill,
                user=user,
                revision=revision,
                files=[],
                note="",
                base_version=None,
            )
        assert error.value.code == "draft_conflict"
        assert error.value.detail["revision"] == 0
        with pytest.raises(SkillsHubError) as error:
            drafts.publish_draft(
                skill=hub_skill,
                user=user,
                revision=revision,
                labels=[],
                acknowledge_warnings=True,
            )
        assert error.value.code == "draft_conflict"
    with pytest.raises(SkillsHubError) as error:
        drafts.publish_draft(
            skill=hub_skill,
            user=user,
            revision=0,
            labels=[],
            acknowledge_warnings=True,
        )
    assert error.value.code == "draft_not_found"
    recreated = drafts.save_draft(
        skill=hub_skill,
        user=user,
        revision=0,
        files=[],
        note="",
        base_version=None,
    )
    assert recreated.pk == original.pk and recreated.revision == tombstone.revision + 1
    assert recreated.discarded_at is None
    assert workspace_stats(organization, workspace, user)["my_drafts"] == 1
    with pytest.raises(SkillsHubError) as error:
        drafts.save_draft(
            skill=hub_skill,
            user=user,
            revision=0,
            files=[],
            note="",
            base_version=None,
        )
    assert error.value.code == "draft_conflict"


@pytest.mark.django_db(transaction=True)
def test_cross_skill_publish_acquires_shared_blobs_in_hash_order(
    hub_skill, organization, workspace, hub_actor, make_skill_files
):
    """Two DB connections reach reversed shared content; no lock is mocked."""
    other = create_skill(
        organization=organization,
        workspace=workspace,
        actor=hub_actor,
        name="other-skill",
        description=hub_skill.description,
        tags=[],
    )
    values = ["Shared content X", "Shared content Y"]
    shared_hashes = {file_sha256(value) for value in values}
    barrier = Barrier(2, timeout=15)
    guard = Lock()
    first_hashes = {}
    calls: dict[int, list[str]] = {}
    real_get_or_create = QuerySet.get_or_create

    def synchronized_get_or_create(queryset, *args, **kwargs):
        if queryset.model is not HubSkillBlob:
            return real_get_or_create(queryset, *args, **kwargs)
        ident, digest = get_ident(), kwargs["sha256"]
        with guard:
            calls.setdefault(ident, []).append(digest)
            first_shared = digest in shared_hashes and ident not in first_hashes
            if first_shared:
                first_hashes[ident] = digest
        if first_shared:
            barrier.wait()
        result = real_get_or_create(queryset, *args, **kwargs)
        # With inconsistent ordering both transactions can hold different keys.
        # Force that interleaving before either asks for its second shared key.
        if first_shared and len(set(first_hashes.values())) == 2:
            barrier.wait()
        return result

    def publish(args):
        skill, contents = args
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '10s'")
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            files = make_skill_files(
                skill.name,
                extra=[
                    {"path": "a.txt", "content": contents[0]},
                    {"path": "b.txt", "content": contents[1]},
                    {"path": "c.txt", "content": contents[0]},
                ],
            )
            result = publish_files(
                skill=skill,
                actor=hub_actor,
                files=files,
                note="Concurrent cross-skill publication",
                base_version=None,
                labels=[],
                acknowledge_warnings=True,
                source={"type": "api"},
            )
            return pid, result.version, files
        finally:
            close_old_connections()

    with patch.object(QuerySet, "get_or_create", synchronized_get_or_create):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(publish, [(hub_skill, values), (other, values[::-1])])
            )
    assert len({pid for pid, _, _ in results}) == 2
    assert len(calls) == 2
    assert all(digests == sorted(set(digests)) for digests in calls.values())
    assert HubSkillBlob.objects.count() == 4  # two roots and two shared texts
    for _, version, files in results:
        assert version.number == 1
        assert {
            row.path: row.blob.content for row in version.files.select_related("blob")
        } == {file["path"]: file["content"] for file in files}


@pytest.mark.parametrize("mode", ["push", "import", "identity"])
@pytest.mark.parametrize(
    "name", ["sk-support-policy-review", "sk-support-policy-guide"]
)
def test_publication_uses_original_frontmatter_not_redacted_diagnostics(
    organization, workspace, hub_actor, make_skill_files, mode, name
):
    files = make_skill_files(name)
    description = (
        f"Use when reviewing {name} instructions and their publication policy."
    )
    files[0]["content"] = (
        f"---\nname: {name}\ndescription: {description}\n"
        f"metadata:\n  example: {name}\n---\nOriginal instructions.\n"
    )
    source = {"type": "api"}
    if mode == "import":
        bundle = parse_upload(
            [("different-folder/SKILL.md", files[0]["content"].encode())]
        )["bundles"][0]
        assert bundle["name"] == name
        assert bundle["checks"]["frontmatter"]["name"].startswith("[redacted")
        files = bundle["files"]
        source = {
            "type": "import",
            "filename": bundle["filename"],
            "root": bundle["root"],
        }
    if mode == "identity":
        skill = create_skill(
            organization=organization,
            workspace=workspace,
            actor=hub_actor,
            name=name,
            description=description,
            tags=[],
        )
        result = publish_files(
            skill=skill,
            actor=hub_actor,
            files=files,
            note="",
            base_version=None,
            labels=[],
            acknowledge_warnings=True,
            source=source,
        )
    else:
        args = {
            "organization": organization,
            "workspace": workspace,
            "actor": hub_actor,
            "files": files,
            "note": "",
            "labels": [],
            "tags": [],
            "acknowledge_warnings": True,
            "source": source,
        }
        result = publish_by_name(**args)
        repeated = publish_by_name(**args)
        assert repeated.unchanged and repeated.version.pk == result.version.pk
    version = result.version
    version.refresh_from_db()
    version.skill.refresh_from_db()
    assert version.skill.name == name and version.skill.description == description
    assert version.frontmatter == parse_frontmatter(files[0]["content"])[0]
    assert version.source == source
    assert version.checks["summary"]["errors"] == 0
    assert version.checks["frontmatter"]["name"].startswith("[redacted")
    assert name not in str(version.checks)
    assert version.files.get(path="SKILL.md").blob.content == files[0]["content"]


@pytest.mark.parametrize("escaped", [r"\0", r"\uD800", r"\uDFFF"])
@pytest.mark.parametrize(
    "field",
    [
        'metadata:\n  example: "VALUE"',
        'metadata:\n  "VALUE": example',
        '"VALUE": example',
        'hooks: ["VALUE"]',
    ],
)
def test_decoded_invalid_frontmatter_fails_checks_before_publication(
    hub_skill, user, publish_skill, make_skill_files, escaped, field
):
    original = drafts.get_draft(skill=hub_skill, user=user)
    files = make_skill_files()
    files[0]["content"] = files[0]["content"].replace(
        "\n---\n",
        "\n" + field.replace("VALUE", escaped) + "\n---\n",
        1,
    )
    with pytest.raises(SkillsHubError) as error:
        publish_skill(
            hub_skill, files=files, labels=["production"], acknowledge_warnings=True
        )
    assert (error.value.code, error.value.status) == ("checks_failed", 422)
    assert {item["code"] for item in error.value.detail["items"]} == {
        "frontmatter_invalid"
    }
    assert error.value.detail["frontmatter"] is None
    assert not hub_skill.versions.exists() and not hub_skill.labels.exists()
    assert not HubSkillBlob.objects.exists()
    assert drafts.get_draft(skill=hub_skill, user=user).files == original.files


def test_name_mismatch_blocks_publication_even_with_warnings_acknowledged(
    hub_skill, user, publish_skill, make_skill_files
):
    first = publish_skill(hub_skill, labels=["production"]).version
    saved = drafts.save_draft(
        skill=hub_skill,
        user=user,
        revision=1,
        base_version=1,
        note="Keep my draft",
        files=make_skill_files(name="other-skill"),
    )
    with pytest.raises(SkillsHubError) as error:
        drafts.publish_draft(
            skill=hub_skill,
            user=user,
            revision=saved.revision,
            labels=["production", "staging"],
            acknowledge_warnings=True,
        )
    assert (error.value.code, error.value.status) == ("checks_failed", 422)
    assert any(
        item["code"] == "name_mismatch" and item["severity"] == "error"
        for item in error.value.detail["items"]
    )
    assert list(hub_skill.versions.values_list("pk", flat=True)) == [first.pk]
    assert list(hub_skill.labels.values_list("name", "version_id")) == [
        ("production", first.pk)
    ]
    assert hub_skill.label_events.count() == 1
    current = drafts.get_draft(skill=hub_skill, user=user)
    assert (current.files, current.revision, current.note) == (
        saved.files,
        saved.revision,
        saved.note,
    )


@pytest.mark.parametrize("parent", ["references", "REFERENCES"])
@pytest.mark.parametrize("reverse", [False, True])
def test_file_directory_collisions_cannot_save_or_publish(
    hub_skill, user, publish_skill, make_skill_files, parent, reverse
):
    extra = [
        {"path": parent, "content": "A file"},
        {"path": "references/guide.md", "content": "Guide"},
    ]
    files = make_skill_files(extra=extra[::-1] if reverse else extra)
    original = drafts.get_draft(skill=hub_skill, user=user)
    with pytest.raises(SkillsHubError) as error:
        drafts.save_draft(
            skill=hub_skill,
            user=user,
            revision=1,
            files=files,
            note="",
            base_version=None,
        )
    assert error.value.code == "invalid_selector"
    with pytest.raises(SkillsHubError) as error:
        publish_skill(hub_skill, files=files, acknowledge_warnings=True)
    assert error.value.code == "checks_failed"
    assert any(
        item["code"] == "path_invalid" and item["severity"] == "error"
        for item in error.value.detail["items"]
    )
    assert not hub_skill.versions.exists()
    assert drafts.get_draft(skill=hub_skill, user=user).files == original.files


def test_library_summary_queries_never_select_draft_content_or_authors(
    organization, workspace, hub_actor, hub_other_user, user
):
    # A default page with two authors and 2 MiB per draft must stay metadata-only.
    files = [
        {"path": f"{index}.txt", "content": "x" * (512 * 1024)} for index in range(4)
    ]
    for index in range(25):
        skill = create_skill(
            organization=organization,
            workspace=workspace,
            actor=hub_actor,
            name=f"library-{index}",
            description="Use when checking library query payloads.",
            tags=[],
        )
        skill.drafts.update(files=files)
        HubSkillDraft.objects.create(skill=skill, user=hub_other_user, files=files)
        if index == 0:
            drafts.discard_draft(skill=skill, user=user)
    serializer_request = SimpleNamespace(
        user=user, organization=organization, workspace=workspace
    )
    # The real view resolves authorization before its serializer/query phase.
    # Keep that prerequisite outside the metadata-only query budget as well.
    serializer_request.skill_principal = principal_from_request(serializer_request)
    context = {
        "request": serializer_request,
        "fetch_counts": {},
        "latest_versions": {},
    }
    with CaptureQueriesContext(connection) as captured:
        rows = list(list_skills(organization, workspace, sort="name", order="asc")[:25])
        summaries = SkillSummary(rows, many=True, context=context).data
    assert len(summaries) == 25
    assert sum(row["has_draft"] for row in summaries) == 24
    draft_queries = [
        query["sql"]
        for query in captured.captured_queries
        if 'FROM "model_hub_hubskilldraft"' in query["sql"]
    ]
    assert len(draft_queries) == 1
    selected = draft_queries[0].split(" FROM ")[0]
    assert '"files"' not in selected and '"note"' not in selected
    assert not any(
        'FROM "accounts_user"' in query["sql"] for query in captured.captured_queries
    )
    assert all(
        "files" in draft.get_deferred_fields()
        for row in rows
        for draft in row.drafts.all()
    )
