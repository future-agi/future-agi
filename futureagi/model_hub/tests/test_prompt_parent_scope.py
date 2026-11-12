"""Prompt and experiment helpers inherit the authorized parent dataset."""

from unittest.mock import patch

import pytest

from accounts.models.workspace import Workspace
from model_hub.models.choices import SourceChoices
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from model_hub.models.run_prompt import RunPrompter
from model_hub.tests.test_dataset_cell_scope import _grid
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from model_hub.tests.test_model_hub_related_scope import _request
from model_hub.views.develop_dataset import ColumnConfigView
from model_hub.views.experiments import ExperimentStatsV2View, ExperimentStatsView
from model_hub.views.run_prompt import (
    AddRunPromptColumnView,
    DatasetRunPromptStatsView,
    PreviewRunPromptColumnView,
    RunPromptForRowsView,
)

pytestmark = pytest.mark.django_db
tenants = tenants_fixture


def _private(a):
    workspace = Workspace.objects.create(
        name="private prompts", organization=a["org"], created_by=a["user"], is_default=False,
    )
    return _grid(a, workspace)


@pytest.mark.parametrize("scope", ["own", "organization", "workspace", "other_dataset"])
@pytest.mark.parametrize("kind", ["RUN_PROMPT", "EXPERIMENT", "OPTIMISATION", "EVALUATION", "EXPERIMENT_EVALUATION", "OPTIMISATION_EVALUATION"])
def test_column_source_must_belong_to_its_dataset(tenants, scope, kind):
    a, b, _ = tenants
    dataset = a["dataset"]
    if scope == "organization":
        dataset = b["dataset"]
    elif scope == "workspace":
        dataset, _, _, _ = _private(a)
    elif scope == "other_dataset":
        dataset, _, _, _ = _grid(a)
    prompt = RunPrompter.no_workspace_objects.create(
        dataset=dataset, organization=dataset.organization, workspace=dataset.workspace,
        model="offline", name="config", messages=[],
    )
    source_id = str(prompt.id)
    if kind == "EXPERIMENT":
        source_id = str(ExperimentsTable.no_workspace_objects.create(name="source", dataset=dataset).id)
    elif kind == "OPTIMISATION":
        source_id = str(OptimizationDataset.objects.create(name="source", dataset=dataset, optimize_type="PROMPT_TEMPLATE").id)
    elif "EVALUATION" in kind:
        template = EvalTemplate.no_workspace_objects.create(name="source", organization=dataset.organization, workspace=dataset.workspace)
        metric = UserEvalMetric.no_workspace_objects.create(
            name="source", dataset=dataset, template=template, organization=dataset.organization,
        )
        source_id = str(metric.id)
        if kind == "EXPERIMENT_EVALUATION":
            experiment = ExperimentsTable.no_workspace_objects.create(name="source", dataset=dataset)
            sibling = ExperimentDatasetTable.objects.create(name="source", experiment=experiment)
            ExperimentPromptConfig.objects.create(
                experiment_dataset=sibling, name="source", model="synthetic", messages=[],
            )
            source_id = f"{sibling.id}-sourceid-{metric.id}"
        elif kind == "OPTIMISATION_EVALUATION":
            sibling = OptimizationDataset.objects.create(name="source", dataset=dataset, optimize_type="PROMPT_TEMPLATE")
            source_id = f"{sibling.id}-sourceid-{metric.id}"
    column = a["column"]
    column.source = getattr(SourceChoices, kind).value
    column.source_id = source_id
    column.save()
    response = ColumnConfigView.as_view()(_request(a, "get", {}), column_id=column.id)
    assert response.status_code == (200 if scope == "own" else 404), response.data


@pytest.mark.parametrize("endpoint", ["stats", "run", "add", "preview", "experiment", "experiment_v2"])
def test_prompt_and_experiment_workspace_scope_without_ambient_context(tenants, endpoint):
    a, _, _ = tenants
    dataset, row, _, _ = _private(a)
    prompt = RunPrompter.no_workspace_objects.create(
        dataset=dataset, organization=a["org"], workspace=dataset.workspace,
        model="", name="config", messages=[],
    )
    experiment = ExperimentsTable.no_workspace_objects.create(name="experiment", dataset=dataset)
    with patch("model_hub.views.run_prompt.run_all_prompts_task.apply_async") as dispatch:
        if endpoint == "stats":
            response = DatasetRunPromptStatsView.as_view()(_request(a, "get", {}), dataset_id=dataset.id)
        elif endpoint == "run":
            response = RunPromptForRowsView.as_view()(_request(a, "post", {
                "run_prompt_ids": [str(prompt.id)], "row_ids": [str(row.id)],
            }))
        elif endpoint in ("add", "preview"):
            view = AddRunPromptColumnView if endpoint == "add" else PreviewRunPromptColumnView
            payload = {"dataset_id": str(dataset.id), "name": "prompt"}
            if endpoint == "preview":
                payload["first_n_rows"] = 1
            response = view.as_view()(_request(a, "post", payload))
        else:
            view = ExperimentStatsView if endpoint == "experiment" else ExperimentStatsV2View
            response = view.as_view()(_request(a, "get", {}), experiment_id=experiment.id)
    assert response.status_code == 404, response.data
    dispatch.assert_not_called()
