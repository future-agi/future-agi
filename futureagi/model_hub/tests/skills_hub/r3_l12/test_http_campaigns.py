"""Synthetic public campaign contract; never enables production repository writes."""

from uuid import uuid4

import pytest

from model_hub.serializers import skills_context_authoring as schemas
from model_hub.tests.skills_hub import test_i2_integration as http_fixtures
from model_hub.tests.skills_hub.r3_l12 import test_campaigns as fixtures

admin = http_fixtures.admin
actor = fixtures.actor
release = fixtures.release
binding = fixtures.binding
enabled = fixtures.enabled
pytestmark = pytest.mark.django_db(transaction=True)
ROOT = "/model-hub/skills/contexts/campaigns/"


def result(response, schema=None, status=200):
    assert response.status_code == status, response.data
    value = response.data["result"]
    if schema:
        output = schema(data=value)
        assert output.is_valid(), output.errors
    return value


def post(client, path, body, schema=None, status=200):
    return result(
        client.post(path, body, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4())),
        schema,
        status,
    )


def test_public_plan_authorize_claim_and_observations_remain_independent(
    admin, enabled, binding, release
):
    campaign = post(
        admin,
        ROOT,
        {
            "name": "Synthetic rollout",
            "targets": [{"binding_id": str(binding.pk), "version": release.number}],
            "parent_campaign_id": None,
        },
        schemas.Campaign,
        201,
    )
    root = ROOT + campaign["id"] + "/"
    result(admin.get(ROOT), schemas.CampaignPage)
    result(admin.get(root), schemas.CampaignDetail)
    unplanned = result(admin.get(root + "targets/"), schemas.CampaignTargets)
    assert unplanned["results"][0]["plan"] is None
    assert unplanned["results"][0]["plan_digest"] is None
    target = root + f"targets/{binding.pk}/"
    plan = fixtures.plan(binding, release)
    recorded = result(
        admin.put(target + "plan/", plan, format="json"), schemas.PlanRecorded
    )
    detail = result(admin.get(root), schemas.CampaignDetail)
    authorized = post(
        admin,
        root + "authorize/",
        {
            "revision": recorded["revision"],
            "expected_manifest_digest": detail["manifest_digest"],
            "decisions": [
                {
                    "binding_id": str(binding.pk),
                    "plan_digest": plan["plan_digest"],
                    "decision": "include",
                    "reason": "Reviewed local instruction adoption",
                }
            ],
            "acknowledge_repository_writes": True,
        },
        schemas.Campaign,
    )
    assert authorized["status"] == "authorized"
    claimed = post(
        admin, target + "claim/", {"plan_digest": plan["plan_digest"]}, schemas.Claim
    )
    post(
        admin,
        target + "attempts/",
        {
            "attempt_id": claimed["attempt_id"],
            "claim_generation": claimed["claim_generation"],
            "state": "pr_open",
            "repository_commit": "b" * 40,
            "pull_request_number": 123,
            "reason": "Synthetic receipt only",
        },
        schemas.Attempt,
    )
    targets = result(admin.get(root + "targets/"), schemas.CampaignTargets)
    assert targets["results"][0]["pr_state"] == "pr_open"
    observations = result(admin.get(target + "observations/"), schemas.Observations)
    assert observations["installed"] is None
    assert observations["discovered"] is None
    assert observations["instructions_delivered"] is None
    detail = result(admin.get(root), schemas.CampaignDetail)
    rollback = post(
        admin,
        root + "rollback/preview/",
        {"revision": detail["revision"]},
        schemas.Rollback,
    )
    assert rollback["executable"] is False


def test_disabled_guard_is_preserved_on_public_plan_and_claim(
    admin, enabled, binding, release, settings
):
    campaign = post(
        admin,
        ROOT,
        {
            "name": "Synthetic disabled guard",
            "targets": [{"binding_id": str(binding.pk), "version": release.number}],
            "parent_campaign_id": None,
        },
        status=201,
    )
    target = ROOT + campaign["id"] + f"/targets/{binding.pk}/"
    settings.SKILLS_CONTEXT_CAMPAIGNS_ENABLED = False
    assert (
        admin.put(
            target + "plan/", fixtures.plan(binding, release), format="json"
        ).status_code
        == 503
    )
    assert (
        admin.post(
            target + "claim/",
            {"plan_digest": fixtures.DIGEST},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        ).status_code
        == 503
    )
