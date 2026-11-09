"""Independent author/reviewer public handoff including seen and replay receipts."""

from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from accounts.models import OrgApiKey
from model_hub.tests.skills_hub import test_i2_integration as http_fixtures
from model_hub.tests.skills_hub.r3_l05 import test_release_requests as fixtures

admin = http_fixtures.admin
actors = fixtures.actors
candidate = fixtures.candidate
pytestmark = pytest.mark.django_db(transaction=True)
BASE = "/model-hub/skills/"


def result(response, status=200):
    assert response.status_code == status, response.data
    return response.data["result"]


def post(client, path, body, status=200, key=None):
    return result(
        client.post(
            BASE + path, body, format="json", HTTP_IDEMPOTENCY_KEY=str(key or uuid4())
        ),
        status,
    )


def test_independent_review_execute_and_receipt_over_http(
    admin, actors, candidate, hub_skill, organization, workspace
):
    _, reviewer = actors
    key = OrgApiKey.objects.create(
        name="HTTP reviewer",
        type="user",
        user_id=reviewer.user_id,
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        organization=organization,
        workspace=workspace,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    old, new = candidate
    run = fixtures.text_run(actors[0], hub_skill, new, baseline=old)
    preview = post(
        admin,
        "release-requests/preview/",
        {
            "resource_id": str(hub_skill.pk),
            "kind": "skill",
            "candidate_version_id": str(new.pk),
            "label": "production",
            "evidence": [
                {
                    "source": "skills_text",
                    "source_id": str(run.pk),
                    "annotation_revision": 0,
                }
            ],
        },
    )
    submitted = post(
        admin,
        "release-requests/",
        {
            "packet": preview["packet"],
            "expected_fingerprint": preview["fingerprint"],
            "recipient_ids": [str(reviewer.user_id)],
        },
        201,
    )
    request_id = submitted["id"]
    inbox = result(client.get(BASE + "review-inbox/"))
    assert inbox["unread_count"] == 1
    assert inbox["results"][0]["id"] == request_id
    detail = result(client.get(BASE + f"release-requests/{request_id}/"))
    assert detail["packet"] == preview["packet"]
    assert (
        client.post(
            BASE + f"review-inbox/{request_id}/seen/",
            {"revision": submitted["revision"]},
            format="json",
        ).status_code
        == 204
    )
    assert result(client.get(BASE + "review-inbox/"))["unread_count"] == 0
    body = {
        "revision": submitted["revision"],
        "expected_fingerprint": preview["fingerprint"],
    }
    own = admin.post(
        BASE + f"release-requests/{request_id}/decisions/",
        {**body, "decision": "approve", "comment": "Self"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert own.status_code in {403, 404}
    decision = post(
        client,
        f"release-requests/{request_id}/decisions/",
        {**body, "decision": "approve", "comment": "Independently reviewed"},
    )
    assert decision["status"] == "approved"
    stale = admin.post(
        BASE + f"release-requests/{request_id}/execute/",
        {**body, "expected_fingerprint": "sha256:" + "0" * 64},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert stale.status_code == 409
    operation = uuid4()
    executed = post(
        admin, f"release-requests/{request_id}/execute/", body, key=operation
    )
    assert executed["post_move_state"]["version"] == new.number
    freshness = executed["currentness_at_action"]["evidence"][0]["currentness"]
    assert freshness["baseline"] is True
    assert freshness["suite"] is None
    assert freshness["competitors"] is None
    assert (
        post(admin, f"release-requests/{request_id}/execute/", body, key=operation)
        == executed
    )
    assert (
        result(admin.get(BASE + f"release-requests/{request_id}/receipt/")) == executed
    )
    assert (
        result(client.get(BASE + "review-inbox/"))["results"][0]["status"] == "released"
    )
