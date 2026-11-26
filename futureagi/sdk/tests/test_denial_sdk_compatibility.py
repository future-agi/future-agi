"""SDK failure responses must be parseable and invalid input must not execute."""

from unittest.mock import Mock, patch

import pytest

from ee.usage.exceptions import UsageLimitExceeded
from ee.usage.schemas.events import CheckResult
from sdk.tests.test_evaluations import api_key, eval_template, sdk_client  # noqa: F401
from tfc.utils.evaluation_denials import EvaluationRateLimited


@pytest.mark.django_db
@pytest.mark.parametrize("code", ["rate_limited", "BUDGET_PAUSED", "FREE_TIER_LIMIT"])
@pytest.mark.parametrize(
    "existing_record", [False, True], ids=["new-denial", "legacy-denial"]
)
def test_async_denial_runtime_is_sdk_compatible(
    sdk_client,  # noqa: F811
    user,
    organization,
    workspace,
    eval_template,  # noqa: F811
    code,
    existing_record,
):
    from model_hub.models.evaluation import Evaluation
    from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

    denial = (
        EvaluationRateLimited()
        if code == "rate_limited"
        else UsageLimitExceeded(
            CheckResult(
                allowed=False,
                error_code=code,
                reason="Synthetic usage paused; resume before retrying.",
            )
        )
    )
    evaluation = Evaluation.objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        eval_template=eval_template,
        input_data={"input": "hello"},
    )
    if existing_record:
        evaluation.status = "failed"
        evaluation.reason = str(denial)
        evaluation.metadata = {"denial": denial.as_dict()}
        evaluation.save()
    else:
        with (
            patch("tfc.temporal.evaluations.activities.close_old_connections"),
            patch("sdk.utils.evaluations._run_eval", side_effect=denial),
        ):
            _run_single_evaluation_sync(str(evaluation.id))
    response = sdk_client.get("/sdk/api/v1/new-eval/", {"eval_id": str(evaluation.id)})
    assert response.status_code == 200
    payload = response.json()["result"]
    assert payload["eval_status"] == "failed"
    result = payload["result"]
    assert result["denial"]["code"] == code
    assert result["value"] is None
    assert result["reason"] == str(denial)
    # ai-evaluation 1.1.0 EvalResult.runtime is an integer, not Optional[int].
    # Omitting runtime also works, but explicit null does not.
    assert result["runtime"] == 0

    # Exercise the actual shipped client when installed in the integration lane.
    # The wire-shape assertions above remain mandatory without the SDK extra.
    from importlib.metadata import PackageNotFoundError, version

    try:
        sdk_version = version("ai-evaluation")
    except PackageNotFoundError:
        return
    from fi.evals.evaluator import Evaluator
    from fi.evals.execution import Execution

    client = Evaluator.__new__(Evaluator)
    client._base_url = "http://127.0.0.1:9"
    client._default_timeout = 1
    client.request = Mock(return_value=Mock(json=lambda: response.json()))
    handle = Execution(
        id=str(evaluation.id),
        kind="eval",
        _refresher=lambda: client._refresh_eval_execution(str(evaluation.id)),
    )
    handle.wait(timeout=1, poll_interval=0, raise_on_failure=False)
    assert handle.status == "failed"
    assert handle.result.runtime == 0
    assert handle.result.output is None
    assert handle.result.reason == str(denial)
    print(
        "SDK_POLL",
        sdk_version,
        code,
        "status=",
        handle.status,
        "runtime=",
        handle.result.runtime,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("empty", ["", "   ", None])
def test_empty_required_input_is_400_before_metering(sdk_client, eval_template, empty):  # noqa: F811
    with (
        patch(
            "sdk.utils.evaluations._log_and_deduct_cost_for_standalone_eval"
        ) as meter,
        patch("evaluations.engine.run_eval") as engine,
    ):
        response = sdk_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": eval_template.name,
                "inputs": {"input": empty, "output": "hello"},
            },
            format="json",
        )
    assert response.status_code == 400, response.data
    assert response.data["code"] == "invalid"
    assert "input" in str(response.data)
    meter.assert_not_called()
    engine.assert_not_called()


@pytest.mark.django_db
def test_nonempty_required_input_still_executes(sdk_client, eval_template):  # noqa: F811
    from types import SimpleNamespace

    result = SimpleNamespace(
        data={"result": True},
        failure=False,
        reason="Synthetic valid input",
        runtime=1,
        model_used=None,
        metrics={},
        metadata={},
        output_type="boolean",
        value=True,
        cost={},
        token_usage={},
    )
    with (
        patch(
            "sdk.utils.evaluations._log_and_deduct_cost_for_standalone_eval",
            return_value=None,
        ),
        patch("evaluations.engine.run_eval", return_value=result) as engine,
    ):
        response = sdk_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": eval_template.name,
                "inputs": {"input": "hello", "output": "hello"},
            },
            format="json",
        )
    assert response.status_code == 200, response.data
    engine.assert_called_once()
