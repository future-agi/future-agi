"""R-16, R-17: attempt identities and usage units, with mocked emission."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator
from ee.jev.metering import build_usage_events

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
    evaluator = JevEvaluator(rule_prompt="Judge", model="jev-latest", client=client)
    evaluator._evaluate()
    return evaluator


def test_r17_usage_properties_and_attempt_ids():
    events = build_usage_events(
        instance(), org_id="trusted-org", cell_or_run_id="run-1", amount=0
    )
    assert len(events) == 2
    assert events[0].event_id == "jev:run-1:1"
    event = events[1]
    assert event.event_id == "jev:run-1:2"
    assert event.org_id == "trusted-org"
    assert event.event_type == "managed_ai_credits_monthly"
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
        billing.return_value.get_eval_per_run_fee.return_value = 0
        billing.return_value.calculate_ai_credits.return_value = 0
        runner._process_eval_result(SimpleNamespace(id="row"), {})
    events = [call.args[0] for call in emit.call_args_list]
    assert [event.event_id for event in events] == ["jev:run-1:1", "jev:run-1:2"]
    assert events[-1].properties["output_tokens"] == 6
