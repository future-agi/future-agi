"""Policy refusals must not execute, charge, or masquerade as server failures."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ee.usage.exceptions import UsageLimitExceeded
from ee.usage.schemas.events import CheckResult
from sdk.tests import test_evaluations as evaluation_fixtures
from tfc.utils.evaluation_denials import EvaluationDenied, EvaluationRateLimited

api_key = evaluation_fixtures.api_key
eval_template = evaluation_fixtures.eval_template
sdk_client = evaluation_fixtures.sdk_client


@pytest.fixture
def metering(monkeypatch):
    check = MagicMock(return_value=CheckResult(allowed=True))
    monkeypatch.setitem(
        sys.modules, "ee.usage.services.metering", SimpleNamespace(check_usage=check)
    )
    return check


@pytest.fixture
def ledger():
    return SimpleNamespace(
        status="rate_limited", cost=0, deducted_cost=0, config="{}", save=MagicMock()
    )


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["eval", "new-eval"])
@pytest.mark.parametrize(
    "denial",
    [
        "rate_limited",
        "PAYMENT_REQUIRED",
        "FREE_TIER_LIMIT",
        "BUDGET_PAUSED",
        "RATE_LIMITED",
    ],
)
def test_sdk_policy_denial_is_not_500(
    sdk_client, eval_template, metering, ledger, route, denial
):
    if denial != "rate_limited":
        metering.return_value = CheckResult(
            allowed=False,
            error_code=denial,
            reason="Usage is blocked",
            dimension="credits",
            current_usage=10,
            limit=10,
        )
    with (
        patch(
            "sdk.utils.evaluations.log_and_deduct_cost_for_api_request",
            return_value=ledger,
        ) as log,
        patch("evaluations.engine.run_eval") as engine,
        patch("sdk.views.evaluations.logger") as logger,
        patch("sdk.utils.evaluations.logger") as helper_logger,
    ):
        payload = (
            {"inputs": [{"input": "hello"}], "config": {str(eval_template.eval_id): {}}}
            if route == "eval"
            else {
                "eval_name": eval_template.name,
                "inputs": {"input": "hello", "output": "world"},
            }
        )
        response = sdk_client.post(f"/sdk/api/v1/{route}/", payload, format="json")
    assert response.status_code == (429 if denial.lower() == "rate_limited" else 402), (
        response.data
    )
    assert response.data["code"] == (
        "rate_limited" if denial.lower() == "rate_limited" else denial
    )
    assert "Retry-After" not in response
    if response.status_code == 402:
        assert response.data["dimension"] == "credits"
        assert response.data["current_usage"] == 10
    engine.assert_not_called()
    logger.exception.assert_not_called()
    helper_logger.exception.assert_not_called()
    if denial != "rate_limited":
        log.assert_not_called()
    assert ledger.status == "rate_limited"
    assert ledger.cost == ledger.deducted_cost == 0


@pytest.mark.django_db
@pytest.mark.parametrize("row_status", [None, "error", "success"])
def test_internal_ledger_failure_remains_500(
    sdk_client, eval_template, metering, ledger, row_status
):
    ledger.status = row_status
    with (
        patch(
            "sdk.utils.evaluations.log_and_deduct_cost_for_api_request",
            return_value=ledger if row_status else None,
        ),
        patch("evaluations.engine.run_eval") as engine,
        patch("sdk.views.evaluations.logger") as logger,
    ):
        response = sdk_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": eval_template.name,
                "inputs": {"input": "hello", "output": "world"},
            },
            format="json",
        )
    assert response.status_code == 500
    logger.exception.assert_called_once()
    engine.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["rate", "budget", "internal"])
def test_background_runner_preserves_denial_ledger(
    eval_template, organization, metering, ledger, kind
):
    from model_hub.views.utils import evals

    instance = MagicMock()
    runner = MagicMock()
    runner._create_eval_instance.return_value = instance
    runner.map_fields.return_value = {"input": "hello"}
    if kind == "budget":
        metering.return_value = CheckResult(
            allowed=False, error_code="BUDGET_PAUSED", reason="Budget paused"
        )
    if kind == "internal":
        ledger.status = "processing"
        instance.run.side_effect = RuntimeError("engine failed")
    with (
        patch.object(evals, "EvaluationRunner", return_value=runner),
        patch.object(evals, "log_and_deduct_cost_for_api_request", return_value=ledger),
        patch.object(evals, "logger") as logger,
    ):
        with pytest.raises(RuntimeError if kind == "internal" else EvaluationDenied):
            evals.run_eval_func(
                {},
                {"input": "hello"},
                eval_template,
                organization,
                resolved_version=SimpleNamespace(id="version", version_number=1),
            )
    if kind == "internal":
        assert ledger.status == "error"
        logger.exception.assert_called()
    else:
        assert ledger.status == "rate_limited"
        ledger.save.assert_not_called()
        instance.run.assert_not_called()
        logger.exception.assert_not_called()


def test_batch_denial_exposes_completed_prefix():
    from sdk.utils.evaluations import _run_batched_standalone_eval

    completed = {"eval_id": "synthetic-eval", "value": "Pass"}
    with patch(
        "sdk.utils.evaluations._run_standalone_eval",
        side_effect=[completed, EvaluationRateLimited()],
    ) as run:
        with pytest.raises(EvaluationDenied) as caught:
            _run_batched_standalone_eval(
                None,
                {"input": ["one", "two", "three"]},
                None,
                None,
                None,
                {},
                False,
                False,
                None,
                None,
            )
    assert run.call_count == 2
    from tfc.utils.general_methods import GeneralMethods

    response = caught.value.response(GeneralMethods())
    assert response.status_code == 429
    assert response.data["completed_results"] == [completed]
    assert response.data["failed_index"] == 1


@pytest.mark.django_db
def test_async_denial_survives_polling(
    sdk_client, user, organization, workspace, eval_template
):
    from model_hub.models.evaluation import Evaluation
    from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

    evaluation = Evaluation.objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        eval_template=eval_template,
        input_data={"input": "hello"},
    )
    with (
        patch("tfc.temporal.evaluations.activities.close_old_connections"),
        patch("sdk.utils.evaluations._run_eval", side_effect=EvaluationRateLimited()),
    ):
        result = _run_single_evaluation_sync(str(evaluation.id))
    assert result["status"] == "failed"
    evaluation.refresh_from_db()
    assert evaluation.value is None
    response = sdk_client.get("/sdk/api/v1/new-eval/", {"eval_id": str(evaluation.id)})
    assert response.status_code == 200
    assert response.data["result"]["eval_status"] == "failed"
    assert response.data["result"]["result"]["denial"]["code"] == "rate_limited"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param(
            json.dumps({"usage": {"total_tokens": 12}, "explanation": "Fail verdict"}),
            id="custom-prompt-json-string",
        ),
        pytest.param({"explanation": "Fail verdict"}, id="dictionary"),
        pytest.param(["Fail verdict"], id="list"),
        pytest.param(12, id="number"),
        pytest.param(True, id="boolean"),
        pytest.param(None, id="null"),
        pytest.param("", id="empty-string"),
    ],
)
def test_poll_failed_evaluation_preserves_metadata_shapes(
    sdk_client, user, eval_template, metadata
):
    from sdk.utils.evaluations import _create_evaluation

    # CustomPromptEvaluator uses JSON-string metadata for ordinary Fail verdicts.
    # The JSONField also accepts other shapes; only dictionaries carry denials.
    evaluation = _create_evaluation(
        eval_template=eval_template,
        inputs={"input": "hello", "output": "world"},
        user=user,
        eval_config={},
        result={
            "failure": True,
            "data": {"result": "Fail"},
            "value": "Fail",
            "output": "choices",
            "reason": "Fail verdict",
            "metadata": metadata,
        },
        error_localizer_enabled=False,
        trace_eval=False,
        custom_eval_name=None,
        span_id=None,
    )
    evaluation.refresh_from_db()
    assert evaluation.status == "failed"
    assert evaluation.metadata == metadata
    response = sdk_client.get("/sdk/api/v1/new-eval/", {"eval_id": str(evaluation.id)})
    assert response.status_code == 200, response.data
    assert response.data["result"]["eval_status"] == "failed"
    result = response.data["result"]["result"]
    assert result["value"] == "Fail"
    assert result["reason"] == "Fail verdict"
    assert "denial" not in result


@pytest.mark.parametrize(
    "denial",
    [
        EvaluationRateLimited(),
        UsageLimitExceeded(
            CheckResult(
                allowed=False, error_code="BUDGET_PAUSED", reason="Budget paused"
            )
        ),
    ],
)
def test_direct_evaluator_never_bypasses_denial(denial):
    from ee.tests.test_direct_evaluator_scoring import (
        _direct_evaluator,
        _eval_template,
        _EvalConfig,
    )

    evaluator = _direct_evaluator()
    template = _eval_template(output="score", output_type_normalized="score")
    with (
        patch("model_hub.views.utils.evals.run_eval_func", side_effect=denial),
        patch(
            "model_hub.models.evals_metric.EvalTemplate.objects.get",
            side_effect=ValueError("not saved"),
        ),
        patch.object(evaluator, "_run_eval_direct") as fallback,
    ):
        with pytest.raises(EvaluationDenied):
            evaluator._run_eval_with_config(
                _EvalConfig(template), {}, "output", "prompt"
            )
    fallback.assert_not_called()


@pytest.mark.parametrize("implementation", ["service", "temporal"])
@pytest.mark.parametrize("kind", ["rate", "budget", "internal"])
def test_simulation_persists_explicit_denial_without_score(implementation, kind):
    from simulate.services import test_executor
    from simulate.temporal.activities import xl

    module = test_executor if implementation == "service" else xl
    template = SimpleNamespace(
        config={"output": "score"}, output_type_normalized="score"
    )
    config = SimpleNamespace(
        id="fixture-eval",
        name="Fixture evaluation",
        eval_template=template,
        mapping={},
        config={},
        model=None,
        kb_id=None,
        error_localizer=False,
        save=MagicMock(),
    )
    run_test = SimpleNamespace(
        organization=SimpleNamespace(id="fixture-org"), workspace=None
    )
    call = SimpleNamespace(
        id="fixture-call",
        test_execution_id="fixture-run",
        test_execution=SimpleNamespace(scenario_ids=[], run_test=run_test),
        agent_version=SimpleNamespace(id="fixture-version"),
        call_metadata={},
        eval_outputs={},
        save=MagicMock(),
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
    error = RuntimeError("engine failed") if kind == "internal" else denial
    target = (
        "simulate.services.test_executor.run_eval_func"
        if implementation == "service"
        else "model_hub.views.utils.evals.run_eval_func"
    )
    with (
        patch.object(module, "close_old_connections"),
        patch.object(module, "effective_simulation_mapping", return_value={}),
        patch.object(module, "build_simulation_context_map", return_value=({}, {})),
        patch("simulate.models.Scenarios.objects.filter") as scenarios,
        patch.object(module, "eval_source", return_value="simulate"),
        patch(target, side_effect=error),
        patch.object(module, "logger") as logger,
    ):
        scenarios.return_value.select_related.return_value.values_list.return_value = []
        with pytest.raises(type(error)):
            if implementation == "service":
                test_executor.TestExecutor._run_single_simulate_evaluation(
                    None, config, call, {}
                )
            else:
                xl._run_single_evaluation(config, call, {})
    result = call.eval_outputs[config.id]
    assert result["output"] is None
    from model_hub.models.choices import StatusType

    assert result["status"] == StatusType.FAILED.value
    if kind != "internal":
        assert result["denial"]["code"] == denial.code
        assert result["reason"] == str(denial)
        logger.error.assert_not_called()
    else:
        assert result["error"] == "error"
        logger.error.assert_called_once()
    call.save.assert_called_once()
    config.save.assert_called_once()


def test_denial_schema_accepts_runtime_billing_response():
    from sdk.serializers.contracts import SDKEvaluationDenialResponseSerializer
    from tfc.utils.general_methods import GeneralMethods

    denial = UsageLimitExceeded(
        CheckResult(
            allowed=False,
            error_code="FREE_TIER_LIMIT",
            reason="Free tier limit reached",
            dimension="evaluations",
            current_usage=5,
            limit=5,
        )
    )
    response = denial.response(GeneralMethods())
    serializer = SDKEvaluationDenialResponseSerializer(data=response.data)
    assert serializer.is_valid(), serializer.errors
    assert response.status_code == 402


def test_composite_child_denial_has_no_score():
    from model_hub.utils.composite_execution import _execute_child

    template = SimpleNamespace(id="fixture-child", name="Fixture child")
    link = SimpleNamespace(order=0, pinned_version=None)
    with (
        patch(
            "model_hub.utils.composite_execution.resolve_child_binding",
            return_value=({}, None),
        ),
        patch(
            "model_hub.views.utils.evals.run_eval_func",
            side_effect=EvaluationRateLimited(),
        ),
    ):
        result = _execute_child(
            child_template=template,
            link=link,
            weight=1,
            mapping={},
            config={},
            model=None,
            org=None,
            workspace=None,
            input_data_types={},
            row_context=None,
            span_context=None,
            trace_context=None,
            session_context=None,
            call_context=None,
            error_localizer=False,
            source="test",
        )
    assert result.status == "failed"
    assert result.error == "rate_limited"
    assert result.score is None
    assert result.output is None


@pytest.mark.requires_ee
def test_simulation_optimizer_does_not_fall_back_after_denial():
    pytest.importorskip("optuna")  # optional agent-optimizer extra
    from ee.agenthub.fix_your_agent.fix_your_agent import SimulationEvaluator
    from ee.tests.test_direct_evaluator_scoring import _eval_template

    evaluator = SimulationEvaluator.__new__(SimulationEvaluator)
    evaluator.organization = SimpleNamespace(id="fixture-org")
    evaluator.workspace = None
    evaluator.eval_source = "test"
    template = _eval_template(output="score", output_type_normalized="score")
    config = SimpleNamespace(
        eval_template=template, model=None, mapping={"input": "transcript"}, config={}
    )
    with (
        patch("django.db.close_old_connections"),
        patch.object(evaluator, "_should_use_eval_runner", return_value=True),
        patch.object(
            evaluator, "_resolve_eval_template_for_runner", return_value=template
        ),
        patch(
            "ee.agenthub.fix_your_agent.fix_your_agent.run_eval_func",
            side_effect=EvaluationRateLimited(),
        ),
        patch("agentic_eval.core_evals.fi_evals.LlmEvaluator") as fallback,
    ):
        with pytest.raises(EvaluationDenied):
            evaluator._run_eval_with_config(config, "hello", "prompt", {})
    fallback.assert_not_called()


def test_legacy_logging_does_not_swallow_typed_denial():
    from ee.usage.utils import usage_entries

    denial = UsageLimitExceeded(
        CheckResult(allowed=False, error_code="BUDGET_PAUSED", reason="Budget paused")
    )
    with (
        patch.object(usage_entries, "check_audio_in_mappings", return_value=(False, 0)),
        patch.object(
            usage_entries.APICallType.objects, "get", return_value=SimpleNamespace()
        ),
        patch.object(
            usage_entries, "check_if_api_call_is_rate_limited", side_effect=denial
        ),
        patch.object(usage_entries, "logger") as logger,
    ):
        with pytest.raises(UsageLimitExceeded):
            usage_entries.log_and_deduct_cost_for_api_request(
                SimpleNamespace(), "turing_large_evaluator"
            )
    logger.exception.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["playground", "draft"])
@pytest.mark.parametrize("kind", ["rate", "budget"])
def test_management_eval_preserves_typed_denials(
    auth_client, user, workspace, route, kind
):
    from model_hub.tests.test_code_eval_setup_error import (
        _js_code_eval,
        _playground,
        _test_evaluation,
    )

    call = _playground if route == "playground" else _test_evaluation
    template = _js_code_eval(user, workspace)
    denial = (
        EvaluationRateLimited()
        if kind == "rate"
        else UsageLimitExceeded(
            CheckResult(
                allowed=False, error_code="BUDGET_PAUSED", reason="Budget paused"
            )
        )
    )
    with (
        patch("model_hub.views.separate_evals.run_eval_func", side_effect=denial),
        patch("model_hub.views.separate_evals.logger") as logger,
    ):
        response = call(auth_client, template)
    assert response.status_code == (429 if kind == "rate" else 402), response.data
    assert response.data["code"] == (
        "rate_limited" if kind == "rate" else "BUDGET_PAUSED"
    )
    logger.exception.assert_not_called()
