"""Debug failures' rule, read for real: it stops a new diagnosis, not one already made.

This module does not use the ``_debug_failures_readable`` stub.
"""

import pytest
from django.utils import timezone

from simulate.models import Scenarios
from simulate.models.run_test import RunTest
from simulate.models.test_execution import CallExecution, TestExecution
from tracer.models.trace_investigation import (
    InvestigationWorkload,
    TraceInvestigationJob,
    TraceInvestigationJobState,
)
from tracer.services.simulation_investigation import (
    SimulationInvestigationConflict,
    _project_for_execution,
    ensure_simulation_investigation,
)
from tracer.services.trace_investigation import (
    claim_due_investigations,
    publish_investigation,
)
from tracer.tests.test_simulation_investigation import _failure_result

pytestmark = pytest.mark.django_db


def _small_run(organization, workspace, calls=5):
    """A finished run of `calls` connected calls, none failing: the rule blocks it."""
    run = RunTest.objects.create(
        name="Small", organization=organization, workspace=workspace
    )
    execution = TestExecution.objects.create(
        run_test=run, status=TestExecution.ExecutionStatus.COMPLETED
    )
    scenario = Scenarios.objects.create(
        name="Refund",
        source="Refund policy",
        organization=organization,
        workspace=workspace,
    )
    for _ in range(calls):
        CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            status=CallExecution.CallStatus.COMPLETED,
            duration_seconds=30,
        )
    return execution


def _run_where_nothing_failed(organization, workspace):
    """A finished run of 15 connected calls, none failing: the rule blocks it."""
    return _small_run(organization, workspace, calls=15)


def _diagnosed_before_the_rule(execution):
    """The run's jobs as a diagnosis made before the rule left them."""
    project = _project_for_execution(execution)
    for call in CallExecution.objects.filter(test_execution=execution):
        TraceInvestigationJob.objects.create(
            project=project,
            test_execution=execution,
            call_execution=call,
            organization_id=execution.run_test.organization_id,
            workspace_id=execution.run_test.workspace_id,
            workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION,
            not_before=timezone.now(),
        )


def _jobs(execution):
    return {
        job.id: (job.generation, job.state)
        for job in TraceInvestigationJob.no_workspace_objects.filter(
            test_execution=execution
        )
    }


def _url(execution):
    return f"/simulate/test-executions/{execution.id}/debug-analysis/"


def test_a_small_run_with_no_diagnosis_is_refused(auth_client, organization, workspace):
    execution = _small_run(organization, workspace)

    with pytest.raises(SimulationInvestigationConflict) as refused:
        ensure_simulation_investigation(execution)
    response = auth_client.post(_url(execution))

    assert refused.value.code == "investigation_too_few_calls"
    assert response.status_code == 409
    assert response.json()["code"] == "investigation_too_few_calls"
    assert _jobs(execution) == {}


def test_a_diagnosis_made_before_the_rule_stays_openable(
    auth_client, organization, workspace
):
    execution = _small_run(organization, workspace)
    _diagnosed_before_the_rule(execution)
    # A project's claims are capped, so read the calls until none is due.
    while claims := claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=5
    )["claims"]:
        for claim in claims:
            job = TraceInvestigationJob.no_workspace_objects.get(id=claim["job_id"])
            result = _failure_result(claim, job.call_execution)
            publish_investigation(
                idempotency_key=str(claim["attempt_id"]),
                lease_token=claim["lease_token"],
                result=result,
                wire_result_digest=result["result_digest"],
            )
    before = _jobs(execution)
    assert {state for _, state in before.values()} == {
        TraceInvestigationJobState.COMPLETED
    }

    ensure_simulation_investigation(execution)
    response = auth_client.post(_url(execution))

    assert response.status_code == 202
    assert response.json()["status"] == "completed"
    assert response.json()["findings"]
    assert _jobs(execution) == before


@pytest.mark.parametrize(
    ("run", "code"),
    [
        (_small_run, "investigation_too_few_calls"),
        (_run_where_nothing_failed, "investigation_nothing_failed"),
    ],
    ids=["too_few_calls", "nothing_failed"],
)
def test_a_blocked_runs_unread_diagnosis_is_refused(
    auth_client, organization, workspace, run, code
):
    execution = run(organization, workspace)
    _diagnosed_before_the_rule(execution)
    TraceInvestigationJob.no_workspace_objects.filter(test_execution=execution).update(
        state=TraceInvestigationJobState.CANCELLED
    )
    before = _jobs(execution)

    response = auth_client.post(_url(execution))

    # Nothing in it was read, and a read again would be new spend.
    assert response.status_code == 409
    assert response.json()["code"] == code
    assert _jobs(execution) == before
