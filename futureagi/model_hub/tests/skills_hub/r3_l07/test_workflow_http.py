"""Real authenticated HTTP for retained bytes, CAS review and redacted capture."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import OrgApiKey
from model_hub.models.skills_hub import HubSkillDraft
from model_hub.models.skills_hub_r3 import (
    HubSkillEvidenceAnnotation,
    HubSkillIntakePlan,
)
from model_hub.models.skills_hub_v12 import HubSkillContextCapture
from model_hub.services.skills_hub.drafts import discard_draft
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.tests.skills_hub.r3_l02.test_intake import (
    accept,
    bind_publish,
    source_plan,
)
from model_hub.tests.skills_hub.r3_l05.test_release_requests import text_run
from model_hub.tests.skills_hub.r3_l06 import conftest as capture_fixtures
from model_hub.tests.skills_hub.r3_l06 import test_capture as capture_tests
from model_hub.tests.skills_hub.r3_l07 import test_http_contract as http_fixtures

pytestmark = pytest.mark.django_db
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub"))]
BASE = "/model-hub/skills/"
admin = http_fixtures.admin
principal = capture_fixtures.principal
context = capture_fixtures.context
trace = capture_tests.trace


@pytest.fixture(autouse=True)
def routes(settings):
    settings.ROOT_URLCONF = __name__
    settings.MIDDLEWARE = [
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
        *settings.MIDDLEWARE,
    ]


@pytest.fixture
def other_scope(user, organization, hub_other_workspace):
    key = OrgApiKey.objects.create(
        name="Other workflow scope",
        type="user",
        user=user,
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        organization=organization,
        workspace=hub_other_workspace,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


def preview_intake(admin, make_skill_files):
    content = make_skill_files(name="retained-http")[0]["content"]
    response = admin.post(
        BASE + "intake/preview/",
        {"files": [SimpleUploadedFile("SKILL.md", content.encode())]},
        format="multipart",
    )
    assert response.status_code == 200, response.data
    assert response["Cache-Control"] == "private, no-store"
    return response.data["result"], content


def decision(plan):
    item = plan["inventory"]["items"][0]
    return {
        "item_key": item["key"],
        "expected_fingerprint": plan["fingerprint"],
        "expected_head_version": item["expected_head_version"],
        "expected_head_digest": item["expected_head_digest"],
        "decision": "replace",
        "mode": "snapshot",
        "rights_confirmed": True,
    }


def test_retained_intake_exact_bytes_replay_expiry_and_scope(
    admin, other_scope, make_skill_files
):
    plan, content = preview_intake(admin, make_skill_files)
    url = BASE + f"intake/{plan['id']}/"
    assert "payload" not in plan
    assert "content" not in plan["inventory"]["items"][0]["files"][0]
    assert admin.get(url).data["result"] == plan
    assert other_scope.get(url).status_code == 404
    body, key = decision(plan), str(uuid4())
    assert admin.post(url + "accept/", body, format="json").status_code == 428
    assert (
        other_scope.post(
            url + "accept/", body, format="json", HTTP_IDEMPOTENCY_KEY=key
        ).status_code
        == 404
    )
    response = admin.post(
        url + "accept/", body, format="json", HTTP_IDEMPOTENCY_KEY=key
    )
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["published"] is False
    draft = HubSkillDraft.objects.get(skill_id=result["resource_id"])
    assert draft.files == [{"path": "SKILL.md", "content": content}]
    HubSkillIntakePlan.objects.filter(pk=plan["id"]).update(
        payload=None, expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert admin.get(url).status_code == 410
    assert (
        admin.post(url + "accept/", body, format="json", HTTP_IDEMPOTENCY_KEY=key).data
        == response.data
    )
    changed = admin.post(
        url + "accept/",
        {**body, "decision": "keep"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert changed.status_code == 409
    receipts = admin.get(url + "receipts/").data["result"]
    assert receipts["count"] == 1 and receipts["results"][0]["result"] == result
    assert other_scope.get(url + "receipts/").status_code == 404


def test_intake_rejects_fresh_bytes_and_stale_fingerprint(admin, make_skill_files):
    plan, _ = preview_intake(admin, make_skill_files)
    url = BASE + f"intake/{plan['id']}/accept/"
    body = decision(plan)
    headers = {"HTTP_IDEMPOTENCY_KEY": str(uuid4())}
    assert (
        admin.post(url, {**body, "files": []}, format="json", **headers).status_code
        == 400
    )
    assert (
        admin.post(
            url,
            {**body, "expected_fingerprint": "sha256:" + "0" * 64},
            format="json",
            **headers,
        ).status_code
        == 409
    )
    assert (
        admin.post(
            url, {**body, "rights_confirmed": False}, format="json", **headers
        ).status_code
        == 422
    )


def test_annotations_append_cas_scope_and_expiry(
    admin, other_scope, principal, hub_skill, publish_skill
):
    run = text_run(principal, hub_skill, publish_skill(hub_skill).version)
    base = BASE + f"evidence/skills_text/{run.pk}/"
    url = base + "annotations/"
    assert admin.get(base).data["result"]["annotation_revision"] == 0
    body = {
        "annotation_revision": 0,
        "verdict": "disagree",
        "attempt_id": None,
        "check_id": None,
        "cited_text": "",
        "comment": "Review this result.",
    }
    assert other_scope.get(url).status_code == 404
    assert other_scope.post(url, body, format="json").status_code == 404
    assert (
        admin.post(url, {**body, "actor_id": str(uuid4())}, format="json").status_code
        == 400
    )
    response = admin.post(url, body, format="json")
    assert response.status_code == 201, response.data
    assert response.data["result"]["actor_id"] == str(principal.user_id)
    assert response.data["result"]["revision"] == 1
    stale = admin.post(url, body, format="json")
    assert stale.status_code == 409 and stale.data["code"] == "annotation_conflict"
    assert HubSkillEvidenceAnnotation.objects.filter(source_id=run.pk).count() == 1
    assert admin.get(base).data["result"]["annotation_revision"] == 1
    assert admin.get(url).data["result"]["count"] == 1
    assert admin.get(url, {"page_size": 101}).status_code == 400
    run.payload_expires_at = timezone.now() - timedelta(seconds=1)
    run.save(update_fields=["payload_expires_at"])
    assert admin.get(url).status_code == 410
    assert (
        admin.post(url, {**body, "annotation_revision": 1}, format="json").status_code
        == 410
    )


def test_trace_capture_http_redacts_then_accepts_once(
    admin, other_scope, principal, context, trace, hub_skill
):
    ref = trace[0]
    source_url = BASE + f"contexts/{context.pk}/captures/source/"
    assert (
        other_scope.post(source_url, {"trace_ref": ref}, format="json").status_code
        == 404
    )
    source_response = admin.post(source_url, {"trace_ref": ref}, format="json")
    assert source_response.status_code == 200, source_response.data
    source = source_response.data["result"]
    assert set(source) == {"trace_ref", "source_fingerprint", "segments"}
    assert "Private system" not in str(source) and "Failed answer" not in str(source)
    assert HubSkillContextCapture.objects.count() == 0
    body = {"trace_ref": ref, "selected_segment_ids": [source["segments"][0]["id"]]}
    preview_url = BASE + f"contexts/{context.pk}/captures/preview/"
    assert other_scope.post(preview_url, body, format="json").status_code == 404
    assert (
        admin.post(
            BASE + f"contexts/{hub_skill.pk}/captures/preview/", body, format="json"
        ).status_code
        == 404
    )
    response = admin.post(preview_url, body, format="json")
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["replay_scope"] == "text_only"
    assert "Private system" not in str(result) and "Failed answer" not in str(result)
    accept_url = BASE + f"contexts/{context.pk}/captures/{result['capture_id']}/accept/"
    accept = {
        "source_fingerprint": result["source_fingerprint"],
        "suite_revision": 0,
        "case": capture_tests.case_input(),
        "acknowledge_workspace_sharing": True,
    }
    key = str(uuid4())
    assert admin.post(accept_url, accept, format="json").status_code == 428
    assert (
        other_scope.post(
            accept_url, accept, format="json", HTTP_IDEMPOTENCY_KEY=key
        ).status_code
        == 404
    )
    denied = admin.post(
        accept_url,
        {**accept, "acknowledge_workspace_sharing": False},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert denied.status_code == 422
    saved = admin.post(accept_url, accept, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert saved.status_code == 200, saved.data
    assert saved.data["result"]["revision"] == 1
    assert saved.data["result"]["cases"][0]["prompt"] == accept["case"]["prompt"]
    assert (
        HubSkillContextCapture.objects.get(pk=result["capture_id"]).selected_text
        is None
    )
    assert (
        admin.post(accept_url, accept, format="json", HTTP_IDEMPOTENCY_KEY=key).data
        == saved.data
    )
    assert (
        admin.post(
            accept_url,
            {**accept, "suite_revision": 1},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        ).status_code
        == 409
    )


@pytest.mark.parametrize("choice", ["keep", "manual"])
def test_mapped_intake_keep_or_reviewed_manual_plan(
    admin, principal, make_skill_files, publish_skill, hub_actor, choice
):
    original, identity = source_plan(principal, make_skill_files())
    skill, _, _ = bind_publish(principal, accept(principal, original), publish_skill)
    discard_draft(skill=skill, user=hub_actor.user)
    files = make_skill_files(body="Source revision two")
    source = {**identity, "revision": "2", "digest": manifest_digest(files)}
    response = admin.post(
        BASE + "intake/preview/",
        {
            "files": [SimpleUploadedFile("SKILL.md", files[0]["content"].encode())],
            "source_identities": json.dumps(
                {original.inventory["items"][0]["key"]: source}
            ),
        },
        format="multipart",
    )
    assert response.status_code == 200, response.data
    plan = response.data["result"]
    assert plan["inventory"]["items"][0]["destination_id"] == str(skill.pk)
    manual_files = make_skill_files(body="Reviewed combined instructions")
    if choice == "manual":
        reconciled = admin.post(
            BASE + f"intake/{plan['id']}/reconcile/",
            {
                "item_key": plan["inventory"]["items"][0]["key"],
                "expected_fingerprint": plan["fingerprint"],
                "files": manual_files,
            },
            format="json",
        )
        assert reconciled.status_code == 200, reconciled.data
        plan = reconciled.data["result"]
        assert plan["inventory"]["items"][0]["manual_reconciliation"] is True
    response = admin.post(
        BASE + f"intake/{plan['id']}/accept/",
        {**decision(plan), "decision": choice},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert response.status_code == 200, response.data
    assert response.data["result"]["decision"] == choice
    if choice == "keep":
        assert response.data["result"]["draft_revision"] is None
    else:
        assert HubSkillDraft.objects.get(skill=skill).files == manual_files


def test_private_plan_and_private_evidence_never_leak_to_workspace_peer(
    admin, principal, make_skill_files, hub_other_user, hub_skill, publish_skill
):
    plan, _ = preview_intake(admin, make_skill_files)
    HubSkillIntakePlan.objects.filter(pk=plan["id"]).update(owner_id=hub_other_user.pk)
    assert admin.get(BASE + f"intake/{plan['id']}/").status_code == 404
    assert admin.get(BASE + f"intake/{plan['id']}/receipts/").status_code == 404
    run = text_run(principal, hub_skill, publish_skill(hub_skill).version)
    run.visibility = "private"
    run.save(update_fields=["visibility"])
    run.job.owner_user_id = hub_other_user.pk
    run.job.save(update_fields=["owner_user_id"])
    assert (
        admin.get(BASE + f"evidence/skills_text/{run.pk}/annotations/").status_code
        == 404
    )


@pytest.mark.parametrize(
    "suffix",
    [
        "intake/preview/",
        "intake/{id}/accept/",
        "contexts/{id}/captures/preview/",
        "evidence/skills_text/{id}/annotations/",
    ],
)
def test_anonymous_workflow_writes_are_denied(suffix):
    response = APIClient().post(BASE + suffix.format(id=uuid4()), {}, format="json")
    assert response.status_code in {401, 403}
