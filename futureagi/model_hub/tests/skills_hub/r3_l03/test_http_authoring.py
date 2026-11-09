"""Public authoring journey, immutable locks and fail-closed campaign admission."""

from uuid import uuid4

import pytest

from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_v12 import HubSkillContextDependency
from model_hub.tests.skills_hub import test_i2_integration as fixtures

admin = fixtures.admin

pytestmark = pytest.mark.django_db(transaction=True)
BASE = "/model-hub/skills/contexts/"


def result(response, status=200):
    assert response.status_code == status, response.data
    return response.data["result"]


def post(client, url, data, status=200):
    return result(
        client.post(url, data, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4())), status
    )


def test_three_contexts_accept_v8_then_publish_without_following_v9(
    admin, hub_skill, publish_skill, make_skill_files
):
    for number in range(1, 9):
        publish_skill(
            hub_skill,
            files=make_skill_files(body=f"Policy {number}"),
            labels=["production"],
        )
    ids = []
    for index in range(3):
        receipt = post(
            admin,
            BASE,
            {
                "name": f"support-{index}",
                "description": "Reviewed support policy",
                "tags": [],
                "files": None,
            },
            201,
        )
        identity = receipt["context_id"]
        ids.append(identity)
        root = f"{BASE}{identity}/"
        draft = result(admin.get(root + "draft/"))
        links = [{"skill_id": str(hub_skill.pk), "selector": {"label": "production"}}]
        body = {"revision": draft["revision"], "links": links, "refresh_skill_ids": []}
        preview = post(admin, root + "dependencies/preview/", body)
        assert preview["resolved_lock"]["links"][0]["version"] == 8
        accepted = post(
            admin,
            root + "dependencies/apply/",
            {**body, "expected_fingerprint": preview["fingerprint"]},
        )
        assert accepted["revision"] == draft["revision"] + 1
    publish_skill(
        hub_skill, files=make_skill_files(body="Policy 9"), labels=["production"]
    )
    for identity in ids:
        root = f"{BASE}{identity}/"
        draft = result(admin.get(root + "draft/"))
        saved = result(
            admin.put(
                root + "draft/",
                {
                    "revision": draft["revision"],
                    "base_version": None,
                    "files": [
                        {"path": "AGENTS.md", "content": "Reviewed instructions"}
                    ],
                    "description": "Retain explicitly accepted policy",
                    "note": "Reviewed",
                },
                format="json",
            )
        )
        assert saved["resolved_lock"]["links"][0]["version"] == 8
        review = post(
            admin, root + "draft/head-review/", {"revision": saved["revision"]}
        )
        ack = {
            "revision": saved["revision"],
            "expected_digest": saved["digest"],
            "expected_head_version": review["expected_head_version"],
            "review_fingerprint": review["review_fingerprint"],
        }
        acknowledged = post(admin, root + "draft/acknowledge-head/", ack)
        post(
            admin,
            root + "draft/publish/",
            {
                **ack,
                "revision": acknowledged["revision"],
                "review_fingerprint": acknowledged["review_fingerprint"],
                "base_version": None,
                "note": "Deliberately published",
                "acknowledge_warnings": True,
            },
            201,
        )
        context = HubSkill.no_workspace_objects.get(pk=identity)
        assert context.latest_version_number == 1
        assert not context.labels.exists()
        assert (
            HubSkillContextDependency.objects.get(
                parent_version__skill=context
            ).child_version.number
            == 8
        )
        detail = result(admin.get(root))
        assert detail["context"]["kind"] == "agent_context"
        assert detail["own_draft"] is None
        assert result(admin.get(root + "versions/"))["count"] == 1
        assert result(admin.get(root + "versions/1/"))["number"] == 1
    assert result(admin.get(BASE))["count"] == 3


def test_http_authoring_rejects_unknown_fields_leaf_ids_and_stale_discard(
    admin, hub_skill
):
    response = admin.post(
        BASE,
        {
            "name": "wrong",
            "description": "Purpose",
            "tags": [],
            "files": None,
            "published": True,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert response.status_code == 400
    assert admin.get(f"{BASE}{hub_skill.pk}/").status_code == 404
    receipt = post(
        admin,
        BASE,
        {"name": "owned", "description": "Purpose", "tags": [], "files": None},
        201,
    )
    root = f"{BASE}{receipt['context_id']}/"
    assert (
        admin.delete(root + "draft/", {"revision": 999}, format="json").status_code
        == 409
    )
    assert (
        admin.delete(
            root + "draft/", {"revision": receipt["revision"]}, format="json"
        ).status_code
        == 204
    )
    started = post(admin, root + "draft/start/", {"revision": 0, "from_version": None})
    assert started["revision"] > receipt["revision"]


def test_campaign_collection_is_mounted_but_execution_stays_disabled(admin, settings):
    settings.SKILLS_CONTEXT_CAMPAIGNS_ENABLED = False
    assert result(admin.get(BASE + "capabilities/")) == {"campaigns_enabled": False}
    assert result(admin.get(BASE + "campaigns/"))["results"] == []
    response = admin.post(
        BASE + "campaigns/",
        {
            "name": "Blocked",
            "targets": [{"binding_id": str(uuid4()), "version": 1}],
            "parent_campaign_id": None,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert response.status_code == 503
    assert response.data["code"] == "skills_unavailable"


def test_context_scope_does_not_leak_foreign_identity(
    admin, context, hub_other_workspace
):
    HubSkill.no_workspace_objects.filter(pk=context.pk).update(
        workspace=hub_other_workspace
    )
    assert admin.get(f"{BASE}{context.pk}/").status_code == 404
    assert result(admin.get(BASE))["count"] == 0
