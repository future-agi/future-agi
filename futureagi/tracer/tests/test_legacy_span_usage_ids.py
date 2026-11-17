"""A legacy span evaluation keeps its usage event ids across the retries of
its activity, and a new dispatch is billed again.

The legacy span evaluation activity (evaluate_observation_span) runs again
when its worker is lost after the evaluation's usage was emitted. Its usage
events are keyed to the span, the activity's workflow run and the eval with
its config, so the billing consumer keeps one charge for the retry. A new
dispatch is a new workflow run and is billed again. This holds for a Jev
evaluation and for any other evaluator, and each child of a composite keeps
its own id.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from evaluations.usage_step import step_event_id
from model_hub.utils.composite_execution import _child_usage_step
from tracer.utils import eval as tracer_eval

pytestmark = pytest.mark.requires_ee

SPAN_ID = "span-1"
CONFIG = SimpleNamespace(
    id="config-1",
    model=None,
    mapping={},
    config={},
    eval_template=SimpleNamespace(id="template-1"),
)
TOKENS = {"input_tokens": 412, "output_tokens": 6}


def _jev_result():
    """A Jev evaluation's result: its client answered on the first attempt."""
    from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator

    client = Mock()
    client.evaluate.return_value = {
        "model": "jev-1.13.0",
        "answers": {"q1": {"type": "noul", "noul": 0.8}},
        "usage": dict(TOKENS),
    }
    client.attempts = [{"attempt": 1, "outcome": "success", "usage": dict(TOKENS)}]
    client.attempt = 1
    client.gateway_request_id = None
    evaluator = JevEvaluator(rule_prompt="Judge", model="jev-latest", client=client)
    evaluator._evaluate()
    return SimpleNamespace(
        model_used="jev-1.13.0",
        cost={"total_cost": 0.0},
        token_usage={"prompt_tokens": 412, "completion_tokens": 6},
        evaluator=evaluator,
    )


def _other_result():
    """Another evaluator's result."""
    return SimpleNamespace(
        model_used="gpt-4o",
        cost={"total_cost": 0.001},
        token_usage={"prompt_tokens": 10, "completion_tokens": 2},
        evaluator=None,
    )


def _bill(result):
    """What the evaluation does that matters here: it bills ``result`` for
    the span, with the log row it made for this attempt."""

    def execute(**kwargs):
        tracer_eval._emit_eval_billing(
            org_id="org",
            api_call_type="turing_large_evaluator",
            source_id="eval",
            target_type=tracer_eval.EvalTargetType.SPAN.value,
            result=result,
            custom_eval_config=CONFIG,
            ws_id=None,
            api_call_log_row=SimpleNamespace(log_id=str(uuid.uuid4())),
            target_id=SPAN_ID,
        )

    return execute


def _run_activity(workflow_run, execute):
    """One attempt of the legacy span evaluation activity in ``workflow_run``,
    whose evaluation is ``execute``. Returns the usage events it emitted."""
    config_model = Mock()
    config_model.objects.get.return_value = CONFIG
    with (
        patch.object(tracer_eval, "CustomEvalConfig", config_model),
        patch.object(tracer_eval, "EvalLogger"),
        patch.object(tracer_eval, "soft_delete_eval_results"),
        patch.object(tracer_eval, "_span_usage_refusal", return_value=None),
        patch.object(tracer_eval, "_process_mapping", return_value={}),
        patch.object(tracer_eval, "_execute_evaluation", side_effect=execute),
        patch(
            "tracer.services.clickhouse.v2.eval_loader.get_observation_span",
            return_value=SimpleNamespace(id=SPAN_ID, project_id="project-1"),
        ),
        patch(
            "tracer.services.eval_tasks.config_hash.resolved_config_hash",
            return_value="config-hash-1",
        ),
        patch("temporalio.activity.in_activity", return_value=True),
        patch(
            "temporalio.activity.info",
            return_value=SimpleNamespace(workflow_run_id=workflow_run),
        ),
        patch("ee.usage.services.config.BillingConfig.get") as billing,
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        billing.return_value.get_eval_per_run_fee.return_value = 0.0005
        billing.return_value.calculate_ai_credits.return_value = 0.06
        assert tracer_eval.evaluate_observation_span(
            observation_span_id=SPAN_ID, custom_eval_config_id=CONFIG.id
        )
    return [call.args[0] for call in emit.call_args_list]


def _charge_ids(events):
    """The ids of the events that carry a charge."""
    assert events
    return {event.event_id for event in events if event.amount}


RESULTS = [
    pytest.param(_jev_result, id="jev"),
    pytest.param(_other_result, id="other-evaluator"),
]


@pytest.mark.parametrize("result", RESULTS)
def test_a_retry_of_the_activity_is_billed_once(result):
    first = _run_activity("workflow-run-1", _bill(result()))
    retry = _run_activity("workflow-run-1", _bill(result()))
    assert len(_charge_ids(first + retry)) == 1


@pytest.mark.parametrize("result", RESULTS)
def test_a_new_dispatch_is_billed_again(result):
    first = _run_activity("workflow-run-1", _bill(result()))
    rerun = _run_activity("workflow-run-2", _bill(result()))
    assert len(_charge_ids(first + rerun)) == 2


def test_each_child_of_a_composite_keeps_its_own_id_across_a_retry():
    ids = []

    def children(**kwargs):
        step = tracer_eval._engine_usage_step(
            tracer_eval.EvalTargetType.SPAN.value, SPAN_ID, CONFIG
        )
        for child_id in ("child-a", "child-b"):
            link = SimpleNamespace(child_id=child_id)
            ids.append(step_event_id(_child_usage_step(step, link)))

    _run_activity("workflow-run-1", children)
    first = list(ids)
    _run_activity("workflow-run-1", children)
    assert None not in first
    assert len(set(first)) == 2
    assert ids[2:] == first


def _bill_alone(execute):
    """``execute`` run on its own in the same activity, outside the legacy
    span evaluation. Returns the usage events it emitted."""
    with (
        patch("temporalio.activity.in_activity", return_value=True),
        patch(
            "temporalio.activity.info",
            return_value=SimpleNamespace(workflow_run_id="workflow-run-1"),
        ),
        patch("ee.usage.services.config.BillingConfig.get") as billing,
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        billing.return_value.get_eval_per_run_fee.return_value = 0.0005
        billing.return_value.calculate_ai_credits.return_value = 0.06
        execute()
    return [call.args[0] for call in emit.call_args_list]


def test_an_evaluation_after_the_activity_returns_is_not_keyed_to_it():
    _run_activity("workflow-run-1", _bill(_other_result()))
    first = _bill_alone(_bill(_other_result()))
    second = _bill_alone(_bill(_other_result()))
    assert len(_charge_ids(first + second)) == 2


def _bill_other_targets(result):
    """An evaluation inside the activity that bills another span and a trace,
    not the span the activity evaluates."""

    def execute(**kwargs):
        for target_type, target_id in (
            (tracer_eval.EvalTargetType.SPAN.value, "span-2"),
            (tracer_eval.EvalTargetType.TRACE.value, "trace-1"),
        ):
            tracer_eval._emit_eval_billing(
                org_id="org",
                api_call_type="turing_large_evaluator",
                source_id="eval",
                target_type=target_type,
                result=result,
                custom_eval_config=CONFIG,
                ws_id=None,
                api_call_log_row=SimpleNamespace(log_id=str(uuid.uuid4())),
                target_id=target_id,
            )

    return execute


def test_another_target_in_the_activity_is_not_keyed_to_its_span():
    first = _run_activity("workflow-run-1", _bill_other_targets(_other_result()))
    retry = _run_activity("workflow-run-1", _bill_other_targets(_other_result()))
    assert len(_charge_ids(first + retry)) == 4
