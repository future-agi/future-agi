"""J12-001: monetary usage quantity nets refunds, including historical rows."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ee.usage.models.managed_credits import ManagedCreditEntry
from ee.usage.services import managed_credits as mc
from ee.usage.services.ledger_reads import entry_json
from ee.usage.tests.ledger_support import spend_record

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
SPENT_AT = datetime(2026, 10, 10, tzinfo=UTC)
REFUNDED_AT = datetime(2026, 10, 11, tzinfo=UTC)


def _spend(org):
    mc.grant(
        org, 2_000_000, idempotency_key="w2-quantity-fund", actor=None, note="test"
    )
    return mc.settle_spend(
        spend_record(
            org,
            "w2-quantity-request",
            billed=1_000_000,
            ts_ms=int(SPENT_AT.timestamp() * 1000),
        )
    )[0]


def _historical_refund(org, spend, amount, period="2026-10"):
    # Insert a legacy-shaped fixture. Never update an append-only ledger row.
    return ManagedCreditEntry.objects.create(
        organization=org,
        kind="refund",
        amount_micros=amount,
        idempotency_key="refund:w2-quantity-request",
        related_entry=spend,
        request_id=spend.request_id,
        product="gateway",
        dimension="managed_spend",
        unit="usd_micros",
        period=period,
        occurred_at=REFUNDED_AT,
        quantity=None,
        funded_promo_micros=amount,
        funded_commit_micros=0,
        funded_purchased_micros=0,
        funded_debt_micros=0,
    )


@pytest.mark.parametrize("amount", [400_000, 1_000_000], ids=["partial", "full"])
def test_j12_new_refund_persists_quantity_and_nets_public_usage(
    organization,
    auth_client,
    monkeypatch,
    amount,
):
    _spend(organization)
    monkeypatch.setattr(mc.timezone, "now", lambda: REFUNDED_AT)
    refund, created = mc.refund(
        organization,
        "w2-quantity-request",
        amount,
        reason="test",
        actor=None,
    )
    assert created
    refund.refresh_from_db()
    assert refund.quantity == Decimal(amount)
    response = auth_client.get(
        "/usage/ledger/v1/usage/",
        {
            "period": "2026-10",
            "group_by": "dimension",
        },
    )
    assert response.status_code == 200, response.content
    row = response.json()["rows"][0]
    assert row["amount_micros"] == 1_000_000 - amount
    assert row["quantity"] == str(1_000_000 - amount)
    assert row["unit"] == "usd_micros"
    assert sum(row["funded"].values()) == row["amount_micros"]


@pytest.mark.parametrize("amount", [400_000, 1_000_000], ids=["partial", "full"])
def test_j12_historical_null_refund_nets_public_usage_without_rewriting(
    organization,
    auth_client,
    amount,
):
    spend = _spend(organization)
    refund = _historical_refund(organization, spend, amount)
    response = auth_client.get(
        "/usage/ledger/v1/usage/",
        {
            "period": "2026-10",
            "group_by": "dimension",
        },
    )
    assert response.status_code == 200, response.content
    row = response.json()["rows"][0]
    assert row["amount_micros"] == 1_000_000 - amount
    assert row["quantity"] == str(1_000_000 - amount)
    assert row["unit"] == "usd_micros"
    assert sum(row["funded"].values()) == row["amount_micros"]
    entries = auth_client.get("/usage/ledger/v1/entries/", {"kind": "refund"})
    assert entries.status_code == 200, entries.content
    assert entries.json()["entries"][0]["quantity"] == str(amount)
    refund.refresh_from_db()
    assert refund.quantity is None


def test_j12_refund_only_period_has_negative_money_and_no_false_zero_quantity(
    organization,
    auth_client,
):
    spend = _spend(organization)
    _historical_refund(organization, spend, 400_000, period="2026-11")
    response = auth_client.get(
        "/usage/ledger/v1/usage/",
        {
            "period": "2026-11",
            "group_by": "dimension",
        },
    )
    assert response.status_code == 200, response.content
    row = response.json()["rows"][0]
    assert row["amount_micros"] == -400_000
    # The public read contract represents negative net quantities as null.
    assert row["quantity"] is None
    assert row["unit"] == "usd_micros"
    assert sum(row["funded"].values()) == -400_000


def test_j12_no_refund_control_and_nonmonetary_quantity_are_unchanged(
    organization,
    auth_client,
):
    spend = _spend(organization)
    response = auth_client.get(
        "/usage/ledger/v1/usage/",
        {
            "period": "2026-10",
            "group_by": "dimension",
        },
    )
    assert response.status_code == 200, response.content
    row = response.json()["rows"][0]
    assert row["amount_micros"] == 1_000_000
    assert row["quantity"] == "1000000"
    # Unsaved projections exercise the fallback boundary without ledger edits.
    spend.kind = "refund"
    spend.amount_micros = 400_000
    spend.quantity = Decimal("12.5")
    assert entry_json(spend)["quantity"] == "12.5"
    spend.quantity = None
    spend.dimension = "tokens"
    spend.unit = "tokens"
    assert entry_json(spend)["quantity"] is None
