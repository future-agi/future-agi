"""R-16, R-17: attempt identities and usage units, with mocked emission."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator
from ee.jev.metering import build_usage_events, usage_event_id
from ee.usage.schemas.event_types import BillingEventType

pytestmark = pytest.mark.requires_ee


def instance():
    client = Mock()
    client.evaluate.return_value = {
        "model": "jev-1.13.0",
        "answers": {"q1": {"type": "noul", "noul": 0.8}},
        "usage": {"input_tokens": 412, "output_tokens": 6},
    }
    client.attempts = [
        {"attempt": 1, "outcome": "error", "usage": {}},
        {
            "attempt": 2,
            "outcome": "success",
            "usage": {"input_tokens": 412, "output_tokens": 6},
        },
    ]
    client.attempt = 2
    client.gateway_request_id = None
    evaluator = JevEvaluator(rule_prompt="Judge", model="jev-latest", client=client)
    evaluator._evaluate()
    return evaluator


def test_r17_usage_properties_and_attempt_ids():
    events = build_usage_events(
        instance(), org_id="trusted-org", cell_or_run_id="run-1", amount=0
    )
    assert len(events) == 2
    assert events[0].event_id == usage_event_id("run-1", 1)
    event = events[1]
    assert event.event_id == usage_event_id("run-1", 2)
    assert event.org_id == "trusted-org"
    assert event.event_type == "jev_evaluator"
    assert event.amount == 0
    assert event.properties == {
        "provider": "typesafe",
        "service": "jev",
        "requested_model": "jev-latest",
        "actual_model": "jev-1.13.0",
        "mapping_revision": instance().last_jev["mapping_revision"],
        "input_tokens": 412,
        "output_tokens": 6,
        "attempt": 2,
        "outcome": "success",
    }
    assert events[0].properties["actual_model"] is None
    assert events[0].properties["outcome"] == "error"


def test_usage_events_are_accepted_by_the_billing_consumer():
    """The consumer drops unknown event types and parses event_id as a UUID."""
    events = build_usage_events(
        instance(), org_id="org", cell_or_run_id="run-1", amount=0.1
    )
    for event in events:
        assert BillingEventType(event.event_type) is BillingEventType.JEV_EVALUATOR
        assert str(uuid.UUID(event.event_id)) == event.event_id
    assert events[0].event_id != events[1].event_id


def test_flat_price_ignores_token_counts():
    # Bring your own key: the eval costs the same however many tokens it used.
    assert instance().cost["total_cost"] == 0


@pytest.mark.parametrize(("markup", "credits"), [("1.2", 0.06), ("1.0", 0.05)])
def test_an_answer_costs_the_eval_fee_in_credits_and_is_charged_that(markup, credits):
    """credits_per_answer is the one price of a Jev answer: Instant eval
    estimates with it and the usage event charges it."""
    from decimal import Decimal

    from ee.jev.metering import credits_per_answer, emit_usage_events
    from ee.usage.schemas.billing_config import BillingConfigSchema
    from ee.usage.services.config import BillingConfig

    config = BillingConfig(
        BillingConfigSchema(
            plans={},
            dimensions={},
            pricing_tiers={},
            entitlements={},
            api_call_types={},
            ee_bands={},
            ai_credit_models={},
            ai_cost_markup=Decimal(markup),
            eval_per_run_fee=Decimal("0.0005"),
        )
    )
    with (
        patch("ee.usage.services.config.BillingConfig.get", return_value=config),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        price = credits_per_answer()
        emit_usage_events(instance(), org_id="org", cell_or_run_id="run")
    assert price == config.calculate_ai_credits(config.get_eval_per_run_fee())
    assert price == pytest.approx(credits)
    # The failed attempt is free; the answer is charged the price.
    assert [call.args[0].amount for call in emit.call_args_list] == [0, price]


def test_r17_completed_result_redelivery_has_same_idempotency_keys():
    evaluator = instance()
    first = build_usage_events(
        evaluator, org_id="org", cell_or_run_id="run", amount=0.1
    )
    redelivered = build_usage_events(
        evaluator, org_id="org", cell_or_run_id="run", amount=0.1
    )
    assert [e.event_id for e in first] == [e.event_id for e in redelivered]
    assert [e.amount for e in first] == [0, 0.1]


def test_r17_dataset_runner_emits_jev_properties():
    from model_hub.views.eval_runner import EvaluationRunner

    evaluator = instance()
    runner = object.__new__(EvaluationRunner)
    runner.user_eval_metric = SimpleNamespace(
        config={"config": {}},
        error_localizer=False,
        id="metric",
        model="jev-latest",
        organization=SimpleNamespace(id="trusted-org"),
        template=SimpleNamespace(error_localizer_enabled=False),
    )
    runner.organization_id = "trusted-org"
    runner.source = "dataset"
    runner.source_id = "dataset-id"
    runner.replace_column_id = "column-id"
    runner.eval_template = SimpleNamespace(eval_type="llm", config={})
    runner.experiment_dataset = False
    runner.optimize = False
    runner.update_config_list_values = Mock(return_value={})
    runner._handle_api_call = Mock(
        return_value=SimpleNamespace(log_id="run-1", config="{}", save=Mock())
    )
    runner._handle_api_call_status = Mock()
    runner._run_evaluation = Mock(
        return_value=(None, None, None, None, evaluator, None)
    )
    runner._format_response = Mock(return_value={"reason": None})
    runner.format_output = Mock(return_value="Passed")
    with (
        patch("model_hub.views.eval_runner.Column.objects.filter") as columns,
        patch("ee.usage.services.config.BillingConfig.get") as billing,
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        columns.return_value.values.return_value = []
        billing.return_value.get_eval_per_run_fee.return_value = 0.0005
        billing.return_value.calculate_ai_credits.return_value = 0.06
        runner._process_eval_result(SimpleNamespace(id="row"), {})
    # Flat pricing: only the per-run fee is converted to credits, and the
    # answer is charged that (credits_per_answer, as on every Jev path).
    conversions = billing.return_value.calculate_ai_credits.call_args_list
    assert conversions and all(c.args == (0.0005,) for c in conversions)
    events = [call.args[0] for call in emit.call_args_list]
    assert [event.amount for event in events] == [0, 0.06]
    assert [event.event_id for event in events] == [
        usage_event_id("run-1", 1),
        usage_event_id("run-1", 2),
    ]
    assert events[-1].properties["output_tokens"] == 6


def test_billing_event_type_relabels_only_jev_runs():
    from sdk.utils.helpers import _get_billing_event_type

    legacy = "turing_large_evaluator"
    assert _get_billing_event_type(legacy, "jev-latest") == "jev_evaluator"
    assert _get_billing_event_type(legacy, None, "jev-1.13.0") == "jev_evaluator"
    assert _get_billing_event_type(legacy, None, evaluator=instance()) == (
        "jev_evaluator"
    )
    assert _get_billing_event_type(legacy, "gpt-4o", evaluator=object()) == legacy
    assert _get_billing_event_type(legacy) == legacy


def test_tracer_eval_billing_labels_jev_by_model_used():
    """A trace eval whose config leaves the model to the template still bills
    as jev_evaluator, from the model the engine actually ran."""
    from tracer.utils.eval import _emit_eval_billing

    result = SimpleNamespace(
        model_used="jev-1.13.0",
        cost={"total_cost": 0.0},
        token_usage={"prompt_tokens": 412, "completion_tokens": 6},
    )
    with (
        patch("ee.usage.services.config.BillingConfig.get") as billing,
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        billing.return_value.get_eval_per_run_fee.return_value = 0.0005
        billing.return_value.calculate_ai_credits.return_value = 0.06
        _emit_eval_billing(
            org_id="org",
            api_call_type="turing_large_evaluator",
            source_id="eval",
            target_type="span",
            result=result,
            custom_eval_config=SimpleNamespace(model=None),
            ws_id=None,
            api_call_log_row=None,
        )
    event = emit.call_args.args[0]
    assert event.event_type == "jev_evaluator"
    assert event.amount == 0.06


def test_tracer_eval_billing_emits_the_jev_events_for_a_jev_run():
    """Span, trace and session evals bill a Jev run with the metering events,
    keyed to the run, whatever model name TypeSafe reported."""
    from tracer.utils.eval import _emit_eval_billing

    evaluator = instance()
    result = SimpleNamespace(
        model_used="jev-1.14.0",
        cost={"total_cost": 0.0},
        token_usage={"prompt_tokens": 412, "completion_tokens": 6},
        evaluator=evaluator,
    )
    with (
        patch("ee.usage.services.config.BillingConfig.get") as billing,
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        billing.return_value.get_eval_per_run_fee.return_value = 0.0005
        billing.return_value.calculate_ai_credits.return_value = 0.06
        _emit_eval_billing(
            org_id="org",
            api_call_type="turing_large_evaluator",
            source_id="eval",
            target_type="span",
            result=result,
            custom_eval_config=SimpleNamespace(model=None),
            ws_id=None,
            api_call_log_row=SimpleNamespace(log_id="run-1"),
        )
    billing.return_value.calculate_ai_credits.assert_called_once_with(0.0005)
    events = [
        call.args[0].model_dump(exclude={"timestamp"}) for call in emit.call_args_list
    ]
    expected = build_usage_events(
        evaluator, org_id="org", cell_or_run_id="run-1", amount=0.06
    )
    assert events == [event.model_dump(exclude={"timestamp"}) for event in expected]
    assert [event["amount"] for event in events] == [0, 0.06]


def test_emit_jev_usage_leaves_other_evaluators_to_the_caller():
    from sdk.utils.helpers import _emit_jev_usage

    with patch("ee.usage.services.emitter.emit") as emit:
        assert _emit_jev_usage(object(), org_id="org", run_id="run") is False
        assert _emit_jev_usage(None, org_id="org", run_id="run") is False
    emit.assert_not_called()
