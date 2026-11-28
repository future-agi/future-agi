"""Test-evaluation endpoint with an LLM (custom prompt) eval.

The playground sends the judge model at the top level of the request
(``model``), next to ``config``. ``config`` itself may carry only the
mapping, or nothing at all.
"""

from unittest.mock import patch

import pytest


def _llm_test_payload(config):
    return {
        "name": "made-up-tone-check",
        "template_type": "Llm",
        "model": "gpt-4o",
        "criteria": "Is {{output}} polite?",
        "output_type": "Pass/Fail",
        "required_keys": ["output"],
        "input_data_types": {"output": "text"},
        "config": config,
    }


@pytest.mark.django_db
def test_llm_eval_with_model_outside_config_runs(auth_client):
    with patch(
        "model_hub.views.separate_evals.run_eval_func",
        return_value={"output": "Passed"},
    ) as run:
        response = auth_client.post(
            "/model-hub/test-evaluation/",
            _llm_test_payload({"mapping": {"output": "Thanks for waiting"}}),
            format="json",
        )

    assert response.status_code == 200, response.data
    run.assert_called_once()
    assert run.call_args.kwargs["model"] == "gpt-4o"


@pytest.mark.django_db
def test_llm_eval_with_empty_config_runs(auth_client):
    with patch(
        "model_hub.views.separate_evals.run_eval_func",
        return_value={"output": "Passed"},
    ) as run:
        response = auth_client.post(
            "/model-hub/test-evaluation/",
            _llm_test_payload({}),
            format="json",
        )

    assert response.status_code == 200, response.data
    run.assert_called_once()
