"""Shared fixtures for the tests of an evaluation step's usage event id.

Each place that bills an evaluation keys its usage events to the step it
evaluates. Its tests use these fixtures: billing at a fixed price, a step whose
identity cannot be read, a part of the step whose read fails, and a Jev
evaluator. Test modules load the fixtures with ``pytest_plugins``. Assertions
stay in the test modules, which pytest rewrites.
"""

from dataclasses import dataclass
from unittest.mock import Mock, patch

import pytest
from structlog.testing import capture_logs

# The event a step id lookup that fails is logged under.
STEP_ID_FAILED = "evaluation_usage_step_id_failed"


@dataclass(frozen=True)
class UnreadableStep:
    """How a step's identity cannot be read in this test, and what the test
    logged."""

    how: str
    logs: list[dict]

    def warnings(self) -> list[dict]:
        """The step id warnings logged so far, as event, level and exc_info."""
        return [
            {key: entry.get(key) for key in ("event", "log_level", "exc_info")}
            for entry in self.logs
            if entry.get("event") == STEP_ID_FAILED
        ]

    def expected_warnings(self, *, lookups: int) -> list[dict]:
        """One warning with its stack for each step id lookup that fails, and
        none when there is nothing to read the step with."""
        if self.how == "unavailable":
            return []
        warning = {"event": STEP_ID_FAILED, "log_level": "warning", "exc_info": True}
        return [warning] * lookups


@pytest.fixture
def billing_config():
    """Billing at a fixed per-run fee and 100 credits per dollar."""
    config = Mock()
    config.get_eval_per_run_fee.return_value = 0.0005
    config.calculate_ai_credits.side_effect = lambda usd: usd * 100
    with patch("ee.usage.services.config.BillingConfig.get", return_value=config):
        yield config


@pytest.fixture(params=["fails", "unavailable"])
def unreadable_step(request, monkeypatch):
    """The step's identity cannot be read: reading it fails, or there is
    nothing to read it with (an ee without step ids)."""
    from ee.usage.schemas import events

    if request.param == "fails":
        monkeypatch.setattr(
            events,
            "stable_evaluation_event_id",
            Mock(side_effect=RuntimeError("step identity unreadable")),
        )
    else:
        monkeypatch.delattr(events, "stable_evaluation_event_id")
    with capture_logs() as logs:
        yield UnreadableStep(how=request.param, logs=logs)


@pytest.fixture
def failed_step_read():
    """Reading a part of the step (the workflow run, the task entry, a
    composite's step) fails, so no step id is derived. The test makes the read
    fail."""
    with capture_logs() as logs:
        yield UnreadableStep(how="fails", logs=logs)


def jev_evaluator():
    """A Jev evaluator that answered on its second attempt."""
    from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator

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
