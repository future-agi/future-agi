"""Exercise every Skills route through real credentials and tenant boundaries."""

import io
import json
import uuid
import zipfile
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from accounts.authentication import generate_encrypted_message
from accounts.models import OrgApiKey
from accounts.models.auth_token import AuthToken
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.skills_hub import HubSkillUsageDaily
from model_hub.services.skills_hub.versions import create_skill
from model_hub.views import skills_hub as views
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db
BASE = "/model-hub/skills/"


@pytest.fixture(params=["key", "jwt"])
def credentials(request, user, workspace, organization):
    if request.param == "key":
        key = OrgApiKey.objects.create(
            name="Skills test consumer",
            api_key=f"test-{uuid.uuid4()}",
            secret_key=f"test-{uuid.uuid4()}",
            type="user",
            user=user,
            organization=organization,
            workspace=workspace,
        )
        return {"HTTP_X_API_KEY": key.api_key, "HTTP_X_SECRET_KEY": key.secret_key}, key
    token = AuthToken.objects.create(
        user=user, auth_type="access", last_used_at=timezone.now()
    )
    value = generate_encrypted_message({"user_id": str(user.pk), "id": str(token.pk)})
    return {
        "HTTP_AUTHORIZATION": f"Bearer {value}",
        "HTTP_X_WORKSPACE_ID": str(workspace.pk),
    }, None


@pytest.fixture
def client(credentials):
    value = APIClient()
    value.credentials(**credentials[0])
    return value


def result(response, status=200):
    assert response.status_code == status, getattr(response, "data", response.content)
    assert response.data["status"] is True
    return response.data["result"]


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        for file in files:
            output.writestr(file["path"], file["content"])
    return SimpleUploadedFile(
        "skills.zip", stream.getvalue(), content_type="application/zip"
    )


def test_full_authoring_lifecycle(client, make_skill_files):
    templates = result(client.get(BASE + "templates/"))
    assert len(templates) == 7
    created = result(
        client.post(
            BASE,
            {
                "name": "help-desk",
                "description": "Use when a support request needs a clear and verifiable next step.",
                "tags": ["support"],
            },
            format="json",
        ),
        201,
    )
    assert created["latest"] is None and created["has_draft"]
    path = BASE + created["id"] + "/"
    assert result(client.get(path))["id"] == created["id"]
    assert result(client.patch(path, {"tags": ["approved"]}, format="json"))[
        "tags"
    ] == ["approved"]
    files = make_skill_files()
    assert (
        result(client.post(BASE + "checks/", {"files": files}, format="json"))[
            "summary"
        ]["errors"]
        == 0
    )
    draft = result(client.get(path + "draft/"))
    saved = result(
        client.put(
            path + "draft/",
            {"files": files, "base_version": None, "revision": draft["revision"]},
            format="json",
        )
    )
    assert saved["revision"] == draft["revision"] + 1
    published = result(
        client.post(
            path + "draft/publish/",
            {"labels": ["production"], "acknowledge_warnings": True},
            format="json",
        ),
        201,
    )
    assert published["version"]["number"] == 1
    assert client.get(path + "draft/").data["code"] == "draft_not_found"
    versions = result(client.get(path + "versions/"))
    assert versions["count"] == 1
    assert result(client.get(path + "versions/1/"))["files"][0]["path"] == "SKILL.md"
    second = result(
        client.post(
            path + "versions/",
            {
                "files": make_skill_files(body="A changed instruction."),
                "base_version": 1,
            },
            format="json",
        ),
        201,
    )
    assert second["version"]["number"] == 2
    moved = result(
        client.put(
            path + "labels/production/",
            {"version": 2, "expected_version": 1, "note": "Reviewed"},
            format="json",
        )
    )
    assert moved["version"] == 2
    assert result(client.get(path + "labels/"))[0]["version"] == 2
    history = result(client.get(path + "labels/history/"))["results"]
    assert history[0]["note"] == "Reviewed"
    compared = result(client.get(path + "compare/", {"from": 1, "to": 2}))
    assert compared["summary"]["modified"] == 1
    restored = result(client.post(path + "versions/1/restore/", {}, format="json"), 201)
    assert restored["version"]["number"] == 3
    assert result(client.get(path + "usage/"))["total"] == 0
    library = result(client.get(BASE))
    assert library["results"][0]["latest_version"] == 3
    assert library["stats"]["in_production"] == 1
    assert result(client.get(BASE, {"search": "not-present"}))["stats"]["total"] == 1
    assert (
        client.delete(
            path + "labels/production/", {"expected_version": 2}, format="json"
        ).status_code
        == 204
    )
    assert client.delete(path + "draft/", {}, format="json").status_code == 204
    assert client.delete(path, {}, format="json").status_code == 204
    assert client.get(path).data["code"] == "skill_not_found"


def test_real_auth_consumption_etag_zip_and_usage(
    client, credentials, hub_skill, publish_skill, make_skill_files
):
    published = publish_skill(hub_skill, labels=["production"])
    path = BASE + str(hub_skill.pk) + "/versions/1/"
    manifest_response = client.get(BASE + "resolve/", {"name": hub_skill.name})
    manifest = result(manifest_response)
    assert manifest["version_id"] == str(published.version.pk)
    assert "content" not in manifest["files"][0]
    full = result(
        client.get(
            BASE + "resolve/", {"name": hub_skill.name, "include_content": "true"}
        )
    )
    assert full["files"][0]["content"] == make_skill_files()[0]["content"]
    cached = client.get(
        BASE + "resolve/",
        {"name": hub_skill.name},
        HTTP_IF_NONE_MATCH=manifest_response["ETag"],
    )
    assert cached.status_code == 304 and cached.content == b""
    assert cached["ETag"] == manifest_response["ETag"]
    chunk = result(client.get(path + "files/", {"path": "SKILL.md", "max_bytes": 30}))
    assert not chunk["eof"]
    result(
        client.get(
            path + "files/", {"path": "SKILL.md", "offset": chunk["next_offset"]}
        )
    )
    for url, query in (
        (BASE + "download/", {"names": "help-desk,missing"}),
        (path + "download/", {}),
    ):
        response = client.get(url, query, HTTP_ACCEPT="application/zip")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.content)) as downloaded:
            assert (
                downloaded.read("help-desk/SKILL.md").decode()
                == make_skill_files()[0]["content"]
            )
            lock = json.loads(downloaded.read(".futureagi-skills.lock.json"))
            assert lock["skills"][0]["digest"] == published.version.digest
            assert lock["skills"][0]["label"] == ("production" if query else None)
            if query:
                assert lock["skipped"] == [
                    {"name": "missing", "reason": "skill_not_found"}
                ]
    missing = client.get(
        BASE + "download/", {"names": "absent"}, HTTP_ACCEPT="application/zip"
    )
    assert missing.status_code == 404
    assert missing["Content-Type"] == "application/json"
    assert missing.json()["code"] == "nothing_to_download"
    summary = result(client.get(BASE + str(hub_skill.pk) + "/usage/"))
    assert summary["by_channel"] == {"api": 4, "download": 2, "mcp": 0}
    key = credentials[1]
    assert set(HubSkillUsageDaily.objects.values_list("consumer_key", flat=True)) == {
        f"key:{key.pk}" if key else f"user:{hub_skill.created_by_id}"
    }


def test_push_idempotency_and_api_key_attribution(
    client, credentials, make_skill_files
):
    payload = {"files": make_skill_files(), "labels": ["production"]}
    first = result(client.post(BASE + "publish/", payload, format="json"), 201)
    second = result(client.post(BASE + "publish/", payload, format="json"))
    assert second["unchanged"] and first["version"]["id"] == second["version"]["id"]
    key = credentials[1]
    if key:
        assert key.hub_skill_versions.get().pk == uuid.UUID(first["version"]["id"])


def test_import_preview_commit_and_unchanged(client, make_skill_files):
    files = make_skill_files(
        extra=[{"path": "references/policy.md", "content": "Policy text."}]
    )
    preview = result(
        client.post(
            BASE + "import/preview/", {"files": archive(files)}, format="multipart"
        )
    )
    item = preview["bundles"][0]
    assert item["status"] == "new" and item["file_count"] == 2
    form = {
        "files": archive(files),
        "selected": json.dumps([item["key"]]),
        "labels": '["production"]',
        "acknowledge_warnings": "true",
    }
    imported = result(client.post(BASE + "import/", form, format="multipart"))
    assert imported["created"][0]["name"] == "help-desk"
    preview = result(
        client.post(
            BASE + "import/preview/", {"files": archive(files)}, format="multipart"
        )
    )
    assert preview["bundles"][0]["status"] == "unchanged"
    form["files"] = archive(files)
    imported = result(client.post(BASE + "import/", form, format="multipart"))
    assert len(imported["unchanged"]) == 1


def test_relative_multipart_paths_survive_and_traversal_is_rejected(
    client, make_skill_files
):
    # A browser's directory upload supplies Content-Disposition filename paths.
    boundary = "skills-test-boundary"
    parts = []
    for path, text in [
        ("desk/SKILL.md", make_skill_files()[0]["content"]),
        ("desk/references/policy.md", "Policy."),
        ("../unsafe.md", "Denied."),
    ]:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{path}"\r\nContent-Type: text/plain\r\n\r\n{text}\r\n'
        )
    response = client.post(
        BASE + "import/preview/",
        data=("".join(parts) + f"--{boundary}--\r\n").encode(),
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    preview = result(response)
    assert preview["bundles"][0]["file_count"] == 2
    assert preview["bundles"][0]["root"] == "desk"
    assert {item["path"] for item in preview["rejected"]} == {"../unsafe.md"}


# Each scoped endpoint (and each method) must hide both foreign workspaces and orgs.
SCOPED = [
    ("get", "", {}),
    ("patch", "", {"tags": []}),
    ("delete", "", {}),
    ("get", "versions/", {}),
    ("post", "versions/", {"files": []}),
    ("get", "versions/1/", {}),
    ("get", "versions/1/files/", {"path": "SKILL.md"}),
    ("get", "versions/1/download/", {}),
    ("post", "versions/1/restore/", {}),
    ("get", "compare/", {"from": 1, "to": 1}),
    ("get", "labels/", {}),
    ("put", "labels/production/", {"version": 1}),
    ("delete", "labels/production/", {}),
    ("get", "labels/history/", {}),
    ("get", "draft/", {}),
    ("put", "draft/", {"files": [], "revision": 0, "base_version": None}),
    ("delete", "draft/", {}),
    ("post", "draft/publish/", {}),
    ("get", "usage/", {}),
]


@pytest.mark.parametrize("method,suffix,payload", SCOPED)
@pytest.mark.parametrize("tenant", ["workspace", "organization"])
def test_every_scoped_endpoint_hides_foreign_tenants(
    client, request, organization, user, hub_actor, tenant, method, suffix, payload
):
    workspace = request.getfixturevalue(
        "hub_other_workspace" if tenant == "workspace" else "hub_other_org_workspace"
    )
    foreign = create_skill(
        organization=workspace.organization,
        workspace=workspace,
        actor=hub_actor,
        name="foreign-skill",
        description="Use when a foreign tenant needs isolated instructions.",
        tags=[],
    )
    url = BASE + str(foreign.pk) + "/" + suffix
    response = getattr(client, method)(
        url, payload, **({} if method == "get" else {"format": "json"})
    )
    assert response.status_code == 404, response.data
    assert response.data["code"] == "skill_not_found"


@pytest.mark.parametrize("tenant", ["workspace", "organization"])
def test_collections_do_not_leak_foreign_names(
    client, request, tenant, hub_actor, publish_skill, make_skill_files
):
    foreign_workspace = request.getfixturevalue(
        "hub_other_workspace" if tenant == "workspace" else "hub_other_org_workspace"
    )
    foreign = create_skill(
        organization=foreign_workspace.organization,
        workspace=foreign_workspace,
        actor=hub_actor,
        name="foreign-skill",
        description="Use when a foreign tenant needs isolated instructions.",
        tags=["foreign"],
    )
    publish_skill(foreign, labels=["production"])
    assert result(client.get(BASE))["count"] == 0
    assert client.get(BASE + "resolve/", {"name": foreign.name}).status_code == 404
    assert client.get(BASE + "download/", {"names": foreign.name}).status_code == 404
    preview = result(
        client.post(
            BASE + "import/preview/",
            {"files": archive(make_skill_files(foreign.name))},
            format="multipart",
        )
    )
    assert preview["bundles"][0]["status"] == "new"
    assert "skill_id" not in preview["bundles"][0]
    pushed = result(
        client.post(
            BASE + "publish/", {"files": make_skill_files(foreign.name)}, format="json"
        ),
        201,
    )
    assert pushed["skill"]["id"] != str(foreign.pk)


ALL_VIEWS = [
    (views.SkillListView, "get"),
    (views.SkillListView, "post"),
    (views.SkillTemplatesView, "get"),
    (views.SkillChecksView, "post"),
    (views.SkillPushView, "post"),
    (views.SkillResolveView, "get"),
    (views.SkillDownloadView, "get"),
    (views.SkillImportPreviewView, "post"),
    (views.SkillImportView, "post"),
    (views.SkillDetailView, "get"),
    (views.SkillDetailView, "patch"),
    (views.SkillDetailView, "delete"),
    (views.SkillVersionsView, "get"),
    (views.SkillVersionsView, "post"),
    (views.SkillVersionView, "get"),
    (views.SkillFileView, "get"),
    (views.SkillVersionDownloadView, "get"),
    (views.SkillRestoreView, "post"),
    (views.SkillCompareView, "get"),
    (views.SkillLabelsView, "get"),
    (views.SkillLabelView, "put"),
    (views.SkillLabelView, "delete"),
    (views.SkillLabelHistoryView, "get"),
    (views.SkillDraftView, "get"),
    (views.SkillDraftView, "put"),
    (views.SkillDraftView, "delete"),
    (views.SkillDraftPublishView, "post"),
    (views.SkillUsageView, "get"),
]


@pytest.mark.parametrize("view,method", ALL_VIEWS)
def test_every_endpoint_requires_resolved_workspace(user, organization, view, method):
    request = getattr(APIRequestFactory(), method)(BASE, {}, format="json")
    force_authenticate(request, user=user)
    request.organization = organization
    request.workspace = None
    response = view.as_view()(request)
    assert response.status_code == 403 and response.data["code"] == "workspace_required"


@pytest.mark.parametrize(
    "method,suffix,payload",
    [row for row in SCOPED if row[0] != "get"]
    + [
        ("post", "checks/", {}),
        ("post", "publish/", {}),
        ("post", "import/", {}),
        ("post", "import/preview/", {}),
        ("post", "create", {}),
    ],
)
def test_read_only_role_cannot_write(
    client, user, workspace, organization, hub_skill, method, suffix, payload
):
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(role=OrganizationRoles.MEMBER, level=Level.MEMBER)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.VIEWER)
    collection = suffix in {
        "checks/",
        "publish/",
        "import/",
        "import/preview/",
        "create",
    }
    url = (
        BASE + ("" if suffix == "create" else suffix)
        if collection
        else BASE + str(hub_skill.pk) + "/" + suffix
    )
    response = getattr(client, method)(url, payload, format="json")
    assert response.status_code == 403, response.data


def test_errors_and_limits(client, hub_skill, publish_skill, make_skill_files):
    path = BASE + str(hub_skill.pk) + "/"
    assert client.get(BASE, {"page_size": 101}).data["code"] == "invalid_selector"
    assert (
        client.get(
            BASE + "resolve/", {"name": hub_skill.name, "version": 1, "label": "latest"}
        ).data["code"]
        == "invalid_selector"
    )
    assert (
        client.get(BASE + "resolve/", {"name": hub_skill.name}).data["code"]
        == "version_not_found"
    )
    publish_skill(hub_skill)
    assert (
        client.get(BASE + "resolve/", {"name": hub_skill.name}).data["code"]
        == "label_not_found"
    )
    assert (
        client.get(BASE + "resolve/", {"name": hub_skill.name, "version": 99}).data[
            "code"
        ]
        == "version_not_found"
    )
    assert (
        client.post(
            BASE, {"name": "BAD", "description": "Description"}, format="json"
        ).data["code"]
        == "invalid_name"
    )
    assert (
        client.post(
            BASE, {"name": hub_skill.name, "description": "Description"}, format="json"
        ).data["code"]
        == "name_taken"
    )
    assert (
        client.patch(path, {"tags": ["BAD TAG"]}, format="json").data["code"]
        == "invalid_tags"
    )
    assert (
        client.put(path + "labels/latest/", {"version": 1}, format="json").data["code"]
        == "invalid_label"
    )
    assert (
        client.put(
            path + "labels/production/",
            {"version": 1, "expected_version": 1},
            format="json",
        ).data["code"]
        == "label_conflict"
    )
    stale = client.post(
        path + "versions/",
        {"files": make_skill_files(), "base_version": None},
        format="json",
    )
    assert stale.status_code == 409 and stale.data["detail"]["latest_version"] == 1
    conflict = client.put(
        path + "draft/",
        {"files": [], "base_version": None, "revision": 0},
        format="json",
    )
    assert conflict.status_code == 409 and conflict.data["code"] == "draft_conflict"
    assert (
        client.post(BASE + "publish/", {"files": []}, format="json").data["code"]
        == "checks_failed"
    )
    warning_files = make_skill_files(extra=[{"path": "empty.md", "content": ""}])
    assert (
        client.post(BASE + "publish/", {"files": warning_files}, format="json").data[
            "code"
        ]
        == "warnings_unacknowledged"
    )
    assert (
        client.post(
            BASE + "import/",
            {"files": archive(make_skill_files()), "selected": "no-json"},
            format="multipart",
        ).data["code"]
        == "import_invalid"
    )
    assert (
        client.get(
            BASE + "download/",
            {"names": hub_skill.name, "tag": "support", "version": 1},
        ).data["code"]
        == "invalid_selector"
    )


@pytest.mark.parametrize("view,method", ALL_VIEWS)
def test_every_endpoint_requires_authentication(view, method):
    request = getattr(APIRequestFactory(), method)(BASE, {}, format="json")
    response = view.as_view()(request)
    assert response.status_code == 401


def test_import_partial_failure_and_invalid_selection(client, make_skill_files):
    files = [
        {"path": "good/" + file["path"], "content": file["content"]}
        for file in make_skill_files("good-skill")
    ] + [{"path": "bad/SKILL.md", "content": "Missing frontmatter."}]
    preview = result(
        client.post(
            BASE + "import/preview/", {"files": archive(files)}, format="multipart"
        )
    )
    keys = [item["key"] for item in preview["bundles"]]
    response = result(
        client.post(
            BASE + "import/",
            {"files": archive(files), "selected": json.dumps(keys)},
            format="multipart",
        )
    )
    assert len(response["created"]) == 1 and len(response["failed"]) == 1
    assert response["failed"][0]["code"] == "checks_failed"
    invalid = client.post(
        BASE + "import/",
        {"files": archive(files), "selected": '["absent"]'},
        format="multipart",
    )
    assert invalid.status_code == 400 and invalid.data["code"] == "import_invalid"
    changed = make_skill_files("good-skill", body="Changed instructions.")
    preview = result(
        client.post(
            BASE + "import/preview/", {"files": archive(changed)}, format="multipart"
        )
    )
    assert preview["bundles"][0]["status"] == "changed"
    imported = result(
        client.post(
            BASE + "import/",
            {
                "files": archive(changed),
                "selected": json.dumps([preview["bundles"][0]["key"]]),
            },
            format="multipart",
        )
    )
    assert imported["updated"][0]["version"] == 2


def test_checks_report_invalid_text_without_drf_rewriting(client, make_skill_files):
    files = make_skill_files(extra=[{"path": "bad.txt", "content": "bad\u0000text"}])
    response = result(client.post(BASE + "checks/", {"files": files}, format="json"))
    assert any(item["code"] == "file_not_text" for item in response["items"])
    blocked = client.post(BASE + "publish/", {"files": files}, format="json")
    assert blocked.status_code == 422 and blocked.data["code"] == "checks_failed"


def test_upload_size_checked_before_read():
    upload = SimpleUploadedFile("large.zip", b"synthetic")
    upload.size = views.limits.MAX_ZIP_BYTES + 1
    with patch.object(upload.file, "read", side_effect=AssertionError("must not read")):
        with pytest.raises(views.SkillsHubError) as error:
            views.parse_uploads([upload])
    assert error.value.code == "payload_too_large"
