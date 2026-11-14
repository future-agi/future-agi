"""Usage tracking for optimizations created without an input column."""

from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.develop_dataset import Dataset
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.tasks.optimisation_runner import process_optimization
from tfc.constants.api_calls import APICallTypeChoices


@pytest.mark.django_db
@pytest.mark.parametrize("include_null_column", [False, True])
def test_columnless_optimization_records_usage(
    auth_client, organization, user, workspace, include_null_column
):
    dataset = Dataset.no_workspace_objects.create(
        name="Columnless Optimization Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    payload = {
        "name": "Columnless Optimization",
        "dataset_id": str(dataset.id),
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Answer"}]}],
        "user_eval_template_ids": [],
        "model_config": {
            "model_name": "gpt-4o-mini",
            "temperature": 0.0,
            "max_tokens": 100,
        },
        "optimize_type": "PROMPT_TEMPLATE",
    }
    if include_null_column:
        payload["column_id"] = None

    response = auth_client.post(
        "/model-hub/optimisation/create/", payload, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    optimization = OptimizationDataset.no_workspace_objects.get(
        dataset=dataset, name=payload["name"]
    )
    assert optimization.column_id is None

    with (
        patch("model_hub.tasks.optimisation_runner.close_old_connections"),
        patch(
            "model_hub.views.develop_optimiser.log_and_deduct_cost_for_api_request"
        ) as record,
        patch(
            "model_hub.views.develop_optimiser.DevelopOptimizer.runner_topk_develop_optimizer"
        ),
        patch(
            "model_hub.views.develop_optimiser.DevelopOptimizer._update_api_call_log_row"
        ),
    ):
        process_optimization(optimization)

    record.assert_called_once_with(
        organization,
        APICallTypeChoices.DATASET_OPTIMIZATION.value,
        {
            "input_tokens": 0,
            "reference_id": str(optimization.id),
            "input": "",
        },
        source="optimisation",
        workspace=workspace,
    )
