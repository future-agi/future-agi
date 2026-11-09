"""S43: execution failures disclose no driver text; refusals stay client errors.

All external workflow/runner calls are mocked; these rows need no provider.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.db import OperationalError
from rest_framework.test import APIRequestFactory, force_authenticate
from temporalio.service import RPCError, RPCStatusCode

from simulate.views.run_test import RunTestExecutionView

MARKER = "S43_PRIVATE_SQL_HOST_MARKER"


@pytest.fixture
def execution_seams(settings):
    settings.HOSTED_RUNNER_ENABLED = False
    settings.TEMPORAL_TEST_EXECUTION_ENABLED = True
    run = MagicMock()
    run.id = uuid4()
    run.organization_id = uuid4()
    run.agent_definition = None
    run.agent_version = None
    run.simulator_agent = None
    run.scenarios.filter.return_value.values_list.return_value = [uuid4()]
    with (
        patch("simulate.views.run_test.get_object_or_404", return_value=run),
        patch("simulate.views.run_test.check_scenarios_incomplete", return_value=None),
        patch(
            "simulate.views.run_test._latest_repository_harness_execution",
            return_value=None,
        ),
        patch("simulate.views.run_test.TestExecution.objects.create") as create,
        patch(
            "simulate.services.alk_simulate_ingestion.precreate_alk_sim_call_executions",
            return_value=[],
        ),
        patch.object(
            RunTestExecutionView, "_hosted_runner_eligible", return_value=True
        ),
        patch.object(RunTestExecutionView, "_hosted_runner_mode", return_value="text"),
        patch("simulate.temporal.client.start_test_execution_workflow") as temporal,
        patch("simulate.temporal.client.start_simulation_runner_workflow") as hosted,
    ):
        create.return_value.id = uuid4()
        temporal.return_value = hosted.return_value = "workflow-id"
        yield SimpleNamespace(run=run, create=create, temporal=temporal, hosted=hosted)


def _execute(run):
    request = APIRequestFactory().post("/execute/", {}, format="json")
    user = SimpleNamespace(id=uuid4(), is_authenticated=True, organization=object())
    request.organization = user.organization
    request.workspace = SimpleNamespace(id=uuid4(), is_default=False)
    force_authenticate(request, user=user)
    return RunTestExecutionView.as_view()(request, run_test_id=run.id)


@pytest.mark.parametrize("arm", ["temporal", "hosted"])
@pytest.mark.parametrize("failure", ["db", "unexpected", "unavailable", "connection"])
def test_execution_helper_errors_are_fixed(execution_seams, settings, arm, failure):
    seams = execution_seams
    settings.HOSTED_RUNNER_ENABLED = arm == "hosted"
    if failure == "db":
        seams.create.side_effect = OperationalError(MARKER)
    elif failure == "unexpected":
        getattr(seams, arm).side_effect = RuntimeError(MARKER)
    elif failure == "connection":
        getattr(seams, arm).side_effect = ConnectionError(MARKER)
    else:
        getattr(seams, arm).side_effect = RPCError(
            MARKER, RPCStatusCode.UNAVAILABLE, b""
        )
    response = _execute(seams.run)
    assert response.status_code == (
        503 if failure in {"unavailable", "connection"} else 500
    )
    assert response.data["code"] == (
        "service_unavailable" if response.status_code == 503 else "server_error"
    )
    assert MARKER not in str(response.data)
    assert "OperationalError" not in str(response.data)
    assert response.data["message"] == (
        "Test execution service is unavailable. Please try again later."
        if failure in {"unavailable", "connection"}
        else "Failed to start test execution. Check the test runs before trying again."
    )
    assert str(seams.run.id) not in str(response.data)
    assert str(seams.create.return_value.id) not in str(response.data)


def test_generic_execution_exception_has_fixed_body(execution_seams):
    with patch.object(
        RunTestExecutionView, "_execute_with_temporal", side_effect=RuntimeError(MARKER)
    ):
        response = _execute(execution_seams.run)
    assert response.status_code == 500
    assert response.data["code"] == "server_error"
    assert str(execution_seams.run.id) not in str(response.data)
    assert (
        response.data["message"]
        == "Failed to execute test. Check the test runs before trying again."
    )
    assert MARKER not in str(response.data)


@pytest.mark.parametrize("arm", ["temporal", "hosted"])
def test_normal_execution_still_starts(execution_seams, settings, arm):
    settings.HOSTED_RUNNER_ENABLED = arm == "hosted"
    response = _execute(execution_seams.run)
    assert response.status_code == 200
    assert response.data["status"] == "started"
    getattr(execution_seams, arm).assert_called_once()


@pytest.mark.parametrize(
    "message",
    [
        "Test is not ready to run",
        "No scenarios found for this test",
        "Run test not found",
    ],
)
def test_fixed_legacy_client_refusals_preserved(execution_seams, settings, message):
    settings.TEMPORAL_TEST_EXECUTION_ENABLED = False
    executor = MagicMock()
    executor.execute_test.return_value = {"success": False, "error": message}
    with patch("simulate.views.run_test.TestExecutor", return_value=executor):
        response = _execute(execution_seams.run)
    assert response.status_code == 400
    assert response.data["message"] == message


def test_unexpected_legacy_error_result_is_not_echoed(execution_seams, settings):
    settings.TEMPORAL_TEST_EXECUTION_ENABLED = False
    executor = MagicMock()
    executor.execute_test.return_value = {"success": False, "error": MARKER}
    with patch("simulate.views.run_test.TestExecutor", return_value=executor):
        response = _execute(execution_seams.run)
    assert response.status_code == 500
    assert response.data["code"] == "server_error"
    assert str(execution_seams.run.id) not in str(response.data)
    assert (
        response.data["message"]
        == "Failed to start test execution. Check the test runs before trying again."
    )
    assert MARKER not in str(response.data)


def test_no_scenarios_remains_fixed_client_refusal(execution_seams):
    execution_seams.run.scenarios.filter.return_value.values_list.return_value = []
    response = _execute(execution_seams.run)
    assert response.status_code == 400
    assert (
        response.data["message"]
        == "At least one scenario is required to execute the test."
    )
    execution_seams.create.assert_not_called()
