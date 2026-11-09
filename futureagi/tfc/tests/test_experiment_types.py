"""Temporal payload compatibility for historical experiment setup results."""

from dataclasses import asdict

import pytest
from temporalio.converter import value_to_type

from tfc.temporal.experiments.types import SetupExperimentOutput


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
