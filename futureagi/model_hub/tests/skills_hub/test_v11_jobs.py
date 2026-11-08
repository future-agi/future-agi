"""Durable acceptance/call lifecycle tests; every provider response is recorded."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from model_hub.models.api_key import ApiKey
from model_hub.models.skills_hub_v11 import (
    HubSkillJob,
    HubSkillModelCall,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.v11.jobs import (
    cancel_job,
    mark_in_flight,
    reconcile_jobs,
    reserve_call,
    settle_call,
    start_job,
)
from model_hub.services.skills_hub.v11.test_runs import (
    create_test_run,
    preview_test_run,
)
from model_hub.services.skills_hub.v11.types import (
    SkillPrincipal,
    SkillScope,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def execution(settings, organization, workspace, user, hub_skill, monkeypatch):
    settings.SKILLS_VERIFIED_MODELS = ["gpt-4o-mini"]
    ApiKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        provider="openai",
        key="synthetic-test-value",
    )
    principal = SkillPrincipal(
        SkillScope(organization.id, workspace.id), user.id, None, "human"
    )
    dispatch = Mock()
    monkeypatch.setattr(
        "model_hub.services.skills_hub.v11.test_runs.dispatch_job", dispatch
    )
    draft = hub_skill.drafts.get(user=user)
    item = {
        "id": str(uuid4()),
        "title": "Refund",
        "source_group_id": "refund-boundary",
        "prompt": "Return the refund JSON.",
        "fixtures": [],
        "expected_trigger": True,
        "checks": [
            {
                "id": str(uuid4()),
                "kind": "json_equals",
                "critical": True,
                "config": {"pointer": "/refund", "value": 20},
            }
        ],
        "reviewed": True,
    }
    config = {
        "candidate": {
            "kind": "draft",
            "revision": draft.revision,
            "digest": manifest_digest(draft.files),
        },
        "cases": {"kind": "ad_hoc", "cases": [item]},
        "baselines": ["without_target"],
        "competitors": [],
        "model": {
            "model_id": "gpt-4o-mini",
            "temperature": 0,
            "max_output_tokens": 100,
        },
        "judge": None,
        "profile": "quick",
        "repetitions": 1,
        "limits": {"max_total_tokens": 150000, "max_estimated_cost_usd": "2.00000000"},
        "purpose": "regression",
        "parent_run_id": None,
    }
    return principal, hub_skill, config, dispatch


def accepted(execution, *, key=None):
    principal, skill, config, _ = execution
    preview = preview_test_run(
        principal=principal, skill_id=skill.id, configuration=config
    )
    request = {
        "configuration": config,
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
    run = create_test_run(
        principal=principal,
        skill_id=skill.id,
        idempotency_key=key or uuid4(),
        request=request,
    )
    return run, request, preview


def test_review_does_not_change_plan_and_is_bound_to_execution(execution):
    run, _, preview = accepted(execution)
    assert run.plan_fingerprint == preview["fingerprint"]
    assert run.execution_fingerprint != run.plan_fingerprint
    assert run.snapshot["execution_fingerprint"] == run.execution_fingerprint
    assert len(run.snapshot["schedule"]) == run.hub_skill_attempts.count() == 2
    assert run.candidate["digest"] == run.candidate["execution_manifest_digest"]
    assert run.execution_configuration["model"] == execution[2]["model"]
    assert execution[3].call_count == 1


def test_idempotency_precedes_changed_draft_and_price(execution):
    key = uuid4()
    run, request, _ = accepted(execution, key=key)
    principal, skill, _, _ = execution
    skill.drafts.filter(user_id=principal.user_id).update(revision=99)
    ApiKey.no_workspace_objects.filter(
        workspace_id=principal.scope.workspace_id
    ).update(deleted=True)
    replay = create_test_run(
        principal=principal, skill_id=skill.id, idempotency_key=key, request=request
    )
    assert replay.pk == run.pk and HubSkillJob.objects.count() == 1
    changed = {**request, "expected_fingerprint": "different"}
    with pytest.raises(SkillsHubError) as caught:
        create_test_run(
            principal=principal, skill_id=skill.id, idempotency_key=key, request=changed
        )
    assert caught.value.code == "idempotency_conflict"


def test_rollback_dispatch_and_admission(execution):
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            accepted(execution)
            raise RuntimeError("synthetic rollback")
    assert not HubSkillJob.objects.exists() and not execution[3].called


def test_target_published_version_cannot_enter_as_competitor(execution, publish_skill):
    principal, skill, config, _ = execution
    version = publish_skill(skill).version
    config["competitors"] = [{"skill_id": str(skill.id), "version": version.number}]
    with pytest.raises(SkillsHubError) as caught:
        preview_test_run(principal=principal, skill_id=skill.id, configuration=config)
    assert caught.value.code == "invalid_test_config"
    assert not HubSkillJob.objects.exists()


def test_call_unknown_retains_spend_and_duplicate_settlement_is_safe(execution):
    run, _, _ = accepted(execution)
    scope = execution[0].scope
    start_job(scope=scope, job_id=run.pk)
    attempt = run.hub_skill_attempts.first()
    attempt.status, attempt.claim_generation = "running", 1
    attempt.save(update_fields=["status", "claim_generation"])
    call = reserve_call(
        job_id=run.pk,
        attempt_id=attempt.id,
        kind="task",
        request_digest="one",
        token_allowance=1000,
        estimated_allowance=Decimal("0.1"),
    )
    connection = run.snapshot["connections"]["task"]
    mark_in_flight(
        call_id=call.id, generation=call.claim_generation, connection=connection
    )
    error = {"code": "provider_timeout", "message": "Synthetic timeout", "detail": {}}
    settle_call(
        call_id=call.id, generation=call.claim_generation, result=None, error=error
    )
    settle_call(
        call_id=call.id, generation=call.claim_generation, result=None, error=error
    )
    job = HubSkillJob.objects.get(pk=run.pk)
    call.refresh_from_db()
    assert job.unknown_call_count == 1 and job.reserved_tokens == 1000
    assert job.reserved_estimate_usd == Decimal("0.1")
    assert call.state == "unknown" and call.finished_at is not None
    cancel_job(principal=execution[0], job_id=run.pk)
    with pytest.raises(SkillsHubError):
        reserve_call(
            job_id=run.pk,
            attempt_id=attempt.id,
            kind="task",
            request_digest="two",
            token_allowance=1000,
            estimated_allowance=Decimal("0.1"),
        )


def test_admission_limit_counts_queued_assist_and_test(execution):
    accepted(execution)
    accepted(execution)
    with pytest.raises(SkillsHubError) as caught:
        accepted(execution)
    assert (
        caught.value.code == "skills_capacity_exceeded"
        and HubSkillJob.objects.count() == 2
    )


def test_reconciler_redispatches_same_job_and_quarantines_live_sender(
    execution, monkeypatch
):
    run, _, _ = accepted(execution)
    dispatch = Mock()
    monkeypatch.setattr("model_hub.services.skills_hub.v11.jobs.dispatch_job", dispatch)
    assert reconcile_jobs()["dispatched"] == 1
    assert dispatch.call_args.kwargs["job_id"] == run.pk
    start_job(scope=execution[0].scope, job_id=run.pk)
    attempt = run.hub_skill_attempts.first()
    attempt.status, attempt.claim_generation = "running", 1
    attempt.save(update_fields=["status", "claim_generation"])
    call = reserve_call(
        job_id=run.pk,
        attempt_id=attempt.id,
        kind="task",
        request_digest="one",
        token_allowance=1000,
        estimated_allowance=Decimal("0.1"),
    )
    mark_in_flight(
        call_id=call.id, generation=1, connection=run.snapshot["connections"]["task"]
    )
    HubSkillJob.objects.filter(pk=run.pk).update(
        heartbeat_at=timezone.now() - timedelta(seconds=180)
    )
    result = reconcile_jobs()
    job = HubSkillJob.objects.get(pk=run.pk)
    assert result["quarantined"] == 1 and job.reservation_released_at is None
    assert job.status == "interrupted"
    call.refresh_from_db()
    assert call.state == "unknown" and call.finished_at is None


def test_recorded_tool_loop_duplicate_activity_and_no_fetches(execution, monkeypatch):
    import litellm

    from model_hub.models.skills_hub import HubSkillUsageDaily
    from model_hub.services.skills_hub.v11.runner import (
        execute_test_attempt,
        variant_bundles,
    )

    run, _, _ = accepted(execution)
    principal = execution[0]
    start_job(scope=principal.scope, job_id=run.pk)
    attempt = run.hub_skill_attempts.get(variant="candidate")
    handle = next(iter(variant_bundles(snapshot=run.snapshot, variant="candidate")))
    responses = [
        litellm.ModelResponse(
            id="recorded-1",
            model="gpt-4o-mini",
            choices=[
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "root",
                                "type": "function",
                                "function": {
                                    "name": "load_skill",
                                    "arguments": '{"skill_id":"' + handle + '"}',
                                },
                            }
                        ],
                    },
                }
            ],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
        litellm.ModelResponse(
            id="recorded-2",
            model="gpt-4o-mini",
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"refund":20}'},
                }
            ],
            usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        ),
    ]
    transport = AsyncMock(side_effect=responses)
    monkeypatch.setattr(litellm, "acompletion", transport)
    asyncio.run(execute_test_attempt(scope=principal.scope, attempt_id=attempt.id))
    asyncio.run(execute_test_attempt(scope=principal.scope, attempt_id=attempt.id))
    attempt.refresh_from_db()
    assert transport.call_count == 2
    assert (
        attempt.status == "completed"
        and attempt.target_loaded
        and attempt.root_fully_delivered
    )
    assert attempt.answer_verdict == attempt.routing_verdict == "pass"
    assert attempt.receipts[0]["tool_call_id"] == "root"
    assert not HubSkillUsageDaily.objects.exists()
    assert HubSkillModelCall.objects.filter(job_id=run.pk).count() == 2
    sent = transport.call_args.kwargs["messages"]
    assert sent[-1]["role"] == "tool" and sent[-1]["tool_call_id"] == "root"


def test_timeout_before_delivery_is_not_negative_selection(execution, monkeypatch):
    import litellm

    from model_hub.services.skills_hub.v11.runner import execute_test_attempt

    run, _, _ = accepted(execution)
    start_job(scope=execution[0].scope, job_id=run.pk)
    attempt = run.hub_skill_attempts.get(variant="candidate")
    transport = AsyncMock(side_effect=TimeoutError("synthetic timeout"))
    monkeypatch.setattr(litellm, "acompletion", transport)
    asyncio.run(execute_test_attempt(scope=execution[0].scope, attempt_id=attempt.id))
    attempt.refresh_from_db()
    assert attempt.status == "incomplete" and attempt.target_loaded is None
    assert attempt.routing_verdict == attempt.answer_verdict == "unverifiable"
    assert transport.call_count == 1
    run.job.refresh_from_db()
    assert run.job.unknown_call_count == 1 and run.job.reserved_tokens > 0


def test_late_fenced_settlement_keeps_usage_without_rewriting_verdict(execution):
    from model_hub.services.skills_hub.v11.runner import _persist
    from model_hub.services.skills_hub.v11.types import CompletionResult

    run, _, _ = accepted(execution)
    start_job(scope=execution[0].scope, job_id=run.pk)
    attempt = run.hub_skill_attempts.first()
    attempt.status, attempt.claim_generation = "running", 1
    attempt.save(update_fields=["status", "claim_generation"])
    call = reserve_call(
        job_id=run.pk,
        attempt_id=attempt.id,
        kind="task",
        request_digest="one",
        token_allowance=1000,
        estimated_allowance=Decimal("0.1"),
    )
    mark_in_flight(
        call_id=call.pk, generation=1, connection=run.snapshot["connections"]["task"]
    )
    attempt.status, attempt.claim_generation = "unknown", 2
    attempt.save(update_fields=["status", "claim_generation"])
    result = CompletionResult(
        assistant_message={"role": "assistant", "content": "Late private answer"},
        finish_reason="stop",
        provider_request_id="recorded",
        returned_model="model",
        usage={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
        latency_ms=10,
        estimated_cost_usd=Decimal("0.01"),
        pricing_source="recorded",
    )
    settle_call(call_id=call.pk, generation=1, result=result, error=None)
    assert not _persist(
        attempt_id=attempt.pk,
        generation=1,
        values={"answer": "Late private answer", "status": "completed"},
    )
    call.refresh_from_db()
    attempt.refresh_from_db()
    run.job.refresh_from_db()
    assert call.usage is not None
    assert call.assistant_message is None and call.usage["total_tokens"] == 8
    assert attempt.status == "unknown" and attempt.answer is None
    assert run.job.known_input_tokens == 5 and run.job.reserved_tokens == 0


def test_prepared_unsent_request_recovers_capacity_after_worker_loss(execution):
    run, _, _ = accepted(execution)
    start_job(scope=execution[0].scope, job_id=run.pk)
    attempt = run.hub_skill_attempts.first()
    attempt.status, attempt.claim_generation = "running", 1
    attempt.save(update_fields=["status", "claim_generation"])
    call = reserve_call(
        job_id=run.pk,
        attempt_id=attempt.id,
        kind="task",
        request_digest="prepared",
        token_allowance=1000,
        estimated_allowance=Decimal("0.1"),
    )
    HubSkillJob.objects.filter(pk=run.pk).update(
        heartbeat_at=timezone.now() - timedelta(seconds=180)
    )
    reconcile_jobs()
    run.job.refresh_from_db()
    call.refresh_from_db()
    assert (
        run.job.status == "interrupted" and run.job.reservation_released_at is not None
    )
    assert run.job.reserved_tokens == 0 and run.job.reserved_estimate_usd == 0
    assert (
        call.state == "failed"
        and call.started_at is None
        and call.usage is not None
        and call.usage["total_tokens"] == 0
    )
    with pytest.raises(SkillsHubError):
        mark_in_flight(
            call_id=call.pk,
            generation=1,
            connection=run.snapshot["connections"]["task"],
        )


def test_attempt_payload_budget_rejects_uninspectable_evidence(execution):
    from model_hub.services.skills_hub.v11.jobs import check_attempt_payload

    run, _, _ = accepted(execution)
    attempt = run.hub_skill_attempts.first()
    check_attempt_payload(attempt_id=attempt.pk, assistant_message={"content": "small"})
    with pytest.raises(SkillsHubError) as caught:
        check_attempt_payload(
            attempt_id=attempt.pk, assistant_message={"content": "x" * 120000}
        )
    assert caught.value.code == "evidence_limit"


def test_revoked_actor_cannot_reserve_new_outbound_work(execution, user):
    run, _, _ = accepted(execution)
    start_job(scope=execution[0].scope, job_id=run.pk)
    attempt = run.hub_skill_attempts.first()
    attempt.status, attempt.claim_generation = "running", 1
    attempt.save(update_fields=["status", "claim_generation"])
    user.is_active = False
    user.save(update_fields=["is_active"])
    with pytest.raises(SkillsHubError) as caught:
        reserve_call(
            job_id=run.pk,
            attempt_id=attempt.pk,
            kind="task",
            request_digest="one",
            token_allowance=1000,
            estimated_allowance=Decimal("0.1"),
        )
    assert (
        caught.value.code == "access_revoked"
        and not HubSkillModelCall.objects.filter(job_id=run.pk).exists()
    )


def test_concurrent_provider_reservations_do_not_exceed_organization_cap(execution):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    run, _, _ = accepted(execution)
    start_job(scope=execution[0].scope, job_id=run.pk)
    attempt = run.hub_skill_attempts.first()
    attempt.status, attempt.claim_generation = "running", 1
    attempt.save(update_fields=["status", "claim_generation"])

    def reserve(index):
        close_old_connections()
        try:
            return reserve_call(
                job_id=run.pk,
                attempt_id=attempt.id,
                kind="task",
                request_digest=str(index),
                token_allowance=1000,
                estimated_allowance=Decimal("0.01"),
            ).id
        except SkillsHubError as error:
            return error.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(reserve, range(6)))
    assert results.count("skills_capacity_exceeded") == 2
    assert (
        HubSkillModelCall.objects.filter(job_id=run.pk, state="prepared").count() == 4
    )
    run.job.refresh_from_db()
    assert run.job.reserved_tokens == 4000


def test_native_workflow_limits_concurrency_and_disables_paid_retries(monkeypatch):
    from temporalio import workflow

    from tfc.temporal.skills_hub.workflows import SkillsJobWorkflow

    active, peak, requests = 0, 0, []

    async def activity(name, request, **options):
        nonlocal active, peak
        requests.append((name, request, options))
        if name == "skills_start_job":
            return {
                "id": "job",
                "kind": "test",
                "status": "running",
                "attempts": ["a", "b", "c"],
                "remaining_seconds": 300,
                "max_active_attempts": 2,
            }
        if name == "skills_execute_attempt":
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return {"id": request["attempt_id"], "status": "completed"}
        return {"id": "job", "status": "completed"}

    monkeypatch.setattr(workflow, "execute_activity", activity)
    monkeypatch.setattr(workflow, "now", timezone.now)
    result = asyncio.run(
        SkillsJobWorkflow().run(
            {"job_id": "job", "organization_id": "org", "workspace_id": "ws"}
        )
    )
    assert result["status"] == "completed" and peak == 2
    paid = [item for item in requests if item[0] == "skills_execute_attempt"]
    assert len(paid) == 3 and all(
        item[2]["retry_policy"].maximum_attempts == 1 for item in paid
    )
    assert all(
        set(item[1]) == {"job_id", "organization_id", "workspace_id", "attempt_id"}
        for item in paid
    )


def test_temporal_dispatch_never_replaces_existing_work(execution, monkeypatch):
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

    from tfc.temporal.skills_hub import get_activities, get_workflows
    from tfc.temporal.skills_hub.client import start_skills_job

    run, _, _ = accepted(execution)
    start = AsyncMock()
    monkeypatch.setattr("tfc.temporal.skills_hub.client.start_workflow_async", start)
    start_skills_job(
        job_id=run.pk, scope=execution[0].scope, deadline_at=run.job.deadline_at
    )
    args = start.call_args.kwargs
    assert args["cancel_existing"] is False
    assert args["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
    assert args["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING
    assert args["workflow_id"] == f"skills-job-{run.pk}"
    assert len(get_workflows()) == 1 and len(get_activities()) == 4
