"""Real Temporal worker, native runner, ORM and cell sink; no provider stand-ins."""

from copy import deepcopy
from importlib import import_module
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from agent_playground.models import Graph, GraphVersion, Node, NodeTemplate, Port
from agent_playground.services.engine import node_runner
from tfc.temporal.experiments.tests.test_e2e_v2_workflow import (
    _get_cells_for_column,
    _get_experiment,
    _get_experiment_columns,
    run_experiment_workflow,
)

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def register_mock_runners(monkeypatch):
    """Override the parent fixture with the shipped runner, not its echo fake."""
    module = import_module(
        "agent_playground.services.engine.runners.deterministic_text"
    )
    monkeypatch.setattr(node_runner, "_runners", dict(node_runner._runners))
    node_runner.register_runner("deterministic_text", module.DeterministicTextRunner())


@pytest.fixture(autouse=True)
def mock_process_row():
    """Fail immediately if either model route is invoked; never fake cell writes."""
    with (
        patch(
            "tfc.temporal.experiments.activities._process_row_sync",
            side_effect=AssertionError("prompt path invoked"),
        ),
        patch(
            "agent_playground.services.engine.runners.llm_prompt.RunPrompt",
            side_effect=AssertionError("provider invoked"),
        ),
    ):
        yield


@pytest.fixture
def native_agent(experiment, organization, workspace, user):
    from model_hub.models.experiments import (
        ExperimentAgentConfig,
        ExperimentDatasetTable,
    )

    module = import_module(
        "agent_playground.services.engine.runners.deterministic_text"
    )
    template = NodeTemplate.no_workspace_objects.create(
        name="deterministic_text", **deepcopy(module.TEMPLATE_DEFAULTS)
    )
    graph = Graph.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        name="Native text graph",
    )
    version = GraphVersion.no_workspace_objects.create(
        graph=graph, version_number=1, status="active"
    )
    node = Node.no_workspace_objects.create(
        graph_version=version,
        node_template=template,
        type="atomic",
        name="Native text",
        config={"input_key": "query", "prefix": "native:", "transform": "upper"},
    )
    for direction, name in (("input", "query"), ("output", "response")):
        Port.no_workspace_objects.create(
            node=node,
            key="custom",
            display_name=name,
            direction=direction,
            data_schema={"type": "string"},
        )
    edt = ExperimentDatasetTable.objects.create(
        experiment=experiment, name="Native text", status="NotStarted"
    )
    return ExperimentAgentConfig.objects.create(
        experiment_dataset=edt,
        graph=graph,
        graph_version=version,
        name="Native text",
        order=0,
    )


# TX1-B: agent graph targets run only when the setting is on.
@override_settings(DSX_CONSOLE_AGENT_EXPERIMENTS_ENABLED=True)
async def test_real_native_worker_reaches_terminal_cells(
    workflow_environment, experiment, native_agent
):
    from model_hub.models.develop_dataset import Cell

    expected = await sync_to_async(list)(
        Cell.objects.filter(
            dataset=experiment.snapshot_dataset, column__name="query"
        ).values_list("value", flat=True)
    )
    assert len(expected) == 3
    result = await run_experiment_workflow(workflow_environment, experiment.id)
    assert result.failed_rows == 0
    assert result.total_rows_processed == 3
    assert (await _get_experiment(experiment.id)).status == "Completed"
    columns = await _get_experiment_columns(experiment.id)
    assert len(columns) == 1
    assert columns[0][2] == "Completed"
    cells = await _get_cells_for_column(columns[0][0])
    assert len(cells) == 3
    assert all(status == "pass" for _, _, status in cells)
    assert sorted(value for _, value, _ in cells) == sorted(
        "native:" + value.upper() for value in expected
    )


# TX1-B: agent graph targets run only when the setting is on.
@override_settings(DSX_CONSOLE_AGENT_EXPERIMENTS_ENABLED=True)
async def test_native_failure_marks_cells_terminal(
    workflow_environment, experiment, native_agent
):
    await sync_to_async(
        Node.no_workspace_objects.filter(
            graph_version=native_agent.graph_version
        ).update
    )(config={"input_key": "absent"})
    result = await run_experiment_workflow(workflow_environment, experiment.id)
    assert result.failed_rows == 3
    columns = await _get_experiment_columns(experiment.id)
    assert len(columns) == 1
    cells = await _get_cells_for_column(columns[0][0])
    assert len(cells) == 3
    assert all(status == "error" for _, _, status in cells)
    # Completed means every row is terminal, not that every row passed.
    assert columns[0][2] == "Completed"
    assert (await _get_experiment(experiment.id)).status == "Completed"


@override_settings(DSX_CONSOLE_AGENT_EXPERIMENTS_ENABLED=False)
async def test_unqualified_native_worker_refuses_before_any_agent_write(
    workflow_environment, experiment, native_agent
):
    """A direct registered workflow start bypasses every API and client gate;
    the agent setup activity still refuses before any column, cell or graph
    execution exists, and the shipped runner is never invoked."""
    from agent_playground.models import GraphExecution

    module = import_module(
        "agent_playground.services.engine.runners.deterministic_text"
    )
    with patch.object(
        module.DeterministicTextRunner,
        "run",
        side_effect=AssertionError("native runner invoked"),
    ):
        result = await run_experiment_workflow(workflow_environment, experiment.id)
    assert result.total_rows_processed == 0
    assert await _get_experiment_columns(experiment.id) == []
    assert not await sync_to_async(
        GraphExecution.no_workspace_objects.filter(
            graph_version=native_agent.graph_version
        ).exists
    )()
