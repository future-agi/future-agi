"""Debug failures' rule, read for real: it stops a new diagnosis, not one already made,
and the calls of a run that completed, failed or was cancelled are read alike.

This module does not use the ``_debug_failures_readable`` stub.
"""

import pytest
from django.utils import timezone

from simulate.models import Scenarios
from simulate.models.run_test import RunTest
from simulate.models.test_execution import CallExecution, TestExecution
from tracer.models.project import Project, ProjectSourceChoices
from tracer.models.trace_investigation import (
    InvestigationWorkload,
    TraceInvestigationJob,
    TraceInvestigationJobState,
)
from tracer.services.simulation_investigation import (
    SimulationInvestigationConflict,
    _project_for_execution,
    ensure_simulation_investigation,
    simulation_evidence_page,
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


def test_a_conflict_outside_the_rule_is_not_read_as_a_run_in_progress(
    auth_client, organization, workspace
):
    execution = _eligible_run(
        organization, workspace, TestExecution.ExecutionStatus.COMPLETED
    )
    # The run's diagnosis project is scoped to another run.
    Project.objects.create(
        organization=organization,
        workspace=workspace,
        name=f"Simulation Debug {execution.id}",
        trace_type="experiment",
        model_type="Numeric",
        source=ProjectSourceChoices.SIMULATOR.value,
        metadata={"simulation_test_execution_id": "another run"},
    )

    response = auth_client.post(_url(execution))

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"
    assert _jobs(execution) == {}


def _read_every_call():
    """Plays the worker: claims each due call, reads its evidence, and publishes a
    report finding one failure. Returns the calls the evidence pages served."""
    served = set()
    # A project's claims are capped, so read the calls until none is due.
    while claims := claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=5
    )["claims"]:
        for claim in claims:
            page = simulation_evidence_page(
                attempt_id=claim["attempt_id"],
                lease_token=claim["lease_token"],
                cursor=0,
            )
            served.update(call["call_execution_id"] for call in page["calls"])
            job = TraceInvestigationJob.no_workspace_objects.get(id=claim["job_id"])
            result = _failure_result(claim, job.call_execution)
            publish_investigation(
                idempotency_key=str(claim["attempt_id"]),
                lease_token=claim["lease_token"],
                result=result,
                wire_result_digest=result["result_digest"],
            )
    return served


def _eligible_run(organization, workspace, status):
    """A run the counts let through, 14 completed calls and one failed, in `status`.
    A cancel also closed one call before it connected."""
    execution = _small_run(organization, workspace, calls=14)
    scenario = execution.calls.first().scenario
    CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        status=CallExecution.CallStatus.FAILED,
        duration_seconds=30,
    )
    CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        status=CallExecution.CallStatus.CANCELLED,
        duration_seconds=0,
    )
    execution.status = status
    execution.save(update_fields=["status"])
    return execution


def test_a_diagnosis_made_before_the_rule_stays_openable(
    auth_client, organization, workspace
):
    execution = _small_run(organization, workspace)
    _diagnosed_before_the_rule(execution)
    _read_every_call()
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


@pytest.mark.parametrize(
    "status",
    [TestExecution.ExecutionStatus.CANCELLED, TestExecution.ExecutionStatus.FAILED],
)
def test_a_stopped_runs_calls_are_read_to_a_diagnosis(
    auth_client, organization, workspace, status
):
    execution = _eligible_run(organization, workspace, status)
    never_connected = execution.calls.get(status=CallExecution.CallStatus.CANCELLED)
    connected = {
        str(call_id)
        for call_id in execution.calls.exclude(id=never_connected.id).values_list(
            "id", flat=True
        )
    }

    started = auth_client.post(_url(execution))
    served = _read_every_call()
    diagnosis = auth_client.get(_url(execution)).json()

    assert started.status_code == 202
    # A call that never connected has nothing to read, so it costs no read.
    assert not TraceInvestigationJob.no_workspace_objects.filter(
        call_execution=never_connected
    ).exists()
    assert served == connected
    # The diagnosis does not wait on it: every call that has a read is read.
    assert diagnosis["status"] == "completed"
    assert diagnosis["report"]["coverage"]["observed_call_count"] == len(connected)
    assert diagnosis["report"]["coverage"]["read_complete"] is True
    # Nothing was read of it, and nothing failed reading it.
    assert str(never_connected.id) not in diagnosis["summary"]["unanalyzed_call_ids"]
    assert diagnosis["findings"]


_VOICE = CallExecution.SimulationCallType.VOICE
_TEXT = CallExecution.SimulationCallType.TEXT
_CANCELLED = CallExecution.CallStatus.CANCELLED


@pytest.mark.parametrize(
    ("call_type", "call_status", "duration", "read"),
    [
        # A call a cancel closed before it started keeps no duration.
        (_VOICE, _CANCELLED, None, False),
        # A chat connected only when it completed.
        (_TEXT, _CANCELLED, None, False),
        (_VOICE, _CANCELLED, 12, True),
        (_VOICE, CallExecution.CallStatus.FAILED, 0, True),
    ],
    ids=[
        "cancelled_before_it_started",
        "cancelled_chat",
        "cancelled_after_it_connected",
        "failed_before_it_connected",
    ],
)
def test_which_calls_of_a_stopped_run_are_read(
    auth_client, organization, workspace, call_type, call_status, duration, read
):
    execution = _eligible_run(
        organization, workspace, TestExecution.ExecutionStatus.CANCELLED
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=execution.calls.first().scenario,
        simulation_call_type=call_type,
        status=call_status,
        duration_seconds=duration,
    )

    auth_client.post(_url(execution))

    assert (
        TraceInvestigationJob.no_workspace_objects.filter(call_execution=call).exists()
        is read
    )


@pytest.mark.parametrize(
    "status",
    [TestExecution.ExecutionStatus.RUNNING, TestExecution.ExecutionStatus.CANCELLED],
)
def test_a_diagnosis_stays_readable_whatever_the_run_does_next(
    auth_client, organization, workspace, status
):
    execution = _eligible_run(
        organization, workspace, TestExecution.ExecutionStatus.COMPLETED
    )
    auth_client.post(_url(execution))
    _read_every_call()
    before = _jobs(execution)
    TestExecution.objects.filter(id=execution.id).update(status=status)
    if status == TestExecution.ExecutionStatus.RUNNING:
        # A rerun puts one of its calls back in flight.
        CallExecution.objects.filter(id=execution.calls.first().id).update(
            status=CallExecution.CallStatus.ONGOING
        )

    read = auth_client.get(_url(execution))
    asked_again = auth_client.post(_url(execution))

    assert read.json()["status"] == "completed"
    assert read.json()["findings"]
    assert asked_again.status_code == 202
    assert asked_again.json()["status"] == "completed"
    assert _jobs(execution) == before
