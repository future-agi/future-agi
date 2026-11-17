"""Shared admission refusals survive both SDK HTTP handlers.

Exercise the real admission -> evaluation wrapper -> decorated POST handler.
Only metering, ORM lookups, analytics and external execution are stubbed. No
billing/provider calls or database writes are needed for these regressions.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from ee.usage.schemas.events import CheckResult, UpgradeCTA
from ee.usage.services import metering
from evaluations import engine
from model_hub.services.ground_truth_service import GroundTruthService
from sdk.utils import evaluations as eval_utils
from sdk.views import evaluations as eval_views
from tfc.constants.api_calls import APICallStatusChoices

pytestmark = pytest.mark.requires_ee


@pytest.fixture(params=["standalone", "protect"])
def endpoint(request, monkeypatch):
    organization = SimpleNamespace(id="admission-test-org")
    user = SimpleNamespace(id="admission-test-user", organization=organization,
                           is_authenticated=True)
    workspace = SimpleNamespace(id="admission-test-workspace")
    template = SimpleNamespace(
        id="admission-test-template",
        name="Admission regression",
        model=None,
        eval_type="llm",
        config={"eval_type_id": "TestEval", "required_keys": ["input"]},
    )
    monkeypatch.setattr(
        eval_utils.EvalTemplate, "no_workspace_objects",
        SimpleNamespace(get=Mock(return_value=template)),
    )
    serializer = SimpleNamespace(
        is_valid=lambda: True, validated_data={"config": {}, "is_batched": False},
    )
    # The new-eval handler validates with StandaloneEvaluationsSerializer. The
    # ConfigureEvaluationsSerializer stub is kept from before it did.
    for name in ("ConfigureEvaluationsSerializer", "StandaloneEvaluationsSerializer"):
        monkeypatch.setattr(eval_views, name, Mock(return_value=serializer))
    monkeypatch.setattr(eval_views, "get_mixpanel_properties", Mock(return_value={}))
    monkeypatch.setattr(eval_views, "track_mixpanel_event", Mock())
    monkeypatch.setattr(GroundTruthService, "inject_context", Mock())
    check = Mock(return_value=CheckResult(allowed=True))
    monkeypatch.setattr(metering, "check_usage", check)
    cost_log = Mock(return_value=SimpleNamespace(
        status=APICallStatusChoices.PROCESSING.value,
    ))
    monkeypatch.setattr(eval_utils, "log_and_deduct_cost_for_api_request", cost_log)
    run_engine = Mock(side_effect=AssertionError("refusal must not run the engine"))
    monkeypatch.setattr(engine, "run_eval", run_engine)
    create = Mock(side_effect=AssertionError("refusal must not create an evaluation"))
    monkeypatch.setattr(eval_utils, "_create_evaluation", create)

    if request.param == "standalone":
        path = "/sdk/api/v1/new-eval/"
        view = eval_views.StandaloneEvalView_v2
        payload = {"eval_name": template.name, "inputs": {"input": "hello"}}
        runner = "_run_standalone_eval"
    else:
        path = "/sdk/api/v1/eval/"
        view = eval_views.StandaloneEvalView
        payload = {"inputs": [{"input": "hello"}], "config": {"1": {}}}
        runner = "_run_protect"

    def post():
        http_request = APIRequestFactory().post(path, payload, format="json")
        http_request.workspace = workspace
        http_request.organization = organization
        force_authenticate(http_request, user=user)
        response = view.as_view()(http_request)
        response.render()
        return response

    return SimpleNamespace(
        post=post, check=check, cost_log=cost_log, run_engine=run_engine,
        create=create, user=user, workspace=workspace, template=template,
        runner=runner,
    )


def assert_refusal(response, code, http_status, *, retryable):
    assert response.status_code == http_status
    assert response.data["status"] is False
    assert response.data["code"] == code
    assert response.data["error_code"] == code
    assert response.data["retryable"] is retryable


@pytest.mark.parametrize(
    "code,reason,http_status,retryable",
    [
        ("FREE_TIER_LIMIT", "Free tier evaluation limit reached", 429, False),
        ("BUDGET_PAUSED", "Usage paused by your budget limit", 429, False),
        ("RATE_LIMITED", "Request limit reached; wait for the next window", 429, True),
        ("PAYMENT_REQUIRED", "Update your payment method to restore access", 402, False),
        ("BALANCE_EXHAUSTED", "Add credits in Settings > Billing", 402, False),
        ("BUDGET_CHECK_UNAVAILABLE", "Budget status is unavailable; retry later", 503, True),
    ],
)
def test_metering_refusal_keeps_status_code_reason_and_recovery(
    endpoint, code, reason, http_status, retryable,
):
    cta = UpgradeCTA(text="Upgrade for more usage", plan="payg")
    endpoint.check.return_value = CheckResult(
        allowed=False, error_code=code, reason=reason, dimension="evals",
        current_usage=100, limit=100,
        upgrade_cta=cta if code == "FREE_TIER_LIMIT" else None,
    )

    response = endpoint.post()

    assert_refusal(response, code, http_status, retryable=retryable)
    assert response.data["message"] == reason
    assert response.data["detail"] == reason
    assert response.data["result"] == reason
    assert response.data["dimension"] == "evals"
    assert response.data["current_usage"] == 100
    assert response.data["limit"] == 100
    if code == "FREE_TIER_LIMIT":
        assert response.data["upgrade_cta"] == cta.model_dump()
    else:
        assert "upgrade_cta" not in response.data
    endpoint.check.assert_called_once()
    endpoint.cost_log.assert_not_called()
    endpoint.run_engine.assert_not_called()
    endpoint.create.assert_not_called()


@pytest.mark.parametrize(
    "log_status,code,http_status,retryable",
    [
        ("rate_limited", "RATE_LIMITED", 429, True),
        ("resource_limit", "RESOURCE_LIMIT", 429, False),
        ("insufficient_credits", "INSUFFICIENT_CREDITS", 402, False),
    ],
)
def test_legacy_admission_status_is_a_typed_refusal(
    endpoint, log_status, code, http_status, retryable,
):
    endpoint.cost_log.return_value = SimpleNamespace(status=log_status)

    response = endpoint.post()

    assert_refusal(response, code, http_status, retryable=retryable)
    assert response.data["message"]
    assert "Unable to run standalone evaluation" not in response.data["message"]
    endpoint.check.assert_called_once()
    endpoint.cost_log.assert_called_once()
    endpoint.run_engine.assert_not_called()
    endpoint.create.assert_not_called()


@pytest.mark.parametrize("failure", ["metering", "cost-log", "missing-log", "error-status"])
def test_admission_check_outage_is_retryable_503(endpoint, failure):
    diagnostic = "private dependency diagnostic"
    if failure == "metering":
        endpoint.check.side_effect = RuntimeError(diagnostic)
    elif failure == "cost-log":
        endpoint.cost_log.side_effect = RuntimeError(diagnostic)
    elif failure == "missing-log":
        endpoint.cost_log.return_value = None
    else:
        endpoint.cost_log.return_value = SimpleNamespace(status="error")

    response = endpoint.post()

    assert_refusal(response, "USAGE_CHECK_UNAVAILABLE", 503, retryable=True)
    assert "retry" in response.data["message"].lower()
    assert diagnostic not in response.content.decode()
    endpoint.check.assert_called_once()
    if failure == "metering":
        endpoint.cost_log.assert_not_called()
    else:
        endpoint.cost_log.assert_called_once()
    endpoint.run_engine.assert_not_called()
    endpoint.create.assert_not_called()


def test_processing_admission_still_returns_the_cost_log(endpoint):
    result = eval_utils._log_and_deduct_cost_for_standalone_eval(
        endpoint.user, endpoint.template, True, {"input": "hello"},
        workspace=endpoint.workspace,
    )

    assert result is endpoint.cost_log.return_value
    endpoint.check.assert_called_once()
    endpoint.cost_log.assert_called_once()


def test_allowed_admission_without_legacy_metering_is_unchanged(endpoint, monkeypatch):
    monkeypatch.setattr(eval_utils, "log_and_deduct_cost_for_api_request", None)

    result = eval_utils._log_and_deduct_cost_for_standalone_eval(
        endpoint.user, endpoint.template, True, {"input": "hello"},
        workspace=endpoint.workspace,
    )

    assert result is None
    endpoint.check.assert_called_once()


def test_unrelated_errors_keep_generic_500(endpoint, monkeypatch):
    runner = Mock(side_effect=RuntimeError("unrelated failure"))
    monkeypatch.setattr(eval_views, endpoint.runner, runner)

    response = endpoint.post()

    runner.assert_called_once()
    assert response.status_code == 500
    assert response.data["code"] == "server_error"
    assert "Unable to run standalone evaluation" in response.data["message"]


def test_engine_error_transport_is_unchanged(endpoint, monkeypatch):
    runner = Mock(side_effect=eval_utils.StandaloneEvaluationError("Evaluation failed"))
    monkeypatch.setattr(eval_views, endpoint.runner, runner)

    response = endpoint.post()

    runner.assert_called_once()
    assert response.status_code == (400 if endpoint.runner == "_run_standalone_eval" else 500)
