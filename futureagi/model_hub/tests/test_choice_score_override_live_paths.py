"""Assertion-red companions to the byte-preserved accepted override suite.

These tests use APIs present before the Choices fix, never its new helpers.
Only the judge and unrelated services are stubbed: request normalization,
runner construction, score formatting and persisted/returned values are real.
On the red sibling, Good scores 1.0 instead of the requested 0.25. Each path
also has a no-override control. Runtime red/green execution is owed separately.
"""

from __future__ import annotations

import ast
import json
from concurrent.futures import Future
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.tests import test_choice_score_override as accepted
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401

# Reuse fixture definitions without collecting or calling the accepted tests.
organization = accepted.organization
user = accepted.user
workspace = accepted.workspace
auth_client = accepted.auth_client
choices_template = accepted.choices_template
dataset = accepted.dataset
output_column = accepted.output_column
pipeline_project = accepted.pipeline_project

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_external_lookups")]

SDK_CONFIGS = accepted.EXTERNAL_PLATFORM_CONFIGS
PIPELINE_CONFIGS = [SDK_CONFIGS[0], SDK_CONFIGS[3], SDK_CONFIGS[4]]
REQUEST_CONFIGS = [
    pytest.param(
        {"run_config": {"choice_scores": {"Good": 0.25}}}, 0.25, id="run-config"
    ),
    pytest.param({"choice_scores": {"Good": 0.25}}, 0.25, id="top-level"),
    pytest.param({}, 1.0, id="no-override"),
]


@contextmanager
def _engine_judge():
    """Keep the SDK engine/formatter real; return only a raw judge label."""
    from evaluations.engine import runner as engine_runner

    judge = accepted._judge_says_good()
    with (
        patch.object(engine_runner, "get_eval_class", return_value=MagicMock()),
        patch.object(engine_runner, "create_eval_instance", return_value=(judge, None)),
        patch.object(engine_runner, "prepare_run_params", return_value={}),
        patch(
            "evaluations.engine.preprocessing.preprocess_inputs",
            side_effect=lambda _name, params: params,
        ),
        patch("sdk.utils.evaluations._log_and_deduct_cost_for_standalone_eval"),
        patch(
            "model_hub.services.ground_truth_service.GroundTruthService.inject_context"
        ),
        patch("ee.usage.services.config.BillingConfig.get", return_value=None),
        patch("ee.usage.services.emitter.emit"),
        patch("tracer.utils.inline_evals.trigger_inline_eval"),
        patch("sdk.utils.evaluations.trigger_inline_eval"),
        patch("sdk.views.evaluations.track_mixpanel_event"),
    ):
        # _run_eval updates this log as JSON, including on error paths.
        from sdk.utils import evaluations

        evaluations._log_and_deduct_cost_for_standalone_eval.return_value = MagicMock(
            config="{}"
        )
        yield judge


@contextmanager
def _legacy_judge():
    """Keep both request-time workers and EvaluationRunner scoring real."""
    judge = accepted._judge_says_good()
    with (
        patch("evaluations.engine.registry.get_eval_class", return_value=MagicMock()),
        patch(
            "evaluations.engine.instance.create_eval_instance",
            return_value=(judge, None),
        ),
        patch(
            "model_hub.views.eval_runner.EvaluationRunner.map_fields",
            return_value={"output": "a reply"},
        ),
        patch(
            "model_hub.services.ground_truth_service.GroundTruthService.inject_context"
        ),
        patch(
            "model_hub.services.ground_truth_service.GroundTruthService.resolve_preview_examples",
            return_value=None,
        ),
        patch("model_hub.views.utils.evals.log_and_deduct_cost_for_api_request", None),
        patch(
            "ee.usage.services.metering.check_usage",
            return_value=SimpleNamespace(allowed=True),
        ),
    ):
        yield judge


@contextmanager
def _inline_executor(**_kwargs):
    """Keep preview DB work in the test transaction, without worker threads."""

    def submit(fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future

    yield SimpleNamespace(submit=submit)


@pytest.mark.parametrize("config,effective", SDK_CONFIGS)
def test_external_platform_records_requested_score(
    auth_client, choices_template, config, effective
):
    from tracer.models.external_eval_config import ExternalEvalConfig, StatusChoices

    config = deepcopy(config)
    response = auth_client.post(
        "/sdk/api/v1/configure-evaluations/",
        {
            "eval_config": {
                "eval_templates": choices_template.name,
                "inputs": {"output": "a reply"},
                "config": config,
            },
            "platform": "langfuse",
            "custom_eval_name": "tone-external-assertion-red",
            **accepted.LANGFUSE_TEST_CREDENTIALS,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    stored = ExternalEvalConfig.no_workspace_objects.get(
        name="tone-external-assertion-red"
    )
    create, send = accepted._run_external_platform_eval(stored)

    assert stored.status == StatusChoices.COMPLETED, stored.error_message
    create.return_value[0].run.assert_called_once()
    assert stored.config == config
    expected = {"score": effective["Good"], "choice": "Good"}
    # The pre-fix implementation reaches this assertion with score=1.0.
    assert stored.eval_results["value"] == expected
    send.assert_called_once()
    assert send.call_args.args[1] == expected


@pytest.mark.parametrize("config,effective", SDK_CONFIGS)
def test_sdk_evaluate_returns_and_records_requested_score(
    auth_client, choices_template, config, effective
):
    from model_hub.models.evaluation import Evaluation

    with _engine_judge() as judge:
        response = auth_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": choices_template.name,
                "inputs": {"output": "a reply"},
                "config": deepcopy(config),
            },
            format="json",
        )

    assert response.status_code == 200, response.content
    judge.run.assert_called_once()
    stored = Evaluation.no_workspace_objects.get(eval_template=choices_template)
    expected = {"score": effective["Good"], "choice": "Good"}
    assert ast.literal_eval(stored.value) == expected
    assert response.data["result"][0]["evaluations"][0]["output"] == expected


@pytest.mark.parametrize("config,effective", PIPELINE_CONFIGS)
def test_pipeline_records_requested_score(
    auth_client, choices_template, pipeline_project, config, effective
):
    from model_hub.models.evaluation import Evaluation, StatusChoices
    from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

    with patch("tfc.temporal.evaluations.start_evaluation_workflow") as start:
        response = auth_client.post(
            "/sdk/api/v1/evaluate-pipeline/",
            accepted._pipeline_body(choices_template, deepcopy(config)),
            format="json",
        )

    assert response.status_code == 200, response.content
    stored = Evaluation.no_workspace_objects.get(eval_template=choices_template)
    assert stored.eval_config == config
    start.assert_called_once_with(evaluation_id=str(stored.id))
    with (
        _engine_judge() as judge,
        patch("tfc.temporal.evaluations.activities.close_old_connections"),
    ):
        outcome = _run_single_evaluation_sync(str(stored.id))

    assert outcome["status"] == StatusChoices.COMPLETED, outcome
    judge.run.assert_called_once()
    stored.refresh_from_db()
    assert ast.literal_eval(stored.value) == {
        "score": effective["Good"],
        "choice": "Good",
    }


@pytest.mark.parametrize("config,score", REQUEST_CONFIGS)
def test_playground_returns_requested_score(
    auth_client, choices_template, config, score
):
    # Do not mock run_eval_func or format_output with the expected score.
    with _legacy_judge() as judge:
        response = accepted._playground_post(
            auth_client, choices_template, deepcopy(config)
        )

    assert response.status_code == 200, response.content
    judge.run.assert_called_once()
    assert response.data["result"]["output"] == {"score": score, "choice": "Good"}


@pytest.mark.parametrize("config,score", REQUEST_CONFIGS)
def test_preview_returns_and_logs_requested_score(
    auth_client, dataset, output_column, choices_template, config, score
):
    from model_hub.models.develop_dataset import Row

    Row.objects.create(dataset=dataset, order=0)
    runtime_config = {"mapping": {"output": str(output_column.id)}, **deepcopy(config)}
    log = MagicMock(config="{}")
    # Only scheduling, input mapping and the billing log are stubbed. The
    # real process_eval_for_single_row constructs and formats the runner.
    with (
        _legacy_judge() as judge,
        patch("model_hub.views.develop_dataset.ThreadPoolExecutor", _inline_executor),
        patch("model_hub.views.utils.evals.close_old_connections"),
        patch(
            "model_hub.views.utils.evals.process_mapping",
            return_value=(["output"], ["a reply"]),
        ),
        patch(
            "model_hub.views.eval_runner.EvaluationRunner._handle_api_call",
            return_value=log,
        ),
    ):
        response = accepted._preview_post(
            auth_client, dataset, choices_template, runtime_config
        )

    assert response.status_code == 200, response.content
    judge.run.assert_called_once()
    results = response.data["result"]["responses"]
    assert len(results) == 1
    assert results[0].get("output_type") == "choices", results
    expected = {"score": score, "choice": "Good"}
    assert results[0]["output"] == expected
    log.save.assert_called_once()
    assert json.loads(log.config)["output"]["output"] == expected
