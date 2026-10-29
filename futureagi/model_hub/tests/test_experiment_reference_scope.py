"""Experiment references retain the caller's organization and workspace."""

from unittest.mock import patch

import pytest

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.tests.test_dataset_cell_scope import _grid
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from model_hub.tests.test_knowledge_base_request_scope import template_for
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db
tenants = tenants_fixture


def _owner(a, b, scope):
    if scope == "organization":
        return b
    if scope == "workspace":
        workspace = Workspace.objects.create(
            name="Private references", organization=a["org"], created_by=a["user"],
            is_default=False,
        )
        return {**a, "workspace": workspace}
    return a


def _experiment(a):
    clear_workspace_context()
    return ExperimentsTable.no_workspace_objects.create(
        name="Reference experiment", dataset=a["dataset"], snapshot_dataset=a["dataset"], column=a["column"],
        user=a["user"], prompt_config=[],
    )


@pytest.mark.parametrize("scope", ["own", "organization", "workspace"])
@pytest.mark.parametrize("save_as_template", [False, True])
def test_add_experiment_eval_template_scope(tenants, scope, save_as_template):
    a, b, clients = tenants
    template = template_for(_owner(a, b, scope))
    experiment = _experiment(a)
    before = list(EvalTemplate.no_workspace_objects.order_by("id").values())
    response = clients["a"].post(
        f"/model-hub/experiments/{experiment.id}/add-eval/",
        {"name": "bound-evaluation", "template_id": str(template.id), "config": {"mapping": {}},
         "save_as_template": save_as_template, "model": "gpt-4o-mini"}, format="json",
    )
    assert response.status_code == (200 if scope == "own" else 404), response.data
    if scope != "own":
        assert list(EvalTemplate.no_workspace_objects.order_by("id").values()) == before
        assert not UserEvalMetric.no_workspace_objects.filter(source_id=str(experiment.id)).exists()
    else:
        assert experiment.user_eval_template_ids.count() == 1


@pytest.mark.parametrize("scope", ["own", "organization", "workspace"])
def test_existing_experiment_metric_template_scope(tenants, scope):
    a, b, clients = tenants
    original = template_for(a)
    target = template_for(_owner(a, b, scope))
    experiment = _experiment(a)
    metric = UserEvalMetric.no_workspace_objects.create(
        name="Existing metric", organization=a["org"], workspace=a["workspace"],
        dataset=a["dataset"], template=original, user=a["user"], config={"mapping": {}},
        source_id=str(experiment.id),
    )
    experiment.user_eval_template_ids.add(metric)
    before = list(UserEvalMetric.no_workspace_objects.order_by("id").values())
    with patch("tfc.temporal.experiments.start_experiment_v2_workflow") as dispatch:
        response = clients["a"].put(
            f"/model-hub/experiments/v2/{experiment.id}/",
            {"user_eval_metrics": [{"id": str(metric.id), "name": metric.name,
              "template_id": str(target.id), "config": {"mapping": {}}, "model": "gpt-4o-mini"}]},
            format="json",
        )
    assert response.status_code == (200 if scope == "own" else 404), response.data
    if scope != "own":
        assert list(UserEvalMetric.no_workspace_objects.order_by("id").values()) == before
        dispatch.assert_not_called()
    else:
        metric.refresh_from_db()
        assert metric.template_id == target.id


@pytest.mark.parametrize("scope", ["own", "organization", "workspace"])
@pytest.mark.parametrize("relation", ["legacy", "direct"])
def test_experiment_column_metadata_scope(tenants, scope, relation):
    a, b, clients = tenants
    owner = _owner(a, b, scope)
    if scope == "workspace":
        dataset, _, column, _ = _grid(a, owner["workspace"])
        owner = {**owner, "dataset": dataset, "column": column}
    experiment = _experiment(owner)
    edt = ExperimentDatasetTable.no_workspace_objects.create(
        name="Reference columns", experiment=experiment if relation == "direct" else None,
    )
    if relation == "legacy":
        experiment.experiments_datasets.add(edt)
    edt.columns.add(owner["column"])
    response = clients["a"].get(f"/model-hub/dataset/columns/{edt.id}/")
    assert response.status_code == (200 if scope == "own" else 404), response.data
    if scope != "own":
        assert str(owner["column"].id) not in response.content.decode()
    else:
        assert str(owner["column"].id) in response.content.decode()


@pytest.mark.parametrize("scope", ["own", "organization", "workspace"])
@pytest.mark.parametrize("endpoint", ["create", "update"])
@pytest.mark.parametrize("reference", ["template", "prompt", "agent"])
def test_v2_references_are_validated_before_mutations(tenants, scope, endpoint, reference):
    a, b, clients = tenants
    owner = _owner(a, b, scope)
    template = template_for(owner if reference == "template" else a)
    prompt_owner = owner if reference == "prompt" else a
    clear_workspace_context()
    prompt = PromptTemplate.no_workspace_objects.create(
        name="Reference prompt", organization=prompt_owner["org"],
        workspace=prompt_owner["workspace"],
    )
    version = PromptVersion.objects.create(original_template=prompt, is_draft=False)
    entry = {"prompt_id": str(prompt.id), "prompt_version": str(version.id), "model": "gpt-4o-mini"}
    if reference == "agent":
        from agent_playground.models.graph import Graph
        from agent_playground.models.graph_version import GraphVersion

        graph = Graph.no_workspace_objects.create(
            name="Reference agent", organization=owner["org"], workspace=owner["workspace"],
            created_by=owner["user"],
        )
        graph_version = GraphVersion.no_workspace_objects.create(graph=graph, version_number=1, status="active")
        entry = {"agent_id": str(graph.id), "agent_version": str(graph_version.id)}
    eval_entry = {"name": "Bound metric", "template_id": str(template.id), "config": {"mapping": {}}, "model": "gpt-4o-mini"}
    payload = {"prompt_config": [entry], "user_eval_metrics": [eval_entry]}
    if endpoint == "create":
        url = "/model-hub/experiments/v2/"
        payload.update(name="New reference experiment", dataset_id=str(a["dataset"].id))
    else:
        experiment = _experiment(a)
        # Include a valid earlier mutation: a later rejected reference must not save it.
        experiment.column = a["column"]
        experiment.save(update_fields=["column"])
        payload["column_id"] = None
        url = f"/model-hub/experiments/v2/{experiment.id}/"
    models = [ExperimentsTable, ExperimentDatasetTable, Dataset, Column, Cell, UserEvalMetric]
    before = {m: list(m._base_manager.order_by("pk").values()) for m in models}
    with patch("tfc.temporal.experiments.start_experiment_v2_workflow") as dispatch:
        response = getattr(clients["a"], "post" if endpoint == "create" else "put")(url, payload, format="json")
    assert response.status_code == (200 if scope == "own" else 404), response.data
    if scope != "own":
        assert {m: list(m._base_manager.order_by("pk").values()) for m in models} == before
        dispatch.assert_not_called()
    else:
        dispatch.assert_called_once()
