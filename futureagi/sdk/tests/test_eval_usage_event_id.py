"""An SDK evaluation that the evaluation worker runs again is billed once.

An async evaluation's usage event is keyed to the evaluation the request
created. When the worker runs it again after a restart or a retry, the event is
the one it emitted before, so the consumer keeps one. Every evaluation a
customer requests is its own event and is billed. A protect call's event is
keyed to its own request. A Jev evaluator's own events are keyed to the
evaluation the same way.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pytest

from evaluations.tests.usage_step_support import jev_evaluator
from sdk.utils import evaluations as sdk_evaluations
from tfc.constants.api_calls import APICallTypeChoices

pytest_plugins = ["evaluations.tests.usage_step_support"]
pytestmark = [pytest.mark.requires_ee, pytest.mark.usefixtures("billing_config")]

TEMPLATE = SimpleNamespace(
    id="template-1",
    name="tone",
    config={"eval_type_id": "CustomPromptEvaluator", "required_keys": []},
)


def _call_log(*_args, **_kwargs):
    """The call log every attempt writes before it runs."""
    log = Mock()
    log.log_id = uuid4()
    log.config = "{}"
    return log


def _engine_result(evaluator=None):
    return SimpleNamespace(
        data={},
        failure=None,
        reason="ok",
        runtime=0.01,
        model_used="turing_large",
        metrics=None,
        metadata={"token_usage": {}},
        output_type="score",
        value=1.0,
        cost={"total_cost": 0.002},
        token_usage={},
        evaluator=evaluator,
    )


def _engine(evaluator=None):
    return (
        patch.object(sdk_evaluations, "_refuse_paused_custom_code"),
        patch.object(
            sdk_evaluations,
            "_log_and_deduct_cost_for_standalone_eval",
            side_effect=_call_log,
        ),
        patch(
            "model_hub.utils.eval_input_validation.validate_eval_inputs",
            lambda _template, inputs, **_kwargs: (None, inputs),
        ),
        patch(
            "model_hub.services.ground_truth_service.GroundTruthService.inject_context"
        ),
        patch("evaluations.engine.run_eval", return_value=_engine_result(evaluator)),
    )


def _run_evaluation_events(evaluation_id, evaluator=None):
    """The evaluation worker running one async evaluation once."""
    from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

    organization = SimpleNamespace(id="org-1")
    evaluation = SimpleNamespace(
        id=evaluation_id,
        workspace=None,
        organization_id="org-1",
        organization=organization,
        user=SimpleNamespace(id=1, organization=None),
        eval_template=TEMPLATE,
        input_data={"input": "Thanks for waiting."},
        model_name="turing_large",
        eval_config={},
        status=None,
        error_localizer_enabled=False,
        save=lambda **_kwargs: None,
    )
    evaluations = SimpleNamespace(
        objects=SimpleNamespace(
            select_related=lambda *_args: SimpleNamespace(get=lambda **_kw: evaluation)
        )
    )
    refuse, cost, validate, ground_truth, engine = _engine(evaluator)
    with (
        refuse,
        cost,
        validate,
        ground_truth,
        engine,
        patch("tfc.temporal.evaluations.activities.close_old_connections"),
        patch("model_hub.models.evaluation.Evaluation", evaluations),
        patch("tracer.utils.inline_evals.trigger_inline_eval"),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        outcome = _run_single_evaluation_sync(evaluation_id)
    assert outcome["status"] != "FAILED", outcome
    return [call.args[0] for call in emit.call_args_list]


def _run_evaluation(evaluation_id):
    [event] = _run_evaluation_events(evaluation_id)
    return event


def _jev_event_ids(evaluation_id):
    events = _run_evaluation_events(evaluation_id, jev_evaluator())
    assert [event.event_type for event in events] == ["jev_evaluator"] * 2
    return [event.event_id for event in events]


def _protect(request_id):
    """One protect call, for the request it was made in."""
    refuse, cost, _validate, _ground_truth, engine = _engine()
    templates = SimpleNamespace(
        no_workspace_objects=SimpleNamespace(get=lambda **_kw: TEMPLATE)
    )
    user = SimpleNamespace(id=1, organization=SimpleNamespace(id="org-1"))
    with (
        refuse,
        cost,
        engine,
        patch.object(sdk_evaluations, "EvalTemplate", templates),
        patch(
            "agentic_eval.core_evals.fi_utils.token_count_helper.calculate_total_cost",
            return_value={"total_cost": 0.001},
        ),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        sdk_evaluations._run_protect(
            {"input": "Thanks for waiting."}, {}, True, 101, user, request_id
        )
    [event] = [call.args[0] for call in emit.call_args_list]
    return event


def test_a_retried_async_evaluation_emits_the_event_it_emitted_before():
    first = _run_evaluation("evaluation-1")
    retried = _run_evaluation("evaluation-1")

    assert retried.event_id == first.event_id


def test_every_evaluation_a_customer_requests_is_billed():
    events = [
        _run_evaluation(evaluation)
        for evaluation in ("evaluation-1", "evaluation-2") * 2
    ]

    assert len({event.event_id for event in events}) == 2


def test_a_retried_async_evaluation_keeps_its_amount_and_type():
    first = _run_evaluation("evaluation-1")
    retried = _run_evaluation("evaluation-1")

    assert retried.event_id == first.event_id
    for event in (first, retried):
        assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        assert event.amount == pytest.approx(0.25)


def test_a_protect_call_is_keyed_to_its_own_request():
    events = [_protect(request) for request in ("request-1", "request-2") * 2]

    assert len({event.event_id for event in events}) == 2
    assert {event.event_type for event in events} == {
        APICallTypeChoices.PROTECT_FLASH_EVALUATOR.value
    }
    assert all(event.amount == pytest.approx(0.15) for event in events)


def test_a_retried_async_jev_evaluation_emits_the_events_it_emitted_before():
    first = _jev_event_ids("evaluation-1")
    retried = _jev_event_ids("evaluation-1")

    assert retried == first


def test_an_async_evaluation_whose_step_cannot_be_read_is_still_billed(
    unreadable_step,
):
    """Each run is billed once under a fresh id: an evaluation is never left
    unbilled because its step cannot be read. A lookup that fails is logged
    with its stack."""
    first = _run_evaluation("evaluation-1")
    retried = _run_evaluation("evaluation-1")

    assert retried.event_id != first.event_id
    for event in (first, retried):
        assert UUID(event.event_id).version == 4
        assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        assert event.amount == pytest.approx(0.25)
    assert unreadable_step.warnings() == unreadable_step.expected_warnings(lookups=2)


def test_a_protect_call_whose_request_cannot_be_read_is_still_billed(
    unreadable_step,
):
    events = [_protect("request-1") for _attempt in range(2)]

    assert events[1].event_id != events[0].event_id
    for event in events:
        assert UUID(event.event_id).version == 4
        assert event.event_type == APICallTypeChoices.PROTECT_FLASH_EVALUATOR.value
        assert event.amount == pytest.approx(0.15)
    assert unreadable_step.warnings() == unreadable_step.expected_warnings(lookups=2)


def test_an_async_jev_evaluation_whose_step_cannot_be_read_is_still_billed(
    unreadable_step,
):
    """A Jev evaluator's own events are keyed to each run's call."""
    ids = _jev_event_ids("evaluation-1") + _jev_event_ids("evaluation-1")

    assert len(set(ids)) == 4
    assert unreadable_step.warnings() == unreadable_step.expected_warnings(lookups=2)
