"""Debug failures refuses a run too small to diagnose, one where nothing failed, or
one whose calls have not all finished."""

import pytest

from simulate.models import Scenarios
from simulate.models.run_test import RunTest
from simulate.models.test_execution import CallExecution, TestExecution
from tracer.models.project import Project
from tracer.models.trace_investigation import TraceInvestigationJob

pytestmark = pytest.mark.django_db

VOICE = CallExecution.SimulationCallType.VOICE
TEXT = CallExecution.SimulationCallType.TEXT
COMPLETED = CallExecution.CallStatus.COMPLETED
FAILED = CallExecution.CallStatus.FAILED


def _run(
    organization, workspace, calls, status=TestExecution.ExecutionStatus.COMPLETED
):
    """A run with one call per (type, status, duration_seconds)."""
    run = RunTest.objects.create(
        name="Refusals", organization=organization, workspace=workspace
    )
    execution = TestExecution.objects.create(run_test=run, status=status)
    scenario = Scenarios.objects.create(
        name="Refund",
        source="Refund policy",
        organization=organization,
        workspace=workspace,
    )
    for call_type, call_status, duration in calls:
        CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            simulation_call_type=call_type,
            status=call_status,
            duration_seconds=duration,
        )
    return execution


def _post(auth_client, execution):
    return auth_client.post(f"/simulate/test-executions/{execution.id}/debug-analysis/")


def _nothing_started(execution):
    assert not TraceInvestigationJob.objects.filter(test_execution=execution).exists()
    assert not Project.objects.filter(name=f"Simulation Debug {execution.id}").exists()


# 14 connected, one of them failing. A voice call that never ran and a chat that
# failed are not connected.
FOURTEEN_CONNECTED = [
    *[(VOICE, COMPLETED, 30)] * 13,
    (VOICE, FAILED, 30),
    (VOICE, COMPLETED, 0),
    (TEXT, FAILED, None),
]


def test_refuses_a_run_with_fewer_than_15_connected_calls(
    auth_client, organization, workspace
):
    execution = _run(organization, workspace, FOURTEEN_CONNECTED)

    response = _post(auth_client, execution)

    assert response.status_code == 409
    assert response.json()["code"] == "investigation_too_few_calls"
    assert (
        response.json()["detail"]
        == "You need at least 15 connected calls to debug failures."
    )
    _nothing_started(execution)


def test_refuses_a_run_where_nothing_failed(auth_client, organization, workspace):
    execution = _run(organization, workspace, [(VOICE, COMPLETED, 30)] * 15)

    response = _post(auth_client, execution)

    assert response.status_code == 409
    assert response.json()["code"] == "investigation_nothing_failed"
    assert (
        response.json()["detail"]
        == "Nothing failed in this run, so there’s nothing to debug."
    )
    _nothing_started(execution)


def test_starts_a_run_with_15_connected_calls_and_a_failure(
    auth_client, organization, workspace
):
    # A chat that completed is connected with no duration.
    execution = _run(
        organization,
        workspace,
        [*[(VOICE, COMPLETED, 30)] * 13, (TEXT, COMPLETED, None), (VOICE, FAILED, 30)],
    )

    response = _post(auth_client, execution)

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert TraceInvestigationJob.objects.filter(test_execution=execution).count() == 15


ELIGIBLE = [*[(VOICE, COMPLETED, 30)] * 14, (VOICE, FAILED, 30)]


@pytest.mark.parametrize(
    "status",
    [
        TestExecution.ExecutionStatus.PENDING,
        TestExecution.ExecutionStatus.RUNNING,
        TestExecution.ExecutionStatus.EVALUATING,
        TestExecution.ExecutionStatus.CANCELLING,
    ],
)
def test_refuses_a_run_still_in_progress(auth_client, organization, workspace, status):
    execution = _run(organization, workspace, ELIGIBLE, status=status)

    response = _post(auth_client, execution)

    assert response.status_code == 409
    assert response.json()["code"] == "execution_not_completed"
    assert (
        response.json()["detail"]
        == "Diagnosis is available once every call in this run finishes."
    )
    _nothing_started(execution)


@pytest.mark.parametrize(
    "status",
    [
        TestExecution.ExecutionStatus.COMPLETED,
        TestExecution.ExecutionStatus.CANCELLED,
        TestExecution.ExecutionStatus.FAILED,
    ],
)
def test_refuses_a_finished_run_with_a_call_that_did_not_finish(
    auth_client, organization, workspace, status
):
    # A step that closes a run can leave a call open.
    open_call = (VOICE, CallExecution.CallStatus.ANALYZING, 0)
    execution = _run(organization, workspace, [*ELIGIBLE, open_call], status=status)

    response = _post(auth_client, execution)

    assert response.status_code == 409
    assert response.json()["code"] == "execution_calls_unfinished"
    assert (
        response.json()["detail"]
        == "Some calls in this run didn’t finish, so Debug failures can’t read it."
    )
    _nothing_started(execution)


def test_the_run_page_reads_the_connected_count_the_refusal_reads(
    auth_client, organization, workspace
):
    execution = _run(organization, workspace, FOURTEEN_CONNECTED)

    response = auth_client.get(f"/simulate/v3/test-executions/{execution.id}/calls/")

    assert response.status_code == 200
    assert response.json()["execution"]["connected_calls"] == 14
