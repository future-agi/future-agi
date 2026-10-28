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
    detect_stable_behavior_failures,
    reconcile_case,
)
from simulate.temporal.activities.certification import (
    certification_finalize_campaign,
    certification_record_case_result,
)
from simulate.temporal.types.certification import (
    CertificationCampaignInput,
    CertificationResultInput,
)

SNAPSHOT_NAME = "alk-production"
SNAPSHOT = "build-123"


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
def test_failed_terminal_receipts_are_stable_behavior_failures(
    organization,
    workspace,
):
    campaign, case = _campaign(organization, workspace)
    job = _job(organization, workspace)
    case.hosted_harness_job = job
    case.save(update_fields=["hosted_harness_job"])
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
    assert failures[0]["status"] == "failed"


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
