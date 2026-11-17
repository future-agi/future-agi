"""The empty-body Stop reports how many rerun workflows it cancelled.

The real response comes from the view and the real cancel helper; only the
Temporal client is a stand-in. The response must validate against the
serializer that documents it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from temporalio.client import WorkflowExecutionStatus

from model_hub.models.choices import StatusType
from model_hub.serializers.experiment_contracts import ExperimentStopResponseSerializer
from model_hub.tests.test_experiment_run_control import call, seed
from model_hub.views.experiments import ExperimentStopV2View


@pytest.mark.django_db
def test_empty_body_stop_reports_the_rerun_count_its_contract_accepts(
    user, workspace, organization
):
    experiment, _, _ = seed(organization, workspace, user)
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    main = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
        ),
        cancel=AsyncMock(),
    )
    reruns = {
        f"rerun-experiment-cells-{experiment.id}-{name}": SimpleNamespace(
            cancel=AsyncMock()
        )
        for name in ("first", "second")
    }
    queries = []

    def handle(workflow_id, **kwargs):
        if workflow_id == f"experiment-{experiment.id}":
            return main
        return reruns[workflow_id]

    def list_workflows(query, **kwargs):
        queries.append(query)

        async def running():
            for workflow_id in reruns:
                yield SimpleNamespace(id=workflow_id)

        return running()

    remote = SimpleNamespace(
        get_workflow_handle=Mock(side_effect=handle),
        list_workflows=Mock(side_effect=list_workflows),
    )
    with patch(
        "tfc.temporal.experiments.client.get_client", AsyncMock(return_value=remote)
    ):
        response = call(ExperimentStopV2View, "post", experiment.id, user, workspace)
    assert response.status_code == 200, response.data
    assert response.data["result"]["workflows_cancelled"] == {
        "main": True,
        "reruns": 2,
    }
    main.cancel.assert_awaited_once()
    for rerun in reruns.values():
        rerun.cancel.assert_awaited_once()
    assert queries == [
        f'WorkflowId STARTS_WITH "rerun-experiment-cells-{experiment.id}-" '
        "AND ExecutionStatus = 'Running'"
    ]
    contract = ExperimentStopResponseSerializer(data=response.data)
    assert contract.is_valid(), contract.errors
    assert contract.validated_data["result"]["workflows_cancelled"]["reruns"] == 2
    experiment.refresh_from_db()
    assert experiment.status == StatusType.CANCELLED.value


@pytest.mark.django_db
def test_empty_body_stop_on_a_cancelled_experiment_reports_zero_reruns(
    user, workspace, organization
):
    experiment, _, _ = seed(organization, workspace, user)
    experiment.status = StatusType.CANCELLED.value
    experiment.save(update_fields=["status"])
    no_temporal = AsyncMock(
        side_effect=AssertionError("no Temporal call for a stopped experiment")
    )
    with patch("tfc.temporal.experiments.client.get_client", no_temporal):
        response = call(ExperimentStopV2View, "post", experiment.id, user, workspace)
    assert response.status_code == 200, response.data
    assert response.data["result"]["workflows_cancelled"] == {
        "main": False,
        "reruns": 0,
    }
    contract = ExperimentStopResponseSerializer(data=response.data)
    assert contract.is_valid(), contract.errors
    assert contract.validated_data["result"]["workflows_cancelled"]["reruns"] == 0
