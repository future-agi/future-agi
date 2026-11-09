"""Native graph experiments keep scoped admission and persist output columns."""

from copy import deepcopy
from importlib import import_module
from unittest.mock import ANY, patch

import pytest
from django.test import override_settings

from agent_playground.models import Graph, GraphVersion, Node, NodeTemplate, Port
from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.experiments import (
    ExperimentAgentConfig,
    ExperimentDatasetTable,
    ExperimentsTable,
)
from model_hub.tests import test_experiment_v2_results_tenancy as tenancy

tenants = tenancy.tenants

V2 = "/model-hub/experiments/v2/"
START = "tfc.temporal.experiments.start_experiment_v2_workflow"


def native_module():
    return import_module("agent_playground.services.engine.runners.deterministic_text")


def make_graph(actor, *, status="active"):
    graph = Graph.no_workspace_objects.create(
        name="Native graph",
        organization=actor["org"],
        workspace=actor["workspace"],
        created_by=actor["user"],
    )
    version = GraphVersion.no_workspace_objects.create(
        graph=graph,
        version_number=1,
        status=status,
    )
    template, _ = NodeTemplate.no_workspace_objects.get_or_create(
        name="deterministic_text",
        defaults=deepcopy(native_module().TEMPLATE_DEFAULTS),
    )
    node = Node.no_workspace_objects.create(
        graph_version=version,
        node_template=template,
        type="atomic",
        name="Transform",
        config={"input_key": "query", "transform": "upper", "prefix": "native:"},
    )
    for direction, name in (("input", "query"), ("output", "response")):
        Port.no_workspace_objects.create(
            node=node,
            key="custom",
            display_name=name,
            direction=direction,
            data_schema={"type": "string"},
        )
    return graph, version, node


def payload(actor, graph, version):
    evaluator = EvalTemplate.no_workspace_objects.create(
        name="native-output-check",
        organization=actor["org"],
        workspace=actor["workspace"],
        owner=OwnerChoices.USER.value,
        criteria="Evaluate {{output}}",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    return {
        "name": "Native experiment",
        "dataset_id": str(actor["exp"]["dataset"].id),
        "experiment_type": "llm",
        "user_eval_metrics": [
            {
                "name": "native-output-check",
                "template_id": str(evaluator.id),
                "config": {"mapping": {"output": "output"}},
            }
        ],
        "prompt_config": [
            {"agent_id": str(graph.id), "agent_version": str(version.id)}
        ],
    }


def counts():
    return tuple(
        model.no_workspace_objects.count()
        if hasattr(model, "no_workspace_objects")
        else model.objects.count()
        for model in (
            ExperimentsTable,
            ExperimentDatasetTable,
            ExperimentAgentConfig,
            Dataset,
            Row,
            Column,
            Cell,
        )
    )


@pytest.mark.parametrize(
    "transform,expected",
    [
        ("identity", "[ Hello ]"),
        ("upper", "[ HELLO ]"),
        ("lower", "[ hello ]"),
    ],
)
def test_native_transform_is_pure_and_repeatable(transform, expected):
    runner = native_module().DeterministicTextRunner()
    config = {
        "input_key": "query",
        "transform": transform,
        "prefix": "[",
        "suffix": "]",
    }
    inputs = {"query": " Hello "}
    original = deepcopy((config, inputs))
    assert runner.run(config, inputs, {}) == {"response": expected}
    assert runner.run(config, inputs, {"workspace_id": "other"}) == {
        "response": expected
    }
    assert (config, inputs) == original


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        {},
        {"input_key": 7},
        {"input_key": "q", "transform": "unknown"},
        {"input_key": "q", "url": "unused"},
    ],
)
def test_native_config_fails_closed(config):
    with pytest.raises(ValueError, match="Invalid deterministic_text config"):
        native_module().DeterministicTextRunner().run(config, {"q": "value"}, {})


@pytest.mark.parametrize(
    "value", [None, 7, {}, "x" * 65537], ids=["null", "number", "object", "oversized"]
)
def test_native_input_must_be_bounded_text(value):
    with pytest.raises(ValueError):
        native_module().DeterministicTextRunner().run(
            {"input_key": "q"}, {"q": value}, {}
        )


@pytest.mark.django_db
# Original ON control retained; RL1891 adds the real OFF positive separately.
@override_settings(DSX_CONSOLE_AGENT_EXPERIMENTS_ENABLED=True)
def test_create_persists_native_output_columns_and_graph_binding(tenants):
    actor = tenants["a"]
    graph, version, node = make_graph(actor)
    with patch(START, return_value="native-workflow") as start:
        response = actor["client"].post(
            V2, payload(actor, graph, version), format="json"
        )
    assert response.status_code == 200, response.content
    experiment = ExperimentsTable.no_workspace_objects.get(name="Native experiment")
    config = ExperimentAgentConfig.no_workspace_objects.get(
        experiment_dataset__experiment=experiment
    )
    assert config.graph_id == graph.id
    assert config.graph_version_id == version.id
    columns = config.experiment_dataset.columns.filter(
        source="experiment", deleted=False
    )
    assert columns.count() == 1
    assert str(columns.first().source_id) == str(config.experiment_dataset_id)
    assert columns.first().metadata["node_id"] == str(node.id)
    assert experiment.snapshot_dataset.organization_id == actor["org"].id
    assert experiment.snapshot_dataset.workspace_id == actor["workspace"].id
    start.assert_called_once_with(
        experiment_id=str(experiment.id), admission_id=ANY, max_concurrent_rows=10
    )


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["foreign", "mismatch", "draft", "invalid_config"])
# All four legacy validation controls remain explicit with Console ON.
@override_settings(DSX_CONSOLE_AGENT_EXPERIMENTS_ENABLED=True)
def test_graph_admission_refuses_before_any_mutation(tenants, case):
    actor = tenants["a"]
    graph, version, node = make_graph(
        tenants["b"] if case == "foreign" else actor,
        status="draft" if case == "draft" else "active",
    )
    if case == "invalid_config":
        # Legacy/imported invalid data must fail before snapshot or worker writes.
        Node.no_workspace_objects.filter(pk=node.pk).update(config={"input_key": 7})
    if case == "mismatch":
        graph, _, _ = make_graph(actor)
    before = counts()
    with patch(START) as start:
        response = actor["client"].post(
            V2, payload(actor, graph, version), format="json"
        )
    assert response.status_code in (400, 403, 404), response.content
    expected_error = {
        "foreign": "not found",
        "mismatch": "does not belong",
        "draft": "draft state",
        "invalid_config": "Invalid deterministic_text config",
    }[case]
    assert expected_error in response.content.decode(), response.content
    assert counts() == before
    start.assert_not_called()
