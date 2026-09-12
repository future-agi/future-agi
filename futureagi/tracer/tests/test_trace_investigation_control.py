import uuid
from copy import deepcopy
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.models.project import Project
from tracer.models.trace_investigation import (
    TraceInvestigationAttempt,
    TraceInvestigationAttemptStatus,
    TraceInvestigationJob,
    TraceInvestigationJobState,
    TraceInvestigationMemorySnapshot,
    TraceInvestigationMemoryStatus,
    TraceInvestigationReport,
)
from tracer.models.trace_scan import TraceScanConfig, TraceScanEngine, TraceScanResult
from tracer.services.trace_investigation import (
    InvestigationConflict,
    InvestigationNotFound,
    canonical_wire_result_digest,
    claim_due_investigations,
    publish_investigation,
    record_trace_notifications,
    update_investigation_attempt,
)
from tracer.services.trace_investigation_memory import (
    change_active_memory,
    create_memory_candidate,
    record_memory_evaluation,
    submit_reviewed_feedback,
)

pytestmark = pytest.mark.django_db


def _delivery(project, *, offset=1, event_id=None, trace_id=None):
    return {
        "topic": "error-feed.trace-available.v1",
        "partition": 0,
        "offset": offset,
        "value": {
            "version": 1,
            "event_id": event_id or uuid.uuid4(),
            "organization_id": project.organization_id,
            "workspace_id": project.workspace_id,
            "project_id": project.id,
            "event_kind": "root_span_written",
            "traces": [
                {
                    "trace_id": trace_id or uuid.uuid4(),
                    "root_span_id": "0123456789abcdef",
                    "root_end_time": timezone.now() - timedelta(minutes=1),
                }
            ],
            "emitted_at": timezone.now(),
        },
    }


def _configure(project):
    return TraceScanConfig.no_workspace_objects.create(
        project=project,
        enabled=True,
        engine=TraceScanEngine.OMEGA,
        scan_version="omega-v1",
        omega_memory=[
            {
                "id": "refund-policy",
                "text": "Compare requested and executed refund amounts.",
            }
        ],
        omega_limits={"max_model_calls": 4},
    )


def _result(claim, *, digest=None, findings=True):
    finding_rows = (
        [
            {
                "finding_id": "finding-1",
                "kind": "outcome",
                "statement": "The executed amount differs from the request.",
                "requirement_id": "requirement-1",
                "evidence_ids": ["evidence-1"],
                "recovery": "not_observed",
                "attribution": {
                    role: {
                        "status": "supported" if role != "origin" else "unknown",
                        "span_id": None if role == "origin" else "0123456789abcdef",
                        "evidence_ids": [] if role == "origin" else ["evidence-1"],
                    }
                    for role in ("origin", "decisive", "symptom")
                },
            }
        ]
        if findings
        else []
    )
    result = {
        "contract_version": claim["contract_version"],
        "organization_id": claim["organization_id"],
        "workspace_id": claim["workspace_id"],
        "project_id": claim["project_id"],
        "job_id": claim["job_id"],
        "generation": claim["generation"],
        "attempt_id": claim["attempt_id"],
        "trace_id": claim["trace_id"],
        "engine_version": claim["engine_version"],
        "read_cutoff": claim["read_cutoff"],
        "memory_snapshot_id": claim["memory"]["snapshot_id"],
        "memory_digest": claim["memory"]["digest"],
        "evidence_digest": f"sha256:{'e' * 64}",
        "execution_status": "completed",
        "outcome": "failure" if findings else "success",
        "findings": finding_rows,
        "requirement_checks": [],
        "evidence_receipts": [
            {
                "evidence_id": "evidence-1",
                "span_id": "0123456789abcdef",
                "parent_span_id": None,
                "excerpt": "requested=100 executed=10",
            }
        ],
        "verification_receipts": [],
        "coverage": {
            "scope": "available trace at read cutoff",
            "observed_span_count": 1,
            "read_complete": True,
            "future_arrivals_known": False,
        },
        "usage": {
            "model_calls": 1,
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": None,
            "cost_status": "partially_unpriced",
        },
        "gateway_accounting": [
            {
                "request_id": "gateway-request-1",
                "model_used": "openai/test",
                "cost": None,
                "raw": {"x-agentcc-model-used": "openai/test"},
            }
        ],
    }
    result["result_digest"] = digest or canonical_wire_result_digest(result)
    return result


def _publish(*, idempotency_key, lease_token, result):
    return publish_investigation(
        idempotency_key=idempotency_key,
        lease_token=lease_token,
        result=result,
        wire_result_digest=canonical_wire_result_digest(result),
    )


@override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0)
def test_notification_batch_is_durable_idempotent_and_tenant_scoped(observe_project):
    delivery = _delivery(observe_project)

    first = record_trace_notifications(deliveries=[delivery])
    duplicate = record_trace_notifications(deliveries=[deepcopy(delivery)])

    assert (first["accepted_events"], first["duplicate_events"]) == (1, 0)
    assert (duplicate["accepted_events"], duplicate["duplicate_events"]) == (0, 1)
    assert duplicate["pending"][0]["generation"] == 1
    assert TraceInvestigationJob.no_workspace_objects.get().root_span_id == (
        "0123456789abcdef"
    )

    conflicting = deepcopy(delivery)
    conflicting["value"]["traces"][0]["root_span_id"] = "fedcba9876543210"
    with pytest.raises(InvestigationConflict):
        record_trace_notifications(deliveries=[conflicting])

    foreign = deepcopy(delivery)
    foreign["offset"] = 2
    foreign["value"]["event_id"] = uuid.uuid4()
    foreign["value"]["organization_id"] = uuid.uuid4()
    with pytest.raises(InvestigationNotFound, match="project scope was not found"):
        record_trace_notifications(deliveries=[foreign])


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
    ERROR_FEED_OMEGA_PROJECT_CONCURRENCY=1,
)
def test_claim_pins_context_and_reserves_project_capacity(observe_project):
    config = _configure(observe_project)
    first = _delivery(observe_project, offset=1)
    second = _delivery(observe_project, offset=2)
    record_trace_notifications(deliveries=[first, second])

    response = claim_due_investigations(
        worker_id="node-1", engine_version="omega-v1", limit=2
    )

    assert len(response["claims"]) == 1
    claim = response["claims"][0]
    assert claim["engine_version"] == "omega-v1"
    assert claim["read_cutoff"] <= timezone.now()
    assert claim["memory"]["entries"] == config.omega_memory
    assert claim["limits"]["max_model_calls"] == 4
    assert claim_due_investigations(
        worker_id="node-2", engine_version="omega-v1", limit=2
    ) == {"claims": []}

    config.omega_memory = []
    config.save(update_fields=["omega_memory", "updated_at"])
    attempt = TraceInvestigationAttempt.no_workspace_objects.get(id=claim["attempt_id"])
    assert attempt.memory == [
        {
            "id": "refund-policy",
            "text": "Compare requested and executed refund amounts.",
        }
    ]


@pytest.mark.parametrize(
    "limits",
    [
        {"max_model_calls": 1},
        {"max_model_calls": True},
        {"max_tool_result_bytes": 256},
        {"max_children": 1, "max_parallel_children": 0},
    ],
)
@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
)
def test_claim_rejects_limits_the_worker_cannot_execute(observe_project, limits):
    config = _configure(observe_project)
    config.omega_limits = limits
    config.save(update_fields=["omega_limits", "updated_at"])
    record_trace_notifications(deliveries=[_delivery(observe_project)])

    with pytest.raises(
        InvestigationConflict,
        match="project Omega limit|max_parallel_children",
    ):
        claim_due_investigations(worker_id="node-1", engine_version="omega-v1", limit=1)


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
    ERROR_FEED_OMEGA_LEASE_SECONDS=120,
)
def test_renew_cancel_and_scope_checks(observe_project):
    _configure(observe_project)
    record_trace_notifications(deliveries=[_delivery(observe_project)])
    claim = claim_due_investigations(
        worker_id="node-1", engine_version="omega-v1", limit=1
    )["claims"][0]

    renewed = update_investigation_attempt(
        attempt_id=claim["attempt_id"],
        organization_id=claim["organization_id"],
        workspace_id=claim["workspace_id"],
        project_id=claim["project_id"],
        job_id=claim["job_id"],
        lease_token=claim["lease_token"],
        action="renew",
        reason="",
    )
    assert renewed["status"] == TraceInvestigationAttemptStatus.CLAIMED

    with pytest.raises(InvestigationNotFound, match="attempt scope was not found"):
        update_investigation_attempt(
            attempt_id=claim["attempt_id"],
            organization_id=uuid.uuid4(),
            workspace_id=claim["workspace_id"],
            project_id=claim["project_id"],
            job_id=claim["job_id"],
            lease_token=claim["lease_token"],
            action="renew",
            reason="",
        )

    cancelled = update_investigation_attempt(
        attempt_id=claim["attempt_id"],
        organization_id=claim["organization_id"],
        workspace_id=claim["workspace_id"],
        project_id=claim["project_id"],
        job_id=claim["job_id"],
        lease_token=claim["lease_token"],
        action="cancel",
        reason="shutdown",
    )
    assert cancelled["cancellation_requested"] is True
    assert cancelled["job_state"] == TraceInvestigationJobState.CANCELLED


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
)
def test_publication_is_idempotent_and_newer_generation_wins(observe_project):
    _configure(observe_project)
    trace_id = uuid.uuid4()
    first_delivery = _delivery(observe_project, offset=1, trace_id=trace_id)
    record_trace_notifications(deliveries=[first_delivery])
    first_claim = claim_due_investigations(
        worker_id="node-1", engine_version="omega-v1", limit=1
    )["claims"][0]

    record_trace_notifications(
        deliveries=[_delivery(observe_project, offset=2, trace_id=trace_id)]
    )
    stale = _publish(
        idempotency_key="first-publication",
        lease_token=first_claim["lease_token"],
        result=_result(first_claim),
    )
    assert stale["grouping_status"] == "stale"
    assert stale["active_projection_updated"] is False
    assert not TraceScanResult.no_workspace_objects.filter(trace_id=trace_id).exists()
    job = TraceInvestigationJob.no_workspace_objects.get(trace_id=trace_id)
    assert (job.generation, job.state) == (2, TraceInvestigationJobState.WAITING)

    second_claim = claim_due_investigations(
        worker_id="node-1", engine_version="omega-v1", limit=1
    )["claims"][0]
    result = _result(second_claim)
    accepted = _publish(
        idempotency_key="second-publication",
        lease_token=second_claim["lease_token"],
        result=result,
    )
    duplicate = _publish(
        idempotency_key="second-publication",
        lease_token=second_claim["lease_token"],
        result=deepcopy(result),
    )

    assert accepted["status"] == "accepted"
    assert accepted["grouping_status"] == "pending"
    assert len(accepted["occurrence_ids"]) == 1
    assert duplicate == {**accepted, "status": "duplicate"}
    projection = TraceScanResult.no_workspace_objects.get(trace_id=trace_id)
    assert projection.has_issues is True
    assert projection.meta["gateway_accounting"][0]["cost"] is None
    report = TraceInvestigationReport.no_workspace_objects.get(id=accepted["report_id"])
    assert report.result["evidence_receipts"][0]["excerpt"] == (
        "requested=100 executed=10"
    )

    conflicting = deepcopy(result)
    conflicting["result_digest"] = f"sha256:{'b' * 64}"
    with pytest.raises(InvestigationConflict):
        _publish(
            idempotency_key="second-publication",
            lease_token=second_claim["lease_token"],
            result=conflicting,
        )


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
)
def test_cancelled_attempt_report_is_retained_without_updating_projection(
    observe_project,
):
    _configure(observe_project)
    record_trace_notifications(deliveries=[_delivery(observe_project)])
    claim = claim_due_investigations(
        worker_id="node-1", engine_version="omega-v1", limit=1
    )["claims"][0]
    update_investigation_attempt(
        attempt_id=claim["attempt_id"],
        organization_id=claim["organization_id"],
        workspace_id=claim["workspace_id"],
        project_id=claim["project_id"],
        job_id=claim["job_id"],
        lease_token=claim["lease_token"],
        action="cancel",
        reason="heartbeat timeout",
    )

    receipt = _publish(
        idempotency_key="late-publication",
        lease_token=claim["lease_token"],
        result=_result(claim),
    )

    assert receipt["status"] == "accepted"
    assert receipt["grouping_status"] == "stale"
    assert receipt["active_projection_updated"] is False
    assert not TraceScanResult.no_workspace_objects.filter(
        trace_id=claim["trace_id"]
    ).exists()
    report = TraceInvestigationReport.no_workspace_objects.get(id=receipt["report_id"])
    assert report.result["gateway_accounting"][0]["cost"] is None


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
)
def test_publication_rejects_cross_project_trace_projection(
    observe_project,
    organization,
    workspace,
):
    _configure(observe_project)
    record_trace_notifications(deliveries=[_delivery(observe_project)])
    claim = claim_due_investigations(
        worker_id="node-1", engine_version="omega-v1", limit=1
    )["claims"][0]
    foreign_project = Project.objects.create(
        name="Other Observe Project",
        organization=organization,
        workspace=workspace,
        model_type=observe_project.model_type,
        trace_type="observe",
        metadata={},
        session_config=[],
    )
    TraceScanResult.no_workspace_objects.create(
        trace_id=claim["trace_id"],
        project=foreign_project,
        status="completed",
    )

    with pytest.raises(
        InvestigationConflict,
        match="trace projection belongs to a different project",
    ):
        _publish(
            idempotency_key="cross-project-publication",
            lease_token=claim["lease_token"],
            result=_result(claim),
        )
    assert not TraceInvestigationReport.no_workspace_objects.filter(
        idempotency_key="cross-project-publication"
    ).exists()


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
)
def test_reviewed_feedback_evaluation_promotion_and_rollback(
    observe_project,
    user,
):
    _configure(observe_project)
    record_trace_notifications(deliveries=[_delivery(observe_project)])
    claim = claim_due_investigations(
        worker_id="node-1", engine_version="omega-v1", limit=1
    )["claims"][0]
    publication = _publish(
        idempotency_key="memory-source-report",
        lease_token=claim["lease_token"],
        result=_result(claim),
    )
    feedback_args = {
        "actor": user,
        "organization_id": observe_project.organization_id,
        "workspace_id": observe_project.workspace_id,
        "project_id": observe_project.id,
        "report_id": publication["report_id"],
        "occurrence_id": publication["occurrence_ids"][0],
        "finding_id": "finding-1",
        "idempotency_key": "review-finding-1",
        "feedback_type": "confirm_finding",
        "comment": "The retained receipt confirms the mismatch.",
    }
    feedback = submit_reviewed_feedback(**feedback_args)
    assert submit_reviewed_feedback(**feedback_args) == feedback
    assert feedback["active_memory_unchanged"] is True

    parent = TraceInvestigationMemorySnapshot.no_workspace_objects.get(
        project=observe_project,
        status=TraceInvestigationMemoryStatus.ACTIVE,
    )
    candidate = create_memory_candidate(
        organization_id=observe_project.organization_id,
        workspace_id=observe_project.workspace_id,
        project_id=observe_project.id,
        expected_parent_snapshot_id=parent.id,
        idempotency_key="candidate-1",
        source_feedback_ids=[feedback["feedback_id"]],
        entries=[
            *parent.entries,
            {
                "id": "refund-receipt-pairing",
                "text": "Retain requested and posted amount evidence together.",
                "source_feedback_id": feedback["feedback_id"],
            },
        ],
    )
    failed_evaluation = record_memory_evaluation(
        organization_id=observe_project.organization_id,
        workspace_id=observe_project.workspace_id,
        project_id=observe_project.id,
        candidate_id=candidate["candidate_id"],
        candidate_digest=candidate["candidate_digest"],
        idempotency_key="evaluation-failed",
        cohort_id="holdout-1",
        metrics={
            "sample_count": 10,
            "precision": 0.5,
            "recall": 0.5,
            "unknown_rate": 0.2,
            "cost_usd": None,
            "latency_ms": 100.0,
        },
        passed=False,
        holdout_disjoint=True,
    )
    change_args = {
        "actor": user,
        "organization_id": observe_project.organization_id,
        "workspace_id": observe_project.workspace_id,
        "project_id": observe_project.id,
        "action": "promote",
        "idempotency_key": "promotion-1",
        "expected_current_snapshot_id": parent.id,
        "target_snapshot_id": candidate["candidate_id"],
        "target_digest": candidate["candidate_digest"],
        "evaluation_id": failed_evaluation["evaluation_id"],
    }
    with pytest.raises(InvestigationConflict, match="does not permit promotion"):
        change_active_memory(**change_args)

    passed_evaluation = record_memory_evaluation(
        organization_id=observe_project.organization_id,
        workspace_id=observe_project.workspace_id,
        project_id=observe_project.id,
        candidate_id=candidate["candidate_id"],
        candidate_digest=candidate["candidate_digest"],
        idempotency_key="evaluation-passed",
        cohort_id="holdout-2",
        metrics={
            "sample_count": 10,
            "precision": 0.9,
            "recall": 0.8,
            "unknown_rate": 0.1,
            "cost_usd": 0,
            "latency_ms": 90.0,
        },
        passed=True,
        holdout_disjoint=True,
    )
    change_args["evaluation_id"] = passed_evaluation["evaluation_id"]
    promoted = change_active_memory(**change_args)
    assert promoted["active_snapshot_id"] == candidate["candidate_id"]

    record_trace_notifications(deliveries=[_delivery(observe_project, offset=99)])
    next_claim = claim_due_investigations(
        worker_id="node-2", engine_version="omega-v1", limit=1
    )["claims"][0]
    assert next_claim["memory"]["snapshot_id"] == str(candidate["candidate_id"])
    assert len(next_claim["memory"]["entries"]) == 2

    rolled_back = change_active_memory(
        actor=user,
        organization_id=observe_project.organization_id,
        workspace_id=observe_project.workspace_id,
        project_id=observe_project.id,
        action="rollback",
        idempotency_key="rollback-1",
        expected_current_snapshot_id=candidate["candidate_id"],
        target_snapshot_id=parent.id,
        target_digest=parent.digest,
        evaluation_id=None,
    )
    assert rolled_back["active_snapshot_id"] == parent.id
    config = TraceScanConfig.no_workspace_objects.get(project=observe_project)
    assert config.omega_memory == parent.entries


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
)
def test_feedback_rejects_member_restricted_to_another_workspace(
    observe_project,
    organization,
    user,
):
    _configure(observe_project)
    record_trace_notifications(deliveries=[_delivery(observe_project)])
    claim = claim_due_investigations(
        worker_id="node-1", engine_version="omega-v1", limit=1
    )["claims"][0]
    publication = _publish(
        idempotency_key="restricted-feedback-source",
        lease_token=claim["lease_token"],
        result=_result(claim),
    )

    restricted = User.objects.create_user(
        email="restricted-workspace@futureagi.com",
        password="testpassword123",
        name="Restricted Workspace Member",
    )
    org_membership = OrganizationMembership.no_workspace_objects.create(
        user=restricted,
        organization=organization,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    other_workspace = Workspace.no_workspace_objects.create(
        name="Other Workspace",
        organization=organization,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.create(
        workspace=other_workspace,
        user=restricted,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=org_membership,
        is_active=True,
    )

    with pytest.raises(InvestigationNotFound, match="project scope was not found"):
        submit_reviewed_feedback(
            actor=restricted,
            organization_id=observe_project.organization_id,
            workspace_id=observe_project.workspace_id,
            project_id=observe_project.id,
            report_id=publication["report_id"],
            occurrence_id=publication["occurrence_ids"][0],
            finding_id="finding-1",
            idempotency_key="restricted-feedback",
            feedback_type="confirm_finding",
            comment="Must not cross the workspace boundary.",
        )


@override_settings(INTERNAL_API_SECRET="test-secret")
def test_internal_endpoint_requires_service_auth_and_accepts_hex_span_id(
    client, observe_project
):
    payload = {"deliveries": [_delivery(observe_project)]}
    path = "/tracer/internal/error-feed-v2/notifications/"

    assert client.post(path, payload, content_type="application/json").status_code in {
        401,
        403,
    }
    response = client.post(
        path,
        payload,
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer test-secret",
    )
    assert response.status_code == 200
    assert response.json()["accepted_events"] == 1
