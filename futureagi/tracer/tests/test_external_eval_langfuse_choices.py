"""Choices scores reach Langfuse without credentials or external services."""

import socket
from copy import deepcopy
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def external_eval(monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("External eval tests must not use the network")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)

    from tracer.utils import external_eval

    return external_eval


@pytest.mark.parametrize("score", [0.25, 1, 0])
def test_choices_result_exports_numeric_score_as_float(external_eval, score):
    result = external_eval._process_results_langfuse({"score": score, "choice": "Good"})

    assert result == float(score)
    assert isinstance(result, float)


@pytest.mark.parametrize(
    ("value", "expected", "expected_type"),
    [
        (["Good", "Bad"], "Good, Bad", str),
        ([], "", str),
        ("Passed", 1, int),
        ("Failed", 0, int),
        (2, 2.0, float),
        (0.75, 0.75, float),
    ],
)
def test_existing_langfuse_value_types_are_unchanged(
    external_eval, value, expected, expected_type
):
    result = external_eval._process_results_langfuse(value)

    assert result == expected
    assert isinstance(result, expected_type)


@pytest.mark.parametrize(
    "value",
    [
        {"choice": "Good"},
        {"score": None, "choice": "Good"},
        {"score": "0.25", "choice": "Good"},
        {"score": [], "choice": "Good"},
        {"score": {}, "choice": "Good"},
    ],
)
def test_choices_result_without_numeric_score_is_rejected(external_eval, value):
    with pytest.raises(ValueError):
        external_eval._process_results_langfuse(value)


def test_choices_external_eval_completes_and_exports_score(external_eval):
    from accounts.models import Organization
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.external_eval_config import (
        ExternalEvalConfig,
        PlatformChoices,
        StatusChoices,
    )

    choice_value = {"score": 0.25, "choice": "Good"}
    reason = "The response matches the Good choice."
    template = EvalTemplate(
        name="Choices eval",
        config={"eval_type_id": "DeterministicEvaluator", "output": "choices"},
        choices=["Good", "Bad"],
        choice_scores={"Good": 0.25, "Bad": 0.0},
    )
    # Use real model instances with mocked persistence so no credentials are
    # needed to satisfy ExternalEvalConfig.full_clean's integration-key checks.
    config = ExternalEvalConfig(
        organization=Organization(name="Choices test organization"),
        eval_template=template,
        name="Choices score",
        platform=PlatformChoices.LANGFUSE,
        credentials={"trace_id": "test-trace", "span_id": "test-span"},
        status=StatusChoices.PENDING,
        mapping={"input": "sample"},
    )
    runner = Mock(spec=external_eval.EvaluationRunner)
    runner._effective_choice_scores.return_value = template.choice_scores
    # Keep production formatting: the raw choice becomes the stored score dict.
    runner.format_output = MethodType(
        external_eval.EvaluationRunner.format_output, runner
    )
    runner.map_fields.return_value = {"input": "sample"}
    eval_instance = runner._create_eval_instance.return_value
    eval_instance.run.return_value = SimpleNamespace(
        eval_results=[{"data": choice_value, "reason": reason}]
    )
    saved_states = []

    def record_save():
        saved_states.append((config.status, deepcopy(config.eval_results)))

    with (
        patch.object(ExternalEvalConfig.objects, "get", return_value=config) as get,
        patch.object(config, "save", side_effect=record_save),
        patch.object(external_eval, "_log_and_deduct_cost_for_external_eval"),
        patch.object(external_eval, "EvaluationRunner", return_value=runner),
        patch("evaluations.engine.registry.get_eval_class"),
        patch("langfuse.Langfuse") as langfuse_class,
    ):
        external_eval.run_external_eval_config(config.id)

    get.assert_called_once_with(id=config.id)
    eval_instance.run.assert_called_once_with(input="sample")
    assert config.eval_results["value"] == choice_value
    assert saved_states[0][1]["value"] == choice_value
    assert config.status == StatusChoices.COMPLETED
    assert config.error_message is None
    assert saved_states[-1] == (StatusChoices.COMPLETED, config.eval_results)
    langfuse_class.assert_called_once_with(secret_key=None, public_key=None, host=None)
    langfuse_class.return_value.create_score.assert_called_once_with(
        trace_id="test-trace",
        observation_id="test-span",
        name="Choices score",
        value=0.25,
        comment=reason,
    )
    assert isinstance(
        langfuse_class.return_value.create_score.call_args.kwargs["value"], float
    )
