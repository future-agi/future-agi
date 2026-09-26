"""Per-call Debug Analysis of a simulation run and its Omega claim boundaries."""

import uuid
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import override_settings
from django.utils import timezone

from simulate.models import Scenarios
from simulate.models.hosted_harness import HostedHarnessJob, HostedHarnessScenario
from simulate.models.run_test import RunTest
from simulate.models.test_execution import CallExecution, TestExecution
from tracer.models.trace_grouping import (
    GroupingFeatureState,
    TraceGroupingFeatureJob,
)
from tracer.models.trace_investigation import (
    TraceInvestigationJob,
    TraceInvestigationJobState,
    TraceInvestigationReport,
)
from tracer.queries.grouping import export_grouping_snapshot
from tracer.services.grouping import context as grouping_context
from tracer.services.grouping.control import claim_feature_jobs, claim_grouping_work
from tracer.services.grouping.feature_completion import complete_feature_job
from tracer.services.simulation_diagnosis import _is_caller_fault
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
from tracer.tests.test_grouping_runtime import FakeFeatureStore, _feature_rows

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


def _failure_result(claim, call):
    """A completed Omega report finding one failure in `call`."""
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
    return result


def test_debug_investigates_each_call_as_its_own_job(
    auth_client, organization, workspace
):
    scenario = Scenarios.objects.create(
        name="Refund",
        source="Refund policy",
        organization=organization,
        workspace=workspace,
    )
    execution, first_call = _execution(
        organization, workspace, scenario, "run", CallExecution.CallStatus.COMPLETED
    )
    second_call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.FAILED,
        call_summary="second call",
    )
    url = f"/simulate/test-executions/{execution.id}/debug-analysis/"

    assert auth_client.post(url).status_code == 202
    assert auth_client.post(url).status_code == 202
    jobs = TraceInvestigationJob.no_workspace_objects.filter(test_execution=execution)
    assert {job.call_execution_id for job in jobs} == {first_call.id, second_call.id}

    claims = claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=2
    )["claims"]
    read = set()
    for claim in claims:
        page = simulation_evidence_page(
            attempt_id=claim["attempt_id"], lease_token=claim["lease_token"], cursor=0
        )
        # A claim reads exactly one call, the way a trace claim reads one trace.
        assert page["total_calls"] == 1
        read.add(page["calls"][0]["call_execution_id"])
    assert read == {str(first_call.id), str(second_call.id)}


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
    job = TraceInvestigationJob.no_workspace_objects.get(call_execution=call)
    job.state = TraceInvestigationJobState.CANCELLED
    job.save(update_fields=["state"])
    assert auth_client.get(url).json()["status"] == "failed"

    retried = auth_client.post(url)
    assert retried.status_code == 202
    assert retried.json()["status"] == "pending"
    assert (
        TraceInvestigationJob.no_workspace_objects.filter(
            test_execution=execution
        ).count()
        == 1
    )
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
    # A sibling call in the same run: the report covers only its own call.
    CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
    )
    result = _failure_result(claim, call)

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


def test_debug_evidence_carries_the_runs_live_eval_verdicts(
    auth_client, organization, workspace
):
    scenario = Scenarios.objects.create(
        name="Refund",
        source="Refund policy",
        organization=organization,
        workspace=workspace,
    )
    execution, call = _execution(
        organization, workspace, scenario, "evals", CallExecution.CallStatus.COMPLETED
    )
    call.eval_outputs = {
        "harness-check": {
            "name": "answers_in_callers_language",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "reason": "Replied in Spanish throughout.",
            "source": "harness",
            "status": "completed",
        },
        # A verdict whose config no longer exists is not evidence.
        str(uuid.uuid4()): {"name": "removed_eval", "output": "Failed"},
    }
    call.save(update_fields=["eval_outputs"])
    auth_client.post(f"/simulate/test-executions/{execution.id}/debug-analysis/")
    claim = claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=1
    )["claims"][0]

    page = simulation_evidence_page(
        attempt_id=claim["attempt_id"], lease_token=claim["lease_token"], cursor=0
    )

    assert page["calls"][0]["evaluations"] == [
        {
            "name": "answers_in_callers_language",
            "value": "Passed",
            "passed": True,
            "reason": "Replied in Spanish throughout.",
        }
    ]


@override_settings(INTERNAL_API_SECRET="test-secret")
def test_debug_reports_of_sibling_calls_stay_current_for_grouping(
    auth_client, organization, workspace
):
    scenario = Scenarios.objects.create(
        name="Refund",
        source="Refund policy",
        organization=organization,
        workspace=workspace,
    )
    execution, first_call = _execution(
        organization, workspace, scenario, "run", CallExecution.CallStatus.COMPLETED
    )
    CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
    )
    auth_client.post(f"/simulate/test-executions/{execution.id}/debug-analysis/")
    claims = claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=2
    )["claims"]
    reports = []
    for claim in claims:
        call = TraceInvestigationJob.no_workspace_objects.get(
            id=claim["job_id"]
        ).call_execution
        result = _failure_result(claim, call)
        receipt = publish_investigation(
            idempotency_key=str(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            result=result,
            wire_result_digest=result["result_digest"],
        )
        reports.append(receipt["report_id"])

    # One call's report must not replace its sibling's: both stay current and
    # keep their grouping work, so the run's calls can cluster together.
    current = TraceInvestigationReport.no_workspace_objects.filter(
        id__in=reports, is_current=True
    )
    assert current.count() == 2
    live = TraceGroupingFeatureJob.no_workspace_objects.filter(
        report_id__in=reports
    ).exclude(state=GroupingFeatureState.SUPERSEDED)
    assert live.count() == 2


def _ready_features():
    for feature in claim_feature_jobs(worker_id="test-feature-worker", limit=10)[
        "claims"
    ]:
        complete_feature_job(
            feature_job_id=uuid.UUID(feature["feature_job_id"]),
            lease_token=feature["lease_token"],
            status="ready",
            features=_feature_rows(feature["snapshot"]),
            store=FakeFeatureStore(),
        )


def _publish(claim):
    call = TraceInvestigationJob.no_workspace_objects.get(
        id=claim["job_id"]
    ).call_execution
    result = _failure_result(claim, call)
    publish_investigation(
        idempotency_key=str(claim["attempt_id"]),
        lease_token=claim["lease_token"],
        result=result,
        wire_result_digest=result["result_digest"],
    )


@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
    ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="10",
    ERROR_FEED_GROUPING_WORK_BUDGET_USD="10",
    ERROR_FEED_GROUPING_TENANT_BUDGET_USD="10",
)
def test_debug_groups_a_run_once_every_call_is_read(
    auth_client, organization, workspace, monkeypatch
):
    monkeypatch.setattr(grouping_context, "GroupingFeatureStore", FakeFeatureStore)
    scenario = Scenarios.objects.create(
        name="Refund",
        source="Refund policy",
        organization=organization,
        workspace=workspace,
    )
    execution, _ = _execution(
        organization, workspace, scenario, "run", CallExecution.CallStatus.COMPLETED
    )
    CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
    )
    auth_client.post(f"/simulate/test-executions/{execution.id}/debug-analysis/")
    first, second = claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=2
    )["claims"]
    _publish(first)
    _ready_features()

    # One call is still being read: grouping it alone would show discovery a
    # single call, so the run waits.
    assert claim_grouping_work(worker_id="test-f6-worker", limit=1)["claims"] == []

    _publish(second)
    _ready_features()

    # The first call's work is still held back from its wait; it joins anyway.
    (claim,) = claim_grouping_work(worker_id="test-f6-worker", limit=1)["claims"]
    # The whole run is one cohort, so a failure shared across calls meets its peers.
    assert len(claim["pending_snapshots"]) == 2


def test_debug_counts_calls_whose_own_analysis_ended_without_a_report(
    auth_client, organization, workspace
):
    scenario = Scenarios.objects.create(
        name="Refund",
        source="Refund policy",
        organization=organization,
        workspace=workspace,
    )
    execution, _ = _execution(
        organization, workspace, scenario, "run", CallExecution.CallStatus.COMPLETED
    )
    CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
    )
    url = f"/simulate/test-executions/{execution.id}/debug-analysis/"
    auth_client.post(url)
    published, abandoned = claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=2
    )["claims"]
    job = TraceInvestigationJob.no_workspace_objects.get(id=published["job_id"])
    result = _failure_result(published, job.call_execution)
    publish_investigation(
        idempotency_key=str(published["attempt_id"]),
        lease_token=published["lease_token"],
        result=result,
        wire_result_digest=result["result_digest"],
    )
    abandoned_call_id = TraceInvestigationJob.no_workspace_objects.get(
        id=abandoned["job_id"]
    ).call_execution_id
    TraceInvestigationJob.no_workspace_objects.filter(id=abandoned["job_id"]).update(
        state=TraceInvestigationJobState.CANCELLED
    )

    body = auth_client.get(url).json()
    # The run still has an answer, but it must not read the unread call as clean.
    assert body["status"] == "completed"
    assert body["summary"]["unanalyzed_call_ids"] == [str(abandoned_call_id)]

    # A call deleted after its job was queued must not break the polled read.
    CallExecution.objects.filter(id=abandoned_call_id).update(deleted=True)
    assert auth_client.get(url).status_code == 200


def _hosted_run(organization, workspace, execution, catalogue=None):
    """An environment job holding one authored scenario, and the run's child job."""

    def job(**fields):
        return HostedHarnessJob.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            run_id=uuid.uuid4(),
            idempotency_key=uuid.uuid4().hex,
            request_digest="digest",
            schema_version="1.6",
            seed=1,
            artifact_level="standard",
            max_artifact_bytes=1024,
            deadline_at=timezone.now() + timedelta(hours=1),
            scenario_count=1,
            payload={"metadata": {}, "runtime": {"max_duration_seconds": 600}},
            **fields,
        )

    environment = job(
        stage_outputs=[{"kind": "sub_goals", "data": {"sub_goals": catalogue or []}}]
    )
    job(environment=environment, run_test=execution.run_test)
    HostedHarnessScenario.no_workspace_objects.create(
        job=environment,
        scenario_key="pin-booking",
        use_case="Book a guest ride",
        sub_goals=["exact_greeting", "spoken_pin_guidance"],
        tests="the agent books after a spoken PIN",
    )


@override_settings(INTERNAL_API_SECRET="test-secret")
def test_debug_findings_are_filed_under_the_calls_authored_goals(
    auth_client, organization, workspace
):
    scenario = Scenarios.objects.create(
        name="Guest rides",
        source="Guest booking",
        organization=organization,
        workspace=workspace,
    )
    execution, call = _execution(
        organization, workspace, scenario, "run", CallExecution.CallStatus.COMPLETED
    )
    call.call_metadata = {"harness_scenario_key": "pin-booking"}
    call.save(update_fields=["call_metadata"])
    _hosted_run(organization, workspace, execution)
    auth_client.post(f"/simulate/test-executions/{execution.id}/debug-analysis/")
    (claim,) = claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=1
    )["claims"]

    page = simulation_evidence_page(
        attempt_id=claim["attempt_id"], lease_token=claim["lease_token"], cursor=0
    )
    assert page["calls"][0]["goals"] == {
        "use_case": "Book a guest ride",
        "sub_goals": ["exact_greeting", "spoken_pin_guidance"],
        "expected_outcome": "the agent books after a spoken PIN",
    }

    result = _failure_result(claim, call)
    invented = deepcopy(result["findings"][0])
    invented["finding_id"] = "invented"
    invented["requirement_id"] = "polite_tone"
    result["findings"][0]["requirement_id"] = "exact_greeting"
    result["findings"].append(invented)
    result["requirement_checks"] = [
        {**result["requirement_checks"][0], "requirement_id": name}
        for name in ("exact_greeting", "polite_tone")
    ]
    result["result_digest"] = canonical_wire_result_digest(result)
    publish_investigation(
        idempotency_key=str(claim["attempt_id"]),
        lease_token=claim["lease_token"],
        result=result,
        wire_result_digest=result["result_digest"],
    )

    findings = auth_client.get(
        f"/simulate/test-executions/{execution.id}/debug-analysis/"
    ).json()["findings"]
    # Only a sub-goal the call was authored to test becomes a goal.
    assert sorted(f["goal"] or "" for f in findings) == ["", "exact_greeting"]


@override_settings(INTERNAL_API_SECRET="test-secret")
def test_debug_diagnosis_counts_broken_goals_from_the_evals(
    auth_client, organization, workspace
):
    scenario = Scenarios.objects.create(
        name="Guest rides",
        source="Guest booking",
        organization=organization,
        workspace=workspace,
    )
    execution, greeted = _execution(
        organization, workspace, scenario, "run", CallExecution.CallStatus.COMPLETED
    )
    looped = CallExecution.objects.create(
        test_execution=execution, scenario=scenario, status="completed"
    )
    derailed = CallExecution.objects.create(
        test_execution=execution, scenario=scenario, status="completed"
    )
    verdicts = {
        greeted.id: [
            {"name": "exact_greeting", "held": False, "judged": True},
            {"name": "spoken_pin_guidance", "held": True, "judged": True},
            {"name": "ride_recorded", "held": True, "judged": False},
            {"judged": True, "held": False},  # malformed: no name
        ],
        looped.id: [
            # Nothing decided this one, so it was not tested on this call.
            {"name": "exact_greeting", "held": None, "judged": True},
            {"name": "spoken_pin_guidance", "held": True, "judged": True},
            # A code checkpoint is a goal too.
            {"name": "ride_recorded", "held": False, "judged": False},
        ],
        derailed.id: [
            {"name": "exact_greeting", "held": True, "judged": True},
            {"name": "ride_recorded", "held": True, "judged": False},
        ],
    }
    for call in (greeted, looped, derailed):
        call.call_metadata = {
            "harness_scenario_key": "pin-booking",
            "hosted_harness_receipt": {"sub_goals": verdicts[call.id]},
        }
        call.save(update_fields=["call_metadata"])
    _hosted_run(
        organization,
        workspace,
        execution,
        catalogue=[
            {
                "name": "exact_greeting",
                "what": "Opens with the exact greeting",
                "judged": "Pass when the agent says the greeting verbatim.",
            }
        ],
    )
    url = f"/simulate/test-executions/{execution.id}/debug-analysis/"
    auth_client.post(url)
    claims = claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=3
    )["claims"]
    statements = {
        greeted.id: [
            ("exact_greeting", "It opened with 'This call is being recorded' first."),
            ("spoken_pin_guidance", "It asked for the PIN twice."),
        ],
        looped.id: [
            ("exact_greeting", "The caller simulator repeated its PIN and hung up."),
            ("exact_greeting", "It skipped the booking offer in its greeting."),
            ("fare_quote", "It never quoted the fare."),
        ],
        derailed.id: [
            ("exact_greeting", "The caller simulator kept repeating its PIN."),
            (
                "pin_readback",
                "It read the caller's PIN back as 'seven sixty two'.",
            ),
        ],
    }
    for claim in claims:
        call = TraceInvestigationJob.no_workspace_objects.get(
            id=claim["job_id"]
        ).call_execution
        result = _failure_result(claim, call)
        template = result["findings"][0]
        result["findings"] = [
            {
                **deepcopy(template),
                "finding_id": f"f{i}",
                "requirement_id": goal,
                "statement": statement,
            }
            for i, (goal, statement) in enumerate(statements[call.id])
        ]
        result["requirement_checks"] = [
            {**result["requirement_checks"][0], "requirement_id": goal}
            for goal in dict(statements[call.id])
        ]
        result["result_digest"] = canonical_wire_result_digest(result)
        publish_investigation(
            idempotency_key=str(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            result=result,
            wire_result_digest=result["result_digest"],
        )

    # A call the run page shows as errored never counts, analysed or not.
    errored = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status="completed",
        call_metadata={"harness_outcome_status": "errored"},
    )

    body = auth_client.get(url).json()
    goals = {goal["goal"]: goal for goal in body["goals"]}
    greeting = goals["exact_greeting"]
    # The eval decides which calls broke; the catalogue names the goal.
    assert greeting["label"] == "Opens with the exact greeting"
    assert greeting["broken_call_ids"] == [str(greeted.id)]
    assert greeting["tested_call_count"] == 2
    assert len(greeting["ways"]) == 1
    assert goals["ride_recorded"]["broken_call_ids"] == [str(looped.id)]
    assert goals["ride_recorded"]["tested_call_count"] == 3
    # A finding on a goal the eval passed is never shown: the eval is the verdict.
    # One on a goal the eval left undecided, or on no authored goal, is a one-off.
    # An agent mistake on a call our caller derailed is still the agent's.
    assert [way["title"] for way in body["one_offs"]] == [
        "It skipped the booking offer in its greeting.",
        "It never quoted the fare.",
        "It read the caller's PIN back as 'seven sixty two'.",
    ]
    # A caller-fault finding is never shown; the evals still measure its call.
    assert all("simulator" not in w["title"] for w in body["one_offs"])
    assert body["summary"]["excluded_call_ids"] == [str(errored.id)]
    assert body["summary"]["measured_call_count"] == 3
    assert body["summary"]["broken_call_count"] == 2
    assert body["summary"]["unanalyzed_call_ids"] == []


@pytest.mark.parametrize(
    "kind, statement, ours",
    [
        # Our caller never set the scenario up, so nothing about the agent was tested.
        (
            "untriggered_sub_goal_evaluation",
            "The call ended without the caller challenging the assistant.",
            True,
        ),
        ("pin_loop", "The caller simulator repeated its PIN and hung up.", True),
        # The agent's own mistake while our caller misbehaved is still the agent's.
        ("incorrect_pin_readback", "It read the caller's PIN back as 762.", False),
    ],
)
def test_debug_caller_faults_are_ours_not_the_agents(kind, statement, ours):
    assert _is_caller_fault(SimpleNamespace(kind=kind, statement=statement)) is ours
