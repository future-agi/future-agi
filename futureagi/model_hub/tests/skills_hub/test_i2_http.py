"""Real authenticated recovery, consumer and forensic HTTP integration."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from model_hub.models.skills_hub_v12 import HubSkillContextCredential
from model_hub.services.skills_hub.drafts import get_draft, save_draft
from model_hub.services.skills_hub.r3.draft_recovery import draft_recovery_digest
from model_hub.services.skills_hub.v12.credentials import issue_credential
from model_hub.tests.skills_hub import test_i2_integration as fixtures

pytestmark = pytest.mark.django_db(transaction=True)
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub"))]
admin = fixtures.admin
principal = fixtures.principal
context_factory = fixtures.context_factory
context = fixtures.context
publish_context = fixtures.publish_context


def test_checkpoint_restore_keeps_later_draft_and_original_receipt(
    admin, hub_skill, user
):
    draft = get_draft(skill=hub_skill, user=user)
    original = draft.files
    url = f"/model-hub/skills/{hub_skill.pk}/draft/checkpoints/"
    read = admin.get(f"/model-hub/skills/{hub_skill.pk}/draft/")
    assert read.status_code == 200, read.data
    digest = read.data["result"]["recovery_digest"]
    assert digest == draft_recovery_digest(draft)
    checkpoint = admin.post(
        url,
        {
            "revision": draft.revision,
            "expected_digest": digest,
            "reason": "manual",
            "suspend": False,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    ).data["result"]["checkpoint"]
    draft = save_draft(
        skill=hub_skill,
        user=user,
        files=[{"path": "SKILL.md", "content": "New unsent alternative"}],
        note="later",
        base_version=None,
        revision=draft.revision,
    )
    restore = url + checkpoint["id"] + "/restore/"
    key = str(uuid4())
    body = {"revision": draft.revision, "expected_digest": checkpoint["digest"]}
    result = admin.post(restore, body, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert result.status_code == 200, result.data
    receipt = result.data["result"]
    assert receipt["preserved_checkpoint_id"] is not None
    restored = get_draft(skill=hub_skill, user=user)
    assert restored.files == original
    assert restored.revision == receipt["draft_revision"]
    saved = save_draft(
        skill=hub_skill,
        user=user,
        files=[{"path": "SKILL.md", "content": "After restore"}],
        note="new",
        base_version=None,
        revision=restored.revision,
    )
    replay = admin.post(restore, body, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert replay.data == result.data
    assert get_draft(skill=hub_skill, user=user).files == saved.files
    wrong = admin.post(
        restore,
        {**body, "revision": saved.revision},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert wrong.status_code == 409
    assert wrong.data["code"] == "idempotency_conflict"


def create_binding(admin, context, version):
    response = admin.post(
        f"/model-hub/skills/contexts/{context.pk}/bindings/",
        {
            "name": "Synthetic runtime",
            "kind": "runtime",
            "selector": {"version": version.number},
            "update_mode": "new_thread",
            "profile": "synthetic-test",
            "profile_version": "1",
            "repository": None,
            "expected_report_interval_seconds": 60,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert response.status_code == 201, response.data
    return response.data["result"]


def consumer_client(principal, context, version):
    issued = issue_credential(
        principal=principal,
        idempotency_key=uuid4(),
        request={
            "purpose": "consume",
            "grants": [
                {
                    "kind": "agent_context",
                    "resource_id": str(context.pk),
                    "labels": [],
                    "version_ids": [str(version.pk)],
                }
            ],
            "binding_ids": [],
            "expires_at": timezone.now() + timedelta(days=1),
        },
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Context " + issued["secret"])
    return client, issued


def test_runtime_http_keeps_purpose_auth_and_unknown_delivery(
    admin, principal, context, publish_context
):
    version = publish_context(context).version
    binding = create_binding(admin, context, version)
    url = f"/model-hub/skills/contexts/{context.pk}/consumers/"
    observations = admin.get(url).data["result"]
    assert observations["count"] == 1
    assert observations["results"][0]["loaded"] is None
    assert observations["results"][0]["states"] == ["unknown"]
    client, issued = consumer_client(principal, context, version)
    session_url = f"/model-hub/skills/contexts/bindings/{binding['id']}/sessions/"
    body = {
        "client_session_key": "synthetic-thread",
        "incarnation_id": str(uuid4()),
        "resume_session_id": None,
        "adapter_version": "1",
        "runtime_version": "synthetic",
        "runtime_config_digest": "sha256:" + "a" * 64,
    }
    key = str(uuid4())
    denied = admin.post(session_url, body, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert denied.status_code in {401, 403}
    opened = client.post(session_url, body, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert opened.status_code == 200, opened.data
    snapshot = opened.data["result"]
    assert snapshot["version_id"] == str(version.pk)
    assert snapshot["resolution_id"]
    assert (
        client.post(session_url, body, format="json", HTTP_IDEMPOTENCY_KEY=key).data
        == opened.data
    )
    checked = client.post(snapshot["access_check_url"], {}, format="json")
    assert checked.data["result"]["digest"] == version.digest
    metadata = HubSkillContextCredential.objects.get(pk=issued["id"])
    assert metadata.last_used_at is not None
    assert client.get(url).status_code in {401, 403}
    # Resolving a session is not an installed, discovered or delivered receipt.
    after = admin.get(url).data["result"]["results"][0]
    assert after["installed"] is after["discovered"] is after["loaded"] is None
    metadata.revoked_at = timezone.now()
    metadata.save(update_fields=["revoked_at"])
    assert client.post(snapshot["access_check_url"], {}, format="json").status_code in {
        401,
        403,
    }


def test_forensic_read_is_separate_from_distribution(
    admin, principal, context, publish_context
):
    version = publish_context(context).version
    client, _ = consumer_client(principal, context, version)
    safety_url = f"/model-hub/skills/version-safety/{version.pk}/"
    denied = admin.put(
        safety_url,
        {
            "revision": 0,
            "quarantined": True,
            "reason": "Synthetic investigation",
            "replacement_version_id": None,
        },
        format="json",
    )
    assert denied.status_code == 200, denied.data
    url = (
        f"/model-hub/skills/contexts/{context.pk}/versions/{version.number}/inspection/"
    )
    inspected = admin.get(url)
    assert inspected.status_code == 200, inspected.data
    assert inspected.data["result"]["distribution_blocked"] is True
    assert inspected.data["result"]["manifest"]["digest"] == version.digest
    assert inspected.data["result"]["descriptor"]["entrypoint"] == "AGENTS.md"
    text = admin.get(url + "files/", {"path": "AGENTS.md"})
    assert text.status_code == 200, text.data
    assert (
        text.data["result"]["text"] == version.files.get(path="AGENTS.md").blob.content
    )
    assert text["Cache-Control"] == "private, no-store"
    assert client.get(url).status_code in {401, 403}
    assert client.get(url + "files/", {"path": "AGENTS.md"}).status_code in {401, 403}
    blocked = client.get(
        "/model-hub/skills/contexts/resolve/",
        {"name": context.name, "version": version.number},
    )
    assert blocked.status_code == 422, blocked.data
    assert blocked.data["code"] == "context_quarantined"
    assert admin.get(url.replace(str(context.pk), str(uuid4()))).status_code == 404
