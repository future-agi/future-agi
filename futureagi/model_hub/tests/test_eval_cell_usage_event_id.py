"""A dataset or experiment cell whose evaluation is retried is billed once.

The usage event a cell's evaluation emits is keyed to the cell (its row, the
column its metric maps and the column it evaluates), the workflow run that
evaluates it and the evaluator. When a worker runs the step again after a
restart or a retry, it builds a new runner in the same workflow run and emits
the event it emitted before, so the consumer keeps one. Another cell, another
column of the same row, or a run the customer starts again, is a new event and
is billed. A Jev evaluator's own events are keyed to the cell the same way.
"""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

pytest.importorskip("ee.usage.services.emitter")

from evaluations.tests.usage_step_support import jev_evaluator  # noqa: E402
from model_hub.views.eval_runner import EvaluationRunner  # noqa: E402
from tfc.constants.api_calls import APICallTypeChoices  # noqa: E402

pytest_plugins = ["evaluations.tests.usage_step_support"]
pytestmark = [pytest.mark.requires_ee, pytest.mark.usefixtures("billing_config")]


def _activity(workflow_run_id):
    return SimpleNamespace(
        workflow_id="experiment-workflow",
        workflow_run_id=workflow_run_id,
        activity_id="7",
        attempt=1,
    )


def _runner(workflow_run_id="run-1", *, column_id="column-1", mapped="column-1"):
    """A runner built inside an activity of a workflow run, as every attempt
    builds its own. It evaluates ``column_id``; ``mapped`` is the column its
    metric maps (the same column for an experiment)."""
    with patch("temporalio.activity.info", return_value=_activity(workflow_run_id)):
        run = EvaluationRunner(
            "metric-1", column=SimpleNamespace(id=column_id), format_output=True
        )
    run.user_eval_metric = SimpleNamespace(
        id="metric-1",
        model="turing_large",
        template=SimpleNamespace(id="template-1"),
        organization=SimpleNamespace(id="org-1"),
    )
    run.organization_id = "org-1"
    run.eval_template = SimpleNamespace(eval_type="llm")
    run.source = "experiment"
    run.source_id = "template-1"
    run.replace_column_id = mapped
    return run


def _events(runner, instance, row_id="row-1"):
    """One attempt at billing a cell. Every attempt logs its own call."""
    with patch("ee.usage.services.emitter.emit") as emit:
        runner._emit_cell_usage(
            instance, SimpleNamespace(log_id=uuid4()), SimpleNamespace(id=row_id)
        )
    return [call.args[0] for call in emit.call_args_list]


def _bill(runner, row_id="row-1"):
    instance = SimpleNamespace(cost={"total_cost": 0.002}, token_usage={})
    with patch("model_hub.views.eval_runner._emit_jev_usage", return_value=False):
        [event] = _events(runner, instance, row_id)
    return event


def _jev_event_ids(runner):
    events = _events(runner, jev_evaluator())
    assert [event.event_type for event in events] == ["jev_evaluator"] * 2
    return [event.event_id for event in events]


def test_a_retried_cell_emits_the_event_it_emitted_before():
    first = _bill(_runner())
    retried = _bill(_runner())

    assert retried.event_id == first.event_id


def test_two_rows_are_two_events():
    events = [_bill(_runner(), row_id=row) for row in ("row-1", "row-2") * 2]

    assert len({event.event_id for event in events}) == 2


def test_a_run_the_customer_starts_again_is_billed_again():
    events = [_bill(_runner(workflow_run_id=run)) for run in ("run-1", "run-2") * 2]

    assert len({event.event_id for event in events}) == 2


def test_a_retried_cell_keeps_its_amount_and_type():
    first = _bill(_runner())
    retried = _bill(_runner())

    assert retried.event_id == first.event_id
    for event in (first, retried):
        assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        assert event.amount == pytest.approx(0.25)


def test_an_optimized_row_bills_its_old_and_new_column_once_each():
    """An optimization evaluates each row twice in one workflow run: on the
    column it had and on the column the optimized prompt wrote. Both runners
    map the optimization's column, as loading the metric sets it, and each
    evaluates its own column."""
    events = [
        _bill(_runner(column_id=column, mapped="optimized-column"))
        for column in ("old-column", "new-column") * 2
    ]

    assert len({event.event_id for event in events}) == 2


def test_a_retried_jev_cell_emits_the_events_it_emitted_before():
    first = _jev_event_ids(_runner())
    retried = _jev_event_ids(_runner())

    assert retried == first


def test_a_jev_cell_in_a_run_the_customer_starts_again_is_billed_again():
    ids = [
        event_id
        for run in ("run-1", "run-2") * 2
        for event_id in _jev_event_ids(_runner(workflow_run_id=run))
    ]

    assert len(set(ids)) == 4


def test_a_cell_whose_step_cannot_be_read_is_still_billed(unreadable_step):
    """Each attempt is billed once under a fresh id: a cell is never left
    unbilled because its step cannot be read. A lookup that fails is logged
    with its stack."""
    first = _bill(_runner())
    retried = _bill(_runner())

    assert retried.event_id != first.event_id
    for event in (first, retried):
        assert UUID(event.event_id).version == 4
        assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        assert event.amount == pytest.approx(0.25)
    assert unreadable_step.warnings() == unreadable_step.expected_warnings(lookups=2)


def test_a_jev_cell_whose_step_cannot_be_read_is_still_billed(unreadable_step):
    """A Jev evaluator's own events are keyed to each attempt's call."""
    ids = _jev_event_ids(_runner()) + _jev_event_ids(_runner())

    assert len(set(ids)) == 4
    assert unreadable_step.warnings() == unreadable_step.expected_warnings(lookups=2)


def test_a_cell_whose_workflow_run_cannot_be_read_is_still_billed(failed_step_read):
    """Each attempt is billed once under a fresh id: a cell is never left
    unbilled because its workflow run cannot be read. The failed read is logged
    with its stack."""
    with patch(
        "tfc.logging.temporal.try_activity_info",
        side_effect=RuntimeError("workflow run unreadable"),
    ):
        first = _bill(_runner())
        retried = _bill(_runner())

    assert retried.event_id != first.event_id
    for event in (first, retried):
        assert UUID(event.event_id).version == 4
        assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        assert event.amount == pytest.approx(0.25)
    assert failed_step_read.warnings() == failed_step_read.expected_warnings(lookups=2)
