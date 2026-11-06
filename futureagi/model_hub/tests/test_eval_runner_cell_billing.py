"""A dataset cell is billed the way every eval path bills: a Jev run through
_emit_jev_usage (the flat per-run fee, attempt-stable ids), anything else as a
priced event. Billing never fails the cell, and a lost charge is logged."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("ee.usage.services.emitter")

from model_hub.views.eval_runner import EvaluationRunner  # noqa: E402

pytestmark = pytest.mark.requires_ee


@pytest.fixture(autouse=True)
def billing_config():
    config = Mock()
    config.get_eval_per_run_fee.return_value = 0.0005
    config.calculate_ai_credits.side_effect = lambda usd: usd * 100
    with patch("ee.usage.services.config.BillingConfig.get", return_value=config):
        yield config


def runner():
    run = EvaluationRunner.__new__(EvaluationRunner)
    run.user_eval_metric = SimpleNamespace(
        id="metric-1",
        model="turing_large",
        template=SimpleNamespace(id="template-1"),
        organization=SimpleNamespace(id="org-1"),
    )
    run.organization_id = "org-1"
    run.eval_template = SimpleNamespace(eval_type="llm")
    run.source = "dataset"
    run.source_id = "dataset-1"
    run.replace_column_id = "column-1"
    return run


def cell():
    instance = SimpleNamespace(cost={"total_cost": 0.0}, token_usage={})
    log = SimpleNamespace(log_id="log-1")
    return instance, log, SimpleNamespace(id="row-1")


def test_a_jev_cell_is_billed_by_the_jev_events_only():
    instance, log, row = cell()
    with (
        patch("model_hub.views.eval_runner._emit_jev_usage", return_value=True) as jev,
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        runner()._emit_cell_usage(instance, log, row)
    jev.assert_called_once_with(instance, org_id="org-1", run_id="log-1")
    emit.assert_not_called()


def test_a_cell_without_a_log_row_bills_jev_under_its_cell_id():
    instance, _, row = cell()
    with (
        patch("model_hub.views.eval_runner._emit_jev_usage", return_value=True) as jev,
        patch("ee.usage.services.emitter.emit"),
    ):
        runner()._emit_cell_usage(instance, None, row)
    assert jev.call_args.kwargs["run_id"] == "column-1:row-1"


def test_any_other_evaluator_is_billed_as_before():
    instance, log, row = cell()
    with (
        patch("model_hub.views.eval_runner._emit_jev_usage", return_value=False),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        runner()._emit_cell_usage(instance, log, row)
    emit.assert_called_once()
    event = emit.call_args.args[0]
    assert event.org_id == "org-1"
    assert event.event_type != "jev_evaluator"
    assert event.properties["source_id"] == "dataset-1"


def test_a_failed_emit_is_logged_and_never_fails_the_cell():
    instance, log, row = cell()
    logger = Mock()
    with (
        patch("model_hub.views.eval_runner._emit_jev_usage", return_value=False),
        patch("ee.usage.services.emitter.emit", side_effect=RuntimeError("redis")),
        patch("model_hub.views.eval_runner.logger", logger),
    ):
        runner()._emit_cell_usage(instance, log, row)
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args[0] == "eval_usage_emit_failed"
    assert logger.warning.call_args.kwargs["exc_info"] is True
