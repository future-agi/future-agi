"""Finished provider replies close their reservation without rewriting usage."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.experiment_row_budget import ExperimentRowBudget
from model_hub.models.experiments import ExperimentsTable
from model_hub.services import experiment_row_budget as budgets

pytestmark = pytest.mark.django_db
PAYLOAD = {
    "model": "settlement-fixture",
    "messages": [{"role": "user", "content": "Reply briefly."}],
    "max_tokens": 64,
}
USAGE = {"prompt_tokens": 7, "completion_tokens": 3}


@pytest.fixture
def budget(organization, workspace, user, monkeypatch):
    dataset = Dataset.objects.create(
        name="Row settlement",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    experiment = ExperimentsTable.objects.create(
        name="Row settlement", dataset=dataset, snapshot_dataset=dataset, user=user
    )
    row = Row.objects.create(dataset=dataset, order=0)
    monkeypatch.setattr(budgets, "_rates", lambda _: (Decimal("1"), Decimal("2")))
    return ExperimentRowBudget.objects.create(
        experiment=experiment,
        row=row,
        variant="prompt:fixture",
        limits={"tokens": 1000, "cost_usd": 1},
    )


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"usage": None},
        {"usage": {}},
        {"usage": {"prompt_tokens": 7}},
        {"usage": {"prompt_tokens": -1, "completion_tokens": 3}},
        {"usage": {"prompt_tokens": 7, "completion_tokens": "3"}},
        {"usage": {"prompt_tokens": True, "completion_tokens": 3}},
        SimpleNamespace(usage=None),
    ],
    ids=[
        "absent",
        "null",
        "empty",
        "partial",
        "negative",
        "text",
        "boolean",
        "object",
    ],
)
def test_unreported_usage_closes_reservation_and_exhausts_budget(budget, response):
    reservation_id, _ = budgets.reserve(budget.id, PAYLOAD)
    budget.refresh_from_db()
    admitted_usage = dict(budget.usage)
    assert set(budget.reservations) == {reservation_id}

    with pytest.raises(ValueError, match="Provider usage is required"):
        budgets.settle(budget.id, reservation_id, response)

    budget.refresh_from_db()
    assert budget.exhausted is True
    assert budget.reservations == {}
    assert budget.usage == admitted_usage
    with pytest.raises(ValueError, match="Per-row execution limit reached"):
        budgets.reserve(budget.id, PAYLOAD)

    # A repeated settlement cannot replace the retained usage or reopen the row.
    budgets.settle(budget.id, reservation_id, {"usage": USAGE})
    budget.refresh_from_db()
    assert budget.exhausted is True
    assert budget.reservations == {}
    assert budget.usage == admitted_usage


def test_confirmed_usage_over_the_limit_closes_reservation(budget):
    reservation_id, _ = budgets.reserve(budget.id, PAYLOAD)
    expected = {"messages": 2, "tokens": 1003, "cost_usd": "0.001006"}

    with pytest.raises(ValueError, match="Per-row execution limit reached"):
        budgets.settle(
            budget.id,
            reservation_id,
            {"usage": {"prompt_tokens": 1000, "completion_tokens": 3}},
        )

    budget.refresh_from_db()
    assert budget.exhausted is True
    assert budget.reservations == {}
    assert budget.usage == expected
    with pytest.raises(ValueError, match="Per-row execution limit reached"):
        budgets.reserve(budget.id, PAYLOAD)
    budget.refresh_from_db()
    assert budget.reservations == {}
    assert budget.usage == expected


def test_unreported_reply_preserves_previously_settled_usage(budget):
    completed_id, _ = budgets.reserve(budget.id, PAYLOAD)
    budgets.settle(budget.id, completed_id, {"usage": USAGE})
    budget.refresh_from_db()
    expected_completed = {"messages": 2, "tokens": 10, "cost_usd": "0.000013"}
    assert budget.usage == expected_completed
    assert budget.reservations == {}

    reservation_id, _ = budgets.reserve(budget.id, PAYLOAD)
    budget.refresh_from_db()
    admitted_usage = dict(budget.usage)
    assert admitted_usage["tokens"] > expected_completed["tokens"]
    with pytest.raises(ValueError, match="Provider usage is required"):
        budgets.settle(budget.id, reservation_id, {})

    budget.refresh_from_db()
    assert budget.exhausted is True
    assert budget.reservations == {}
    assert budget.usage == admitted_usage
    budgets.settle(budget.id, completed_id, {"usage": USAGE})
    budget.refresh_from_db()
    assert budget.usage == admitted_usage
    assert budget.reservations == {}


def test_exhausted_budget_closes_each_finished_reservation(budget):
    unreported_id, _ = budgets.reserve(budget.id, PAYLOAD)
    pending_id, _ = budgets.reserve(budget.id, PAYLOAD)
    budget.refresh_from_db()
    admitted_usage = dict(budget.usage)
    pending = dict(budget.reservations[pending_id])
    expected = {
        **admitted_usage,
        "tokens": admitted_usage["tokens"] - pending["tokens"] + 10,
        "cost_usd": str(
            Decimal(admitted_usage["cost_usd"])
            - Decimal(pending["cost_usd"])
            + Decimal("0.000013")
        ),
    }

    with pytest.raises(ValueError, match="Provider usage is required"):
        budgets.settle(budget.id, unreported_id, {})
    budget.refresh_from_db()
    assert budget.exhausted is True
    assert budget.reservations == {pending_id: pending}
    assert budget.usage == admitted_usage

    # Another in-flight reply still settles after the row has been exhausted.
    with pytest.raises(ValueError, match="Per-row execution limit reached"):
        budgets.settle(budget.id, pending_id, {"usage": USAGE})
    budget.refresh_from_db()
    assert budget.exhausted is True
    assert budget.reservations == {}
    assert budget.usage == expected
