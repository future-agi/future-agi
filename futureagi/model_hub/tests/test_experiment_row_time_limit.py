"""A per-row time limit is checked before and after each model call, not during
one: a call that overruns the limit finishes, and then the row stops.

The prompt row runs through provider_policy.prompt_response on the experiment
row's own provider key. The row budget is an in-memory stand-in with only a
time limit, as budget_for_prompt builds it for limits={"timeout_s": ...}; the
model call is a stub that records when it starts and ends.
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from model_hub.services import credit_ledger_port as cl

pytestmark = pytest.mark.django_db
LIMIT_REACHED = "Per-row execution limit reached"


@pytest.fixture(autouse=True)
def _meter_safety(monkeypatch):
    monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
    monkeypatch.setattr(cl, "_sleep", lambda _delay: None)
    cl.reset_meter_safety()
    yield
    cl.reset_meter_safety()


@pytest.fixture
def row_budget(monkeypatch):
    from model_hub.services import experiment_row_budget

    budget = SimpleNamespace(
        id=uuid.uuid4(),
        exhausted=False,
        deadline=timezone.now() + timedelta(seconds=30),
        usage={},
        limits={"timeout_s": 30},
    )
    budget.refresh_from_db = lambda: None
    monkeypatch.setattr(
        experiment_row_budget, "budget_for_prompt", lambda *a, **k: budget
    )
    return budget


@pytest.fixture
def experiment(organization, workspace):
    return SimpleNamespace(
        id=uuid.uuid4(),
        dataset=SimpleNamespace(organization=organization, workspace=workspace),
    )


def _own_key(**_kwargs):
    return cl.PayerRoute.byok(provider="openai")


def _run_prompt(calls, during=None):
    def litellm_response():
        calls.append("started")
        if during is not None:
            during()
        calls.append("finished")
        return "answer", {
            "data": {"response": "answer"},
            "metadata": {
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "total_tokens": 16,
                },
                "response_time": 0.2,
            },
        }

    return SimpleNamespace(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Answer briefly."}],
        max_tokens=None,
        litellm_response=litellm_response,
    )


def _respond(run_prompt, experiment):
    from tfc.temporal.experiments.provider_policy import prompt_response

    with cl.use_credit_ledger(cl.MockCreditLedger()), cl.use_route_resolver(_own_key):
        return prompt_response(run_prompt, experiment, uuid.uuid4(), uuid.uuid4())


def test_call_that_overruns_finishes_then_the_row_stops(row_budget, experiment):
    calls = []

    def overrun():
        # The time limit passes while the model call is in flight.
        row_budget.deadline = timezone.now() - timedelta(seconds=1)

    with pytest.raises(ValueError, match=LIMIT_REACHED):
        _respond(_run_prompt(calls, during=overrun), experiment)
    # The call was not cut short: it ran to its end, and then the row stopped.
    assert calls == ["started", "finished"]


def test_row_past_its_time_limit_starts_no_new_call(row_budget, experiment):
    row_budget.deadline = timezone.now() - timedelta(seconds=1)
    calls = []
    with pytest.raises(ValueError, match=LIMIT_REACHED):
        _respond(_run_prompt(calls), experiment)
    assert calls == []


def test_call_within_its_time_limit_returns_its_answer(row_budget, experiment):
    calls = []
    response, info = _respond(_run_prompt(calls), experiment)
    assert response == "answer"
    assert calls == ["started", "finished"]
    assert info["execution_limits"] == {"timeout_s": 30}
