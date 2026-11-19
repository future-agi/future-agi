"""Integration regressions use real principals, persistence and the shared worker."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import OrgApiKey
from model_hub.models.skills_hub_r3 import (
    HubSkillDraftCheckpoint,
    HubSkillSpendReservation,
)
from model_hub.models.skills_hub_v11 import HubSkillJob, HubSkillTestEvidence
from model_hub.serializers.skills_hub_v12 import ContextReleaseState
from model_hub.services.skills_hub.authoring_foundation import save_owned_draft
from model_hub.services.skills_hub.drafts import save_draft
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.labels import set_label
from model_hub.services.skills_hub.r3.spend_policy import spend_summary
from model_hub.services.skills_hub.v11 import jobs, runner
from model_hub.services.skills_hub.v11.test_runs import share_test_run
from model_hub.services.skills_hub.v11.types import CompletionResult
from model_hub.services.skills_hub.v12.releases import context_release_state
from model_hub.tests.skills_hub import test_v11_jobs as execution_fixtures
from model_hub.tests.skills_hub.r3_l03 import conftest as context_fixtures

pytestmark = pytest.mark.django_db(transaction=True)
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub"))]
execution = execution_fixtures.execution
principal = context_fixtures.principal
context_factory = context_fixtures.context_factory
context = context_fixtures.context
publish_context = context_fixtures.publish_context


@pytest.fixture
def admin(user, organization, workspace, settings):
    settings.ROOT_URLCONF = __name__
    settings.SKILLS_CONTEXT_CONSUMPTION_ENABLED = True
    settings.MIDDLEWARE = [
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
        *settings.MIDDLEWARE,
    ]
    key = OrgApiKey.objects.create(
        name="Synthetic integrated actor",
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


def test_legacy_destructive_save_checkpoints_original_bytes(
    hub_skill, user, make_skill_files
):
    draft = hub_skill.drafts.get(user=user)
    original = make_skill_files(
        extra=[{"path": "policy.md", "content": "Keep this policy."}]
    )
    draft = save_draft(
        skill=hub_skill,
        user=user,
        files=original,
        note="before",
        base_version=None,
        revision=draft.revision,
    )
    save_draft(
        skill=hub_skill,
        user=user,
        files=original[:1],
        note="after",
        base_version=None,
        revision=draft.revision,
    )
    checkpoint = HubSkillDraftCheckpoint.objects.get(skill=hub_skill)
    assert checkpoint.payload is not None
    assert checkpoint.payload["files"] == original
    assert checkpoint.payload is not None
    assert checkpoint.payload["note"] == "before"
    assert checkpoint.owner_id == user.pk and checkpoint.reason == "deletion"


def test_safe_save_checkpoints_old_purpose_not_replacement(principal, context):
    draft = context.drafts.get(user_id=principal.user_id)
    draft.files.append({"path": "policy.md", "content": "Retain me."})
    draft.save(update_fields=["files"])
    old_purpose = draft.context_spec["description"]
    save_owned_draft(
        principal=principal,
        resource_id=context.pk,
        kind="agent_context",
        revision=draft.revision,
        base_version=None,
        files=draft.files[:1],
        note="new",
        description="New purpose",
    )
    checkpoint = HubSkillDraftCheckpoint.objects.get(skill=context)
    assert checkpoint.payload is not None
    assert checkpoint.payload["context_spec"]["description"] == old_purpose
    assert checkpoint.payload is not None
    assert checkpoint.payload["files"][1]["content"] == "Retain me."


def test_checkpoint_capacity_rolls_back_destructive_save(
    hub_skill, user, make_skill_files
):
    from django.db import transaction

    from model_hub.services.skills_hub.r3.draft_recovery import checkpoint_draft_locked
    from model_hub.services.skills_hub.types import Actor
    from model_hub.services.skills_hub.v11.governance import principal_for_actor

    draft = hub_skill.drafts.get(user=user)
    draft.files = make_skill_files(extra=[{"path": "keep.md", "content": "keep"}])
    draft.save(update_fields=["files"])
    with transaction.atomic():
        for _ in range(20):
            checkpoint_draft_locked(
                principal=principal_for_actor(skill=hub_skill, actor=Actor(user, None)),
                skill=hub_skill,
                draft=draft,
                reason="manual",
            )
    with pytest.raises(SkillsHubError, match="20 retained"):
        save_draft(
            skill=hub_skill,
            user=user,
            files=draft.files[:1],
            note="",
            base_version=None,
            revision=draft.revision,
        )
    draft.refresh_from_db()
    assert draft.files[-1]["path"] == "keep.md"


def test_release_state_serializes_real_label_actor(principal, context, publish_context):
    from model_hub.models.skills_hub import HubSkillLabel

    version = publish_context(context).version
    HubSkillLabel.objects.create(
        skill=context, name="staging", version=version, updated_by_id=principal.user_id
    )
    result = ContextReleaseState(
        context_release_state(principal=principal, context_id=context.pk)
    ).data
    assert result["labels"][0]["updated_by"]["id"] == str(principal.user_id)
    assert result["labels"][0]["version_id"] == str(version.pk)


@pytest.mark.parametrize(
    ("enabled", "limit", "code"),
    [(False, "100", "spend_policy_required"), (True, "1", "budget_exhausted")],
)
def test_new_job_enforces_explicit_policy_and_rolls_back(
    execution, workspace_spend_policy, enabled, limit, code
):
    workspace_spend_policy.enabled = enabled
    workspace_spend_policy.period_limit_usd = Decimal(limit)
    workspace_spend_policy.save(update_fields=["enabled", "period_limit_usd"])
    with pytest.raises(SkillsHubError) as error:
        execution_fixtures.accepted(execution)
    assert error.value.code == code
    assert not HubSkillJob.objects.exists()
    assert not execution[3].called


@pytest.mark.parametrize("explicit_policy", [False, True])
def test_actual_worker_calls_share_one_admission_and_release_unused(
    execution, workspace_spend_policy, explicit_policy
):
    if not explicit_policy:
        workspace_spend_policy.delete()
    run, _, _ = execution_fixtures.accepted(execution)
    scope = execution[0].scope
    root = HubSkillSpendReservation.objects.get(operation_id=run.pk)
    assert root.source_id == run.pk and root.reserved_usd == Decimal("2")
    jobs.start_job(scope=scope, job_id=run.pk)
    attempt = run.hub_skill_attempts.first()
    attempt.status = "running"
    attempt.save(update_fields=["status"])
    call = jobs.reserve_call(
        job_id=run.pk,
        attempt_id=attempt.pk,
        kind="task",
        request_digest="synthetic",
        token_allowance=10,
        estimated_allowance=Decimal("0.2"),
    )
    jobs.mark_in_flight(
        call_id=call.pk,
        generation=call.claim_generation,
        connection=run.snapshot["connections"]["task"],
    )
    assert spend_summary(principal=execution[0])["active_senders"] == 1
    jobs.settle_call(
        call_id=call.pk,
        generation=call.claim_generation,
        result=None,
        error={"code": "synthetic_timeout"},
    )
    row = HubSkillSpendReservation.objects.get(operation_id=call.pk)
    assert row.state == "unknown" and not row.sender_active
    jobs.finish_job(scope=scope, job_id=run.pk, failure_code="execution_unknown")
    assert Decimal(spend_summary(principal=execution[0])["committed_usd"]) == Decimal(
        "0.2"
    )


def test_legacy_move_records_pre_move_baseline(execution, publish_skill, hub_actor):
    principal, skill, config, _ = execution
    baseline = publish_skill(skill, labels=["production"]).version
    candidate = publish_skill(
        skill,
        files=[
            {**file, "content": file["content"] + "\nNew instruction."}
            for file in skill.drafts.get(user_id=principal.user_id).files
        ],
    ).version
    config["candidate"] = {
        "kind": "version",
        "version": candidate.number,
        "digest": candidate.digest,
    }
    config["baselines"] = ["production"]
    run, _, _ = execution_fixtures.accepted(execution)
    jobs.finish_job(scope=principal.scope, job_id=run.pk)
    share_test_run(
        principal=principal, run_id=run.pk, acknowledge_snapshot_sharing=True
    )
    set_label(
        skill=skill,
        actor=hub_actor,
        name="production",
        version_number=candidate.number,
        expected_version=baseline.number,
        evidence_run_id=run.pk,
    )
    evidence = HubSkillTestEvidence.objects.get(run=run)
    assert evidence.currentness_at_action["baseline"] is True
    assert evidence.label_event is not None
    assert evidence.label_event.to_version == candidate.number


def test_checkpoint_http_binds_real_owner_and_original_operation(
    admin, hub_skill, user
):
    from model_hub.services.skills_hub.r3.draft_recovery import draft_recovery_digest

    draft = hub_skill.drafts.get(user=user)
    url = f"/model-hub/skills/{hub_skill.pk}/draft/checkpoints/"
    body = {
        "revision": draft.revision,
        "expected_digest": draft_recovery_digest(draft),
        "reason": "manual",
        "suspend": False,
    }
    key = str(uuid4())
    first = admin.post(url, body, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert first.status_code == 200, first.data
    second = admin.post(url, body, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert second.data == first.data
    assert admin.get(url).data["result"]["count"] == 1
    assert HubSkillDraftCheckpoint.objects.get().owner_id == user.pk


def test_spend_http_requires_key_and_returns_explicit_decimals(admin):
    url = "/model-hub/skills/spend-policy/"
    assert admin.get(url).data["result"]["period_limit_usd"] is None
    body = {"revision": 0, "enabled": True, "period_limit_usd": "3.00000000"}
    assert admin.put(url, body, format="json").status_code == 428
    saved = admin.put(url, body, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4()))
    assert saved.status_code == 200, saved.data
    assert saved.data["result"]["remaining_usd"] == "3.00000000"


def test_context_worker_preserves_reference_and_records_actual_root(
    execution, context, publish_context, monkeypatch
):
    from model_hub.services.skills_hub.v12 import testing

    principal, _, config, _ = execution
    version = publish_context(context).version
    configuration = {
        **config,
        "candidate": {
            "kind": "version",
            "version": version.number,
            "digest": version.digest,
        },
        "baselines": [{"kind": "without_context"}],
        "harness": testing.HARNESS_VERSION,
    }
    configuration["cases"]["cases"][0]["expected_trigger"] = None
    monkeypatch.setattr(
        runner, "SUPPORTED_HARNESSES", (testing.HARNESS_VERSION,), raising=False
    )
    monkeypatch.setattr(testing, "dispatch_job", lambda **kwargs: None)
    preview = testing.preview_context_test(
        principal=principal, context_id=context.pk, configuration=configuration
    )
    request = {
        "configuration": configuration,
        "expected_fingerprint": preview["fingerprint"],
        "acknowledge_provider_processing": True,
        "inventory_review": {
            "fingerprint": preview["fingerprint"],
            "reviewed_all_files": True,
            "no_known_answer_keys": True,
            "classifications": [],
            "false_positive_secret_findings": [],
        },
    }
    run = testing.create_context_test(
        principal=principal,
        context_id=context.pk,
        request=request,
        idempotency_key=uuid4(),
    )
    transport = AsyncMock(
        return_value=CompletionResult(
            assistant_message={"role": "assistant", "content": '{"refund":20}'},
            finish_reason="stop",
            provider_request_id="synthetic",
            returned_model="gpt-4o-mini",
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            latency_ms=1,
            estimated_cost_usd=Decimal("0.00001"),
            pricing_source="synthetic",
        )
    )
    monkeypatch.setattr(
        "model_hub.services.skills_hub.v11.model_calls.complete_skill_turn", transport
    )
    jobs.start_job(scope=principal.scope, job_id=run.pk)
    for attempt in run.hub_skill_attempts.all():
        asyncio.run(
            runner.execute_test_attempt(scope=principal.scope, attempt_id=attempt.pk)
        )
        attempt.refresh_from_db()
        assert attempt.status == "completed", attempt.error
        assert (
            attempt.answer_verdict == "pass"
            and attempt.routing_verdict == "not_applicable"
        )
        if attempt.variant == "candidate":
            assert attempt.receipts[0]["path"] == "AGENTS.md"
            assert attempt.receipts[0]["tool_call_id"] == ""
            assert attempt.root_fully_delivered is True
        else:
            assert attempt.receipts == []
    assert transport.await_count == 2
    assert (
        "AGENTS.md" in transport.await_args_list[0].kwargs["messages"][1]["content"]
        or "AGENTS.md" in transport.await_args_list[1].kwargs["messages"][1]["content"]
    )


def test_retention_invokes_checkpoint_purge(hub_skill, user):
    row = HubSkillDraftCheckpoint.objects.create(
        organization_id=hub_skill.organization_id,
        workspace_id=hub_skill.workspace_id,
        skill=hub_skill,
        owner=user,
        draft_revision=1,
        reason="manual",
        digest="sha256:" + "a" * 64,
        payload={"files": [{"path": "private.md", "content": "private synthetic"}]},
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    result = jobs.purge_expired_payloads()
    row.refresh_from_db()
    assert result["checkpoints"] == 1 and row.payload is None
