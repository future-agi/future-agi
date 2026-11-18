"""The real MCC runner's corrected score reaches the response and saved log."""

import json
from types import SimpleNamespace

import pytest

from agentic_eval.core_evals.fi_evals.base_evaluator import (
    FiApiService,
    FiLoggingHelper,
)
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.views.utils import evals as evals_module
from tfc.constants.api_calls import APICallStatusChoices

usage_models = pytest.importorskip("ee.usage.models.usage", reason="requires ee/")
APICallLog = usage_models.APICallLog
pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]


@pytest.mark.parametrize(
    "constant_truth", [False, True], ids=["constant-prediction", "constant-truth"]
)
def test_mcc_playground_persists_normalized_zero(
    monkeypatch, auth_client, organization, workspace, constant_truth
):
    template = EvalTemplate.no_workspace_objects.create(
        name="mcc-runtime-fixture",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "output": "score",
            "eval_type_id": "MatthewsCorrelation",
            "required_keys": ["output", "expected"],
        },
        output_type_normalized="percentage",
        visible_ui=True,
        pass_threshold=0.5,
    )
    log_row = APICallLog.objects.create(
        organization=organization,
        workspace=workspace,
        status=APICallStatusChoices.PROCESSING.value,
        cost=0,
        source="eval_playground",
        source_id=str(template.id),
        config="{}",
    )
    # Only billing and outbound usage reporting are replaced. The endpoint,
    # registry, evaluator, result formatting and ORM save all run normally.
    monkeypatch.setattr(
        evals_module, "log_and_deduct_cost_for_api_request", lambda **_kwargs: log_row
    )
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr("ee.usage.services.emitter.emit", lambda _event: None)
    monkeypatch.setattr(FiApiService, "log_usage", lambda **_kwargs: None)
    monkeypatch.setattr(FiLoggingHelper, "create_eval_request", lambda **_kwargs: None)
    output, expected = ["a"] * 10, ["a"] * 8 + ["b", "c"]
    if constant_truth:
        output, expected = expected, output

    response = auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": "",
            "mapping": {"output": json.dumps(output), "expected": json.dumps(expected)},
            "config": {"params": {}},
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    log_row.refresh_from_db()
    stored = json.loads(log_row.config)["output"]
    assert stored == {"output": 0.5, "reason": "MCC: 0.0000, normalized=0.5000"}
    assert log_row.status == APICallStatusChoices.SUCCESS.value
    assert response.data["result"]["output"] == 0.5
    assert response.data["result"]["reason"] == stored["reason"]
