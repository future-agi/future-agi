"""run_eval_func on a custom deterministic eval whose rule prompt is empty.

The evaluator rejects the arguments in ``validate_args``. That is a problem
with the eval's own configuration, so the caller must get a readable
failure, and ``model_hub.views.utils.evals`` must not log it at error level
(error-level lines raise an alert as a new backend error issue).
"""

from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.views.utils import evals as evals_module
from model_hub.views.utils.evals import run_eval_func


def _deterministic_template(organization, workspace, criteria):
    return EvalTemplate.no_workspace_objects.create(
        name="deterministic-args-fixture",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "output": "choices",
            "eval_type_id": "DeterministicEvaluator",
            "required_keys": ["input"],
            "custom_eval": True,
        },
        eval_tags=["llm"],
        criteria=criteria,
        choices=["Yes", "No"],
        model="turing_large",
        visible_ui=True,
    )


def _run(template, organization):
    with capture_logs() as logs:
        with pytest.raises(Exception) as exc_info:
            run_eval_func(
                {"config": {}, "params": {}},
                {"input": "The sky is blue."},
                template,
                organization,
                source="eval_playground",
            )
    return exc_info.value, logs


def _error_lines(logs):
    return [
        entry
        for entry in logs
        if entry.get("log_level") in ("error", "critical", "exception")
    ]


@pytest.fixture(autouse=True)
def _no_metering(monkeypatch):
    monkeypatch.setattr(evals_module, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(
        "model_hub.views.utils.evals.GroundTruthService.resolve_preview_examples",
        lambda **_kwargs: None,
    )
    # The evaluator's LLM base builds a managed gateway client on init; the
    # failure under test happens before any model call, so a stub is enough.
    monkeypatch.setattr(
        "ee.usage.services.gateway_llm_client.get_gateway_client",
        lambda *_args, **_kwargs: MagicMock(),
    )
    monkeypatch.setattr(
        "ee.usage.services.gateway_llm_client.get_async_gateway_client",
        lambda *_args, **_kwargs: MagicMock(),
    )


@pytest.mark.django_db
def test_empty_rule_prompt_gives_readable_failure_without_error_log(
    organization, workspace
):
    template = _deterministic_template(organization, workspace, criteria="")

    error, logs = _run(template, organization)

    assert "rule_prompt" in str(error)
    assert _error_lines(logs) == [], (
        "a deterministic eval with a missing rule prompt is a config problem "
        f"and must not be logged at error level: {_error_lines(logs)}"
    )


@pytest.mark.django_db
def test_missing_rule_prompt_gets_the_same_handled_outcome(organization, workspace):
    template = _deterministic_template(organization, workspace, criteria=None)

    error, logs = _run(template, organization)

    assert "rule_prompt" in str(error)
    assert _error_lines(logs) == []
