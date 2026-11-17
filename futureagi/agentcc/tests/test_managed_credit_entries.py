"""OSS wallet attribution and settled reads; only synthetic ledger boundaries."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from django.db.models import Q

from agentcc.serializers.managed_credits import (
    ENTRY_CSV_COLUMNS,
    MAX_PAGE_SIZE,
    ManagedCreditEntrySerializer,
    decode_cursor,
    encode_cursor,
    entry_csv_row,
    entry_values,
)
from agentcc.tests import test_managed_credits_api as wallet
from agentcc.views import managed_credits as views
from tfc.constants.levels import Level

# Reuse the wallet clients, but NOT wallet.ledger (which requires enterprise).
make_client = wallet.make_client
admin_client = wallet.admin_client
member_client = wallet.member_client
staff_client = wallet.staff_client
other_org = wallet.other_org

AT = datetime(2026, 9, 1, tzinfo=UTC)
ORG_ID = UUID(int=100)
ATTRIBUTION = {"org_id", "api_key_id", "agent_run_id", "agent_id"}


def _entry(number=1, **overrides):
    fields = {
        "id": UUID(int=number),
        "organization_id": ORG_ID,
        "occurred_at": AT,
        "kind": "spend",
        "amount_micros": -1250,
        "request_id": f"request-{number}.1",
        "model": "test-model",
        "provider": "test-provider",
        "markup_micros": 50,
        "markup_bps": 500,
        "cost_source": "provider_reported",
        "stripe_ref": "",
        "api_key_id": "key-attribution",
        "agent_run_id": "run-attribution",
        # Never trust a same-named incidental attribute as G08's key mapping.
        "agent_id": "untrusted-agent",
    }
    return SimpleNamespace(**(fields | overrides))


def _matches(row, condition):
    if isinstance(condition, Q):
        values = [_matches(row, child) for child in condition.children]
        result = all(values) if condition.connector == Q.AND else any(values)
        return not result if condition.negated else result
    name, expected = condition
    field, _, operation = name.partition("__")
    actual = getattr(row, field)
    if operation == "in":
        return actual in expected
    if operation == "lt":
        return actual < expected
    if operation == "gte":
        return actual >= expected
    assert not operation, operation
    return actual == expected


class _Rows(list):
    """Small queryset stand-in; no enterprise model or Redis is imported."""

    def filter(self, *conditions, **lookups):
        conditions = (*conditions, *lookups.items())
        return _Rows(row for row in self if all(_matches(row, q) for q in conditions))

    def order_by(self, *fields):
        assert fields == ("-occurred_at", "-id")
        return _Rows(sorted(self, key=lambda row: (row.occurred_at, row.id), reverse=True))

    def iterator(self, *, chunk_size):
        assert chunk_size == 500
        return iter(self)


@pytest.fixture
def ledger_fake(monkeypatch):
    # Same boundary pattern as the managed-credit view tests: patch the served
    # module's _ledger, not an enterprise import or a whole sys.modules snapshot.
    rows = _Rows()

    def read(organization, *, start=None, end=None):
        scoped = rows.filter(organization_id=organization.pk)
        if start is not None:
            scoped = scoped.filter(occurred_at__gte=start)
        if end is not None:
            scoped = scoped.filter(occurred_at__lt=end)
        return scoped

    ledger = SimpleNamespace(rows=rows, ledger_entries=MagicMock(side_effect=read))
    boundary = MagicMock(return_value=ledger)
    monkeypatch.setattr(views, "_ledger", boundary)
    ledger.boundary = boundary
    return ledger


@pytest.fixture
def settled():
    # Keep serializer/HTTP regressions collectable with TESTS-ONLY.patch too.
    from agentcc.services.managed_credit_entries import settled_spend_entries

    return settled_spend_entries


def test_attribution_fields_keep_existing_fields_and_exact_amounts():
    entry = _entry()
    values = entry_values(entry)
    data = ManagedCreditEntrySerializer(entry).data
    assert set(values) == set(data) == {"id", *ENTRY_CSV_COLUMNS, *ATTRIBUTION}
    for result in (values, data):
        assert result["org_id"] == str(ORG_ID)
        assert result["api_key_id"] == "key-attribution"
        assert result["agent_run_id"] == "run-attribution"
        assert result["agent_id"] is None
        assert result["request_id"] == "request-1.1"
        assert result["amount_usd"] == Decimal("-0.001250")
        assert result["markup_usd"] == Decimal("0.000050")
    assert data["id"] == str(entry.id)
    assert data["occurred_at"].startswith("2026-09-01T")


@pytest.mark.parametrize("value", ["", None])
def test_blank_attribution_is_null(value):
    entry = _entry(api_key_id=value, agent_run_id=value)
    for result in (entry_values(entry), ManagedCreditEntrySerializer(entry).data):
        assert result["api_key_id"] is None
        assert result["agent_run_id"] is None
        assert result["agent_id"] is None


def test_old_ledger_missing_attribution_attributes_still_serializes():
    entry = _entry()
    for name in ("organization_id", "api_key_id", "agent_run_id", "agent_id"):
        delattr(entry, name)
    for result in (entry_values(entry), ManagedCreditEntrySerializer(entry).data):
        assert result["org_id"] == ""
        assert all(result[name] is None for name in ATTRIBUTION - {"org_id"})
        assert result["request_id"] == "request-1.1"


def test_csv_contract_is_not_widened_by_json_attribution():
    assert list(ENTRY_CSV_COLUMNS) == wallet.CSV_COLUMNS
    plain = _entry()
    attributed = _entry(api_key_id="another-key", agent_run_id="another-run")
    assert entry_csv_row(plain) == entry_csv_row(attributed)
    assert len(entry_csv_row(attributed)) == len(wallet.CSV_COLUMNS)


@pytest.mark.integration
def test_spend_route_returns_attribution_and_scopes_org(
    admin_client, organization, other_org, ledger_fake
):
    ledger_fake.rows.extend([
        _entry(1, organization_id=organization.pk),
        _entry(2, organization_id=organization.pk, kind="grant"),
        _entry(3, organization_id=other_org.pk),
    ])
    result = wallet._result(admin_client.get(f"{wallet.BASE}entries/", {"kind": "spend"}))
    assert len(result["entries"]) == 1
    assert result["entries"][0]["org_id"] == str(organization.pk)
    assert result["entries"][0]["api_key_id"] == "key-attribution"
    assert result["entries"][0]["agent_run_id"] == "run-attribution"
    assert result["entries"][0]["agent_id"] is None
    ledger_fake.ledger_entries.assert_called_once_with(organization, start=None, end=None)


@pytest.mark.integration
def test_service_and_route_exchange_cursors_without_gaps(
    admin_client, organization, other_org, ledger_fake, settled, monkeypatch
):
    # Split a same-time tie over the boundary; also include an older row,
    # another org, and a non-spend. Only the four committed spend rows qualify.
    ledger_fake.rows.extend([
        *(_entry(i, organization_id=organization.pk) for i in (1, 2, 3)),
        _entry(4, organization_id=organization.pk, occurred_at=AT - timedelta(days=1)),
        _entry(5, organization_id=other_org.pk),
        _entry(6, organization_id=organization.pk, kind="grant"),
    ])
    hot_read = MagicMock(side_effect=AssertionError("settled reads must not use hot state"))
    monkeypatch.setattr(views.gateway_credits, "get_credits", hot_read)
    monkeypatch.setattr(views, "_balance_exhausted", hot_read)
    first = settled(organization, limit=2)
    response = admin_client.get(f"{wallet.BASE}entries/", {"kind": "spend", "limit": 2})
    assert response.status_code == 200
    assert response.data["result"] == first
    assert [entry["id"] for entry in first["entries"]] == [str(UUID(int=i)) for i in (3, 2)]
    assert decode_cursor(first["next_cursor"]) == (AT, UUID(int=2))
    second_response = admin_client.get(
        f"{wallet.BASE}entries/",
        {"kind": "spend", "limit": 2, "cursor": first["next_cursor"]},
    )
    assert second_response.status_code == 200
    second = settled(organization, cursor=response.data["result"]["next_cursor"], limit=2)
    assert second == second_response.data["result"]
    assert [entry["id"] for entry in second["entries"]] == [str(UUID(int=i)) for i in (1, 4)]
    assert second["next_cursor"] is None
    assert hot_read.call_count == 0
    assert all(call.args == (organization,) for call in ledger_fake.ledger_entries.call_args_list)


@pytest.mark.parametrize("cursor", ["not-a-cursor", "", "W10", "bnVsbA"])
def test_service_refuses_malformed_cursor_before_ledger(cursor, ledger_fake, settled):
    with pytest.raises(ValueError, match="not a managed-credit entries cursor"):
        settled(SimpleNamespace(pk=ORG_ID), cursor=cursor)
    ledger_fake.boundary.assert_not_called()


@pytest.mark.integration
def test_route_refuses_malformed_cursor_before_ledger(admin_client, ledger_fake):
    response = admin_client.get(f"{wallet.BASE}entries/", {"kind": "spend", "cursor": "bad"})
    assert response.status_code == 400
    ledger_fake.boundary.assert_not_called()


@pytest.mark.parametrize("limit,expected", [(0, 1), (-1, 1), (1, 1), (201, 200), (1000, 200)])
def test_service_clamps_page_size(limit, expected, ledger_fake, settled):
    ledger_fake.rows.extend(_entry(i) for i in range(1, MAX_PAGE_SIZE + 2))
    page = settled(SimpleNamespace(pk=ORG_ID), limit=limit)
    assert len(page["entries"]) == expected
    assert page["next_cursor"] == encode_cursor(AT, UUID(int=MAX_PAGE_SIZE + 2 - expected))


def test_service_default_page_and_empty_terminal_page(ledger_fake, settled):
    org = SimpleNamespace(pk=ORG_ID)
    assert settled(org) == {"entries": [], "next_cursor": None}
    ledger_fake.rows.extend(_entry(i) for i in range(1, 52))
    page = settled(org)
    assert len(page["entries"]) == 50
    assert page["next_cursor"] == encode_cursor(AT, UUID(int=2))
    assert settled(org, cursor=encode_cursor(AT, UUID(int=1))) == {
        "entries": [], "next_cursor": None,
    }


@pytest.mark.integration
@pytest.mark.parametrize("level", [Level.OWNER, Level.ADMIN])
def test_entries_remain_owner_admin_reads(level, organization, make_client, ledger_fake):
    client = make_client(organization, level)
    response = client.get(f"{wallet.BASE}entries/", {"kind": "spend"})
    assert response.status_code == 200
    ledger_fake.boundary.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize("client_name", ["member_client", "staff_client"])
def test_entries_deny_member_even_when_staff(client_name, request, ledger_fake):
    client = request.getfixturevalue(client_name)
    response = client.get(f"{wallet.BASE}entries/", {"kind": "spend"})
    assert response.status_code == 403
    ledger_fake.boundary.assert_not_called()


@pytest.mark.integration
def test_entries_deny_owner_of_another_org(
    organization, other_org, make_client, ledger_fake
):
    client = make_client(organization, Level.OWNER)
    # The workspace-scoped request belongs to another org where this user is
    # not a member, rather than their Owner home organization.
    _, workspace = wallet._user_in(other_org, Level.MEMBER)
    client.set_workspace(workspace)
    response = client.get(f"{wallet.BASE}entries/", {"kind": "spend"})
    assert response.status_code == 403
    ledger_fake.boundary.assert_not_called()


@pytest.mark.parametrize("organization", [None, SimpleNamespace(pk=None)])
def test_service_requires_a_scoped_organization(organization, ledger_fake, settled):
    with pytest.raises(ValueError, match="organization is required"):
        settled(organization)
    ledger_fake.boundary.assert_not_called()


def test_http_and_service_use_one_query_function():
    from agentcc.services.managed_credit_entries import credit_entries

    assert views.credit_entries is credit_entries
