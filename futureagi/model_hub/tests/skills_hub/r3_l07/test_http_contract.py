"""Exercise the integrated consumption facade through real authentication."""

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import OrgApiKey
from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_v12 import (
    HubSkillContextCredential,
    HubSkillContextResolution,
)
from model_hub.services.skills_hub.versions import persist_immutable_version_locked

pytestmark = pytest.mark.django_db
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub"))]
BASE = "/model-hub/skills/"


@pytest.fixture(autouse=True)
def routes(settings):
    settings.ROOT_URLCONF = __name__
    settings.SKILLS_CONTEXT_CONSUMPTION_ENABLED = True
    settings.MIDDLEWARE = [
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
        *settings.MIDDLEWARE,
    ]


@pytest.fixture
def admin(user, organization, workspace):
    key = OrgApiKey.objects.create(
        name="Synthetic L7 user",
        type="user",
        user=user,
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        organization=organization,
        workspace=workspace,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


@pytest.fixture(params=["skill", "agent_context"])
def released(request, organization, workspace, hub_actor, make_skill_files):
    kind = request.param
    skill = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        kind=kind,
        name="l7-synthetic",
        description="Synthetic retained purpose",
        protected_label_names=[],
    )
    text = '"\\\nélève🙂' * 15000
    if kind == "skill":
        files = make_skill_files(name=skill.name)
        frontmatter = {"name": skill.name, "description": skill.description}
    else:
        files = [{"path": "AGENTS.md", "content": "Read synthetic policy.\n"}]
        frontmatter = {}
    files += [{"path": "references/escaped.md", "content": text}]
    files += [
        {"path": f"references/{n:02d}.md", "content": f"Synthetic {n}\n"}
        for n in range(30)
    ]
    if kind == "agent_context":
        descriptor = {
            "schema": 1,
            "kind": kind,
            "name": skill.name,
            "description": skill.description,
            "entrypoint": "AGENTS.md",
            "owned_paths": sorted(file["path"] for file in files),
            "links": [],
        }
        files.append(
            {
                "path": ".futureagi/context.json",
                "content": json.dumps(
                    descriptor, sort_keys=True, separators=(",", ":")
                ),
            }
        )
    with transaction.atomic():
        locked = HubSkill.no_workspace_objects.select_for_update().get(pk=skill.pk)
        version, _ = persist_immutable_version_locked(
            skill=locked,
            actor=hub_actor,
            files=files,
            note="",
            parent_number=None,
            origin_number=None,
            description=skill.description,
            frontmatter=frontmatter,
            checks={"summary": {"errors": 0}},
            source={},
        )
    return version, files, "contexts/" if kind == "agent_context" else "consumption/"


@pytest.fixture
def issued(admin, released):
    version, _, _ = released
    body = {
        "purpose": "consume",
        "grants": [
            {
                "kind": version.skill.kind,
                "resource_id": str(version.skill_id),
                "labels": [],
                "version_ids": [str(version.pk)],
            }
        ],
        "binding_ids": [],
        "expires_at": (timezone.now() + timedelta(days=1)).isoformat(),
    }
    operation = str(uuid4())
    response = admin.post(
        BASE + "contexts/credentials/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=operation,
    )
    assert response.status_code == 201, response.data
    consumer = APIClient()
    consumer.credentials(
        HTTP_AUTHORIZATION=f"Context {response.data['result']['secret']}"
    )
    return consumer, response.data["result"], operation, body


def resolve(consumer, released):
    version, _, prefix = released
    response = consumer.get(
        BASE + prefix + "resolve/",
        {"name": version.skill.name, "version": version.number},
    )
    assert response.status_code == 200, response.data
    assert response.data["status"] is True
    return response.data["result"]


def test_complete_inventory_and_escaped_utf8_chunks(issued, released):
    consumer, _, _, _ = issued
    version, files, prefix = released
    manifest = resolve(consumer, released)
    assert manifest["resource_id"] == str(version.skill_id)
    assert manifest["kind"] == version.skill.kind
    assert "context_id" not in manifest and "skill_id" not in manifest
    headers = {"HTTP_X_CONTEXT_RESOLUTION": manifest["resolution_id"]}
    url = BASE + prefix + f"{version.skill_id}/versions/{version.number}/"
    rows = list(manifest["files"])
    next_offset = manifest["next_file_offset"]
    while next_offset is not None:
        response = consumer.get(
            url + "manifest/", {"file_offset": next_offset}, **headers
        )
        assert response.status_code == 200, response.data
        page = response.data["result"]
        assert page["version_id"] == str(version.pk)
        assert page["resolution_id"] == manifest["resolution_id"]
        rows.extend(page["files"])
        next_offset = page["next_file_offset"]
    assert sorted(row["path"] for row in rows) == sorted(file["path"] for file in files)
    expected = next(
        file["content"] for file in files if file["path"] == "references/escaped.md"
    )
    offset, pieces = 0, []
    while True:
        response = consumer.get(
            url + "files/",
            {"path": "references/escaped.md", "offset": offset, "max_bytes": 32768},
            **headers,
        )
        assert response.status_code == 200, response.data
        chunk = response.data["result"]
        assert chunk["resource_id"] == str(version.skill_id)
        assert (
            chunk["version_id"] == str(version.pk) and chunk["digest"] == version.digest
        )
        assert chunk["offset"] == offset
        assert len(chunk["text"].encode()) <= 32768
        pieces.append(chunk["text"])
        if chunk["eof"]:
            assert chunk["next_offset"] is None
            break
        assert chunk["next_offset"] == offset + len(chunk["text"])
        assert chunk["next_offset"] > offset
        offset = chunk["next_offset"]
    assert "".join(pieces) == expected
    assert chunk["sha256"] == hashlib.sha256(expected.encode()).hexdigest()
    access = consumer.post(url + "access-check/", {}, format="json", **headers)
    assert access.status_code == 200 and access.data["result"]["allowed"] is True
    assert access.data["result"]["digest"] == version.digest
    assert (
        HubSkillContextResolution.objects.filter(credential_id=issued[1]["id"]).count()
        == 1
    )
    assert (
        not version.skill.usage_days.exists()
    ), "Continuation is not installation or delivery."


@pytest.mark.parametrize(
    "suffix,method",
    [("manifest/", "get"), ("files/?path=AGENTS.md", "get"), ("access-check/", "post")],
)
def test_all_continuations_require_lease_and_deny_revocation(
    admin, issued, released, suffix, method
):
    consumer, credential, _, _ = issued
    version, _, prefix = released
    manifest = resolve(consumer, released)
    url = BASE + prefix + f"{version.skill_id}/versions/1/" + suffix
    call = getattr(consumer, method)
    missing = call(url, {}, format="json")
    assert (
        missing.status_code == 428
        and missing.data["code"] == "context_precondition_required"
    )
    response = admin.delete(BASE + f"contexts/credentials/{credential['id']}/")
    assert response.status_code == 204
    denied = call(
        url,
        {},
        format="json",
        HTTP_X_CONTEXT_RESOLUTION=manifest["resolution_id"],
        HTTP_IF_NONE_MATCH=f'"{manifest["digest"]}"',
    )
    assert (
        denied.status_code == 403
        and denied.data["code"] == "context_credential_revoked"
    )
    assert "files" not in denied.data and "text" not in denied.data


def test_replay_after_expiry_uses_original_key_and_no_second_secret(admin, issued):
    _, first, operation, body = issued
    HubSkillContextCredential.objects.filter(pk=first["id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    replay = admin.post(
        BASE + "contexts/credentials/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=operation,
    )
    assert replay.status_code == 200, replay.data
    assert replay.data["result"]["id"] == first["id"]
    assert replay.data["result"]["secret"] is None
    assert replay.data["result"]["secret_available"] is False
    changed = {**body, "expires_at": (timezone.now() + timedelta(days=2)).isoformat()}
    conflict = admin.post(
        BASE + "contexts/credentials/",
        changed,
        format="json",
        HTTP_IDEMPOTENCY_KEY=operation,
    )
    assert (
        conflict.status_code == 409 and conflict.data["code"] == "idempotency_conflict"
    )


def test_wrong_kind_and_human_auth_cannot_use_consumption(admin, issued, released):
    consumer, _, _, _ = issued
    version, _, prefix = released
    wrong = "consumption/" if prefix == "contexts/" else "contexts/"
    response = consumer.get(
        BASE + wrong + "resolve/", {"name": version.skill.name, "version": 1}
    )
    assert response.status_code == 404
    assert version.skill.name not in json.dumps(response.data)
    response = admin.get(
        BASE + prefix + "resolve/", {"name": version.skill.name, "version": 1}
    )
    assert response.status_code == 401


def test_credential_list_scope_and_pagination(
    admin, issued, workspace, hub_other_workspace
):
    _, first, _, _ = issued
    response = admin.get(BASE + "contexts/credentials/", {"page_size": 1})
    assert response.status_code == 200
    page = response.data["result"]
    assert page["count"] == 1 and page["results"][0]["id"] == first["id"]
    assert "secret" not in json.dumps(page)
    denied = admin.get(
        BASE + "contexts/credentials/", HTTP_X_WORKSPACE_ID=str(hub_other_workspace.pk)
    )
    assert denied.status_code in {403, 404}
    assert first["id"] not in json.dumps(denied.data)
    bad_page = admin.get(BASE + "contexts/credentials/", {"page_size": 101})
    assert bad_page.status_code == 400
