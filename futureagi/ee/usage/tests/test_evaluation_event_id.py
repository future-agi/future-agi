"""The usage event id of one evaluation step.

The same step always gets the same id, so the consumer, which keeps one event
per id, bills a step that a worker runs again once. Any other step gets a
different id. A step that does not know what it evaluated, the run it is in or
what evaluated it has no id: its events are keyed on their own call.
"""

import uuid

import pytest

STEP = {
    "evaluated": ("row-1", "column-1"),
    "run": ("workflow-1", "run-1"),
    "evaluator": ("metric-1", 3),
}


def _event_id(**changed):
    from ee.usage.schemas.events import stable_evaluation_event_id

    return stable_evaluation_event_id(**{**STEP, **changed})


def test_the_same_step_always_gets_the_same_id():
    assert _event_id() == _event_id()


@pytest.mark.parametrize(
    "changed",
    [
        {"evaluated": ("row-2", "column-1")},
        {"evaluated": ("row-1", "column-2")},
        {"run": ("workflow-1", "run-2")},
        {"run": ("workflow-2", "run-1")},
        {"evaluator": ("metric-2", 3)},
        {"evaluator": ("metric-1", 4)},
    ],
)
def test_any_part_of_the_step_changed_gives_a_different_id(changed):
    assert _event_id(**changed) != _event_id()


def test_values_are_never_run_together():
    assert _event_id(evaluated=("row-1:column", "1")) != _event_id(
        evaluated=("row-1", "column:1")
    )


def test_the_id_is_a_uuid_the_consumer_can_store():
    event_id = _event_id()
    assert str(uuid.UUID(event_id)) == event_id


@pytest.mark.parametrize("part", ["evaluated", "run", "evaluator"])
@pytest.mark.parametrize("unknown", [None, "", (None, None)])
def test_a_step_with_an_unknown_part_has_no_id(part, unknown):
    assert _event_id(**{part: unknown}) is None
