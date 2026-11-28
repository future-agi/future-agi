"""Public J1 review: immutable source mappings, exact bytes and tenant isolation."""

import base64
import io
import zipfile
from uuid import uuid4

import pytest
from django.db import transaction
from django.urls import include, path

from model_hub.models.skills_hub import HubSkill, HubSkillDraft
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.r3.migration_receipts import (
    bind_migration_publication_locked,
)
from model_hub.tests.skills_hub.r3_l07 import test_workflow_http as fixtures

pytestmark = pytest.mark.django_db
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub"))]
BASE = "/model-hub/skills/"
admin = fixtures.admin
other_scope = fixtures.other_scope


@pytest.fixture(autouse=True)
def routes(settings):
    settings.ROOT_URLCONF = __name__
    settings.MIDDLEWARE = [
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
        *settings.MIDDLEWARE,
    ]


def export_input(files, revision="1"):
    return {
        "format": "langfuse-skills-v1",
        "origin": "langfuse.example.test",
        "project": "synthetic-library",
        "skills": [
            {
                "resource": "immutable-source",
                "revision": revision,
                "digest": manifest_digest(files),
                "files": files,
            }
        ],
    }


def test_j1_public_review_replay_and_exact_export(
    admin, other_scope, make_skill_files, publish_skill
):
    files = make_skill_files(name="j1-migration", body="Exact café\r\n")
    response = admin.post(
        BASE + "intake/langfuse/preview/", export_input(files), format="json"
    )
    assert response.status_code == 200, response.data
    plan = response.data["result"]
    item = plan["inventory"]["items"][0]
    url = BASE + f"intake/{plan['id']}/"
    review_body = {
        "item_key": item["key"],
        "expected_fingerprint": plan["fingerprint"],
    }
    review = admin.post(url + "review/", review_body, format="json")
    assert review.status_code == 200, review.data
    assert review["Cache-Control"] == "private, no-store"
    assert (
        review.data["result"]["source_hub"]["files"][0]["from_text"]
        == files[0]["content"]
    )
    assert review.data["result"]["has_draft"] is False
    assert (
        other_scope.post(url + "review/", review_body, format="json").status_code == 404
    )
    body, key = fixtures.decision(plan), str(uuid4())
    accepted = admin.post(
        url + "accept/", body, format="json", HTTP_IDEMPOTENCY_KEY=key
    )
    assert accepted.status_code == 200, accepted.data
    assert (
        admin.post(url + "accept/", body, format="json", HTTP_IDEMPOTENCY_KEY=key).data
        == accepted.data
    )
    resource = accepted.data["result"]["resource_id"]
    skill = HubSkill._base_manager.get(pk=resource)
    assert skill.latest_version_number == 0 and skill.labels.count() == 0
    draft = HubSkillDraft.objects.get(skill=skill)
    assert draft.files == files
    version = publish_skill(skill, files=draft.files).version
    with transaction.atomic():
        bind_migration_publication_locked(skill=skill, draft=draft, version=version)
    # Deliberately publish through the fixture; this is an HTTP regression, not
    # complete browser-job or production publication evidence.
    download = BASE + f"{resource}/versions/{version.number}/migration-export/"
    result = admin.get(download)
    assert result.status_code == 200, result.data
    assert other_scope.get(download).status_code == 404
    value = result.data["result"]
    assert len(value["mapping_receipts"]) == 1
    assert value["mapping_receipts"][0]["source_resource"] == "immutable-source"
    assert value["mapping_receipts"][0]["relation"] == "identical"
    assert value["exit"] == {
        "source_modified": False,
        "credentials_revoked": False,
        "local_removal": "reviewed_client_plan_required",
    }
    with zipfile.ZipFile(
        io.BytesIO(base64.b64decode(value["archive_base64"]))
    ) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    for file in files:
        assert any(
            name.endswith(file["path"]) and content == file["content"].encode()
            for name, content in members.items()
        )

    # A second immutable revision must resolve the same destination and expose
    # retained Base/Source/Hub, while an existing draft forbids replacement.
    updated = make_skill_files(name="j1-migration", body="Source second revision")
    response = admin.post(
        BASE + "intake/langfuse/preview/", export_input(updated, "2"), format="json"
    )
    assert response.status_code == 200, response.data
    second = response.data["result"]
    next_item = second["inventory"]["items"][0]
    assert next_item["destination_id"] == resource
    review_url = BASE + f"intake/{second['id']}/"
    response = admin.post(
        review_url + "review/",
        {
            "item_key": next_item["key"],
            "expected_fingerprint": second["fingerprint"],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["result"]["basis_payload_available"] is True
    assert response.data["result"]["has_draft"] is True
    assert (
        response.data["result"]["base_source"]["files"][0]["to_text"]
        == updated[0]["content"]
    )
    response = admin.post(
        review_url + "accept/",
        fixtures.decision(second),
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert response.status_code == 409, response.data
    draft.refresh_from_db()
    assert draft.files == files
    assert HubSkill._base_manager.filter(name="j1-migration").count() == 1


def test_j1_langfuse_digest_and_unknown_fields_fail_closed(admin, make_skill_files):
    body = export_input(make_skill_files(name="j1-rejected"))
    body["skills"][0]["digest"] = "sha256:" + "0" * 64
    response = admin.post(BASE + "intake/langfuse/preview/", body, format="json")
    assert response.status_code == 409, response.data
    response = admin.post(
        BASE + "intake/langfuse/preview/",
        {**body, "remote_url": "https://invalid.example"},
        format="json",
    )
    assert response.status_code == 400
    assert not HubSkill._base_manager.filter(name="j1-rejected").exists()
