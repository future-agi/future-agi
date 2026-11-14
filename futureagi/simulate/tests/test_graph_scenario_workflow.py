"""Regression tests for the graph-scenario workflow's legacy v2 branch."""

from unittest.mock import AsyncMock, call, patch

import pytest

from tfc.temporal.simulate.types import (
    CreateGraphScenarioWorkflowInput,
    CreateGraphScenarioWorkflowOutput,
    ExtractIntentsInput,
)
from tfc.temporal.simulate.workflows import CreateGraphScenarioWorkflow

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_v2_graph_scenario_extracts_intents_and_completes():
    """A v2-only history must reach extraction after successful setup."""
    workflow_input = CreateGraphScenarioWorkflowInput(
        scenario_id="scenario-v2",
        validated_data={"name": "Legacy scenario"},
    )
    agent_definition = {"agent_name": "Test agent"}
    transcripts = {"call-1": "Help with an order"}
    cases = [{"input": "Where is my order?"}]
    execute_activity = AsyncMock(
        side_effect=[
            {
                "status": "COMPLETED",
                "graph_id": "graph-v2",
                "agent_definition_data": agent_definition,
                "transcripts": transcripts,
                "no_of_rows": 7,
            },
            {"status": "COMPLETED", "intent_dict": {}},
            {"status": "COMPLETED", "branches_metadata": []},
            {"status": "COMPLETED", "cases": cases},
            {"status": "COMPLETED", "validated_cases": cases},
            {"status": "COMPLETED", "dataset_id": "dataset-v2"},
            {
                "status": "COMPLETED",
                "scenario_id": "scenario-v2",
                "dataset_id": "dataset-v2",
            },
        ]
    )

    # Temporal returns False for the absent v3 marker and True for recorded v2.
    with (
        patch(
            "tfc.temporal.simulate.workflows.workflow.patched",
            side_effect=[False, True],
        ) as patched,
        patch(
            "tfc.temporal.simulate.workflows.workflow.execute_activity",
            execute_activity,
        ),
    ):
        result = await CreateGraphScenarioWorkflow().run(workflow_input)

    assert patched.call_args_list == [
        call("v3-granular-activities"),
        call("v2-multi-activity-graph-scenario"),
    ]
    assert [args.args[0] for args in execute_activity.await_args_list] == [
        "setup_graph_scenario_activity",
        "extract_intents_activity",
        "process_branches_activity",
        "generate_cases_for_intent_activity",
        "categorize_and_validate_activity",
        "create_scenario_dataset_activity",
        "finalize_graph_scenario_activity",
    ]
    assert execute_activity.await_args_list[1].args[1] == ExtractIntentsInput(
        scenario_id="scenario-v2",
        graph_id="graph-v2",
        agent_definition_data=agent_definition,
        transcripts=transcripts,
        no_of_rows=7,
    )
    assert result == CreateGraphScenarioWorkflowOutput(
        scenario_id="scenario-v2", dataset_id="dataset-v2", status="COMPLETED"
    )


async def test_v2_graph_scenario_setup_failure_does_not_extract_intents():
    """The neighbouring setup-failure path must still stop before extraction."""
    workflow_input = CreateGraphScenarioWorkflowInput(
        scenario_id="scenario-v2", validated_data={}
    )
    execute_activity = AsyncMock(
        return_value={"status": "FAILED", "error": "Scenario not found"}
    )

    with (
        patch(
            "tfc.temporal.simulate.workflows.workflow.patched",
            side_effect=[False, True],
        ),
        patch(
            "tfc.temporal.simulate.workflows.workflow.execute_activity",
            execute_activity,
        ),
    ):
        result = await CreateGraphScenarioWorkflow().run(workflow_input)

    execute_activity.assert_awaited_once()
    assert execute_activity.await_args.args[0] == "setup_graph_scenario_activity"
    assert result == CreateGraphScenarioWorkflowOutput(
        scenario_id="scenario-v2", status="FAILED", error="Scenario not found"
    )
