"""Defaults for newly created simulator agents; explicit choices still win."""

import pytest

from simulate.serializers.requests.scenarios import ScenarioCreateRequestSerializer
from simulate.services.alk_simulate_ingestion import _simulator_llm_model


def test_scenario_model_defaults_to_gpt_4o():
    field = ScenarioCreateRequestSerializer().fields["model"]

    assert field.run_validation() == "gpt-4o"


def test_scenario_model_preserves_explicit_choice():
    field = ScenarioCreateRequestSerializer().fields["model"]

    assert field.run_validation("custom-model") == "custom-model"


@pytest.mark.parametrize("configured_model", [None, "", "custom-model"])
def test_hosted_simulator_model_matches_runner_default(monkeypatch, configured_model):
    if configured_model is None:
        monkeypatch.delenv("SIMULATOR_LLM_MODEL", raising=False)
    else:
        monkeypatch.setenv("SIMULATOR_LLM_MODEL", configured_model)

    assert _simulator_llm_model() == (configured_model or "gpt-4.1")
