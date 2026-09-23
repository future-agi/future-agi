"""Execution-scoped Debug Analysis and Omega claim boundaries."""

import uuid
from copy import deepcopy

import pytest
from django.test import override_settings

from simulate.models import Scenarios
from simulate.models.run_test import RunTest
from simulate.models.test_execution import CallExecution, TestExecution
from tracer.models.trace_investigation import (
    TraceInvestigationJob,
    TraceInvestigationJobState,
    TraceInvestigationReport,
)
from tracer.queries.grouping import export_grouping_snapshot
from tracer.services.simulation_investigation import (
    SimulationInvestigationConflict,
    simulation_evidence_page,
)
from tracer.services.trace_investigation import (
    InvestigationConflict,
    canonical_wire_result_digest,
    claim_due_investigations,
    publish_investigation,
)

pytestmark = pytest.mark.django_db


def _execution(organization, workspace, scenario, name, call_status):
    run = RunTest.objects.create(
        name=name, organization=organization, workspace=workspace
    )
    execution = TestExecution.objects.create(
        run_test=run, status=TestExecution.ExecutionStatus.COMPLETED
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=call_status,
        call_summary=f"{name} only",
    )
    return execution, call


def test_debug_claim_reads_only_the_selected_execution(
    auth_client, organization, workspace
):
    scenario = Scenarios.objects.create(
        name="Refund",
        source="Refund policy",
        organization=organization,
        workspace=workspace,
    )
    selected, selected_call = _execution(
        organization, workspace, scenario, "selected", CallExecution.CallStatus.FAILED
    )
    other, other_call = _execution(
        organization, workspace, scenario, "other", CallExecution.CallStatus.COMPLETED
    )
    url = f"/simulate/test-executions/{selected.id}/debug-analysis/"

    assert auth_client.get(url).json()["status"] == "not_requested"
    started = auth_client.post(url)
    assert started.status_code == 202
    assert started.json()["status"] == "pending"
    assert auth_client.post(url).json()["job_id"] == started.json()["job_id"]
    assert (
        auth_client.get(f"/simulate/test-executions/{other.id}/debug-analysis/").json()[
            "status"
        ]
        == "not_requested"
    )

    claim = claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=1
    )["claims"][0]
    assert claim["workload_type"] == "simulation_test_execution"
    assert claim["test_execution_id"] == selected.id
    assert "trace_id" not in claim
    page = simulation_evidence_page(
        attempt_id=claim["attempt_id"], lease_token=claim["lease_token"], cursor=0
    )
    assert page["total_calls"] == 1
    assert page["next_cursor"] == 1
    assert page["calls"][0]["call_execution_id"] == str(selected_call.id)
    assert page["calls"][0]["call_summary"] == "selected only"
    assert str(other_call.id) not in str(page)
    with pytest.raises(SimulationInvestigationConflict, match="Attempt is not active"):
        simulation_evidence_page(
            attempt_id=claim["attempt_id"], lease_token="wrong-lease", cursor=0
        )


def test_debug_rejects_nonterminal_calls_then_retries_cancelled_job(
    auth_client, organization, workspace
):
    scenario = Scenarios.objects.create(
        name="Billing",
        source="Billing policy",
        organization=organization,
        workspace=workspace,
    )
    execution, call = _execution(
        organization, workspace, scenario, "retry", CallExecution.CallStatus.PENDING
    )
    url = f"/simulate/test-executions/{execution.id}/debug-analysis/"
    assert auth_client.post(url).status_code == 409
    assert auth_client.get(url).json()["status"] == "not_requested"

    call.status = CallExecution.CallStatus.COMPLETED
    call.save(update_fields=["status"])
    first = auth_client.post(url).json()
    job = TraceInvestigationJob.no_workspace_objects.get(id=uuid.UUID(first["job_id"]))
    job.state = TraceInvestigationJobState.CANCELLED
    job.save(update_fields=["state"])
    assert auth_client.get(url).json()["status"] == "failed"

    retried = auth_client.post(url)
    assert retried.status_code == 202
    assert retried.json()["status"] == "pending"
    assert retried.json()["job_id"] == first["job_id"]
    assert retried.json()["generation"] == first["generation"] + 1
    assert auth_client.post(url).json()["generation"] == retried.json()["generation"]


@override_settings(INTERNAL_API_SECRET="test-secret")
def test_debug_publication_retains_scoped_findings_for_grouping(
    auth_client, client, organization, workspace
):
    scenario = Scenarios.objects.create(
        name="Refund",
        source="Refund policy",
        organization=organization,
        workspace=workspace,
    )
    execution, call = _execution(
        organization, workspace, scenario, "selected", CallExecution.CallStatus.FAILED
    )
    _, foreign_call = _execution(
        organization, workspace, scenario, "other", CallExecution.CallStatus.FAILED
    )
    url = f"/simulate/test-executions/{execution.id}/debug-analysis/"
    assert auth_client.post(url).status_code == 202
    claim = claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=1
    )["claims"][0]
    result = {
        "contract_version": claim["contract_version"],
        "workload_type": claim["workload_type"],
        "organization_id": claim["organization_id"],
        "workspace_id": claim["workspace_id"],
        "project_id": claim["project_id"],
        "job_id": claim["job_id"],
        "generation": claim["generation"],
        "attempt_id": claim["attempt_id"],
        "test_execution_id": claim["test_execution_id"],
        "engine_version": claim["engine_version"],
        "read_cutoff": claim["read_cutoff"],
        "memory_snapshot_id": claim["memory"]["snapshot_id"],
        "memory_digest": claim["memory"]["digest"],
        "evidence_digest": f"sha256:{'e' * 64}",
        "execution_status": "completed",
        "outcome": "failure",
        "findings": [
            {
                "finding_id": "refund-mismatch",
                "kind": "outcome",
                "statement": "The refund amount was incorrect.",
                "requirement_id": "refund-policy",
                "evidence_ids": ["refund-evidence"],
                "recovery": "not_observed",
                "attribution": {
                    role: {
                        "status": "unknown" if role == "origin" else "supported",
                        "call_execution_id": None if role == "origin" else call.id,
                        "evidence_ids": [] if role == "origin" else ["refund-evidence"],
                    }
                    for role in ("origin", "decisive", "symptom")
                },
            }
        ],
        "requirement_checks": [
            {
                "requirement_id": "refund-policy",
                "requirement": "Refund the requested amount.",
                "status": "violated",
                "evidence_ids": ["refund-evidence"],
            }
        ],
        "evidence_receipts": [
            {
                "evidence_id": "refund-evidence",
                "call_execution_id": call.id,
                "excerpt": "requested=100 executed=10",
            }
        ],
        "verification_receipts": [],
        "coverage": {
            "scope": "completed simulation calls",
            "observed_call_count": 1,
            "read_complete": True,
        },
        "usage": {
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0,
            "cost_status": "fully_priced",
        },
        "gateway_accounting": [],
    }
    result["result_digest"] = canonical_wire_result_digest(result)

    foreign = deepcopy(result)
    foreign["evidence_receipts"][0]["call_execution_id"] = foreign_call.id
    foreign["result_digest"] = canonical_wire_result_digest(foreign)
    with pytest.raises(InvestigationConflict, match="outside execution scope"):
        publish_investigation(
            idempotency_key=str(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            result=foreign,
            wire_result_digest=foreign["result_digest"],
        )
    wire_result = deepcopy(result)
    wire_result["read_cutoff"] = claim["read_cutoff"].isoformat()
    wire_result["result_digest"] = canonical_wire_result_digest(wire_result)

    response = client.post(
        "/tracer/internal/error-feed-v2/reports/",
        {
            "idempotency_key": str(claim["attempt_id"]),
            "lease_token": claim["lease_token"],
            "result": wire_result,
        },
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer test-secret",
    )
    assert response.status_code == 200, response.json()
    receipt = response.json()
    assert receipt["status"] == "accepted"
    assert receipt["grouping_status"] == "pending"
    report = TraceInvestigationReport.no_workspace_objects.get(id=receipt["report_id"])
    assert report.test_execution_id == execution.id
    assert report.trace_id is None
    assert report.evidence_receipts.get().call_execution_id == call.id
    assert (
        auth_client.get(url).json()["findings"][0]["statement"]
        == result["findings"][0]["statement"]
    )

    snapshot = export_grouping_snapshot(report=report)
    assert snapshot["report"]["workload_type"] == "simulation_test_execution"
    assert snapshot["report"]["test_execution_id"] == str(execution.id)
    assert snapshot["report"]["evidence_receipts"][0]["call_execution_id"] == str(
        call.id
    )
    assert snapshot["report"]["trace_id"] is None
