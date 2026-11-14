"""Registered classification scores survive the playground-to-usage-log path."""

import json
from types import SimpleNamespace

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.views.utils import evals as evals_module
from tfc.constants.api_calls import APICallStatusChoices


@pytest.mark.django_db
@pytest.mark.requires_ee
@pytest.mark.parametrize("eval_id", ["PrecisionScore", "FBetaScore"])
@pytest.mark.parametrize(
    "params, predictions, labels, precision, fbeta, policy",
    [
        (
            {},
            ["correct"] * 3 + ["incorrect"] * 2,
            ["correct"] + ["incorrect"] * 4,
            1 / 3,
            1 / 2,
            "positive='correct'",
        ),
        (
            {},
            ["a", "a", "b", "extra"],
            ["a", "b", "b", "c"],
            3 / 8,
            1 / 3,
            "macro over 4 classes",
        ),
        (
            {"positive_label": 0},
            [0, 0, -1, -1, 1],
            [0, -1, -1, -1, 1],
            1 / 2,
            2 / 3,
            "positive='0'",
        ),
    ],
    ids=["known-pair", "macro", "explicit-zero"],
)
def test_real_function_score_is_saved_and_read_back(
    monkeypatch,
    organization,
    workspace,
    eval_id,
    params,
    predictions,
    labels,
    precision,
    fbeta,
    policy,
):
    usage = pytest.importorskip("ee.usage.models.usage")
    template = EvalTemplate.no_workspace_objects.create(
        name=f"classification-storage-{eval_id.lower()}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "output": "score",
            "eval_type_id": eval_id,
            "required_keys": ["output", "expected"],
            "function_eval": True,
            "config": params,
        },
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )
    log_row = usage.APICallLog.objects.create(
        organization=organization,
        workspace=workspace,
        source="eval_playground",
        source_id=str(template.id),
        status=APICallStatusChoices.PROCESSING.value,
        config="{}",
        cost=0,
        deducted_cost=0,
    )
    # Only billing admission/emission and external telemetry are stubbed. Class
    # lookup, construction, mapping, execution, formatting and ORM save are real.
    monkeypatch.setattr(
        evals_module, "log_and_deduct_cost_for_api_request", lambda **_kwargs: log_row
    )
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr("ee.usage.services.emitter.emit", lambda _event: None)
    monkeypatch.setattr(
        "agentic_eval.core_evals.llm_services.fi_api_service.FiApiService.log_usage",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "agentic_eval.core_evals.fi_evals.base_evaluator.BaseEvaluator._log_evaluation_request",
        lambda *_args: None,
    )

    result = evals_module.run_eval_func(
        {"config": params},
        {"output": predictions, "expected": labels},
        template,
        organization,
        workspace=workspace,
        model=None,
    )
    expected_score = precision if eval_id == "PrecisionScore" else fbeta
    assert result["output"] == pytest.approx(expected_score)
    assert policy in result["reason"]
    assert result["log_id"] == str(log_row.log_id)
    log_row.refresh_from_db()
    assert log_row.status == APICallStatusChoices.SUCCESS.value
    stored = json.loads(log_row.config)
    assert stored["output"]["output"] == pytest.approx(expected_score)
    assert stored["output"]["reason"] == result["reason"]
    assert stored["input"] == {"output": predictions, "expected": labels}
