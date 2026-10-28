"""Service-level tests for certification reconciliation and classification.

``reconcile_case`` is the plan's "job state alone is not success" gate: a
terminal HostedHarnessJob only passes when scenario registrations, terminal
receipts, call executions, transcripts, snapshot identity and the cleanup
receipt are all present.
"""

import uuid
from datetime import timedelta

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
    HostedHarnessReceipt,
    HostedHarnessScenario,
    RunTest,
    Scenarios,
    TestExecution,
)
from simulate.services.certification import (
    classify_job_failure,
    detect_stable_behavior_failures,
    reconcile_case,
)

SNAPSHOT = "sha256:" + "ab" * 32


def _campaign(organization, workspace):
    campaign = CertificationCampaign.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        deployment_id="dep-1",
        deployment_identity={"alk_snapshot_digest": SNAPSHOT},
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
            "interactions": [{"interaction": 1}, {"interaction": 2}],
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
    )


def _complete_evidence(job, organization, workspace):
    attempt = HostedHarnessAttempt.no_workspace_objects.create(
        job=job,
        attempt_number=1,
        token_hash="t" * 64,
        fence_hash="f" * 64,
        expires_at=timezone.now() + timedelta(hours=1),
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
            body={},
        )
        HostedHarnessArtifact.no_workspace_objects.create(
            job=job,
            sha256=f"{i}" * 64,
            kind="transcript",
            size=1,
            content_type="application/json",
            object_key=f"k/{i}",
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
        "scenario_registrations",
        "terminal_receipts",
        "call_executions",
        "transcripts",
        "cleanup_receipt",
        "snapshot_digest",
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
