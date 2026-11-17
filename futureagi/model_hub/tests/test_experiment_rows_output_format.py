"""The rows view reports a V2 experiment's output format from its prompt configs.

V2 experiments keep the deprecated ``prompt_config`` JSON empty and store the
format on ExperimentPromptConfig. Reading the JSON made every V2 experiment
report "text", which hid the diff-vs-baseline toggle on the results grid.
"""

from types import SimpleNamespace
from unittest.mock import patch

from model_hub.views import experiments as experiments_view


def test_v2_experiment_reads_the_format_from_its_prompt_configs():
    experiment = SimpleNamespace(prompt_config=[])
    with patch.object(
        experiments_view, "get_v2_output_format", return_value="json"
    ) as v2_format:
        assert experiments_view._rows_output_format(experiment, True) == "json"
    v2_format.assert_called_once_with(experiment)


def test_legacy_experiment_reads_the_prompt_config_json():
    experiment = SimpleNamespace(prompt_config=[{"output_format": "json"}])
    with patch.object(experiments_view, "get_v2_output_format") as v2_format:
        assert experiments_view._rows_output_format(experiment, False) == "json"
    v2_format.assert_not_called()


def test_legacy_experiment_without_a_readable_format_reports_text():
    for prompt_config in (None, [], [{}], ["not-a-dict"]):
        experiment = SimpleNamespace(prompt_config=prompt_config)
        assert experiments_view._rows_output_format(experiment, False) == "text"
