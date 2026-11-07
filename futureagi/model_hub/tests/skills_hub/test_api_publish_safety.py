"""Real-auth regression tests for draft review CAS and import provenance."""

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from model_hub.models.skills_hub import HubSkill
from model_hub.tests.skills_hub import test_api_endpoints as fixtures
from model_hub.tests.skills_hub.test_api_contracts import assert_contract

client = fixtures.client
credentials = fixtures.credentials
BASE = fixtures.BASE
result = fixtures.result
pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("revision", [-1, 0, 1, 3])
def test_draft_publish_cas_rejects_unreviewed_edits(
    client, hub_skill, make_skill_files, force, revision
):
    path = BASE + str(hub_skill.pk) + "/"
    saved = result(
        client.put(
            path + "draft/",
            {
                "files": make_skill_files(body="An edit made after review opened"),
                "base_version": None,
                "revision": 1,
            },
            format="json",
        )
    )
    response = client.post(
        path + "draft/publish/",
        {
            "revision": revision,
            "force": force,
            "labels": ["production"],
        },
        format="json",
    )
    assert response.status_code == 409 and response.data["code"] == "draft_conflict"
    assert response.data["detail"]["revision"] == saved["revision"]
    assert response.data["detail"]["updated_at"]
    assert_contract(response)
    assert result(client.get(path + "draft/"))["files"] == saved["files"]
    assert not hub_skill.versions.exists() and not hub_skill.labels.exists()
    published = client.post(
        path + "draft/publish/",
        {
            "revision": saved["revision"],
            "labels": ["production"],
        },
        format="json",
    )
    assert result(published, 201)["version"]["number"] == 1
    assert_contract(published)
    assert not hub_skill.drafts.exists()


@pytest.mark.parametrize("revision", [None, 1.5, "not-a-number"])
def test_draft_publish_invalid_revision_is_not_ignored(client, hub_skill, revision):
    response = client.post(
        BASE + str(hub_skill.pk) + "/draft/publish/",
        {
            "revision": revision,
        },
        format="json",
    )
    assert response.status_code == 400
    assert not hub_skill.versions.exists() and hub_skill.drafts.exists()


def test_import_records_each_zip_origin_and_root(client, make_skill_files):
    def upload(name, root, skill_name):
        file = fixtures.archive(
            [
                {"path": root + "/" + item["path"], "content": item["content"]}
                for item in make_skill_files(skill_name)
            ]
        )
        file.name = name
        return file

    def files():
        return [
            upload("first.zip", "support", "support-skill"),
            upload("second.zip", "research", "research-skill"),
        ]

    preview = result(
        client.post(BASE + "import/preview/", {"files": files()}, format="multipart")
    )
    imported = result(
        client.post(
            BASE + "import/",
            {
                "files": files(),
                "selected": json.dumps([item["key"] for item in preview["bundles"]]),
            },
            format="multipart",
        )
    )
    assert len(imported["created"]) == 2 and not imported["failed"]
    for skill_name, filename, root in [
        ("support-skill", "first.zip", "support"),
        ("research-skill", "second.zip", "research"),
    ]:
        skill = HubSkill.no_workspace_objects.get(name=skill_name)
        source = {"type": "import", "filename": filename, "root": root}
        assert skill.source == skill.versions.get().source == source
        assert result(client.get(BASE + str(skill.pk) + "/"))["source"] == source


def test_loose_import_provenance_uses_root_instruction_filename(
    client, make_skill_files
):
    def files():
        return [
            SimpleUploadedFile("SKILL.md", make_skill_files()[0]["content"].encode())
        ]

    preview = result(
        client.post(BASE + "import/preview/", {"files": files()}, format="multipart")
    )
    imported = result(
        client.post(
            BASE + "import/",
            {
                "files": files(),
                "selected": json.dumps([preview["bundles"][0]["key"]]),
            },
            format="multipart",
        )
    )
    skill = HubSkill.no_workspace_objects.get(pk=imported["created"][0]["skill_id"])
    assert (
        skill.source
        == skill.versions.get().source
        == {
            "type": "import",
            "filename": "SKILL.md",
            "root": "",
        }
    )


def test_compare_preserves_integer_and_draft_wire_refs(
    client, hub_skill, publish_skill
):
    publish_skill(hub_skill)
    response = client.get(
        BASE + str(hub_skill.pk) + "/compare/", {"from": 1, "to": "draft"}
    )
    compared = result(response)
    assert compared["from"] == 1 and type(compared["from"]) is int
    assert compared["to"] == "draft"
    assert_contract(response)
