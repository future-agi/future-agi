"""W10-EXPERIMENTS-DATASETS-L03: conversion must retain usable eval bindings."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    DataTypeChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.views.datasets.create import dataset_from_experiment
from tfc.middleware.workspace_context import clear_workspace_context, workspace_context

pytestmark = pytest.mark.django_db


def _state():
    return tuple(
        list(model.no_workspace_objects.order_by("pk").values())
        for model in (Dataset, Column, UserEvalMetric, Row, Cell)
    )


@pytest.fixture
def eval_graph(monkeypatch):
    clear_workspace_context()
    organization = Organization.objects.create(name="Eval copy organization")
    user = User.objects.create_user(
        email="eval-copy@example.test",
        password=None,
        name="Eval copy",
        organization=organization,
    )
    workspace = Workspace.objects.create(
        name="Eval copy workspace",
        organization=organization,
        created_by=user,
        is_default=True,
    )
    monkeypatch.setattr(
        dataset_from_experiment, "log_and_deduct_cost_for_resource_request", None
    )
    monkeypatch.setattr(
        dataset_from_experiment, "get_recommendations", lambda dataset: None
    )

    def build(source_kind, relations="all"):
        dataset = Dataset.no_workspace_objects.create(
            name=f"Eval source {uuid4()}",
            organization=organization,
            workspace=workspace,
        )
        input_column = Column.no_workspace_objects.create(
            name="response",
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
            data_type=DataTypeChoices.TEXT.value,
        )
        template = EvalTemplate.no_workspace_objects.create(
            name="copy-judge",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            model="turing_small",
            eval_type="code",
            config={
                "required_keys": ["output"],
                "output": "Pass/Fail",
                "eval_type_id": "CustomCodeEval",
            },
        )
        version = None
        if relations in ("pinned", "all"):
            version = EvalTemplateVersion.objects.create_version(
                eval_template=template,
                config_snapshot=dict(template.config),
                organization=organization,
                workspace=workspace,
                model="turing_small",
            )
        group = None
        if relations in ("grouped", "all"):
            group = EvalGroup.no_workspace_objects.create(
                name="copy-group",
                organization=organization,
                workspace=workspace,
                created_by=user,
            )
            group.eval_templates.add(template)
        metric = UserEvalMetric.no_workspace_objects.create(
            name="copy-judge",
            dataset=dataset,
            organization=organization,
            workspace=workspace if relations in ("workspace", "all") else None,
            user=user,
            template=template,
            pinned_version=version,
            eval_group=group,
            model="turing_small",
            status=StatusType.COMPLETED.value,
            config={
                "mapping": {
                    "output": str(input_column.id),
                    "context": [str(input_column.id)],
                }
            },
            composite_weight_overrides={str(template.id): 0.75},
        )
        # Keep nullable legacy bindings nullable even if save signals stamp them.
        if relations not in ("workspace", "all"):
            UserEvalMetric.no_workspace_objects.filter(pk=metric.pk).update(
                workspace=None
            )
            metric.refresh_from_db()
        source = (
            SourceChoices.EVALUATION.value
            if source_kind == "dataset"
            else SourceChoices.EXPERIMENT_EVALUATION.value
        )
        source_id = str(metric.id)
        if source_kind == "experiment":
            source_id = f"{template.id}-sourceid-{metric.id}"
        score_column = Column.no_workspace_objects.create(
            name="judge score",
            dataset=dataset,
            source=source,
            source_id=source_id,
            data_type=DataTypeChoices.FLOAT.value,
        )
        row = Row.no_workspace_objects.create(dataset=dataset, order=0)
        Cell.no_workspace_objects.create(
            dataset=dataset, row=row, column=input_column, value="An answer"
        )
        Cell.no_workspace_objects.create(
            dataset=dataset, row=row, column=score_column, value="0.75"
        )
        experiment = ExperimentsTable.objects.create(
            name="Completed eval experiment",
            dataset=dataset,
            column=input_column,
            user=user,
            status=StatusType.COMPLETED.value,
        )
        experiment.user_eval_template_ids.add(metric)
        table = ExperimentDatasetTable.objects.create(
            name="Completed results",
            experiment=experiment,
            status=StatusType.COMPLETED.value,
        )
        experiment.experiments_datasets.add(table)
        table.columns.set([input_column, score_column])

        def post():
            name = f"Eval copy {uuid4()}"
            request = APIRequestFactory().post(
                f"/model-hub/develops/{table.id}/create-dataset/",
                {"name": name},
                format="json",
            )
            request.organization = organization
            request.workspace = workspace
            force_authenticate(request, user=user)
            with workspace_context(workspace, organization=organization, user=user):
                response = dataset_from_experiment.CreateDatasetFromExpView.as_view()(
                    request, exp_dataset_id=str(table.id)
                )
            return response, name

        return SimpleNamespace(
            dataset=dataset,
            metric=metric,
            input_column=input_column,
            score_column=score_column,
            workspace=workspace,
            organization=organization,
            user=user,
            post=post,
        )

    yield build
    clear_workspace_context()


@pytest.mark.parametrize("source_kind", ["dataset", "experiment", "experiment-raw"])
@pytest.mark.parametrize(
    "relations", ["workspace", "pinned", "grouped", "all", "legacy"]
)
def test_conversion_preserves_and_remaps_evaluation_binding(
    eval_graph, source_kind, relations
):
    graph = eval_graph(source_kind, relations)
    original_config = deepcopy(graph.metric.config)
    response, name = graph.post()
    assert response.status_code == 200, response.data
    destination = Dataset.no_workspace_objects.get(name=name)
    copied_metric = UserEvalMetric.no_workspace_objects.get(dataset=destination)
    copied_input = Column.no_workspace_objects.get(dataset=destination, name="response")
    copied_score = Column.no_workspace_objects.get(
        dataset=destination, name="judge score"
    )
    assert copied_metric.id != graph.metric.id
    assert copied_metric.workspace_id == destination.workspace_id == graph.workspace.id
    assert copied_metric.organization_id == graph.organization.id
    assert copied_metric.user_id == graph.user.id
    assert copied_metric.template_id == graph.metric.template_id
    assert copied_metric.pinned_version_id == graph.metric.pinned_version_id
    assert copied_metric.eval_group_id == graph.metric.eval_group_id
    assert copied_metric.model == graph.metric.model
    assert (
        copied_metric.composite_weight_overrides
        == graph.metric.composite_weight_overrides
    )
    assert copied_metric.config == {
        "mapping": {"output": str(copied_input.id), "context": [str(copied_input.id)]}
    }
    assert copied_score.source == SourceChoices.EVALUATION.value
    assert str(copied_score.source_id) == str(copied_metric.id)
    assert (
        Cell.no_workspace_objects.get(dataset=destination, column=copied_score).value
        == "0.75"
    )
    graph.metric.refresh_from_db()
    assert graph.metric.config == original_config
    assert Cell.no_workspace_objects.get(column=graph.score_column).value == "0.75"


@pytest.mark.parametrize("source_kind", ["dataset", "experiment"])
@pytest.mark.parametrize("failure", ["invalid-id", "missing-metric", "create-failed"])
def test_conversion_rolls_back_when_evaluation_cannot_be_copied(
    eval_graph, monkeypatch, source_kind, failure
):
    graph = eval_graph(source_kind)
    if failure in ("invalid-id", "missing-metric"):
        value = "invalid-evaluation-id" if failure == "invalid-id" else str(uuid4())
        if source_kind == "experiment":
            value = f"{graph.metric.template_id}-sourceid-{value}"
        Column.no_workspace_objects.filter(pk=graph.score_column.pk).update(
            source_id=value
        )
    else:
        original_save = UserEvalMetric.save

        def fail_destination_save(instance, *args, **kwargs):
            if instance.dataset_id != graph.dataset.id:
                raise ValueError("Evaluation copy unavailable")
            return original_save(instance, *args, **kwargs)

        monkeypatch.setattr(UserEvalMetric, "save", fail_destination_save)

    before = _state()
    response, name = graph.post()
    assert response.status_code == 400, response.data
    assert "evaluation" in str(response.data).lower()
    assert "no dataset was created" in str(response.data).lower()
    assert not Dataset.no_workspace_objects.filter(name=name).exists()
    assert _state() == before
