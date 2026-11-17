"""MONEY-R2 / J12-001-RESIDUAL: dimension filters honor immutable legacy rows."""

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from ee.usage.models.managed_credits import ManagedCreditEntry

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
OCCURRED_AT = datetime(2026, 10, 11, tzinfo=UTC)
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
TAGGED_FIELDS = {
    "product": "gateway",
    "dimension": "managed_spend",
    "unit": "usd_micros",
    "period": "2026-10",
}


def _entry(org, label, kind="refund", **overrides):
    # Pin the real migration defaults independently of the production predicate.
    return ManagedCreditEntry.objects.create(
        **{
            **LEGACY_FIELDS,
            "organization": org,
            "kind": kind,
            "amount_micros": -1_000_000 if kind == "spend" else 400_000,
            "idempotency_key": f"w8-dimension-{label}",
            "occurred_at": OCCURRED_AT,
            **overrides,
        }
    )


def _stored(*entries):
    return list(
        ManagedCreditEntry.objects.filter(pk__in=[e.pk for e in entries])
        .order_by("pk")
        .values()
    )


def _entries(client, fmt, params, *, workspace_id=None):
    route = "/usage/ledger/v1/entries/"
    if workspace_id is not None:
        route = f"/usage/ledger/v1/workspaces/{workspace_id}/entries/"
    response = client.get(route, {**params, "format": fmt})
    assert response.status_code == 200, response.content
    if fmt == "csv":
        assert response["Content-Type"].startswith("text/csv")
        return list(csv.DictReader(io.StringIO(response.content.decode())))
    return response.json()["entries"]


@pytest.mark.parametrize("fmt", ["json", "csv"])
@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"product": "gateway"},
        {"product": "gateway", "period": "2026-10"},
        {"product": "gateway", "kind": "refund", "period": "2026-10"},
        {"kind": "spend", "period": "2026-10"},
    ],
    ids=[
        "dimension-only",
        "gateway",
        "gateway-period",
        "gateway-refund-period",
        "spend-period",
    ],
)
def test_ledger_managed_dimension_includes_legacy_and_tagged_entries(
    organization, auth_client, fmt, filters
):
    legacy = [_entry(organization, kind, kind) for kind in ("spend", "refund")]
    for entry in legacy:
        entry.refresh_from_db()
        assert entry.related_entry_id is None
        assert {
            field: getattr(entry, field) for field in LEGACY_FIELDS
        } == LEGACY_FIELDS
    tagged = [
        _entry(organization, f"tagged-{kind}", kind, **TAGGED_FIELDS)
        for kind in ("spend", "refund")
    ]
    before = _stored(*legacy, *tagged)
    entries = _entries(auth_client, fmt, {"dimension": "managed_spend", **filters})
    expected = [
        entry
        for entry in (*legacy, *tagged)
        if "kind" not in filters or entry.kind == filters["kind"]
    ]
    assert {entry["id"] for entry in entries} == {str(entry.pk) for entry in expected}
    for entry in entries:
        assert entry["product"] == "gateway"
        assert entry["dimension"] == "managed_spend"
        assert entry["unit"] == "usd_micros"
        assert entry["period"] == "2026-10"
        assert entry["quantity"] == (
            "1000000" if entry["kind"] == "spend" else "400000"
        )
    assert _stored(*legacy, *tagged) == before


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_ledger_managed_dimension_keeps_period_bounds(organization, auth_client, fmt):
    rows = [
        _entry(
            organization,
            "before",
            occurred_at=datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC),
        ),
        _entry(organization, "start", occurred_at=datetime(2026, 10, 1, tzinfo=UTC)),
        _entry(
            organization,
            "last",
            occurred_at=datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC),
        ),
        _entry(organization, "after", occurred_at=datetime(2026, 11, 1, tzinfo=UTC)),
    ]
    before = _stored(*rows)
    entries = _entries(
        auth_client,
        fmt,
        {
            "dimension": "managed_spend",
            "product": "gateway",
            "kind": "refund",
            "period": "2026-10",
        },
    )
    assert {entry["id"] for entry in entries} == {str(rows[1].pk), str(rows[2].pk)}
    assert _stored(*rows) == before


@pytest.mark.parametrize("fmt", ["json", "csv"])
@pytest.mark.parametrize(
    ("kind", "metadata"),
    [
        ("spend", {"dimension": "tokens"}),
        ("refund", {"dimension": "tokens"}),
        ("refund", {"quantity": Decimal("12.5")}),
        ("refund", {"product": "eval"}),
        ("refund", {"unit": "tokens"}),
        ("refund", {"funding": "purchased"}),
        ("refund", {"period": "2026-10"}),
        (
            "refund",
            {
                "funded_promo_micros": 0,
                "funded_commit_micros": 0,
                "funded_purchased_micros": 400_000,
                "funded_debt_micros": 0,
            },
        ),
    ],
    ids=[
        "spend-dimension",
        "refund-dimension",
        "quantity",
        "product",
        "unit",
        "funding",
        "period",
        "funded",
    ],
)
def test_ledger_managed_dimension_does_not_reinterpret_explicit_metadata(
    organization, auth_client, fmt, kind, metadata
):
    explicit = _entry(organization, "explicit", kind, **metadata)
    tagged = _entry(organization, "tagged", **TAGGED_FIELDS)
    before = _stored(explicit, tagged)
    unfiltered = _entries(auth_client, fmt, {})
    assert {entry["id"] for entry in unfiltered} == {str(explicit.pk), str(tagged.pk)}
    if "quantity" in metadata:
        row = next(entry for entry in unfiltered if entry["id"] == str(explicit.pk))
        assert row["quantity"] == "12.5"
        assert row["dimension"] == ("" if fmt == "csv" else None)
    entries = _entries(auth_client, fmt, {"dimension": "managed_spend"})
    assert {entry["id"] for entry in entries} == {str(tagged.pk)}
    assert _stored(explicit, tagged) == before


@pytest.mark.parametrize("fmt", ["json", "csv"])
@pytest.mark.parametrize("dimension", ["tokens", "other_dimension"])
def test_ledger_other_dimension_filters_stay_exact(
    organization, auth_client, fmt, dimension
):
    rows = [
        _entry(organization, f"{dim or 'legacy'}-{kind}", kind, dimension=dim)
        for dim in ("", "managed_spend", "tokens", "other_dimension")
        for kind in ("spend", "refund")
    ]
    before = _stored(*rows)
    entries = _entries(auth_client, fmt, {"dimension": dimension})
    assert {entry["id"] for entry in entries} == {
        str(row.pk) for row in rows if row.dimension == dimension
    }
    assert all(entry["dimension"] == dimension for entry in entries)
    assert _stored(*rows) == before


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_ledger_managed_dimension_keeps_workspace_and_org_scope(
    organization, auth_client, workspace, fmt
):
    other_org = type(organization).objects.create(
        name="Other dimension-filter organization"
    )
    rows = [
        _entry(organization, "workspace-refund", workspace_id=workspace.pk),
        _entry(organization, "workspace-spend", "spend", workspace_id=workspace.pk),
        _entry(organization, "other-workspace", workspace_id=uuid4()),
        _entry(organization, "no-workspace"),
        _entry(other_org, "other-org", workspace_id=workspace.pk),
    ]
    before = _stored(*rows)
    entries = _entries(
        auth_client,
        fmt,
        {"dimension": "managed_spend", "product": "gateway", "period": "2026-10"},
        workspace_id=workspace.pk,
    )
    assert {entry["id"] for entry in entries} == {str(rows[0].pk), str(rows[1].pk)}
    assert _stored(*rows) == before
