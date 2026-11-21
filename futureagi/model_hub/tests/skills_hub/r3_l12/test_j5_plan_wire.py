"""Exact SDK diff bytes must survive the public serializer's digest boundary."""

import pytest

from model_hub.serializers import skills_context_authoring as schemas
from model_hub.services.skills_hub.v12.campaigns import target_plan_digest
from model_hub.tests.skills_hub.r3_l12 import test_campaigns as fixtures
from model_hub.tests.skills_hub.r3_l12 import test_http_campaigns as http

admin = http.admin
actor = fixtures.actor
release = fixtures.release
binding = fixtures.binding
enabled = fixtures.enabled
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "preview",
    [
        "--- AGENTS.md\n+++ AGENTS.md\n@@ -1 +1 @@\n local policy\n+managed policy\n",
        "  retained leading spaces\n\tretained trailing tab\t\n",
        "\n",
    ],
)
def test_public_campaign_plan_retains_exact_review_whitespace(
    admin, enabled, binding, release, preview
):
    campaign = http.post(
        admin,
        http.ROOT,
        {
            "name": "Synthetic exact preview",
            "targets": [{"binding_id": str(binding.pk), "version": release.number}],
            "parent_campaign_id": None,
        },
        status=201,
    )
    plan = fixtures.plan(binding, release)
    plan["changes"][0]["diff_preview"] = preview
    plan["plan_digest"] = target_plan_digest(plan)
    root = http.ROOT + campaign["id"] + "/"
    recorded = http.result(
        admin.put(root + f"targets/{binding.pk}/plan/", plan, format="json"),
        schemas.PlanRecorded,
    )
    assert recorded["plan_digest"] == plan["plan_digest"]
    observed = http.result(admin.get(root + "targets/"), schemas.CampaignTargets)
    retained = observed["results"][0]["plan"]
    assert retained["changes"][0]["diff_preview"] == preview
    assert target_plan_digest(retained) == plan["plan_digest"]
