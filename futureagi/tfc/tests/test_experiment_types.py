"""Temporal payload compatibility for historical experiment setup results."""

from dataclasses import asdict

import pytest
from temporalio.converter import value_to_type

from tfc.temporal.experiments.types import (
    ProcessPromptWorkflowInput,
    ProcessRowInput,
    RunExperimentInput,
    SetupAgentOutput,
    SetupExperimentOutput,
)


@pytest.mark.parametrize("agent_configs", [None, [], ["agent-config-id"]])
def test_setup_result_round_trips_optional_agent_configs(agent_configs):
    result = SetupExperimentOutput(
        experiment_id="experiment-id",
        dataset_id="dataset-id",
        prompt_configs=[],
        eval_template_ids=[],
        status="ready",
        agent_configs=agent_configs,
    )

    # The real Temporal JSON decoder consults the dataclass type annotations.
    assert value_to_type(SetupExperimentOutput, asdict(result)) == result


def test_historical_setup_result_can_omit_agent_configs():
    payload = {
        "experiment_id": "experiment-id",
        "dataset_id": "dataset-id",
        "prompt_configs": [],
        "eval_template_ids": [],
        "status": "ready",
    }
    result = value_to_type(SetupExperimentOutput, payload)

    assert result.agent_configs is None
    assert value_to_type(SetupExperimentOutput, asdict(result)) == result


@pytest.mark.parametrize("agent_configs", [1, False, 1.5])
def test_setup_result_rejects_non_iterable_agent_configs(agent_configs):
    payload = asdict(SetupExperimentOutput("experiment", "dataset", [], [], "ready"))
    payload["agent_configs"] = agent_configs
    with pytest.raises(TypeError):
        value_to_type(SetupExperimentOutput, payload)


@pytest.mark.parametrize(
    ("agent_configs", "expected"),
    [("abc", ["a", "b", "c"]), ({"id": "agent-config-id"}, ["id"])],
)
def test_setup_result_preserves_temporal_iterable_decoding(agent_configs, expected):
    # Temporal's list decoder historically accepts iterables, not just JSON arrays.
    # Annotation modernization must preserve that existing payload behavior.
    payload = asdict(SetupExperimentOutput("experiment", "dataset", [], [], "ready"))
    payload["agent_configs"] = agent_configs
    assert value_to_type(SetupExperimentOutput, payload).agent_configs == expected


@pytest.mark.parametrize(
    "result",
    [
        ProcessRowInput("row", "column", "dataset", "experiment", [], "model", {}),
        ProcessRowInput(
            "row",
            "column",
            "dataset",
            "experiment",
            [],
            "model",
            {},
            output_format="json",
            run_prompt_config={"temperature": 0},
        ),
        ProcessPromptWorkflowInput(
            "experiment",
            "dataset",
            {"messages": [{"role": "user", "content": "test"}]},
            {"model_id": "fixture-model"},
        ),
        SetupAgentOutput(
            experiment_id="experiment",
            experiment_agent_config_id="agent",
            experiment_dataset_id="experiment-dataset",
            graph_version_id="graph",
            node_column_mapping={"node": "column"},
            row_ids=["row"],
            status="ready",
            terminal_column_ids=["column"],
        ),
        RunExperimentInput("experiment", rerun_prompt_config_ids=["prompt"]),
    ],
)
def test_modern_annotations_preserve_temporal_wire_shapes(result):
    assert value_to_type(type(result), asdict(result)) == result


def test_temporal_rerun_list_defaults_remain_independent():
    first = value_to_type(RunExperimentInput, {"experiment_id": "first"})
    second = value_to_type(RunExperimentInput, {"experiment_id": "second"})
    first.rerun_prompt_config_ids.append("prompt")
    assert second.rerun_prompt_config_ids == []
