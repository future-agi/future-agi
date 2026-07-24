from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from rest_framework import status

from simulate.models.test_execution import TestExecution as ExecutionModel
from simulate.views.run_test import (
    RunTestExecutionView,
)
from simulate.views.run_test import TestExecutionCancelView as CancelExecutionView


@pytest.mark.unit
def test_execute_always_dispatches_to_temporal(settings):
    settings.TEMPORAL_TEST_EXECUTION_ENABLED = False
    organization = SimpleNamespace(id=uuid4())
    scenario_id = uuid4()
    scenario_manager = MagicMock()
    scenario_manager.filter.return_value.values_list.return_value = [scenario_id]
    run_test = SimpleNamespace(
        id=uuid4(),
        agent_definition=None,
        scenarios=scenario_manager,
    )
    request = SimpleNamespace(
        method="POST",
        organization=organization,
        user=SimpleNamespace(id=uuid4(), organization=organization),
        data={"scenario_ids": [str(scenario_id)]},
    )
    temporal_result = {
        "success": True,
        "execution_id": str(uuid4()),
        "run_test_id": str(run_test.id),
        "status": "started",
        "total_scenarios": 1,
        "total_calls": 0,
    }

    with (
        patch("simulate.views.run_test.TestExecutor") as legacy_executor,
        patch("simulate.views.run_test.get_object_or_404", return_value=run_test),
        patch("simulate.views.run_test.check_scenarios_incomplete", return_value=None),
    ):
        view = RunTestExecutionView()
        with patch.object(
            view, "_execute_with_temporal", return_value=temporal_result
        ) as temporal_dispatch:
            legacy_executor.return_value.execute_test.return_value = temporal_result
            response = view.post(request, run_test_id=run_test.id)

    assert response.status_code == status.HTTP_200_OK
    temporal_dispatch.assert_called_once_with(
        run_test=run_test,
        scenario_ids=[scenario_id],
        simulator_id=None,
    )
    legacy_executor.return_value.execute_test.assert_not_called()


@pytest.mark.unit
def test_cancel_always_dispatches_to_temporal(settings):
    settings.TEMPORAL_TEST_EXECUTION_ENABLED = False
    organization = SimpleNamespace(id=uuid4())
    run_test = SimpleNamespace(id=uuid4(), organization=organization, deleted=False)
    test_execution = MagicMock()
    test_execution.id = uuid4()
    test_execution.run_test = run_test
    test_execution.run_test_id = run_test.id
    test_execution.status = ExecutionModel.ExecutionStatus.RUNNING
    request = SimpleNamespace(
        method="POST",
        organization=organization,
        user=SimpleNamespace(organization=organization),
        data={},
    )
    temporal_result = {
        "success": True,
        "message": "Cancellation signal sent to workflow",
        "test_execution_id": str(test_execution.id),
    }

    with (
        patch("simulate.views.run_test.TestExecutor") as legacy_executor,
        patch("simulate.views.run_test.get_object_or_404", return_value=test_execution),
    ):
        view = CancelExecutionView()
        with patch.object(
            view, "_cancel_with_temporal", return_value=temporal_result
        ) as temporal_dispatch:
            legacy_executor.return_value.cancel_test.return_value = temporal_result
            response = view.post(request, test_execution_id=test_execution.id)

    assert response.status_code == status.HTTP_200_OK
    temporal_dispatch.assert_called_once_with(test_execution)
    legacy_executor.return_value.cancel_test.assert_not_called()
