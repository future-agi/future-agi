"""Real-credential regressions for label selection, batch exports and caching."""

import hashlib
import io
import json
import zipfile

import pytest

from model_hub.models.skills_hub import HubSkillUsageDaily
from model_hub.services.skills_hub.versions import create_skill
from model_hub.tests.skills_hub import test_api_endpoints as fixtures

BASE = fixtures.BASE
client = fixtures.client
credentials = fixtures.credentials
result = fixtures.result
pytestmark = pytest.mark.django_db


def test_history_label_mutations_preserve_audit_reads(
    client, hub_skill, publish_skill, make_skill_files
):
    publish_skill(hub_skill, labels=["history"])
    second = publish_skill(
        hub_skill, files=make_skill_files(body="Review the changed instructions.")
    ).version
    path = BASE + str(hub_skill.pk) + "/labels/history/"
    moved = result(
        client.put(
            path,
            {"version": 2, "expected_version": 1, "note": "Reviewed history label"},
            format="json",
        )
    )
    assert moved["name"] == "history"
    assert moved["version_id"] == str(second.pk)
    assert moved["version"] == 2
    conflict = client.put(path, {"version": 1, "expected_version": 1}, format="json")
    assert conflict.status_code == 409 and conflict.data["code"] == "label_conflict"
    assert (
        client.delete(path, {"expected_version": 2}, format="json").status_code == 204
    )
    assert result(client.get(BASE + str(hub_skill.pk) + "/labels/")) == []
    events = result(client.get(path, {"label": "history"}))
    assert events["count"] == 3
    assert [(row["from_version"], row["to_version"]) for row in events["results"]] == [
        (2, None),
        (1, 2),
        (None, 1),
    ]
    assert {row["label"] for row in events["results"]} == {"history"}
    assert events["results"][1]["note"] == "Reviewed history label"
    assert all(row["actor"] is not None for row in events["results"])


@pytest.mark.parametrize("selection", ["names", "tag"])
@pytest.mark.parametrize("tenant", ["workspace", "organization"])
def test_batch_download_packages_and_counts_each_resolved_skill(
    client,
    credentials,
    request,
    organization,
    workspace,
    hub_actor,
    publish_skill,
    make_skill_files,
    actor_for_workspace,
    selection,
    tenant,
):
    selected = []
    for name in ("alpha-skill", "beta-skill", "unlabelled-skill"):
        skill = create_skill(
            organization=organization,
            workspace=workspace,
            actor=hub_actor,
            name=name,
            description="Use when a batch download needs complete, immutable instructions.",
            tags=["batch"],
        )
        files = make_skill_files(
            name,
            extra=[{"path": "references/policy.md", "content": f"Policy for {name}."}],
        )
        version = publish_skill(
            skill,
            files=files,
            labels=[] if name == "unlabelled-skill" else ["production"],
            expected_release_revision=0,
        ).version
        # Latest must not silently replace the production pointer in the ZIP.
        if name == "alpha-skill":
            publish_skill(skill, files=make_skill_files(name, body="Not promoted."))
        selected.append((skill, version, files))
    foreign_workspace = request.getfixturevalue(
        "hub_other_workspace" if tenant == "workspace" else "hub_other_org_workspace"
    )
    foreign_actor = actor_for_workspace(foreign_workspace)
    foreign = create_skill(
        organization=foreign_workspace.organization,
        workspace=foreign_workspace,
        actor=foreign_actor,
        name="foreign-skill",
        description="Use when a foreign tenant has the same discovery tag.",
        tags=["batch"],
    )
    publish_skill(
        foreign, actor=foreign_actor, labels=["production"], expected_release_revision=0
    )
    query = {
        "label": "production",
        **(
            {"names": ",".join(skill.name for skill, _, _ in selected)}
            if selection == "names"
            else {"tag": "batch"}
        ),
    }
    response = client.get(BASE + "download/", query, HTTP_ACCEPT="application/zip")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    expected_files = {
        f"{skill.name}/{file['path']}": file["content"].encode()
        for skill, _, files in selected[:2]
        for file in files
    }
    with zipfile.ZipFile(io.BytesIO(response.content)) as downloaded:
        assert set(downloaded.namelist()) == {
            *expected_files,
            ".futureagi-skills.lock.json",
        }
        for path, content in expected_files.items():
            assert downloaded.read(path) == content
        lock = json.loads(downloaded.read(".futureagi-skills.lock.json"))
    assert lock["schema"] == 1
    assert lock["skills"] == [
        {
            "name": skill.name,
            "version": version.number,
            "version_id": str(version.pk),
            "digest": version.digest,
            "label": "production",
        }
        for skill, version, _ in selected[:2]
    ]
    assert lock["skipped"] == [
        {"name": "unlabelled-skill", "reason": "label_not_found"}
    ]
    key = credentials[1]
    consumer = f"key:{key.pk}" if key else f"user:{hub_actor.user.pk}"
    assert set(
        HubSkillUsageDaily.objects.values_list(
            "version_id", "channel", "consumer_key", "count"
        )
    ) == {(version.pk, "download", consumer, 1) for _, version, _ in selected[:2]}
    for skill, _, _ in selected[:2]:
        summary = result(client.get(BASE + str(skill.pk) + "/usage/"))
        assert summary["by_channel"] == {"api": 0, "download": 1, "mcp": 0}
        assert summary["total"] == 1


def test_stale_etag_after_production_move_returns_new_version(
    client, hub_skill, publish_skill, make_skill_files
):
    first = publish_skill(hub_skill, labels=["production"]).version
    query = {"name": hub_skill.name, "include_content": "true"}
    initial_response = client.get(BASE + "resolve/", query)
    initial = result(initial_response)
    assert initial_response["ETag"] == f'"{first.digest}"'
    assert initial["digest"] == first.digest
    assert initial["version_id"] == str(first.pk)
    files = make_skill_files(body="Follow the newly reviewed production instructions.")
    second = publish_skill(hub_skill, files=files).version
    assert second.digest != first.digest
    result(
        client.put(
            BASE + str(hub_skill.pk) + "/labels/production/",
            {"version": 2, "expected_version": 1},
            format="json",
        )
    )
    refreshed_response = client.get(
        BASE + "resolve/", query, HTTP_IF_NONE_MATCH=initial_response["ETag"]
    )
    refreshed = result(refreshed_response)
    assert refreshed_response["ETag"] == f'"{second.digest}"'
    assert refreshed["version"] == 2
    assert refreshed["version_id"] == str(second.pk)
    assert refreshed["digest"] == second.digest
    assert refreshed["requested"] == {"label": "production"}
    assert refreshed["files"][0]["content"] == files[0]["content"]
    cached = client.get(
        BASE + "resolve/", query, HTTP_IF_NONE_MATCH=refreshed_response["ETag"]
    )
    assert cached.status_code == 304 and cached.content == b""
    assert cached["ETag"] == refreshed_response["ETag"]


def test_manifest_file_paging_is_optional_and_lossless(
    client, hub_skill, publish_skill, make_skill_files
):
    files = make_skill_files(
        extra=[
            {"path": f"references/file-{index:03}.md", "content": f"Reference {index}."}
            for index in range(29)
        ]
    )
    version = publish_skill(hub_skill, files=files, labels=["production"]).version
    query = {
        "name": hub_skill.name,
        "version": version.number,
        "include_content": "true",
    }
    complete = result(client.get(BASE + "resolve/", query))
    assert len(complete["files"]) == len(files)
    assert complete["frontmatter"] == version.frontmatter
    received, offset = [], 0
    while True:
        manifest = result(
            client.get(
                BASE + "resolve/", {**query, "file_offset": offset, "file_limit": 7}
            )
        )
        assert manifest["file_count"] == len(files)
        assert manifest["file_offset"] == offset
        assert manifest["version_id"] == str(version.pk)
        assert len(manifest["files"]) <= 7
        received.extend(manifest["files"])
        offset = manifest["next_file_offset"]
        if offset is None:
            break
        assert offset == len(received)
    assert received == complete["files"]
    for actual, expected in zip(
        received, sorted(files, key=lambda file: file["path"]), strict=True
    ):
        assert actual == {
            "path": expected["path"],
            "content": expected["content"],
            "size_bytes": len(expected["content"].encode()),
            "sha256": hashlib.sha256(expected["content"].encode()).hexdigest(),
        }
    exhausted = result(
        client.get(
            BASE + "resolve/", {**query, "file_offset": len(files), "file_limit": 7}
        )
    )
    assert exhausted["files"] == [] and exhausted["next_file_offset"] is None
    for invalid in (
        {"file_offset": -1},
        {"file_offset": len(files) + 1},
        {"file_offset": "bad"},
        {"file_limit": 0},
        {"file_limit": 26},
    ):
        response = client.get(BASE + "resolve/", {**query, **invalid})
        assert (
            response.status_code == 400 and response.data["code"] == "invalid_selector"
        )
