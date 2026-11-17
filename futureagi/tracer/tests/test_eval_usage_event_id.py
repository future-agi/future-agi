"""An Observe evaluation that the eval task engine runs again is billed once.

The usage event of an evaluation the engine runs is keyed to the span, trace or
session it evaluates, the task entry it runs and the eval with the config it
runs under. An entry run again after a lost worker or a retry emits the event
it emitted before, so the consumer keeps one. Another entry, a Delete & rerun
(a new entry) and an edited eval (a new config) are new events and are billed.
A Jev evaluator's own events are keyed to the entry the same way.
"""

import importlib
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from django.utils import timezone

# Break the tracer.utils.eval <-> model_hub.tasks import cycle for test-time
# imports, as the other tracer eval tests do.
import model_hub.tasks  # noqa: F401
from evaluations.tests.usage_step_support import jev_evaluator
from tfc.constants.api_calls import APICallTypeChoices
from tracer.models.observation_span import EvalEntryStatus, EvalTargetType
from tracer.services.eval_tasks.entries import writing_onto_entry

pytest_plugins = ["evaluations.tests.usage_step_support"]
pytestmark = [pytest.mark.requires_ee, pytest.mark.usefixtures("billing_config")]


def _result():
    return SimpleNamespace(
        cost={"total_cost": 0.002}, token_usage={}, model_used="turing_large"
    )


def _eval_config():
    return SimpleNamespace(
        id="config-1",
        eval_template_id="template-1",
        mapping={},
        model="turing_large",
        project_id="project-1",
    )


def _billing(**target):
    """What the eval core passes when it bills an evaluation. Every attempt
    logs its own call."""
    return {
        "org_id": "org-1",
        "api_call_type": APICallTypeChoices.TURING_LARGE_EVALUATOR.value,
        "source_id": "template-1",
        "target_type": EvalTargetType.SPAN.value,
        "result": _result(),
        "custom_eval_config": _eval_config(),
        "ws_id": "workspace-1",
        "api_call_log_row": SimpleNamespace(log_id=uuid4()),
        **target,
    }


def _events_on_entry(entry_id, **billing):
    from tracer.utils.eval import _emit_eval_billing

    with (
        writing_onto_entry(entry_id),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        _emit_eval_billing(**_billing(**billing))
    return [call.args[0] for call in emit.call_args_list]


def _bill_on_entry(entry_id):
    [event] = _events_on_entry(entry_id)
    return event


def _jev_event_ids_on_entry(entry_id):
    result = _result()
    result.evaluator = jev_evaluator()
    events = _events_on_entry(entry_id, result=result)
    assert [event.event_type for event in events] == ["jev_evaluator"] * 2
    return [event.event_id for event in events]


def _run_entry(entry_id="entry-1", span_id="span-1", config_hash="hash-1"):
    """The engine running one claimed span entry: run_entry and its dispatch
    into the eval core run as they are, with the reads, the eval itself and the
    terminal write replaced. The eval bills what it evaluated, as the eval core
    does after a run."""
    run_entry_module = importlib.import_module("tracer.services.eval_tasks.run_entry")
    eval_module = importlib.import_module("tracer.utils.eval")

    fresh = SimpleNamespace(
        id=entry_id,
        custom_eval_config_id="config-1",
        eval_task_id="task-1",
        target_type=EvalTargetType.SPAN,
        observation_span_id=span_id,
        output_metadata={},
        status=EvalEntryStatus.RUNNING,
        updated_at=timezone.now(),
        error=False,
        refresh_from_db=lambda: None,
    )
    config = _eval_config()
    claimed = SimpleNamespace(first=lambda: fresh, update=lambda **_kwargs: 1)
    task = SimpleNamespace(project=SimpleNamespace(id="project-1"))

    def _evaluate(**kwargs):
        # The eval core names the span it evaluated when it bills it.
        eval_module._emit_eval_billing(
            **_billing(target_id=str(kwargs["observation_span_id"]))
        )

    with (
        patch.object(
            run_entry_module,
            "EvalLogger",
            SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kw: claimed)),
        ),
        patch.object(
            run_entry_module,
            "CustomEvalConfig",
            SimpleNamespace(
                objects=SimpleNamespace(
                    select_related=lambda *_args: SimpleNamespace(
                        get=lambda **_kw: config
                    )
                )
            ),
        ),
        patch.object(
            run_entry_module,
            "EvalTask",
            SimpleNamespace(
                objects=SimpleNamespace(
                    select_related=lambda *_args: SimpleNamespace(
                        get=lambda **_kw: task
                    )
                )
            ),
        ),
        patch.object(
            run_entry_module, "resolved_config_hash", lambda _config: config_hash
        ),
        patch.object(run_entry_module, "mark_terminal", lambda *_a, **_kw: True),
        patch.object(run_entry_module, "_reseed_eval_clustering", lambda *_a: None),
        # The organization's usage check has its own tests; these entries run
        # as an organization the check allows.
        patch.object(run_entry_module, "_usage_refusal", lambda *_a, **_kw: None),
        patch(
            "tracer.services.clickhouse.v2.eval_loader.get_observation_span",
            lambda *_a, **_kw: SimpleNamespace(id=span_id),
        ),
        patch.object(eval_module, "_process_mapping", lambda *_a: {}),
        patch.object(eval_module, "_execute_evaluation", _evaluate),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        status = run_entry_module.run_entry(SimpleNamespace(id=entry_id))
    assert status == EvalEntryStatus.COMPLETED
    [event] = [call.args[0] for call in emit.call_args_list]
    return event


def test_a_retried_entry_emits_the_event_it_emitted_before():
    first = _bill_on_entry("entry-1")
    retried = _bill_on_entry("entry-1")

    assert retried.event_id == first.event_id


def test_the_engine_bills_an_entry_it_runs_again_once():
    first = _run_entry()
    retried = _run_entry()

    assert retried.event_id == first.event_id


def test_two_spans_are_two_events():
    events = [
        _run_entry(entry_id=entry, span_id=span)
        for entry, span in (("entry-1", "span-1"), ("entry-2", "span-2")) * 2
    ]

    assert len({event.event_id for event in events}) == 2


def test_two_spans_are_two_events_when_nothing_else_differs():
    """The span is part of the key: on the same entry under the same config,
    two spans are two events."""
    events = [_run_entry(span_id=span) for span in ("span-1", "span-2") * 2]

    assert len({event.event_id for event in events}) == 2


def test_a_delete_and_rerun_is_a_new_entry_and_is_billed_again():
    events = [_run_entry(entry_id=entry) for entry in ("entry-1", "entry-3") * 2]

    assert len({event.event_id for event in events}) == 2


def test_an_edited_eval_runs_under_a_new_config_and_is_billed_again():
    events = [_run_entry(config_hash=digest) for digest in ("hash-1", "hash-2") * 2]

    assert len({event.event_id for event in events}) == 2


def test_an_entry_run_again_keeps_its_amount_and_type():
    first = _run_entry()
    retried = _run_entry()

    assert retried.event_id == first.event_id
    for event in (first, retried):
        assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        assert event.amount == pytest.approx(0.25)


def test_a_retried_jev_entry_emits_the_events_it_emitted_before():
    first = _jev_event_ids_on_entry("entry-1")
    retried = _jev_event_ids_on_entry("entry-1")

    assert retried == first


def test_two_jev_entries_are_billed_apart():
    ids = [
        event_id
        for entry in ("entry-1", "entry-2") * 2
        for event_id in _jev_event_ids_on_entry(entry)
    ]

    assert len(set(ids)) == 4


def test_an_entry_whose_step_cannot_be_read_is_still_billed(unreadable_step):
    """Each attempt is billed once under a fresh id: an entry is never left
    unbilled because its step cannot be read. A lookup that fails is logged
    with its stack."""
    first = _bill_on_entry("entry-1")
    retried = _bill_on_entry("entry-1")

    assert retried.event_id != first.event_id
    for event in (first, retried):
        assert UUID(event.event_id).version == 4
        assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        assert event.amount == pytest.approx(0.25)
    assert unreadable_step.warnings() == unreadable_step.expected_warnings(lookups=2)


def test_a_jev_entry_whose_step_cannot_be_read_is_still_billed(unreadable_step):
    """A Jev evaluator's own events are keyed to each attempt's call."""
    ids = _jev_event_ids_on_entry("entry-1") + _jev_event_ids_on_entry("entry-1")

    assert len(set(ids)) == 4
    assert unreadable_step.warnings() == unreadable_step.expected_warnings(lookups=2)


def test_an_entry_whose_task_entry_cannot_be_read_is_still_billed(failed_step_read):
    """Each attempt is billed once under a fresh id: an entry is never left
    unbilled because the engine's task entry cannot be read. The failed read is
    logged with its stack."""
    with patch(
        "tracer.utils.eval.engine_entry_run",
        side_effect=RuntimeError("task entry unreadable"),
    ):
        first = _bill_on_entry("entry-1")
        retried = _bill_on_entry("entry-1")

    assert retried.event_id != first.event_id
    for event in (first, retried):
        assert UUID(event.event_id).version == 4
        assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        assert event.amount == pytest.approx(0.25)
    assert failed_step_read.warnings() == failed_step_read.expected_warnings(lookups=2)
