"""A Jev run's charge keeps one usage event id across the retries of the step
that ran it, and a new run is charged again.

A worker that runs an evaluation step again (a retry after its events were
emitted) can reach the answer on another HTTP attempt than the first time. The
event that carries the charge is keyed to the run alone, so the billing
consumer keeps one charge. Each attempt the client made keeps its own
zero-amount record.
"""

from unittest.mock import Mock

import pytest

from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator
from ee.jev.metering import build_usage_events

pytestmark = pytest.mark.requires_ee

FEE = 0.06
TOKENS = {"input_tokens": 412, "output_tokens": 6}


def _evaluator(outcomes):
    """A Jev evaluator whose client made one attempt per outcome; the last
    one is the answer."""
    client = Mock()
    client.evaluate.return_value = {
        "model": "jev-1.13.0",
        "answers": {"q1": {"type": "noul", "noul": 0.8}},
        "usage": dict(TOKENS),
    }
    client.attempts = [
        {
            "attempt": number,
            "outcome": outcome,
            "usage": dict(TOKENS) if outcome == "success" else {},
        }
        for number, outcome in enumerate(outcomes, start=1)
    ]
    client.attempt = len(outcomes)
    client.gateway_request_id = None
    evaluator = JevEvaluator(rule_prompt="Judge", model="jev-latest", client=client)
    evaluator._evaluate()
    return evaluator


def _charge_ids(*runs):
    """The ids of the events that carry a charge, over ``runs`` given as
    (attempt outcomes, run id) pairs."""
    ids = set()
    for outcomes, run_id in runs:
        events = build_usage_events(
            _evaluator(outcomes), org_id="org", cell_or_run_id=run_id, amount=FEE
        )
        ids.update(event.event_id for event in events if event.amount)
    return ids


@pytest.mark.parametrize(
    ("first", "retry"),
    [
        pytest.param(["success"], ["error", "success"], id="first-then-second"),
        pytest.param(["error", "success"], ["success"], id="second-then-first"),
        pytest.param(
            ["error", "error", "success"], ["error", "success"], id="third-then-second"
        ),
    ],
)
def test_a_retry_that_succeeds_on_another_attempt_is_charged_once(first, retry):
    assert len(_charge_ids((first, "step-1"), (retry, "step-1"))) == 1


def test_a_retry_that_succeeds_on_the_same_attempt_is_charged_once():
    assert len(_charge_ids((["success"], "step-1"), (["success"], "step-1"))) == 1


def test_a_new_run_is_charged_again():
    assert len(_charge_ids((["success"], "step-1"), (["success"], "step-2"))) == 2


def test_each_attempt_keeps_its_own_record():
    events = build_usage_events(
        _evaluator(["error", "error", "success"]),
        org_id="org",
        cell_or_run_id="step-1",
        amount=FEE,
    )
    assert [event.properties["attempt"] for event in events] == [1, 2, 3]
    assert [event.amount for event in events] == [0, 0, FEE]
    assert len({event.event_id for event in events}) == 3
