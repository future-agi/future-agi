import uuid
from copy import deepcopy
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from tracer.models.project import Project
from tracer.models.trace_investigation import (
    TraceInvestigationAttempt,
    TraceInvestigationAttemptStatus,
    TraceInvestigationJob,
    TraceInvestigationJobState,
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
        sampling_rate=1.0,
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
    _configure(observe_project)
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
