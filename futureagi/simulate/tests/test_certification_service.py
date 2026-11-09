"""Service-level tests for certification reconciliation and classification.

``reconcile_case`` is the plan's "job state alone is not success" gate: a
terminal HostedHarnessJob only passes when scenario registrations, terminal
receipts, call executions, transcripts, snapshot identity and the cleanup
receipt are all present.
"""

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from simulate.models import (
    CallExecution,
    CertificationCampaign,
    CertificationCase,
    HostedHarnessArtifact,
    HostedHarnessAttempt,
    HostedHarnessCleanupReceipt,
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessManifest,
    HostedHarnessReceipt,
    HostedHarnessScenario,
    RunTest,
    Scenarios,
    TestExecution,
)
from simulate.services.certification import (
    CertificationError,
    _materialized_agent,
    build_case_job_payload,
    classify_job_failure,
    create_campaign,
    detect_exploratory_failures,
    detect_stable_behavior_failures,
    reconcile_case,
)
from simulate.temporal.activities.certification import (
    certification_finalize_campaign,
    certification_record_case_result,
    certification_submit_run,
)
from simulate.temporal.types.certification import (
    CertificationCampaignInput,
    CertificationCaseInput,
    CertificationResultInput,
)

SNAPSHOT_NAME = "alk-production"
SNAPSHOT = "build-123"


@pytest.mark.django_db
def test_campaign_uses_at_most_half_the_minimum_runner_capacity(
    organization, workspace
):
    campaign, _case = _campaign(organization, workspace)
    assert campaign.max_active_cases == 2


@pytest.mark.django_db(transaction=True)
def test_new_campaign_reuses_one_previous_environment(organization, workspace):
    interaction = {
        "interaction": 1,
        "scenario_key": "agent__interaction_01",
        "agent_config": {
            "mode": "connect_only",
            "config": {"assistant_id": "agent-123"},
        },
        "secret_ref_set": [],
    }
    normalized = {
        "version": 1,
        "suite_key": "test_agents_v1",
        "defaults": {"compatibility_case_count": 1},
        "cases": [
            {
                "case_key": "agent__primary",
                "enabled": True,
                "tiers": ["quick", "full"],
                "scenario_count": 2,
                "agent": {"name": "Agent", "connector": "vapi"},
                "target": {"kind": "hosted_service", "ref": "vapi://agent"},
                "run_test_id": "l2_audio_room_speak_l2",
                "authorization": {
                    "approved_at": "2026-10-01T00:00:00Z",
                    "approved_by": "original-reviewer",
                    "target_ownership": "futureagi",
                },
                "interactions": [interaction, {**interaction, "interaction": 2}],
            }
        ],
    }
    with (
        patch(
            "simulate.services.certification.validated_suite",
            return_value=normalized,
        ),
        patch("simulate.temporal.client.start_certification_workflow"),
    ):
        previous, _created = create_campaign(
            organization,
            workspace,
            deployment_id="deployment-1",
            deployment_identity={},
            suite_key="test_agents_v1",
        )
        previous_case = previous.cases.get()
        environment = _job(organization, workspace)
        previous_case.hosted_harness_job = environment
        previous_case.state = CertificationCase.State.COMPLETED
        previous_case.save(update_fields=["hosted_harness_job", "state"])
        previous.state = CertificationCampaign.State.COMPLETED
        previous.finished_at = timezone.now()
        previous.save(update_fields=["state", "finished_at"])
        previous.refresh_from_db()
        assert previous.normalized_suite["defaults"]["certification_tier"] == "full"
        assert (
            CertificationCase.no_workspace_objects.filter(
                campaign=previous,
                state=CertificationCase.State.COMPLETED,
                hosted_harness_job__state=HostedHarnessJob.State.COMPLETED,
            ).count()
            == 1
        )
        normalized["cases"][0]["authorization"]["approved_by"] = "renewed-reviewer"

        current, _created = create_campaign(
            organization,
            workspace,
            deployment_id="deployment-2",
            deployment_identity={},
            suite_key="test_agents_v1",
        )
        quick, _created = create_campaign(
            organization,
            workspace,
            deployment_id="deployment-3",
            deployment_identity={},
            suite_key="test_agents_v1",
            tier="quick",
        )
        normalized["cases"][0]["target"]["ref"] = "vapi://replacement-agent"
        changed_target, _created = create_campaign(
            organization,
            workspace,
            deployment_id="deployment-4",
            deployment_identity={},
            suite_key="test_agents_v1",
        )

    assert current.cases_total == 2, list(
        current.cases.values_list("case_key", flat=True)
    )
    compatibility = current.cases.get(case_key="agent__primary__previous_environment")
    assert current.cases_total == 2
    assert compatibility.hosted_harness_job_id == environment.id
    assert compatibility.spec["reuse_environment"] is True
    assert compatibility.spec["scenario_count"] == 1
    assert compatibility.spec["authorization"]["approved_by"] == "renewed-reviewer"
    assert quick.cases_total == 1
    assert changed_target.cases_total == 1


def test_materialized_agent_uses_the_shared_agent_contract():
    with pytest.raises(CertificationError, match="invalid agent configuration"):
        _materialized_agent(
            {
                "case_key": "invalid-chat-direction",
                "agent": {"connector": "retell_chat"},
                "interactions": [
                    {
                        "agent_config": {
                            "call_direction": "inbound",
                            "config": {},
                        }
                    }
                ],
            }
        )


def test_case_payload_materializes_frozen_agent_config():
    executable = {
        "mode": "connect_only",
        "call_direction": "inbound",
        "config": {"assistant_id": "agent-123"},
    }
    case = SimpleNamespace(
        case_key="agent_one__primary",
        spec={
            "agent": {
                "connector": "vapi",
                "source": {
                    "repository": "https://github.com/future-agi/agent-one",
                    "commit_sha": "a" * 40,
                    "path": "agents/agent-one",
                },
            },
            "target": {"kind": "hosted_service", "ref": "agent-123"},
            "run_test_id": "l2_audio_room_speak_l2",
            "authorization": {"target_ownership": "futureagi"},
            "interactions": [
                {
                    "scenario_key": f"scenario-{index}",
                    "seed": index,
                    "agent_config": executable,
                    "secret_ref_set": [
                        {
                            "manager": "platform-vault",
                            "key": "VAPI_API_KEY",
                            "purpose": "target_provider",
                        }
                    ],
                }
                for index in (1, 2)
            ],
        },
    )
    campaign = SimpleNamespace(
        id=uuid.uuid4(),
        suite_key="test",
        suite_digest="sha256:" + "0" * 64,
        deployment_id="deployment",
        normalized_suite={"defaults": {}},
    )

    payload = build_case_job_payload(case, campaign)
    assert payload["agent"]["mode"] == "connect_only"
    assert payload["agent"]["call_direction"] == "inbound"
    assert payload["agent"]["config"] == {"assistant_id": "agent-123"}
    assert payload["source"]["path"] == "agents/agent-one"
    certification = payload["metadata"]["certification"]
    assert certification["production_call_authorization"] == {
        "target_ownership": "futureagi"
    }
    assert "authorization" not in certification


def _campaign(organization, workspace):
    campaign = CertificationCampaign.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        deployment_id="dep-1",
        deployment_identity={
            "alk_snapshot": {"name": SNAPSHOT_NAME, "digest": SNAPSHOT}
        },
        suite_key="test_agents_v1",
        suite_digest="sha256:" + "0" * 64,
        normalized_suite={
            "cases": [],
            "suite_key": "test_agents_v1",
            "version": 1,
            "defaults": {},
        },
        idempotency_key=f"certification:dep-1:{uuid.uuid4()}",
        cases_total=1,
    )
    case = CertificationCase.no_workspace_objects.create(
        campaign=campaign,
        case_key="agent_one__primary",
        sequence=0,
        idempotency_key=f"cert:case:{uuid.uuid4()}",
        spec={
            "case_key": "agent_one__primary",
            "run_test_id": "l1_chat_top_voice",
            "interactions": [
                {"interaction": 1, "scenario_key": "k0"},
                {"interaction": 2, "scenario_key": "k1"},
            ],
            "agent": {"name": "Agent One"},
            "target": {"kind": "hosted_service", "ref": "x"},
        },
    )
    return campaign, case


def _job(organization, workspace, state=HostedHarnessJob.State.COMPLETED):
    return HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        idempotency_key=f"job-{uuid.uuid4()}",
        request_digest="sha256:" + "0" * 64,
        schema_version="1.4",
        payload={},
        state=state,
        seed=1,
        scenario_count=2,
        artifact_level="full",
        max_artifact_bytes=1,
        deadline_at=timezone.now() + timedelta(hours=1),
        current_attempt_number=1,
    )


def _complete_evidence(job, organization, workspace):
    attempt = HostedHarnessAttempt.no_workspace_objects.create(
        job=job,
        attempt_number=1,
        token_hash="t" * 64,
        fence_hash="f" * 64,
        expires_at=timezone.now() + timedelta(hours=1),
        state=HostedHarnessAttempt.State.COMPLETED,
        terminal_stage="completed",
        terminal_event_received=True,
        manifest_acked=True,
        snapshot_name=SNAPSHOT_NAME,
        snapshot_digest=SNAPSHOT,
    )
    run_test = RunTest.no_workspace_objects.create(
        name="rt",
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
        organization=organization,
    )
    execution = TestExecution.no_workspace_objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_calls=2,
        completed_calls=2,
        failed_calls=0,
    )
    scenario_row = Scenarios.objects.create(
        name="s",
        source="test",
        organization=organization,
        workspace=workspace,
    )
    manifest_entries = []
    execution_manifest = []
    for i in range(2):
        hs = HostedHarnessScenario.no_workspace_objects.create(
            job=job,
            scenario_key=f"k{i}",
            number=i + 1,
        )
        call = CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario_row,
            simulation_call_type="text",
            status="completed",
        )
        ex = HostedHarnessExecution.no_workspace_objects.create(
            job=job,
            source_scenario=hs,
            execution_key=f"e{i}",
            trial_index=0,
            call_execution=call,
        )
        HostedHarnessReceipt.no_workspace_objects.create(
            job=job,
            attempt=attempt,
            scenario=hs,
            execution=ex,
            attempt_number=1,
            digest=f"sha256:{'1' * 64}",
            status="passed",
            body={
                "call": {
                    "transcript_artifact": "sha256:" + f"{i}" * 64,
                    "recording_artifacts": [],
                }
            },
        )
        HostedHarnessArtifact.no_workspace_objects.create(
            job=job,
            sha256=f"{i}" * 64,
            kind="transcript",
            size=1,
            content_type="application/json",
            object_key=f"k/{i}",
            scenario_key=f"e{i}",
        )
        manifest_entries.append(
            {
                "artifact_id": "sha256:" + f"{i}" * 64,
                "kind": "transcript",
                "size": 1,
                "scenario_key": f"e{i}",
            }
        )
        execution_manifest.append(
            {
                "execution_key": f"e{i}",
                "scenario_key": f"k{i}",
                "trial_index": 1,
            }
        )
    job.payload = {"metadata": {"execution_manifest": execution_manifest}}
    job.save(update_fields=["payload", "updated_at"])
    HostedHarnessManifest.no_workspace_objects.create(
        attempt=attempt,
        digest="sha256:" + "9" * 64,
        complete=True,
        body={"entries": manifest_entries},
    )
    return attempt


@pytest.mark.django_db(transaction=True)
def test_reconcile_passes_only_with_complete_evidence(organization, workspace):
    campaign, case = _campaign(organization, workspace)
    job = _job(organization, workspace)
    case.hosted_harness_job = job
    case.save(update_fields=["hosted_harness_job"])

    attempt = _complete_evidence(job, organization, workspace)
    HostedHarnessCleanupReceipt.no_workspace_objects.create(
        attempt=attempt,
        provider_ref="p",
        verified_absent=True,
    )

    result = reconcile_case(case)
    assert result["ok"], result["missing"]
    assert result["failure_class"] is None


@pytest.mark.django_db(transaction=True)
def test_failed_grade_keeps_complete_receipt_evidence(organization, workspace):
    _campaign_row, case = _campaign(organization, workspace)
    job = _job(organization, workspace)
    case.hosted_harness_job = job
    case.save(update_fields=["hosted_harness_job"])
    attempt = _complete_evidence(job, organization, workspace)
    HostedHarnessReceipt.no_workspace_objects.filter(
        job=job, attempt=attempt, scenario__scenario_key="k0"
    ).update(status="failed")
    HostedHarnessCleanupReceipt.no_workspace_objects.create(
        attempt=attempt, provider_ref="p", verified_absent=True
    )

    result = reconcile_case(case)

    assert result["ok"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("receipt_status", ("errored", "skipped"))
def test_incomplete_receipt_status_blocks_certification(
    organization, workspace, receipt_status
):
    _campaign_row, case = _campaign(organization, workspace)
    job = _job(organization, workspace)
    case.hosted_harness_job = job
    case.save(update_fields=["hosted_harness_job"])
    attempt = _complete_evidence(job, organization, workspace)
    HostedHarnessReceipt.no_workspace_objects.filter(
        job=job, attempt=attempt, scenario__scenario_key="k0"
    ).update(status=receipt_status)
    HostedHarnessCleanupReceipt.no_workspace_objects.create(
        attempt=attempt, provider_ref="p", verified_absent=True
    )

    result = reconcile_case(case)

    assert "scenario:k0:receipt" in result["missing"]


@pytest.mark.django_db(transaction=True)
def test_terminal_job_still_fails_without_evidence(organization, workspace):
    campaign, case = _campaign(organization, workspace)
    job = _job(organization, workspace)  # completed state, zero evidence
    case.hosted_harness_job = job
    case.save(update_fields=["hosted_harness_job"])

    result = reconcile_case(case)
    assert not result["ok"]
    for name in (
        "current_attempt",
        "terminal_event",
        "manifest",
        "cleanup_receipt",
        "snapshot_identity",
    ):
        assert name in result["missing"], name
    assert result["failure_class"] == "cleanup"


@pytest.mark.django_db(transaction=True)
def test_unverified_cleanup_classifies_cleanup(organization, workspace):
    campaign, case = _campaign(organization, workspace)
    job = _job(organization, workspace)
    case.hosted_harness_job = job
    case.save(update_fields=["hosted_harness_job"])
    attempt = _complete_evidence(job, organization, workspace)
    HostedHarnessCleanupReceipt.no_workspace_objects.create(
        attempt=attempt,
        provider_ref="p",
        verified_absent=False,
    )
    result = reconcile_case(case)
    assert not result["ok"]
    assert result["failure_class"] == "cleanup"


@pytest.mark.django_db(transaction=True)
def test_forced_cancel_requires_cleanup_but_not_guest_artifacts(
    organization, workspace
):
    _campaign_row, case = _campaign(organization, workspace)
    job = _job(organization, workspace, state=HostedHarnessJob.State.CANCELED)
    case.hosted_harness_job = job
    case.save(update_fields=["hosted_harness_job"])
    attempt = HostedHarnessAttempt.no_workspace_objects.create(
        job=job,
        attempt_number=1,
        token_hash="t" * 64,
        fence_hash="f" * 64,
        expires_at=timezone.now() + timedelta(hours=1),
        state=HostedHarnessAttempt.State.CANCELED,
        terminal_stage="canceled",
        snapshot_name=SNAPSHOT_NAME,
        snapshot_digest=SNAPSHOT,
    )
    HostedHarnessCleanupReceipt.no_workspace_objects.create(
        attempt=attempt, provider_ref="forced", verified_absent=True
    )

    result = reconcile_case(case, expected_terminal="canceled")

    assert result["ok"] is True
    assert "terminal_event" not in result["checks"]
    assert "manifest" not in result["checks"]


@pytest.mark.django_db(transaction=True)
def test_stale_attempt_evidence_cannot_satisfy_current_attempt(organization, workspace):
    _campaign_row, case = _campaign(organization, workspace)
    job = _job(organization, workspace)
    case.hosted_harness_job = job
    case.save(update_fields=["hosted_harness_job"])
    old_attempt = _complete_evidence(job, organization, workspace)
    HostedHarnessCleanupReceipt.no_workspace_objects.create(
        attempt=old_attempt, provider_ref="old", verified_absent=True
    )
    job.current_attempt_number = 2
    job.save(update_fields=["current_attempt_number"])
    HostedHarnessAttempt.no_workspace_objects.create(
        job=job,
        attempt_number=2,
        token_hash="2" * 64,
        fence_hash="3" * 64,
        expires_at=timezone.now() + timedelta(hours=1),
        state=HostedHarnessAttempt.State.COMPLETED,
        terminal_stage="completed",
        terminal_event_received=True,
        snapshot_name=SNAPSHOT_NAME,
        snapshot_digest=SNAPSHOT,
    )

    result = reconcile_case(case)
    assert not result["ok"]
    assert "manifest" in result["missing"]
    assert any(name.endswith(":receipt") for name in result["missing"])


@pytest.mark.django_db(transaction=True)
def test_cross_scenario_artifact_cannot_satisfy_interaction(organization, workspace):
    _campaign_row, case = _campaign(organization, workspace)
    job = _job(organization, workspace)
    case.hosted_harness_job = job
    case.save(update_fields=["hosted_harness_job"])
    attempt = _complete_evidence(job, organization, workspace)
    HostedHarnessCleanupReceipt.no_workspace_objects.create(
        attempt=attempt, provider_ref="p", verified_absent=True
    )
    HostedHarnessArtifact.no_workspace_objects.filter(
        job=job, scenario_key="e0"
    ).update(scenario_key="e1")

    result = reconcile_case(case)
    assert "scenario:k0:transcript" in result["missing"]


@pytest.mark.django_db(transaction=True)
def test_sibling_run_execution_cannot_satisfy_current_manifest(organization, workspace):
    _campaign_row, case = _campaign(organization, workspace)
    current = _job(organization, workspace)
    case.run_job = current
    case.save(update_fields=["run_job"])
    attempt = _complete_evidence(current, organization, workspace)
    HostedHarnessCleanupReceipt.no_workspace_objects.create(
        attempt=attempt, provider_ref="p", verified_absent=True
    )

    sibling = _job(organization, workspace)
    existing = HostedHarnessExecution.no_workspace_objects.get(
        job=current, execution_key="e0"
    )
    sibling_call = CallExecution.objects.create(
        test_execution=existing.call_execution.test_execution,
        scenario=existing.call_execution.scenario,
        simulation_call_type="text",
        status="completed",
    )
    HostedHarnessExecution.no_workspace_objects.create(
        job=sibling,
        source_scenario=existing.source_scenario,
        execution_key="sibling-e0",
        trial_index=1,
        call_execution=sibling_call,
    )
    payload = dict(current.payload)
    metadata = dict(payload["metadata"])
    metadata["execution_manifest"] = [
        {**entry, "execution_key": "sibling-e0"}
        if entry["scenario_key"] == "k0"
        else entry
        for entry in metadata["execution_manifest"]
    ]
    payload["metadata"] = metadata
    current.payload = payload
    current.save(update_fields=["payload", "updated_at"])

    result = reconcile_case(case)

    assert "scenario:k0:execution" in result["missing"]


@pytest.mark.django_db(transaction=True)
def test_submit_run_reuses_one_authored_scenario_once(
    organization, workspace, monkeypatch
):
    _campaign_row, case = _campaign(organization, workspace)
    case.spec = {
        **case.spec,
        "reuse_environment": True,
        "scenario_count": 1,
        "interactions": case.spec["interactions"][:1],
    }
    environment = _job(organization, workspace)
    case.hosted_harness_job = environment
    case.save(update_fields=["hosted_harness_job", "spec"])
    for index, key in enumerate(("authored-a", "authored-b"), start=1):
        HostedHarnessScenario.no_workspace_objects.create(
            job=environment, scenario_key=key, number=index
        )
    run = _job(organization, workspace, state=HostedHarnessJob.State.QUEUED)
    run.payload = {
        "retry": {
            "max_infrastructure_attempts": 1,
            "initial_backoff_seconds": 1,
            "max_backoff_seconds": 1,
        }
    }
    run.save(update_fields=["payload", "updated_at"])
    selected = []
    scheduled = []

    def create_selected(environment_arg, *, scenario_keys, trials, idempotency_key):
        selected.append((environment_arg.id, scenario_keys, trials, idempotency_key))
        return run, True

    monkeypatch.setattr(
        "simulate.services.hosted_harness.create_selected_harness_run",
        create_selected,
    )
    monkeypatch.setattr(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow",
        lambda job_id, *_args: scheduled.append(job_id),
    )

    value = CertificationCaseInput(case_id=str(case.id))
    asyncio.run(certification_submit_run(value))
    asyncio.run(certification_submit_run(value))

    case.refresh_from_db()
    assert case.run_job_id == run.id
    assert selected == [
        (
            environment.id,
            ["authored-a"],
            1,
            f"{case.idempotency_key}:run",
        )
    ]
    assert scheduled == [str(run.id), str(run.id)]


@pytest.mark.django_db(transaction=True)
def test_submit_run_marks_child_failed_after_scheduler_retries(
    organization, workspace, monkeypatch
):
    _campaign_row, case = _campaign(organization, workspace)
    environment = _job(organization, workspace)
    run = _job(organization, workspace, state=HostedHarnessJob.State.QUEUED)
    run_test = RunTest.no_workspace_objects.create(
        name="scheduler failure",
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
        organization=organization,
    )
    execution = TestExecution.no_workspace_objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.PENDING,
    )
    run.payload = {
        "retry": {
            "max_infrastructure_attempts": 1,
            "initial_backoff_seconds": 1,
            "max_backoff_seconds": 1,
        }
    }
    run.test_execution = execution
    run.save(update_fields=["payload", "test_execution", "updated_at"])
    case.hosted_harness_job = environment
    case.run_job = run
    case.save(update_fields=["hosted_harness_job", "run_job"])

    def fail_schedule(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow",
        fail_schedule,
    )
    monkeypatch.setattr(
        "simulate.temporal.activities.certification.activity.info",
        lambda: SimpleNamespace(attempt=3),
    )

    with pytest.raises(RuntimeError, match="offline"):
        asyncio.run(certification_submit_run(CertificationCaseInput(str(case.id))))

    run.refresh_from_db()
    execution.refresh_from_db()
    assert run.state == HostedHarnessJob.State.FAILED
    assert run.failure["code"] == "scheduler_unavailable"
    assert execution.status == TestExecution.ExecutionStatus.FAILED
    assert execution.completed_at is not None
    assert execution.error_reason == "Certification child run could not be scheduled"


@pytest.mark.django_db(transaction=True)
def test_terminal_case_transition_is_replay_safe(organization, workspace):
    campaign, case = _campaign(organization, workspace)
    value = CertificationResultInput(
        case_id=str(case.id),
        outcome="failed",
        failure_class="evidence",
        failure_detail={"reason": "test"},
    )
    asyncio.run(certification_record_case_result(value))
    asyncio.run(certification_record_case_result(value))

    campaign.refresh_from_db()
    case.refresh_from_db()
    assert case.state == CertificationCase.State.FAILED
    assert campaign.cases_failed == 1
    assert campaign.notifications.count() == 1


@pytest.mark.django_db(transaction=True)
def test_finalizer_turns_incomplete_case_into_explicit_failure(organization, workspace):
    campaign, case = _campaign(organization, workspace)
    result = asyncio.run(
        certification_finalize_campaign(
            CertificationCampaignInput(campaign_id=str(campaign.id))
        )
    )

    campaign.refresh_from_db()
    case.refresh_from_db()
    assert result.state == CertificationCampaign.State.FAILED
    assert campaign.state == CertificationCampaign.State.FAILED
    assert case.state == CertificationCase.State.FAILED
    assert case.failure_detail["reason"] == "orchestration_incomplete_at_finalize"
    assert case.failure_detail["previous_state"] == CertificationCase.State.PENDING


@pytest.mark.django_db(transaction=True)
def test_finalizer_reports_duration_and_harness_cost(organization, workspace):
    campaign, case = _campaign(organization, workspace)
    campaign.started_at = timezone.now() - timedelta(seconds=90)
    campaign.normalized_suite = {
        **campaign.normalized_suite,
        "defaults": {
            "certification_tier": "quick",
            "expected_duration_seconds": {"quick": 1800},
        },
    }
    campaign.save(update_fields=["started_at", "normalized_suite"])
    environment = _job(organization, workspace)
    run = _job(organization, workspace)
    for job, cost in ((environment, 1.25), (run, 0.75)):
        job.payload = {
            "metadata": {"harness_spend": {"total_usd": cost, "unpriced_turns": 0}}
        }
        job.save(update_fields=["payload", "updated_at"])
    case.hosted_harness_job = environment
    case.run_job = run
    case.state = CertificationCase.State.COMPLETED
    case.save(update_fields=["hosted_harness_job", "run_job", "state"])

    asyncio.run(
        certification_finalize_campaign(
            CertificationCampaignInput(campaign_id=str(campaign.id))
        )
    )

    campaign.refresh_from_db()
    assert campaign.result["tier"] == "quick"
    assert campaign.result["expected_duration_seconds"] == 1800
    assert campaign.result["duration_seconds"] >= 90
    assert campaign.result["cost_usd"] == 2.0
    assert campaign.result["unpriced_turns"] == 0


@pytest.mark.django_db(transaction=True)
def test_failed_terminal_receipts_are_stable_behavior_failures(
    organization,
    workspace,
):
    campaign, case = _campaign(organization, workspace)
    job = _job(organization, workspace)
    case.hosted_harness_job = job
    case.spec["interactions"][0]["stable_assertions"] = ["tone"]
    case.save(update_fields=["hosted_harness_job", "spec"])
    attempt = _complete_evidence(job, organization, workspace)
    hs = HostedHarnessScenario.no_workspace_objects.filter(job=job).first()
    HostedHarnessReceipt.no_workspace_objects.create(
        job=job,
        attempt=attempt,
        scenario=hs,
        attempt_number=1,
        digest="sha256:" + "2" * 64,
        status="failed",
        body={"evaluations": [{"name": "tone", "status": "failed"}]},
    )
    failures = detect_stable_behavior_failures(case)
    assert len(failures) == 1
    assert failures[0]["evaluation"] == "tone"
    assert failures[0]["status"] == "failed"


@pytest.mark.django_db(transaction=True)
def test_unreviewed_grade_failure_is_alert_only(organization, workspace):
    _campaign_row, case = _campaign(organization, workspace)
    job = _job(organization, workspace)
    case.hosted_harness_job = job
    case.save(update_fields=["hosted_harness_job"])
    attempt = _complete_evidence(job, organization, workspace)
    receipt = HostedHarnessReceipt.no_workspace_objects.filter(
        job=job, attempt=attempt
    ).first()
    receipt.status = "failed"
    receipt.body = {
        **receipt.body,
        "evaluations": [{"name": "tone", "status": "failed"}],
    }
    receipt.save(update_fields=["status", "body"])

    assert detect_stable_behavior_failures(case) == []
    assert detect_exploratory_failures(case) == [
        {
            "receipt_id": str(receipt.id),
            "evaluation": "tone",
            "status": "failed",
            "exploratory": False,
        }
    ]


@pytest.mark.django_db(transaction=True)
def test_classify_job_failure_maps_terminal_domain(organization, workspace):
    job = _job(organization, workspace, state=HostedHarnessJob.State.FAILED)
    assert classify_job_failure(job) == "target"  # no attempt -> default
    HostedHarnessAttempt.no_workspace_objects.create(
        job=job,
        attempt_number=1,
        token_hash="t" * 64,
        fence_hash="f" * 64,
        expires_at=timezone.now() + timedelta(hours=1),
        terminal_failure={"domain": "infrastructure"},
    )
    assert classify_job_failure(job) == "environment"
