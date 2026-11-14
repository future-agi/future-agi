"""Re-running chosen calls of a finished harness Run inside that same Run.

Each test starts from a Run whose two calls both finished and were graded, then
re-runs one of them through ``POST /simulate/test-executions/{id}/rerun-calls/``.
"""

import asyncio
from unittest.mock import patch

import pytest
from django.utils import timezone

from simulate.models import CallExecution, HostedHarnessJob, TestExecution
from simulate.services.hosted_harness import (
    create_hosted_job,
    create_selected_harness_run,
    provision_scenarios,
    record_cleanup,
    register_attempt,
)
from simulate.services.hosted_harness_gateway import HostedHarnessGateway
from simulate.tests.test_harness_provider import _v1_payload

BASE_URL = "https://harness.example.test"


def _persona(key):
    return {
        "scenario_key": key,
        "name": key,
        "role": "customer",
        "situation": f"{key} situation",
        "outcome": f"{key} outcome",
        "persona": {"name": key},
    }


def _finish(attempt, terminal_stage, provider_ref):
    attempt.provider_ref = provider_ref
    attempt.terminal_stage = terminal_stage
    attempt.terminal_event_received = True
    attempt.save(
        update_fields=[
            "provider_ref",
            "terminal_stage",
            "terminal_event_received",
            "updated_at",
        ]
    )
    return record_cleanup(attempt.id, provider_ref=provider_ref, verified_absent=True)


@pytest.fixture
def finished_run(user, workspace, settings):
    """A Run of two calls, both finished and graded, its first attempt cleaned up."""
    settings.HARNESS_PUBLIC_BASE_URL = BASE_URL
    environment, _ = create_hosted_job(
        user.organization,
        _v1_payload(scenario_count=2),
        idempotency_key="environment",
        workspace=workspace,
    )
    authoring = register_attempt(environment.id, endpoint_base_url=BASE_URL).attempt
    provision_scenarios(
        authoring,
        {
            "operation": "provision",
            "name": "Re-run suite",
            "modality": "text",
            "personas": [_persona("scenario-a"), _persona("scenario-b")],
        },
    )
    environment.refresh_from_db()
    payload = dict(environment.payload)
    payload["metadata"] = {
        **(payload.get("metadata") or {}),
        "authoring_object_key": "harness/environments/authored.tar.gz",
    }
    environment.payload = payload
    environment.state = HostedHarnessJob.State.COMPLETED
    environment.current_stage = "completed"
    environment.save(update_fields=["payload", "state", "current_stage", "updated_at"])

    run, _ = create_selected_harness_run(
        environment,
        scenario_keys=["scenario-a", "scenario-b"],
        trials=1,
        idempotency_key="run",
    )
    first = register_attempt(run.id, endpoint_base_url=BASE_URL).attempt
    for call in CallExecution.no_workspace_objects.filter(
        test_execution_id=run.test_execution_id
    ):
        call.status = CallExecution.CallStatus.COMPLETED
        call.completed_at = timezone.now()
        call.call_metadata = {**(call.call_metadata or {}), "eval_completed": True}
        call.save(update_fields=["status", "completed_at", "call_metadata"])
    _finish(first, "completed", "sandbox-1")
    TestExecution.no_workspace_objects.filter(id=run.test_execution_id).update(
        status=TestExecution.ExecutionStatus.COMPLETED
    )
    run.refresh_from_db()
    return run


@pytest.fixture
def calls(finished_run):
    chosen, other = CallExecution.no_workspace_objects.filter(
        test_execution_id=finished_run.test_execution_id
    ).order_by("call_metadata__harness_scenario_key")
    return chosen, other


@pytest.fixture
def workflow():
    with (
        patch(
            "simulate.temporal.client.hosted_harness_gateway_workflow_running",
            return_value=False,
        ) as running,
        patch(
            "simulate.temporal.client.start_hosted_harness_gateway_workflow"
        ) as start,
    ):
        yield running, start


def _rerun(auth_client, run, call):
    return auth_client.post(
        f"/simulate/test-executions/{run.test_execution_id}/rerun-calls/",
        {"rerun_type": "call_and_eval", "call_execution_ids": [str(call.id)]},
        format="json",
    )


def _status(model, obj):
    return model.no_workspace_objects.values_list("status", flat=True).get(id=obj.id)


@pytest.mark.django_db
def test_rerun_attempt_keeps_its_full_infrastructure_retry_budget(
    auth_client, finished_run, calls, workflow
):
    chosen, other = calls

    response = _rerun(auth_client, finished_run, chosen)

    assert response.status_code == 202, response.content
    assert _status(CallExecution, chosen) == CallExecution.CallStatus.PENDING
    assert _status(CallExecution, other) == CallExecution.CallStatus.COMPLETED
    rerun_attempt = register_attempt(
        finished_run.id, endpoint_base_url=BASE_URL
    ).attempt
    assert rerun_attempt.attempt_number == 2
    # A first transient sandbox loss in the re-run gets a fresh attempt, the same
    # budget a new Run's first attempt has.
    gateway = HostedHarnessGateway.__new__(HostedHarnessGateway)
    assert gateway._should_retry(rerun_attempt, "infrastructure")


@pytest.mark.django_db
def test_rerun_is_refused_while_the_previous_workflow_is_still_exiting(
    auth_client, finished_run, calls, workflow
):
    running, start = workflow
    running.return_value = True
    chosen, _ = calls

    response = _rerun(auth_client, finished_run, chosen)

    assert response.status_code == 409, response.content
    assert response.json()["error"] == "rerun_in_progress"
    start.assert_not_called()
    assert _status(CallExecution, chosen) == CallExecution.CallStatus.COMPLETED
    finished_run.refresh_from_db()
    assert finished_run.state == HostedHarnessJob.State.COMPLETED


@pytest.mark.django_db
def test_cancelled_rerun_settles_the_run_from_its_calls(
    auth_client, finished_run, calls, workflow, django_capture_on_commit_callbacks
):
    chosen, other = calls
    assert _rerun(auth_client, finished_run, chosen).status_code == 202
    rerun_attempt = register_attempt(
        finished_run.id, endpoint_base_url=BASE_URL
    ).attempt

    with django_capture_on_commit_callbacks(execute=True):
        _finish(rerun_attempt, "canceled", "sandbox-2")

    assert _status(CallExecution, chosen) == CallExecution.CallStatus.CANCELLED
    assert _status(CallExecution, other) == CallExecution.CallStatus.COMPLETED
    assert (
        _status(TestExecution, finished_run.test_execution)
        == TestExecution.ExecutionStatus.COMPLETED
    )


# Queries under asyncio.run() use their own connection, which sees only committed rows.
@pytest.mark.django_db(transaction=True)
def test_launch_failure_before_a_new_attempt_fails_the_chosen_call_with_its_reason(
    auth_client, finished_run, calls, workflow, monkeypatch
):
    from simulate.temporal.activities import hosted_harness_gateway as activities
    from simulate.temporal.types.hosted_harness_gateway import (
        HostedHarnessLaunchFailureInput,
    )

    async def _inline(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(activities, "_run_db", _inline)
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    chosen, other = calls
    assert _rerun(auth_client, finished_run, chosen).status_code == 202

    recovery = asyncio.run(
        activities.record_hosted_harness_launch_failure(
            HostedHarnessLaunchFailureInput(job_id=str(finished_run.id))
        )
    )

    # The earlier attempt finished and was cleaned up; it is not resumed.
    assert recovery.attempt_id is None
    finished_run.refresh_from_db()
    assert finished_run.state == HostedHarnessJob.State.FAILED
    assert finished_run.failure["code"] == "hosted_launch_failed"
    chosen.refresh_from_db()
    assert chosen.status == CallExecution.CallStatus.FAILED
    assert chosen.ended_reason == finished_run.failure["message"]
    assert _status(CallExecution, other) == CallExecution.CallStatus.COMPLETED
    assert (
        _status(TestExecution, finished_run.test_execution)
        == TestExecution.ExecutionStatus.COMPLETED
    )


@pytest.mark.django_db
def test_unschedulable_rerun_fails_only_the_chosen_call(
    auth_client, finished_run, calls, workflow, django_capture_on_commit_callbacks
):
    _, start = workflow
    start.side_effect = RuntimeError("temporal unavailable")
    chosen, other = calls

    with django_capture_on_commit_callbacks(execute=True):
        response = _rerun(auth_client, finished_run, chosen)

    assert response.status_code == 503, response.content
    assert response.json()["error"] == "scheduler_unavailable"
    chosen.refresh_from_db()
    assert chosen.status == CallExecution.CallStatus.FAILED
    assert chosen.ended_reason
    assert _status(CallExecution, other) == CallExecution.CallStatus.COMPLETED
    assert (
        _status(TestExecution, finished_run.test_execution)
        == TestExecution.ExecutionStatus.COMPLETED
    )
