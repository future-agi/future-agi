"""An evaluation's usage record and log keep only its run settings.

run_eval_func (the evaluation playground, the SDK, Fix Your Agent, composite
and DAG evaluations) and the external evaluation runner write a usage record
holding the settings each evaluation ran with. The record holds the run
settings only (evaluations.run_settings.usage_record_settings), whatever the
settings handed in already hold, and the settings handed in are left as they
were. The dataset optimizer's evaluator logs the run settings only.

The real engine prepares, builds and runs each evaluator. Only the model
transports (see evaluations/engine/tests/test_version_model_needs_its_own_key),
few-shot retrieval, the usage check, the billing emit and the usage logger
are replaced. The replacement logger writes the record as the usage logger
does: an APICallLog row holding the settings as JSON text, left processing
so the evaluation goes on. Every credential value here is synthetic.
"""

import copy
import json
import uuid
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from evaluations.engine.tests.test_version_model_needs_its_own_key import (
    OTHER_MODEL,
    STORED_KEY,
    make_template,
    transports,
)
from model_hub.models.api_key import ApiKey
from model_hub.views.eval_runner import EvaluationRunner
from model_hub.views.utils.evals import run_eval_func
from tfc.constants.api_calls import APICallStatusChoices

usage_models = pytest.importorskip("ee.usage.models.usage", reason="requires ee/")
APICallLog = usage_models.APICallLog
pytestmark = pytest.mark.requires_ee

STORED_SETTINGS_VALUE = "sk-test-SYNTHETIC-STALE-0003"
OUTPUT = "The answer is 4."
JUDGE_REASON = "The output is correct."


@pytest.fixture
def usage_log():
    """The usage logger, replaced by one that writes the record as it does.
    Yields the settings it was handed, as JSON text, one per call."""
    handed = []

    def log(
        organization,
        api_call_type,
        config=None,
        source=None,
        source_id=None,
        workspace=None,
    ):
        text = json.dumps(config or {}, default=str)
        handed.append(text)
        return APICallLog.objects.create(
            organization=organization,
            workspace=workspace,
            cost=0,
            status=APICallStatusChoices.PROCESSING.value,
            config=text,
            reference_id=(config or {}).get("reference_id", ""),
            source=source,
            source_id=str(source_id) if source_id is not None else None,
        )

    with (
        patch(
            "model_hub.views.utils.evals.log_and_deduct_cost_for_api_request",
            side_effect=log,
        ),
        patch(
            "tracer.utils.external_eval.log_and_deduct_cost_for_api_request",
            side_effect=log,
        ),
    ):
        yield handed


@contextmanager
def evaluation_services():
    """What an evaluation calls besides the engine, replaced: few-shot
    retrieval (it reads stored feedback through the embedding store), the
    usage check (allowed) and the billing emit."""
    with ExitStack() as stack:
        enter = stack.enter_context
        enter(patch.object(EvaluationRunner, "get_few_shot_examples", return_value=[]))
        enter(
            patch(
                "ee.usage.services.metering.check_usage",
                return_value=SimpleNamespace(allowed=True, reason=""),
            )
        )
        enter(patch("ee.usage.services.config.BillingConfig.get", return_value=None))
        enter(patch("ee.usage.services.emitter.emit"))
        yield


def pass_fail_template(organization, workspace):
    """A Pass/Fail judge template, scored as Pass/Fail (as saving one through
    the product sets it)."""
    template = make_template(organization, workspace)
    template.output_type_normalized = "pass_fail"
    template.save(update_fields=["output_type_normalized"])
    return template


def assert_record_holds_none_of(handed, values):
    """Neither the settings handed to the usage logger nor the stored usage
    records hold any of ``values``."""
    assert handed
    for given in handed:
        for value in values:
            assert value not in given
    for row in APICallLog.objects.all():
        stored = json.dumps(row.config)
        for value in values:
            assert value not in stored


@pytest.mark.django_db
def test_the_usage_record_holds_the_run_settings_of_any_settings_given(
    organization, workspace, usage_log
):
    """The settings handed in already hold a key and a provider, at the top
    level and in the evaluator settings: the usage record holds neither, and
    the settings handed in are left as they were."""
    template = make_template(organization, workspace)
    settings = {
        "api_key": STORED_SETTINGS_VALUE,
        "provider": "openai",
        "config": {
            "api_key": STORED_SETTINGS_VALUE,
            "provider": "openai",
            "check_internet": False,
        },
        "run_config": {"pass_threshold": 0.5},
    }
    given = copy.deepcopy(settings)

    with transports(), evaluation_services():
        result = run_eval_func(
            settings,
            {"output": OUTPUT},
            template,
            organization,
            model=template.model,
            workspace=workspace,
        )

    (row,) = APICallLog.objects.filter(organization=organization)
    assert result["log_id"] == str(row.log_id)
    assert_record_holds_none_of(usage_log, [STORED_SETTINGS_VALUE])
    (record,) = [json.loads(text) for text in usage_log]
    assert "provider" not in record
    assert "provider" not in record["config"]
    assert record["run_config"] == {"pass_threshold": 0.5}
    assert record["config"]["check_internet"] is False
    assert settings == given


@pytest.mark.django_db
def test_inputs_taken_from_the_settings_are_run_settings(
    organization, workspace, usage_log
):
    """No mappings are given, so the evaluation takes its inputs from the
    top-level settings: a key there is not taken as an input, and the usage
    record does not hold it."""
    template = make_template(organization, workspace)
    settings = {"config": {}, "output": OUTPUT, "api_key": STORED_SETTINGS_VALUE}

    with transports() as seams, evaluation_services():
        run_eval_func(
            settings,
            {},
            template,
            organization,
            model=template.model,
            workspace=workspace,
        )

    seams.turing.return_value.chat_completion.assert_called_once()
    assert_record_holds_none_of(usage_log, [STORED_SETTINGS_VALUE])
    (record,) = [json.loads(text) for text in usage_log]
    assert record["mappings"] == {"output": OUTPUT}


@pytest.mark.django_db
def test_the_usage_record_holds_the_run_settings_when_a_key_is_stored(
    organization, workspace, usage_log
):
    """A key is stored for the judge model: the evaluation calls the model
    with it, the usage record holds the run settings only, and the settings
    handed in (Fix Your Agent's shape) are left as they were."""
    template = make_template(organization, workspace, model=OTHER_MODEL)
    ApiKey.objects.create(
        organization=organization,
        workspace=workspace,
        provider="openai",
        key=STORED_KEY,
    )
    settings = {"config": {}}

    with transports() as seams, evaluation_services():
        run_eval_func(
            settings,
            {"output": OUTPUT},
            template,
            organization,
            model=OTHER_MODEL,
            workspace=workspace,
        )

    seams.completion.assert_called_once()
    assert seams.completion.call_args.kwargs["api_key"] == STORED_KEY
    assert_record_holds_none_of(usage_log, [STORED_KEY])
    assert settings == {"config": {}}


@pytest.mark.django_db
def test_an_external_evaluation_usage_record_holds_the_run_settings(
    organization, workspace, usage_log
):
    """An external evaluation's stored settings hold a key: its usage record
    holds the run settings only, and the stored settings are left as they
    were."""
    from tracer.utils.external_eval import _log_and_deduct_cost_for_external_eval

    template = make_template(organization, workspace)
    stored = {
        "api_key": STORED_SETTINGS_VALUE,
        "config": {"api_key": STORED_SETTINGS_VALUE, "check_internet": False},
        "params": {"threshold": 0.5},
    }
    binding = SimpleNamespace(
        id=uuid.uuid4(),
        organization=organization,
        workspace=workspace,
        eval_template=template,
        model=template.model,
        config=copy.deepcopy(stored),
    )

    with evaluation_services():
        _log_and_deduct_cost_for_external_eval(binding, False, emit_usage=False)

    assert_record_holds_none_of(usage_log, [STORED_SETTINGS_VALUE])
    (record,) = [json.loads(text) for text in usage_log]
    assert record["params"] == {"threshold": 0.5}
    assert record["is_futureagi_eval"] is False
    assert binding.config == stored


@pytest.mark.django_db
def test_the_dataset_optimizer_logs_the_run_settings(organization, workspace):
    """The dataset optimizer's evaluator logs the settings it evaluates with:
    the log holds the run settings only."""
    from ee.agenthub.fix_your_agent import direct_evaluator as module

    template = pass_fail_template(organization, workspace)
    evaluator = module.DirectEvaluator.__new__(module.DirectEvaluator)
    evaluator.execution_model = template.model
    evaluator.organization = organization
    evaluator.workspace = workspace
    evaluator.eval_source = "fix_your_agent"
    binding = SimpleNamespace(
        eval_template=template,
        mapping={"output": "output"},
        config={
            "api_key": STORED_SETTINGS_VALUE,
            "config": {"api_key": STORED_SETTINGS_VALUE},
            "run_config": {"pass_threshold": 0.5},
        },
    )

    with (
        patch.object(module, "logger") as logger,
        patch(
            "model_hub.views.utils.evals.run_eval_func",
            return_value={"output": "Passed", "reason": JUDGE_REASON},
        ),
    ):
        score, reason = evaluator._run_eval_with_config(binding, {}, OUTPUT, "")

    assert (score, reason) == (1.0, JUDGE_REASON)
    logged = repr(logger.mock_calls)
    assert "pass_threshold" in logged
    assert STORED_SETTINGS_VALUE not in logged
