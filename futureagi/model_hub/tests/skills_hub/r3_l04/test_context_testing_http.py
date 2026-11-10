"""Public context preflight/admission cannot fall back to leaf routes or another scope."""

import asyncio
from copy import deepcopy
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from model_hub.models import HubSkillJob
from model_hub.tests.skills_hub import test_i2_integration as auth
from model_hub.tests.skills_hub.r3_l04 import test_context_plan as plan

admin = auth.admin
context = plan.context
configuration = plan.configuration
pytestmark = pytest.mark.django_db


def test_context_preview_is_public_and_freezes_historical_baseline(admin, context):
    skill, version = context
    response = admin.post(
        f"/model-hub/skills/contexts/{skill.pk}/test-runs/preview/",
        configuration(version),
        format="json",
    )
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["candidate"]["digest"] == version.digest
    assert result["reference"]["version"] == 4
    assert not HubSkillJob.objects.exists()


@pytest.mark.parametrize("fault", ["routing", "competitor", "extra"])
def test_context_preview_rejects_leaf_semantics(admin, context, fault):
    skill, version = context
    config = deepcopy(configuration(version))
    if fault == "routing":
        config["cases"]["cases"][0]["expected_trigger"] = True
    elif fault == "competitor":
        config["competitors"] = [{"skill_id": str(skill.pk), "version": 1}]
    else:
        config["unreviewed_field"] = True
    response = admin.post(
        f"/model-hub/skills/contexts/{skill.pk}/test-runs/preview/",
        config,
        format="json",
    )
    assert response.status_code == 400, response.data
    assert not HubSkillJob.objects.exists()


def test_context_test_routes_do_not_accept_leaf_resources(admin, hub_skill):
    response = admin.get(f"/model-hub/skills/contexts/{hub_skill.pk}/test-runs/")
    assert response.status_code == 404, response.data


def test_context_test_routes_hide_other_workspace(admin, context, other_workspace):
    skill, _ = context
    skill.workspace = other_workspace
    skill.save(update_fields=["workspace"])
    response = admin.get(f"/model-hub/skills/contexts/{skill.pk}/test-runs/")
    assert response.status_code == 404, response.data


def test_leaf_routes_do_not_accept_context_resources(admin, context):
    skill, version = context
    response = admin.post(
        f"/model-hub/skills/{skill.pk}/test-runs/preview/",
        configuration(version),
        format="json",
    )
    assert response.status_code == 404, response.data


def test_missing_context_run_does_not_leak_other_resources(admin, context):
    skill, _ = context
    response = admin.get(
        f"/model-hub/skills/contexts/{skill.pk}/test-runs/{uuid4()}/"
    )
    assert response.status_code == 404, response.data


@pytest.mark.django_db(transaction=True)
def test_http_admission_executes_context_protocol_and_retains_reference(
    admin, context, workspace_spend_policy, user, organization, workspace, monkeypatch
):
    from model_hub.models import HubSkillTestRun
    from model_hub.services.skills_hub.v11 import jobs, runner
    from model_hub.services.skills_hub.v11.types import (
        CompletionResult, SkillPrincipal, SkillScope,
    )
    from model_hub.services.skills_hub.v12 import testing

    skill, version = context
    root = f"/model-hub/skills/contexts/{skill.pk}/test-runs/"
    config = configuration(version)
    config["limits"]["max_total_tokens"] = 150000
    config["limits"]["max_estimated_cost_usd"] = "2.00000000"
    monkeypatch.setattr(testing, "dispatch_job", lambda **kwargs: None)
    preview = admin.post(root + "preview/", config, format="json")
    assert preview.status_code == 200, preview.data
    fingerprint = preview.data["result"]["fingerprint"]
    payload = {
        "configuration": config,
        "expected_fingerprint": fingerprint,
        "acknowledge_provider_processing": True,
        "inventory_review": {
            "fingerprint": fingerprint, "reviewed_all_files": True,
            "no_known_answer_keys": True, "classifications": [],
            "false_positive_secret_findings": [],
        },
    }
    key = str(uuid4())
    accepted = admin.post(root, payload, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert accepted.status_code == 202, accepted.data
    replay = admin.post(root, payload, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert replay.status_code == 202, replay.data
    assert replay.data["result"]["id"] == accepted.data["result"]["id"]
    assert HubSkillJob.objects.filter(skill=skill).count() == 1
    run = HubSkillTestRun.objects.get(pk=accepted.data["result"]["id"])
    transport = AsyncMock(return_value=CompletionResult(
        assistant_message={"role": "assistant", "content": "safe"},
        finish_reason="stop", provider_request_id="synthetic-context-http",
        returned_model="gpt-4o-mini",
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        latency_ms=1, estimated_cost_usd=Decimal("0.00001"), pricing_source="synthetic",
    ))
    monkeypatch.setattr(
        "model_hub.services.skills_hub.v11.model_calls.complete_skill_turn", transport
    )
    principal = SkillPrincipal(SkillScope(organization.id, workspace.id), user.id, None, "human")
    jobs.start_job(scope=principal.scope, job_id=run.pk)
    for attempt in run.hub_skill_attempts.all():
        asyncio.run(runner.execute_test_attempt(scope=principal.scope, attempt_id=attempt.pk))
        detail = admin.get(root + f"{run.pk}/attempts/{attempt.pk}/")
        assert detail.status_code == 200, detail.data
        body = detail.data["result"]
        assert body["status"] == "completed", body
        assert body["routing_verdict"] == "not_applicable"
        assert body["answer_verdict"] == "pass"
        assert body["root_fully_delivered"] is True
        assert body["receipts"][0]["path"] == "AGENTS.md"
        assert body["receipts"][0]["source_version_id"] is None
        assert body["reference"]["version"] == 4
        assert body["case"]["expected_trigger"] is None
        assert body["case"]["reviewed_by"]["id"] == str(user.pk)
        assert body["case"]["origin"] == "manual"
        assert body["case"]["exposed_to_assist"] is False
    jobs.finish_job(scope=principal.scope, job_id=run.pk)
    summary = admin.get(root + f"{run.pk}/")
    assert summary.status_code == 200, summary.data
    assert summary.data["result"]["subject_kind"] == "agent_context"
    assert summary.data["result"]["reference"]["version"] == 4
    assert summary.data["result"]["production"] is None
    result = admin.get(root + f"{run.pk}/results/")
    assert result.status_code == 200, result.data
    assert {row["variant"] for row in result.data["result"]["results"][0]["variants"]} == {"candidate", "reference"}
    assert transport.await_count == 2
    listing = admin.get(root)
    assert listing.status_code == 200, listing.data
    assert [row["id"] for row in listing.data["result"]["results"]] == [str(run.pk)]
    retained = admin.get(root + f"{run.pk}/files/", {
        "snapshot_skill_id": str(skill.pk), "variant": "reference", "path": "AGENTS.md",
    })
    assert retained.status_code == 200, retained.data
    assert retained.data["result"]["text"] == "Synthetic policy version 4."
