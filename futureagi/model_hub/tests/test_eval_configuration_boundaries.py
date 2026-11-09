"""Expected eval setup failures retain safe messages and failed-call state."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.eval_input_validation import validate_eval_inputs
from model_hub.views.utils import evals as runner
from tfc.constants.api_calls import APICallStatusChoices

pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]


def assert_one_warning(logs):
    assert not [
        e for e in logs if e.get("log_level") in {"error", "exception", "critical"}
    ]
    assert (
        len([e for e in logs if e.get("event") == "evaluation_configuration_invalid"])
        == 1
    )


@pytest.fixture
def configured_runner(monkeypatch):
    # Exercise the licensed evaluator path, independently of this test install's
    # entitlement. The evaluator and shared execution boundary remain real.
    monkeypatch.setattr(
        "tfc.ee_gates.ee_model_gate_for_template", lambda *a, **kw: None
    )
    log = SimpleNamespace(
        status=APICallStatusChoices.PROCESSING.value, config="{}", save=MagicMock()
    )
    monkeypatch.setattr(runner, "log_and_deduct_cost_for_api_request", lambda **kw: log)
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *a, **kw: SimpleNamespace(allowed=True),
    )
    gateway = MagicMock()
    monkeypatch.setattr(
        "ee.usage.services.gateway_llm_client.get_gateway_client",
        lambda *a, **kw: gateway,
    )
    monkeypatch.setattr(
        "ee.usage.services.gateway_llm_client.get_async_gateway_client",
        lambda *a, **kw: gateway,
    )
    monkeypatch.setattr(
        runner.GroundTruthService, "inject_context", lambda *a, **kw: None
    )
    return log, gateway


def template_for(organization, workspace, criteria="", required_keys=None):
    return EvalTemplate.no_workspace_objects.create(
        name="configuration-fixture",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="llm",
        config={
            "output": "choices",
            "eval_type_id": "DeterministicEvaluator",
            "required_keys": required_keys or ["input"],
            "custom_eval": False,
        },
        criteria=criteria,
        choices=["Yes", "No"],
        model="turing_large",
        visible_ui=True,
    )


@pytest.mark.parametrize("criteria", ["", "   "])
def test_saved_preview_names_missing_criteria(
    auth_client, organization, workspace, configured_runner, criteria
):
    template = template_for(organization, workspace, criteria)
    with capture_logs() as logs:
        response = auth_client.post(
            "/model-hub/eval-playground/",
            {
                "template_id": str(template.id),
                "model": "turing_large",
                "mapping": {"input": "Synthetic content"},
                "config": {},
            },
            format="json",
        )
    assert response.status_code == 400
    assert "rule_prompt" in str(response.data)
    assert "retry" not in str(response.data).lower()
    assert_one_warning(logs)
    log, gateway = configured_runner
    assert log.status == APICallStatusChoices.ERROR.value
    assert "rule_prompt" in json.loads(log.config)["output"]["reason"]
    log.save.assert_called_once()
    gateway.chat.completions.create.assert_not_called()


def test_new_template_test_names_missing_criteria(auth_client, configured_runner):
    with capture_logs() as logs:
        response = auth_client.post(
            "/model-hub/test-evaluation/",
            {
                "name": "configuration-fixture",
                "template_type": "Futureagi",
                "output_type": "choices",
                "choices": {"Yes": "yes", "No": "no"},
                "required_keys": ["input"],
                "criteria": "",
                "model": "turing_large",
                "config": {"mapping": {"input": "Synthetic content"}},
            },
            format="json",
        )
    assert response.status_code == 400
    assert "rule_prompt" in str(response.data)
    assert_one_warning(logs)


@pytest.mark.parametrize("value", ["", "  ", None, [], {}, "null"])
def test_empty_declared_input_is_typed_and_safe(value):
    template = SimpleNamespace(config={"required_keys": ["system_prompt"]})
    with pytest.raises(ValueError) as raised:
        validate_eval_inputs(template, {"system_prompt": value})
    assert type(raised.value).__name__ == "MissingEvalInputError"
    assert "system_prompt" in str(raised.value)


def test_empty_input_preview_preserves_failed_record(
    auth_client, organization, workspace, configured_runner
):
    template = template_for(
        organization, workspace, "Grade {{system_prompt}}", ["system_prompt"]
    )
    with capture_logs() as logs:
        response = auth_client.post(
            "/model-hub/eval-playground/",
            {
                "template_id": str(template.id),
                "model": "turing_large",
                "mapping": {"system_prompt": ""},
                "config": {},
            },
            format="json",
        )
    assert response.status_code == 400
    assert "system_prompt" in str(response.data)
    assert_one_warning(logs)
    assert configured_runner[0].status == APICallStatusChoices.ERROR.value


def test_unexpected_evaluator_value_error_remains_private(
    auth_client, organization, workspace, configured_runner, monkeypatch
):
    from ee.evals.futureagi.eval_deterministic.evaluator import DeterministicEvaluator

    monkeypatch.setattr(
        DeterministicEvaluator,
        "run",
        MagicMock(side_effect=ValueError("private implementation detail")),
    )
    template = template_for(organization, workspace, "Grade {{input}}")
    with capture_logs() as logs:
        response = auth_client.post(
            "/model-hub/eval-playground/",
            {
                "template_id": str(template.id),
                "model": "turing_large",
                "mapping": {"input": "Synthetic content"},
                "config": {},
            },
            format="json",
        )
    assert response.status_code == 400
    assert "private implementation detail" not in str(response.data)
    DeterministicEvaluator.run.assert_called_once()
    assert any(e.get("log_level") in {"error", "exception"} for e in logs)


@pytest.mark.parametrize("model", ["protect", "protect_flash"])
def test_protect_keeps_rubric_exemption(model):
    from agentic_eval.core.utils.model_config import ModelConfigs
    from ee.evals.futureagi.eval_deterministic.evaluator import DeterministicEvaluator
    from model_hub.utils.eval_input_validation import validate_eval_configuration

    evaluator = object.__new__(DeterministicEvaluator)
    evaluator.provider = getattr(ModelConfigs, model.upper()).provider
    evaluator.rule_prompt = ""
    validate_eval_configuration("DeterministicEvaluator", evaluator)
    evaluator.validate_args()


def test_trace_keeps_failed_result_contract(
    organization, workspace, configured_runner, monkeypatch
):
    import uuid

    from model_hub.views.separate_evals import TraceEvalView
    from tracer.models.trace import Trace

    template = template_for(organization, workspace)
    trace_id = uuid.uuid4()
    monkeypatch.setattr(
        Trace.objects,
        "get",
        lambda **kw: SimpleNamespace(
            id=trace_id, input={"input": "Synthetic content"}, output={}
        ),
    )
    request = SimpleNamespace(
        data={"trace_id": str(trace_id), "model": "turing_large"},
        organization=organization,
    )
    with capture_logs() as logs:
        response = TraceEvalView().post(request, template.id)
    assert response.status_code == 200
    assert response.data["result"]["status"] == "failed"
    assert "rule_prompt" in response.data["result"]["reason"]
    assert_one_warning(logs)


@pytest.mark.parametrize("kind", ["rate", "budget"])
def test_trace_preserves_policy_denial_after_configuration_handler_merge(
    organization, workspace, monkeypatch, kind
):
    import uuid

    from ee.usage.exceptions import UsageLimitExceeded
    from ee.usage.schemas.events import CheckResult
    from model_hub.views import separate_evals
    from tfc.utils.evaluation_denials import EvaluationRateLimited
    from tracer.models.trace import Trace

    template = template_for(organization, workspace, "Grade {{input}}")
    trace_id = uuid.uuid4()
    monkeypatch.setattr(
        Trace.objects,
        "get",
        lambda **kw: SimpleNamespace(
            id=trace_id, input={"input": "Synthetic content"}, output={}
        ),
    )
    denial = (
        EvaluationRateLimited()
        if kind == "rate"
        else UsageLimitExceeded(
            CheckResult(
                allowed=False, error_code="BUDGET_PAUSED", reason="Budget paused"
            )
        )
    )
    runner_mock = MagicMock(side_effect=denial)
    monkeypatch.setattr(runner, "run_eval_func", runner_mock)
    request = SimpleNamespace(
        data={"trace_id": str(trace_id), "model": "turing_large"},
        organization=organization,
    )
    with capture_logs() as logs:
        response = separate_evals.TraceEvalView().post(request, template.id)
    runner_mock.assert_called_once()
    assert response.status_code == (429 if kind == "rate" else 402)
    assert response.data["code"] == (
        "rate_limited" if kind == "rate" else "BUDGET_PAUSED"
    )
    assert not [
        e for e in logs if e.get("log_level") in {"error", "exception", "critical"}
    ]


def test_missing_provider_preview_logs_once(
    auth_client, organization, workspace, configured_runner
):
    template = template_for(organization, workspace, "Grade {{input}}")
    template.config.update(eval_type_id="CustomPromptEvaluator", provider="openai")
    template.save(update_fields=["config"])
    with capture_logs() as logs:
        response = auth_client.post(
            "/model-hub/eval-playground/",
            {
                "template_id": str(template.id),
                "model": "gpt-4o-mini",
                "mapping": {"input": "Synthetic content"},
                "config": {},
            },
            format="json",
        )
    assert response.status_code == 400
    assert "openai" in str(response.data)
    assert_one_warning(logs)


def test_alternate_engine_does_not_require_deterministic_attributes():
    from model_hub.utils.eval_input_validation import validate_eval_configuration

    validate_eval_configuration("DeterministicEvaluator", object())


def test_custom_partial_and_unmapped_rules_are_unchanged():
    template = SimpleNamespace(
        config={"custom_eval": True, "required_keys": ["input", "context"]}
    )
    warning, values = validate_eval_inputs(
        template, {"input": "text"}, mapped_keys={"input", "context"}
    )
    assert warning["empty_keys"] == ["context"]
    assert values == {"input": "text", "context": ""}
    system = SimpleNamespace(
        config={"required_keys": ["input"], "optional_keys": ["context"]}
    )
    assert validate_eval_inputs(system, {}) == (None, {})
    assert validate_eval_inputs(system, {"input": ""}, mapped_keys=set()) == (
        None,
        {"input": ""},
    )
