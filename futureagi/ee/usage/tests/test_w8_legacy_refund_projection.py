"""MONEY-R1 / J12-001: pre-0031 refunds remain immutable but net public reads."""

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ee.usage.models.managed_credits import ManagedCreditEntry

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
SPENT_AT = datetime(2026, 10, 10, tzinfo=UTC)
REFUNDED_AT = datetime(2026, 10, 11, tzinfo=UTC)
LEGACY_FIELDS = {
    "product": "",
    "dimension": "",
    "unit": "",
    "period": "",
    "funding": "",
    "quantity": None,
    "funded_promo_micros": None,
    "funded_commit_micros": None,
    "funded_purchased_micros": None,
    "funded_debt_micros": None,
}


def _entry(org, kind, amount, **overrides):
    # Insert the actual 0031 defaults; 0032 never backfilled ledger entries.
    fields = {
        **LEGACY_FIELDS,
        "organization": org,
        "kind": kind,
        "amount_micros": amount,
        "idempotency_key": f"w8-legacy-{kind}",
        "request_id": "w8-legacy-request",
        "occurred_at": SPENT_AT if kind == "spend" else REFUNDED_AT,
        **overrides,
    }
    return ManagedCreditEntry.objects.create(**fields)


def _pair(org):
    spend = _entry(org, "spend", -1_000_000)
    refund = _entry(org, "refund", 400_000)
    # related_entry was also introduced in 0031 and stays NULL in this fixture.
    for entry in (spend, refund):
        entry.refresh_from_db()
        assert entry.related_entry_id is None
        assert {
            field: getattr(entry, field) for field in LEGACY_FIELDS
        } == LEGACY_FIELDS
    return spend, refund


def _stored(*entries):
    # Snapshot every stored column, not just the fields being projected.
    return list(
        ManagedCreditEntry.objects.filter(pk__in=[e.pk for e in entries])
        .order_by("pk")
        .values()
    )


def _get(client, route, params):
    response = client.get(f"/usage/ledger/v1/{route}/", params)
    assert response.status_code == 200, response.content
    return response


def _usage(client, period="2026-10", **params):
    return _get(
        client, "usage", {"period": period, "group_by": "dimension", **params}
    ).json()


def _assert_net(body, amount, quantity):
    assert body["totals"]["amount_micros"] == amount
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["product"] == "gateway"
    assert row["dimension"] == "managed_spend"
    assert row["unit"] == "usd_micros"
    assert row["quantity"] == quantity
    assert row["amount_micros"] == amount
    for item in (row, body["totals"]):
        assert item["funded"]["purchased"] == amount
        assert sum(item["funded"].values()) == amount


@pytest.mark.parametrize("product", [None, "gateway"], ids=["all-products", "gateway"])
def test_legacy_same_period_refund_nets_amount_and_quantity(
    organization, auth_client, product
):
    spend, refund = _pair(organization)
    before = _stored(spend, refund)
    params = {"product": product} if product else {}
    # Repeated GETs must neither persist the projection nor net the refund twice.
    for _ in range(2):
        _assert_net(_usage(auth_client, **params), 600_000, "600000")
    assert _stored(spend, refund) == before


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_legacy_refund_visible_in_gateway_entries_without_rewriting(
    organization, auth_client, fmt
):
    spend, refund = _pair(organization)
    before = _stored(spend, refund)
    response = _get(
        auth_client,
        "entries",
        {"product": "gateway", "period": "2026-10", "format": fmt},
    )
    if fmt == "csv":
        entries = list(csv.DictReader(io.StringIO(response.content.decode())))
    else:
        entries = response.json()["entries"]
    assert {e["id"] for e in entries} == {str(spend.pk), str(refund.pk)}
    projected = next(e for e in entries if e["id"] == str(refund.pk))
    assert projected["product"] == "gateway"
    assert projected["dimension"] == "managed_spend"
    assert projected["unit"] == "usd_micros"
    assert projected["quantity"] == "400000"
    assert projected["period"] == "2026-10"
    assert _stored(spend, refund) == before


@pytest.mark.parametrize(
    "metadata",
    [
        {"product": "eval"},
        {"dimension": "tokens"},
        {"unit": "tokens"},
        {"quantity": Decimal("12.5")},
        {"product": "gateway", "dimension": "tokens", "unit": "tokens"},
        {
            "product": "gateway",
            "dimension": "tokens",
            "unit": "tokens",
            "quantity": Decimal("12.5"),
        },
    ],
    ids=["product", "dimension", "unit", "quantity", "tokens-null", "tokens-explicit"],
)
def test_explicit_nonmonetary_metadata_is_not_reinterpreted(
    organization, auth_client, metadata
):
    refund = _entry(organization, "refund", 400_000, **metadata)
    before = _stored(refund)
    entries = _get(auth_client, "entries", {"kind": "refund"}).json()["entries"]
    assert len(entries) == 1
    projected = entries[0]
    for field in ("product", "dimension", "unit", "period"):
        assert projected[field] == metadata.get(field)
    expected_quantity = "12.5" if metadata.get("quantity") is not None else None
    assert projected["quantity"] == expected_quantity
    filtered = _get(
        auth_client, "entries", {"kind": "refund", "product": "gateway"}
    ).json()["entries"]
    expected_ids = {str(refund.pk)} if metadata.get("product") == "gateway" else set()
    assert {e["id"] for e in filtered} == expected_ids
    assert _stored(refund) == before


def _tagged_fields(amount, period="2026-10"):
    return {
        "product": "gateway",
        "dimension": "managed_spend",
        "unit": "usd_micros",
        "period": period,
        "funding": "purchased",
        "quantity": Decimal(abs(amount)),
        "funded_promo_micros": 0,
        "funded_commit_micros": 0,
        "funded_purchased_micros": amount,
        "funded_debt_micros": 0,
    }


@pytest.mark.parametrize("quantity", [None, Decimal(400_000)], ids=["null", "stored"])
def test_post_0031_refund_projection_is_unchanged(organization, auth_client, quantity):
    spend = _entry(organization, "spend", -1_000_000, **_tagged_fields(-1_000_000))
    refund_fields = {**_tagged_fields(400_000), "quantity": quantity}
    refund = _entry(organization, "refund", 400_000, **refund_fields)
    before = _stored(spend, refund)
    _assert_net(_usage(auth_client, product="gateway"), 600_000, "600000")
    entries = _get(
        auth_client, "entries", {"kind": "refund", "product": "gateway"}
    ).json()["entries"]
    assert len(entries) == 1
    assert entries[0]["quantity"] == "400000"
    assert entries[0]["period"] == "2026-10"
    assert _stored(spend, refund) == before


@pytest.mark.parametrize("shape", ["legacy", "post-0031"])
def test_refund_only_period_keeps_negative_money_and_null_quantity(
    organization, auth_client, shape
):
    spend = _entry(organization, "spend", -1_000_000)
    fields = _tagged_fields(400_000, "2026-11") if shape == "post-0031" else {}
    refund = _entry(
        organization,
        "refund",
        400_000,
        **{**fields, "occurred_at": datetime(2026, 11, 1, tzinfo=UTC)},
    )
    before = _stored(spend, refund)
    _assert_net(_usage(auth_client), 1_000_000, "1000000")
    _assert_net(_usage(auth_client, "2026-11"), -400_000, None)
    assert _stored(spend, refund) == before
