"""The managed-credit console API (GW-WALLET, ported from the integration
line), under /agentcc/managed-credits/.

Balance, entries with CSV, the monthly statement, checkout, settings, and the
staff grant, adjust and refund. Every read and write goes through the managed
credit ledger; the gateway's hot-path account is read through
``gateway_admin/credits.py``, mocked here. Cloud billing is reached
through one module, ``ee.cloud.billing.managed_credits_checkout``, which is
absent from this tree and stood in for here: Stripe is never called.

Release-line rules over the port: balances and entries are Owner/Admin reads
(billing-ledger-read-v1 section 2); internal columns never leave the server
(RA9: upstream cost and the note); a build without cloud billing answers a
truthful 503 billing_unavailable; credit is written only by the ledger's own
fulfilment and staff paths, never by any route an org admin's browser reaches.
"""

import csv
import io
import json
import secrets
import sys
import types
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc import permissions as gateway_permissions
from agentcc.auto_reload_terms import (
    AUTO_RELOAD_CONSENT_TERMS,
    AUTO_RELOAD_CONSENT_VERSION,
)
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests.module_standins import stand_in_modules
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

BASE = "/agentcc/managed-credits/"
LEDGER_MODULE = "ee.usage.services.managed_credits"
CHECKOUT_MODULE = "ee.cloud.billing.managed_credits_checkout"
GATEWAY_ADMIN = "agentcc.services.gateway_admin.credits.GatewayAdminClient"
# 32+ characters: Django refuses a weak admin token (round 4 port, W3 merge).
ADMIN_TOKEN = secrets.token_hex(32)
MICROS = 1_000_000

CSV_COLUMNS = [
    "occurred_at",
    "kind",
    "amount_usd",
    "request_id",
    "model",
    "provider",
    "markup_usd",
    "markup_bps",
    "cost_source",
    "stripe_ref",
]
# billing-ledger-read-v1 RA9: never in any response.
INTERNAL_FIELDS = (
    "landed_cost",
    "upstream_fee_bps",
    "processing_fee_bps",
    "upstream_key_id",
    "payment_fee",
    "note",
    "upstream_cost",
    "upstream_usd",
    "served_upstream_provider",
    "created_by",
)

LEVEL_ROLES = {
    Level.OWNER: OrganizationRoles.OWNER,
    Level.ADMIN: OrganizationRoles.ADMIN,
    Level.MEMBER: OrganizationRoles.MEMBER,
}


@contextmanager
def _stand_in_modules(entries):
    """Put ``entries`` in sys.modules for the block (a None value makes that
    import fail), then restore exactly those names: each to the module it
    held before, or absent again.

    Not patch.dict(sys.modules): its exit restores the whole dictionary, so it
    evicts every module first imported inside the block. When the block holds
    a worker's first request, those are the URLconf and every view module it
    imports. Django keeps serving the evicted view classes, while a later
    string-target patch (``agentcc.views.<module>.<name>``) replaces the name in
    a fresh copy of the module that the served view never reads."""
    absent = object()
    previous = {name: sys.modules.get(name, absent) for name in entries}
    sys.modules.update(entries)
    try:
        yield
    finally:
        for name, module in previous.items():
            if module is absent:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


# --- fixtures -----------------------------------------------------------------


@pytest.fixture
def ledger():
    """E1's ledger service; the suite needs the ledger the API is built on."""
    return pytest.importorskip(LEDGER_MODULE)


def _user_in(organization, level, *, staff=False):
    """A user who belongs to ``organization`` at ``level`` (every test user has
    an org: the org-less request is refused before any of these views run)."""
    user = User.objects.create_user(
        email=f"d4-{uuid.uuid4().hex[:8]}@example.com",
        password="testpassword123",
        name="D4 User",
        organization=organization,
        organization_role=LEVEL_ROLES[level],
    )
    if staff:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=LEVEL_ROLES[level],
        level=level,
        is_active=True,
    )
    workspace = Workspace.no_workspace_objects.filter(
        organization=organization, is_default=True
    ).first() or Workspace.objects.create(
        name=f"D4 {uuid.uuid4().hex[:6]}",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    return user, workspace


@pytest.fixture
def make_client():
    clients = []

    def build(organization, level, *, staff=False):
        user, workspace = _user_in(organization, level, staff=staff)
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.stop_workspace_injection()


@pytest.fixture
def admin_client(organization, make_client):
    return make_client(organization, Level.ADMIN)


@pytest.fixture
def member_client(organization, make_client):
    return make_client(organization, Level.MEMBER)


@pytest.fixture
def staff_client(organization, make_client):
    return make_client(organization, Level.MEMBER, staff=True)


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name="D4 Other Organization")


@pytest.fixture
def auto_reload_available():
    """Answer as a build that runs auto-reload would. This release runs none,
    so its settings save refuses turning it on (test_auto_reload_unavailable);
    tests of the consent and reload rules for such a build use this."""
    from agentcc.views import managed_credits as views

    with patch.object(views, "AUTO_RELOAD_AVAILABLE", True, create=True):
        yield


# Patch the permission module used by the requests. Module stand-ins below
# preserve unrelated lazy imports, including Django's cached view callbacks.
@pytest.fixture
def token_client():
    """A caller holding the gateway admin token, with no user."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
    with patch.object(gateway_permissions, "AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
        yield client


class FakeGateway:
    """The gateway admin API's credits routes: GET reads the account, PUT is
    credits-granted-push-v1. ``account`` None is an org with no account hash,
    which the gateway reads as zeros."""

    def __init__(self):
        self.account = None
        self.error = None
        self.calls = []

    def request(self, method, path, json=None, params=None):
        self.calls.append((method, path, json))
        if self.error is not None:
            raise self.error
        if method == "PUT":
            granted, version = json["granted_total_micros"], json["version"]
            acct = self.account or {"granted": 0, "version": 0, "spent": 0, "held": 0}
            if version > acct["version"] or self.account is None:
                acct.update(granted=granted, version=version)
            acct["spent"] = max(acct["spent"], json["settled_spend_floor_micros"])
            self.account = acct
            return {
                "applied": True,
                "granted_total_micros": acct["granted"],
                "granted_version": acct["version"],
                "spent_micros": acct["spent"],
                "held_micros": acct["held"],
                "available_micros": acct["granted"] - acct["spent"] - acct["held"],
            }
        acct = self.account or {"granted": 0, "version": 0, "spent": 0, "held": 0}
        available = acct["granted"] - acct["spent"] - acct["held"]
        return {
            "org_id": path.split("/")[3],
            "granted_total_micros": acct["granted"],
            "granted_version": acct["version"],
            "spent_micros": acct["spent"],
            "held_micros": acct["held"],
            "available_micros": available,
            "balance_usd": available / MICROS,
            "markup_percent": 5,
            "outbox_pending": 0,
            "ledger": "redis",
        }

    def pushes(self):
        return [c for c in self.calls if c[0] == "PUT"]


@pytest.fixture
def gateway():
    fake = FakeGateway()
    with patch(GATEWAY_ADMIN) as client_class:
        client_class.return_value.request.side_effect = fake.request
        yield fake


def _opt_in(organization, **managed_models):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        managed_models={"enabled": True, **managed_models},
    )


def _entry(ledger, organization, kind, micros, at, **fields):
    """A ledger row at a chosen time, for windows the service functions cannot
    back-date (they stamp ``now``)."""
    return ledger.ManagedCreditEntry.objects.create(
        organization=organization,
        kind=kind,
        amount_micros=micros,
        idempotency_key=f"test:{uuid.uuid4()}",
        occurred_at=at,
        **fields,
    )


def _spend(ledger, organization, request_id, billed, *, model="openai/gpt-6-sol"):
    """Settle one managed-settlement-v1 spend through E1."""
    entry, _ = ledger.settle_spend(
        {
            "v": "1",
            "kind": "spend",
            "request_id": request_id,
            "generation_id": request_id.split(".")[0],
            "org_id": str(organization.pk),
            "billed_micros": str(billed),
            "upstream_micros": str(billed - billed // 21),
            "markup_micros": str(billed // 21),
            "markup_bps": "500",
            "model": model,
            "provider": "openrouter",
            "cost_source": "provider_reported",
            "usage_estimated": "0",
            "ts_ms": str(int(datetime.now(UTC).timestamp() * 1000)),
        }
    )
    return entry


def _result(response):
    assert response.status_code == 200, response.content
    return response.json()["result"]


def _csv_rows(response):
    body = (
        b"".join(response.streaming_content) if response.streaming else response.content
    )
    return list(csv.reader(io.StringIO(body.decode())))


# --- reads ----------------------------------------------------------------------


@pytest.mark.integration
class TestSummary:
    def test_summary_combines_gateway_and_ledger(
        self, ledger, organization, admin_client, gateway
    ):
        _opt_in(organization)
        ledger.grant(
            organization, 30 * MICROS, idempotency_key="g1", actor=None, note="pilot"
        )
        _spend(ledger, organization, "req-sum-1", 2_500_000)
        gateway.account = {
            "granted": 30 * MICROS,
            "version": 1,
            "spent": 3_100_000,
            "held": 400_000,
        }

        result = _result(admin_client.get(BASE))

        assert result["enabled"] is True
        assert result["granted_usd"] == 30.0
        assert result["settled_spend_usd"] == 2.5
        assert result["available_usd"] == 27.5
        assert result["gateway"] == {
            "spent_usd": 3.1,
            "held_usd": 0.4,
            "available_usd": 26.5,
        }
        assert result["gateway_warning"] is None
        # Spent at the gateway, not yet settled into the ledger by the drain.
        assert result["pending_settlement_usd"] == 0.6
        assert result["markup_percent"] == 5
        assert result["currency"] == "USD"
        assert result["auto_reload"] == {
            "enabled": False,
            "threshold_usd": None,
            "amount_usd": None,
            "disabled_reason": "auto_reload_unavailable",
            "consent_version": AUTO_RELOAD_CONSENT_VERSION,
            "consent_terms": AUTO_RELOAD_CONSENT_TERMS,
        }
        assert result["last_reconciled_at"] is None
        # No push: the gateway holds this org's account.
        assert gateway.pushes() == []

    def test_summary_reads_the_ledger_balance_with_platform_spend(
        self, ledger, organization, admin_client, gateway
    ):
        """The summary reads ``ledger_balance``. What is available
        is F - S - P, so a wallet-funded platform debit (P) lowers it as much
        as gateway spend (S) does; R's granted minus settled spend ignored P.
        The push the gateway gets is ``pushed_value``: granted max(F - P, 0)."""
        _opt_in(organization)
        ledger.grant(
            organization, 30 * MICROS, idempotency_key="g1", actor=None, note="pilot"
        )
        _spend(ledger, organization, "req-platform-1", 2_500_000)
        now = datetime.now(UTC)
        ledger.debit_usage(
            organization,
            dimension="agent_runs",
            product="agents",
            unit="runs",
            quantity=Decimal(2),
            amount_micros=4 * MICROS,
            period=now.strftime("%Y-%m"),
            seq=0,
            funding_source="wallet",
            occurred_at=now,
            price_catalog_version="price-catalog.v0",
        )
        gateway.account = None  # the summary reports loss without repairing it

        result = _result(admin_client.get(BASE))

        balance = ledger.ledger_balance(organization)
        assert balance.balance_micros == 23_500_000
        assert result["granted_usd"] == 30.0
        assert result["settled_spend_usd"] == 2.5
        assert result["available_usd"] == 23.5
        assert result["balance_exhausted"] is False
        assert result["gateway_warning"] == "syncing"
        assert result["gateway"] is None
        assert gateway.pushes() == []
        # Explicit repair still uses F - P, but a summary GET never triggers it.
        ledger.ensure_account_pushed(organization)
        (push,) = gateway.pushes()
        account = ledger.get_account(organization)
        assert push[2] == ledger.pushed_value(account).as_request()
        assert push[2]["granted_total_micros"] == 26 * MICROS
        assert push[2]["settled_spend_floor_micros"] == 2_500_000

    def test_summary_says_when_the_ledger_refuses_managed_spend(
        self, ledger, organization, admin_client, gateway
    ):
        """``balance_exhausted`` is the ledger's own admission answer
        (``balance_cache.admission_refusal`` for gateway_managed_spend,
        BALANCE_EXHAUSTED): true once the balance is at or below zero."""
        _opt_in(organization)
        ledger.grant(organization, MICROS, idempotency_key="g1", actor=None, note="x")
        _spend(ledger, organization, "req-exhausts", MICROS)

        result = _result(admin_client.get(BASE))

        assert result["available_usd"] == 0
        assert result["balance_exhausted"] is True
        assert result["freeze_reason"] == ""  # used up, not frozen

    def test_summary_names_a_receivable_instead_of_add_credits(
        self, ledger, organization, admin_client, gateway
    ):
        """An org that owes (any debt) is frozen: the summary
        says so, so the console need not show a generic "add credits"."""
        _opt_in(organization)
        _spend(ledger, organization, "req-owes", MICROS)  # no credit: 1 USD owed

        result = _result(admin_client.get(BASE))

        assert result["available_usd"] == -1.0
        assert result["balance_exhausted"] is True
        assert result["freeze_reason"] == "receivable_open"

    def test_summary_names_an_open_dispute_while_money_is_in_the_wallet(
        self,
        ledger,
        organization,
        admin_client,
        gateway,
        django_capture_on_commit_callbacks,
    ):
        """An open dispute freezes the whole wallet: what is left (6 USD) is
        not spendable, and the summary names the dispute as the reason."""
        _opt_in(organization)
        ledger.fulfil_topup(
            organization,
            payment=ledger.TopupPayment(
                source=ledger.TopupSource.PAYMENT_INTENT,
                object_id="pi_summary_disp",
                amount_micros=10 * MICROS,
                mode=ledger.StripeMode.TEST,
            ),
        )
        ledger.hold_dispute(
            organization,
            dispute_id="dp_summary_disp",
            payment_intent_id="pi_summary_disp",
            amount_micros=4 * MICROS,
            mode=ledger.StripeMode.TEST,
        )

        result = _result(admin_client.get(BASE))

        assert result["available_usd"] == 6.0
        assert result["balance_exhausted"] is True
        assert result["freeze_reason"] == "dispute_open"

        # Released: its commit refreshes the balance cache, so spending resumes.
        with django_capture_on_commit_callbacks(execute=True):
            ledger.release_dispute(
                organization, dispute_id="dp_summary_disp", mode=ledger.StripeMode.TEST
            )
        result = _result(admin_client.get(BASE))
        assert result["available_usd"] == 10.0
        assert (result["balance_exhausted"], result["freeze_reason"]) == (False, "")

    def test_summary_is_scoped_to_the_request_org(
        self, ledger, organization, other_org, admin_client, gateway
    ):
        ledger.grant(other_org, 9 * MICROS, idempotency_key="g", actor=None, note="x")

        result = _result(admin_client.get(BASE))

        assert result["granted_usd"] == 0
        assert gateway.calls[0][1] == f"/-/orgs/{organization.pk}/credits"

    def test_min_topup_default_25(self, ledger, organization, admin_client, gateway):
        _opt_in(organization)
        assert _result(admin_client.get(BASE))["min_topup_usd"] == 25
        assert _result(admin_client.get(f"{BASE}settings/"))["min_topup_usd"] == 25

        AgentccOrgConfig.no_workspace_objects.filter(organization=organization).update(
            managed_models={"enabled": True, "topup": {"min_usd": 40}}
        )
        assert _result(admin_client.get(BASE))["min_topup_usd"] == 40

    def test_missing_gateway_account_reports_syncing_without_push(
        self, ledger, organization, admin_client, gateway
    ):
        _opt_in(organization)
        ledger.grant(
            organization, 10 * MICROS, idempotency_key="g1", actor=None, note="x"
        )
        # The ledger has credit; the gateway reads as zeros: its hash is gone.
        gateway.account = None

        result = _result(admin_client.get(BASE))

        assert result["gateway"] is None
        assert result["gateway_warning"] == "syncing"
        assert result["pending_settlement_usd"] is None
        assert gateway.pushes() == []
        assert gateway.account is None
        # Reads stay read-only until config push or the drain repairs the account.
        assert _result(admin_client.get(BASE)) == result
        assert gateway.pushes() == []

    def test_first_read_of_an_opted_in_org_does_not_create_its_account(
        self, ledger, organization, admin_client, gateway
    ):
        _opt_in(organization)
        assert ledger.get_account(organization) is None

        result = _result(admin_client.get(BASE))

        assert result["gateway_warning"] == "syncing"
        assert ledger.get_account(organization) is None
        assert gateway.pushes() == []
        assert _result(admin_client.get(BASE)) == result
        assert ledger.get_account(organization) is None
        assert gateway.pushes() == []

    @pytest.mark.parametrize("opted_in", [False, True])
    @pytest.mark.parametrize("account_kind", ["missing", "zero", "funded"])
    def test_summary_preserves_response_bytes_without_writes(
        self, ledger, organization, admin_client, gateway, opted_in, account_kind
    ):
        if opted_in:
            _opt_in(organization)
        funded = account_kind == "funded"
        if account_kind != "missing":
            ledger.ManagedCreditAccount.objects.create(
                organization=organization,
                granted_total_micros=10 * MICROS if funded else 0,
                settled_spend_micros=2 * MICROS if funded else 0,
                purchased_balance_micros=8 * MICROS if funded else 0,
                version=1 if funded else 0,
                low_balance_threshold_micros=MICROS if funded else None,
                autoreload_enabled=funded,
                autoreload_threshold_micros=3 * MICROS if funded else None,
                autoreload_amount_micros=25 * MICROS if funded else None,
                autoreload_disabled_reason="test reason" if funded else "",
                last_reconciled_at=datetime(2026, 10, 1, tzinfo=UTC)
                if funded
                else None,
            )
        accounts = ledger.ManagedCreditAccount.objects.filter(organization=organization)
        before = list(accounts.values())
        syncing = opted_in and account_kind != "zero"
        # Pin the pre-change wire bytes, including defaults formerly read from
        # the zero account that GET created. No production serializer builds this.
        expected = json.dumps(
            {
                "status": True,
                "result": {
                    "managed_models_available": True,
                    "enabled": opted_in,
                    "granted_usd": 10.0 if funded else 0.0,
                    "settled_spend_usd": 2.0 if funded else 0.0,
                    "available_usd": 8.0 if funded else 0.0,
                    "balance_exhausted": not funded,
                    "freeze_reason": "",
                    "gateway": None
                    if syncing
                    else {"spent_usd": 0.0, "held_usd": 0.0, "available_usd": 0.0},
                    "gateway_warning": "syncing" if syncing else None,
                    "pending_settlement_usd": None if syncing else 0.0,
                    "markup_percent": None if syncing else 5.0,
                    "min_topup_usd": 25.0,
                    "low_balance_threshold_usd": 1.0 if funded else None,
                    "auto_reload": {
                        "enabled": funded,
                        "threshold_usd": 3.0 if funded else None,
                        "amount_usd": 25.0 if funded else None,
                        # This release runs no auto-reload, whatever is stored.
                        "disabled_reason": "auto_reload_unavailable",
                        "consent_version": AUTO_RELOAD_CONSENT_VERSION,
                        "consent_terms": AUTO_RELOAD_CONSENT_TERMS,
                    },
                    "last_reconciled_at": "2026-10-01T00:00:00Z" if funded else None,
                    "billing_available": False,
                    "currency": "USD",
                },
            },
            separators=(",", ":"),
        ).encode()
        with stand_in_modules({CHECKOUT_MODULE: None}):
            for _ in range(2):
                response = admin_client.get(BASE)
                assert response.status_code == 200
                assert response.content == expected
                assert gateway.pushes() == []
                assert list(accounts.values()) == before
        assert gateway.account is None

    def test_org_not_opted_in_is_never_pushed(
        self, ledger, organization, admin_client, gateway
    ):
        ledger.grant(organization, MICROS, idempotency_key="g1", actor=None, note="x")

        result = _result(admin_client.get(BASE))

        assert result["enabled"] is False
        assert result["gateway_warning"] is None
        assert gateway.pushes() == []

    @pytest.mark.parametrize(
        ("error", "warning"),
        [
            (GatewayClientError("Cannot connect to gateway"), "unreachable"),
            (GatewayClientError("Gateway returned 503", 503), "unreachable"),
            (
                GatewayClientError("Gateway returned 404", 404),
                "managed_models_disabled",
            ),
        ],
    )
    def test_gateway_failure_degrades_to_ledger_only(
        self, ledger, organization, admin_client, gateway, error, warning
    ):
        _opt_in(organization)
        ledger.grant(organization, MICROS, idempotency_key="g1", actor=None, note="x")
        gateway.error = error

        result = _result(admin_client.get(BASE))

        assert result["granted_usd"] == 1.0
        assert result["gateway"] is None
        assert result["gateway_warning"] == warning
        assert result["markup_percent"] is None
        assert gateway.pushes() == []

    def test_ledger_absent_is_503(self, organization, admin_client, gateway):
        with stand_in_modules({LEDGER_MODULE: None}):
            response = admin_client.get(BASE)
        assert response.status_code == 503
        assert response.json()["code"] == "billing_unavailable"

    @pytest.mark.parametrize(
        ("error", "available"),
        [
            (None, True),
            (GatewayClientError("Gateway returned 404", 404), False),
            (GatewayClientError("Cannot connect to gateway"), False),
        ],
    )
    def test_managed_models_available_only_when_the_gateway_says_so(
        self, ledger, organization, admin_client, gateway, error, available
    ):
        """The console shows the billing page only when the gateway serves
        managed models: its credits route answers (404 managed_models_disabled
        while no provider is managed: true, the shipped default)."""
        _opt_in(organization)
        ledger.ensure_account_pushed(organization)
        gateway.error = error

        result = _result(admin_client.get(BASE))

        assert result["managed_models_available"] is available


@pytest.mark.integration
class TestAccountPushPaths:
    def test_config_push_still_creates_and_pushes_the_account(
        self, ledger, organization, gateway, django_capture_on_commit_callbacks
    ):
        from agentcc.services.config_push import push_org_config

        config = _opt_in(organization)
        assert ledger.get_account(organization) is None
        with (
            patch("agentcc.services.config_push.get_gateway_client") as client,
            patch.object(
                ledger, "ensure_account_pushed", wraps=ledger.ensure_account_pushed
            ) as ensure,
            django_capture_on_commit_callbacks(execute=True),
        ):
            assert push_org_config(str(organization.pk), config) is True
            client.return_value.set_org_config.assert_called_once()
        ensure.assert_called_once_with(organization)
        assert ledger.get_account(organization) is not None
        assert gateway.account is not None
        assert gateway.pushes() == [
            (
                "PUT",
                f"/-/orgs/{organization.pk}/credits/granted",
                {
                    "granted_total_micros": 0,
                    "version": 0,
                    "settled_spend_floor_micros": 0,
                },
            )
        ]

    def test_settlement_drain_still_creates_and_pushes_the_account(
        self, ledger, organization, gateway
    ):
        from ee.usage.services import managed_settlement as settlement

        _opt_in(organization)
        assert ledger.get_account(organization) is None
        client = MagicMock(spec=["smembers", "spop"])
        client.smembers.return_value = set()
        client.spop.side_effect = [[str(organization.pk)], []]
        # The integrated ledger owns usage emission; the drain has no emission flag.
        with patch.object(
            settlement, "ensure_account_pushed", wraps=ledger.ensure_account_pushed
        ) as ensure:
            counts = settlement.drain_managed_settlements(client=client)
        ensure.assert_called_once_with(organization)
        client.spop.assert_called_with(settlement.NEEDS_PUSH, settlement.PUSH_BATCH)
        assert counts["pushed"] == 1
        assert counts["push_failed"] == 0
        assert ledger.get_account(organization) is not None
        assert gateway.account is not None
        assert gateway.pushes() == [
            (
                "PUT",
                f"/-/orgs/{organization.pk}/credits/granted",
                {
                    "granted_total_micros": 0,
                    "version": 0,
                    "settled_spend_floor_micros": 0,
                },
            )
        ]


@pytest.mark.integration
class TestEntries:
    def test_entries_cursor_and_csv(
        self, ledger, organization, other_org, admin_client
    ):
        start = datetime(2026, 9, 1, tzinfo=UTC)
        made = []
        for i in range(5):
            made.append(
                _entry(
                    ledger,
                    organization,
                    "spend",
                    -(i + 1) * 1000,
                    start + timedelta(hours=i),
                    request_id=f"req-{i}",
                    model="openai/gpt-6-sol",
                    provider="openrouter",
                    upstream_cost_micros=(i + 1) * 900,
                    markup_micros=(i + 1) * 100,
                    markup_bps=500,
                    cost_source="provider_reported",
                )
            )
        # Two entries at one instant, split across the first page boundary
        # (limit 2, after the newest entry): the cursor orders them by id.
        tie = start + timedelta(hours=9)
        made.append(_entry(ledger, organization, "grant", 7 * MICROS, tie, note="a"))
        made.append(_entry(ledger, organization, "grant", 8 * MICROS, tie, note="b"))
        made.append(
            _entry(ledger, organization, "topup", MICROS, tie + timedelta(hours=1))
        )
        _entry(ledger, other_org, "grant", MICROS, tie, note="other org")
        newest_first = sorted(made, key=lambda e: (e.occurred_at, e.id), reverse=True)

        seen, cursor = [], None
        for _ in range(10):
            query = {"limit": 2, **({"cursor": cursor} if cursor else {})}
            page = _result(admin_client.get(f"{BASE}entries/", query))
            assert len(page["entries"]) <= 2
            seen.extend(page["entries"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert [e["id"] for e in seen] == [str(e.id) for e in newest_first]
        spend = next(e for e in seen if e["request_id"] == "req-4")
        assert spend["amount_usd"] == -0.005
        assert spend["markup_usd"] == 0.0005
        assert spend["markup_bps"] == 500
        assert spend["kind"] == "spend"
        for entry in seen:
            assert not [k for k in entry if k.startswith(INTERNAL_FIELDS)], entry

        page = _result(
            admin_client.get(
                f"{BASE}entries/",
                {
                    "kind": "spend",
                    "from": "2026-09-01T01:00:00Z",
                    "to": "2026-09-01T03:00:00Z",
                },
            )
        )
        assert [e["request_id"] for e in page["entries"]] == ["req-2", "req-1"]

        response = admin_client.get(f"{BASE}entries/", {"format": "csv"})
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment" in response["Content-Disposition"]
        rows = _csv_rows(response)
        assert rows[0] == CSV_COLUMNS
        assert len(rows) == 1 + len(made)
        grants = sorted(row[2] for row in rows[1:] if row[1] == "grant")
        assert grants == ["7.000000", "8.000000"]
        req4 = next(row for row in rows[1:] if row[3] == "req-4")
        assert req4[1:8] == [
            "spend",
            "-0.005000",
            "req-4",
            "openai/gpt-6-sol",
            "openrouter",
            "0.000500",
            "500",
        ]

    def test_entries_csv_neutralises_formula_cells(
        self, ledger, organization, admin_client
    ):
        at = datetime(2026, 9, 2, tzinfo=UTC)
        _entry(
            ledger,
            organization,
            "spend",
            -2500,
            at,
            request_id="req-cf",
            model="@cf/meta/llama-4",
            provider="workers-ai",
        )
        _entry(ledger, organization, "topup", 500, at, stripe_ref="=HYPERLINK(1)")

        rows = _csv_rows(admin_client.get(f"{BASE}entries/", {"format": "csv"}))

        cf = next(row for row in rows[1:] if row[3] == "req-cf")
        assert cf[4] == "'@cf/meta/llama-4"
        # Amounts are numbers, not text: a spend's minus sign is kept as is.
        assert cf[2] == "-0.002500"
        topup = next(row for row in rows[1:] if row[1] == "topup")
        assert topup[9] == "'=HYPERLINK(1)"

    def test_csv_that_fails_mid_stream_says_so(
        self, ledger, organization, admin_client
    ):
        at = datetime(2026, 9, 3, tzinfo=UTC)
        for i in range(3):
            _entry(ledger, organization, "grant", MICROS, at + timedelta(minutes=i))
        from agentcc.views import managed_credits as views

        real_row = views.entry_csv_row
        calls = []

        def fail_on_third(entry):
            calls.append(entry)
            if len(calls) == 3:
                raise views.DatabaseError("connection lost")
            return real_row(entry)

        with patch.object(views, "entry_csv_row", fail_on_third):
            rows = _csv_rows(admin_client.get(f"{BASE}entries/", {"format": "csv"}))

        assert rows[0] == CSV_COLUMNS
        assert len(rows) == 4
        assert rows[-1] == ["# export failed after 2 rows; download it again"]

    @pytest.mark.parametrize("fmt", ["json", "csv"])
    def test_bad_cursor_is_a_json_400(self, ledger, organization, admin_client, fmt):
        response = admin_client.get(
            f"{BASE}entries/", {"cursor": "not-a-cursor", "format": fmt}
        )
        assert response.status_code == 400
        assert response["Content-Type"].startswith("application/json")
        assert "cursor" in response.content.decode()

    def test_unknown_kind_and_inverted_range_are_400(
        self, ledger, organization, admin_client
    ):
        assert admin_client.get(f"{BASE}entries/", {"kind": "bonus"}).status_code == 400
        inverted = {"from": "2026-10-02T00:00:00Z", "to": "2026-10-01T00:00:00Z"}
        assert admin_client.get(f"{BASE}entries/", inverted).status_code == 400


@pytest.mark.integration
class TestStatement:
    def test_statement_rebuilds_exactly_from_entries(
        self, ledger, organization, other_org, admin_client
    ):
        before = datetime(2026, 8, 31, 23, 59, 59, 999999, tzinfo=UTC)
        first = datetime(2026, 9, 1, tzinfo=UTC)
        last = datetime(2026, 9, 30, 23, 59, 59, 999999, tzinfo=UTC)
        after = datetime(2026, 10, 1, tzinfo=UTC)
        rows = [
            ("grant", 50 * MICROS, before, {}),
            ("spend", -1_234_567, before, {"model": "m/a"}),
            ("topup", 25 * MICROS, first, {"stripe_ref": "cs_1"}),
            ("grant", 3_000_001, first + timedelta(days=3), {}),
            ("grant", 1_000_002, first + timedelta(days=8), {}),
            ("refund", 11_815, first + timedelta(days=4), {"request_id": "r1"}),
            ("adjustment", -700_003, first + timedelta(days=5), {}),
            ("expiry", -1_000_000, first + timedelta(days=6), {}),
            ("spend", -11_815, first + timedelta(days=1), {"model": "m/a"}),
            ("spend", -2_000_009, first + timedelta(days=2), {"model": "m/b"}),
            ("spend", -333_333, last, {"model": "m/a"}),
            ("grant", 99 * MICROS, after, {}),
        ]
        for kind, micros, at, fields in rows:
            _entry(
                ledger,
                organization,
                kind,
                micros,
                at,
                upstream_cost_micros=-micros * 20 // 21 if kind == "spend" else None,
                markup_micros=-micros // 21 if kind == "spend" else None,
                **fields,
            )
        _entry(ledger, other_org, "grant", 7 * MICROS, first)

        result = _result(admin_client.get(f"{BASE}statement/", {"month": "2026-09"}))

        def usd(micros):
            return float(Decimal(micros) / MICROS)

        in_month = [r for r in rows if first <= r[2] < after]
        opening = sum(r[1] for r in rows if r[2] < first)
        closing = opening + sum(r[1] for r in in_month)
        assert result["month"] == "2026-09"
        assert result["opening_balance"] == usd(opening)
        assert result["closing_balance"] == usd(closing)
        assert result["credits_by_kind"] == {
            "topup": usd(25 * MICROS),
            "grant": usd(3_000_001 + 1_000_002),
            "refund": usd(11_815),
            "adjustment": usd(-700_003),
            "expiry": usd(-1_000_000),
            "topup_reversal": 0,
            "dispute_hold": 0,
            "dispute_release": 0,
        }
        by_model = {line["model"]: line for line in result["spend_by_model"]}
        assert set(by_model) == {"m/a", "m/b"}
        assert by_model["m/a"]["count"] == 2
        assert by_model["m/a"]["spend_usd"] == usd(11_815 + 333_333)
        assert by_model["m/a"]["markup_usd"] == usd(11_815 // 21 + 333_333 // 21)
        assert "upstream_usd" not in by_model["m/a"]
        assert by_model["m/b"]["spend_usd"] == usd(2_000_009)
        # The statement balances: opening + credits - spend = closing.
        credits = sum(Decimal(str(v)) for v in result["credits_by_kind"].values())
        spend = sum(
            Decimal(str(line["spend_usd"])) for line in result["spend_by_model"]
        )
        assert Decimal(str(result["opening_balance"])) + credits - spend == Decimal(
            str(result["closing_balance"])
        )

        rows_csv = _csv_rows(
            admin_client.get(f"{BASE}statement/", {"month": "2026-09", "format": "csv"})
        )
        assert rows_csv[0] == [
            "line",
            "key",
            "count",
            "amount_usd",
            "markup_usd",
            "estimated",
        ]
        lines = {(row[0], row[1]): row for row in rows_csv[1:]}
        assert lines[("opening_balance", "")][3] == f"{Decimal(opening) / MICROS:.6f}"
        assert lines[("closing_balance", "")][3] == f"{Decimal(closing) / MICROS:.6f}"
        assert lines[("spend", "m/b")][3] == "2.000009"
        assert lines[("credit", "adjustment")][3] == "-0.700003"

    def test_statement_marks_spend_billed_on_an_estimate(
        self, ledger, organization, admin_client
    ):
        """A spend billed on an estimate (``usage_estimated``: the upstream
        reported no usage, so the gateway billed its own count) is its own
        ``spend_by_model`` line, marked ``estimated``, in the JSON and in the
        CSV's last column; every other line's cell there is empty."""
        first = datetime(2026, 9, 1, tzinfo=UTC)
        for micros, estimated, model, day in (
            (-1_000, False, "m/a", 1),
            (-2_000, True, "m/a", 2),
            (-4_000, True, "m/a", 3),
            (-8_000, False, "m/a", 4),
            (-16_000, False, "m/b", 5),
        ):
            _entry(
                ledger,
                organization,
                "spend",
                micros,
                first + timedelta(days=day),
                model=model,
                markup_micros=0,
                usage_estimated=estimated,
            )

        result = _result(admin_client.get(f"{BASE}statement/", {"month": "2026-09"}))

        lines = {
            (line["model"], line["estimated"]): line
            for line in result["spend_by_model"]
        }
        assert set(lines) == {("m/a", False), ("m/a", True), ("m/b", False)}
        assert (lines[("m/a", True)]["count"], lines[("m/a", True)]["spend_usd"]) == (
            2,
            0.006,
        )
        assert (lines[("m/a", False)]["count"], lines[("m/a", False)]["spend_usd"]) == (
            2,
            0.009,
        )
        rows = _csv_rows(
            admin_client.get(f"{BASE}statement/", {"month": "2026-09", "format": "csv"})
        )
        assert rows[0][-1] == "estimated"
        assert sorted((r[1], r[3], r[5]) for r in rows[1:] if r[0] == "spend") == [
            ("m/a", "0.006000", "true"),
            ("m/a", "0.009000", "false"),
            ("m/b", "0.016000", "false"),
        ]
        assert {r[5] for r in rows[1:] if r[0] != "spend"} == {""}

    def test_statement_and_entries_carry_refunds_and_disputes_of_top_ups(
        self, ledger, organization, admin_client
    ):
        """The ledger's credit-side kinds: a Stripe refund of a top-up
        (topup_reversal), a dispute's hold (dispute_hold) and its release
        (dispute_release). Each is a credits_by_kind line, so the statement
        still balances (opening + credits - spend = closing); the entries list
        shows and filters them, and both answers match their serializers."""
        from agentcc.serializers import managed_credits as shapes

        payment = ledger.TopupPayment(
            source=ledger.TopupSource.CHECKOUT_SESSION,
            object_id="cs_2a_kinds",
            amount_micros=20 * MICROS,
            mode=ledger.StripeMode.TEST,
            payment_intent_id="pi_2a_kinds",
        )
        ledger.fulfil_topup(organization, payment=payment)
        _spend(ledger, organization, "req-2a-kinds", 1_500_000)
        ledger.reverse_topup(
            organization,
            refund_id="re_2a_kinds",
            payment_intent_id="pi_2a_kinds",
            amount_micros=5 * MICROS,
            mode=ledger.StripeMode.TEST,
        )
        ledger.hold_dispute(
            organization,
            dispute_id="dp_2a_kinds",
            payment_intent_id="pi_2a_kinds",
            amount_micros=3 * MICROS,
            mode=ledger.StripeMode.TEST,
        )
        ledger.release_dispute(
            organization, dispute_id="dp_2a_kinds", mode=ledger.StripeMode.TEST
        )
        month = datetime.now(UTC).strftime("%Y-%m")

        statement = admin_client.get(f"{BASE}statement/", {"month": month})
        result = _result(statement)

        assert result["credits_by_kind"]["topup_reversal"] == -5.0
        assert result["credits_by_kind"]["dispute_hold"] == -3.0
        assert result["credits_by_kind"]["dispute_release"] == 3.0
        credits = sum(Decimal(str(v)) for v in result["credits_by_kind"].values())
        spend = sum(
            Decimal(str(line["spend_usd"])) for line in result["spend_by_model"]
        )
        closing = Decimal(str(result["closing_balance"]))
        assert Decimal(str(result["opening_balance"])) + credits - spend == closing
        assert closing == Decimal("13.5")
        assert shapes.ManagedCreditStatementResponseSerializer(
            data=statement.json()
        ).is_valid()

        entries = admin_client.get(
            f"{BASE}entries/", {"kind": "topup_reversal,dispute_hold,dispute_release"}
        )
        rows = _result(entries)["entries"]
        assert sorted(row["kind"] for row in rows) == [
            "dispute_hold",
            "dispute_release",
            "topup_reversal",
        ]
        validated = shapes.ManagedCreditEntriesResponseSerializer(data=entries.json())
        assert validated.is_valid(), validated.errors

    def test_empty_month_and_bad_month(self, ledger, organization, admin_client):
        result = _result(admin_client.get(f"{BASE}statement/", {"month": "2025-01"}))
        assert result["opening_balance"] == 0
        assert result["closing_balance"] == 0
        assert result["spend_by_model"] == []
        assert set(result["credits_by_kind"].values()) == {0}
        for bad in ("2026-13", "2026-9", "september", "", "0000-01", "9999-12"):
            response = admin_client.get(f"{BASE}statement/", {"month": bad})
            assert response.status_code == 400, bad


# --- checkout and settings -----------------------------------------------------


def _install_checkout(create_session, out_of_range=None, fulfil=None):
    """Stand in for cloud billing's checkout module, absent from this tree: the one
    interface checkout reaches cloud billing through. ``create_session`` opens
    a hosted Checkout session; ``fulfil`` is the landing-page fulfilment that
    re-fetches the session and credits it through the ledger's ``fulfil_topup``
    with its ``payment_intent_id``."""
    module = types.ModuleType(CHECKOUT_MODULE)
    module.create_session = create_session
    if out_of_range is not None:
        module.TopupAmountOutOfRange = out_of_range
    if fulfil is not None:
        module.fulfil = fulfil
    parents = {
        "ee.cloud": types.ModuleType("ee.cloud"),
        "ee.cloud.billing": types.ModuleType("ee.cloud.billing"),
    }
    return stand_in_modules({**parents, CHECKOUT_MODULE: module})


def _fulfil_session(ledger, org, session_id, micros):
    """Credit a paid Checkout session as cloud billing does: through fulfil_topup,
    naming the session's PaymentIntent, in test mode."""
    return ledger.fulfil_topup(
        org,
        payment=ledger.TopupPayment(
            source=ledger.TopupSource.CHECKOUT_SESSION,
            object_id=session_id,
            amount_micros=micros,
            mode=ledger.StripeMode.TEST,
            payment_intent_id=f"pi_{session_id}",
        ),
    )


def _bind_checkout(org, session_id, amount_usd="25.00", livemode=False):
    """Bind a Checkout session to ``org`` as POST checkout/ does when cloud
    billing returns it (MUST-FIX G): the landing page fulfils only a session
    bound to the calling org."""
    from agentcc.services import checkout_verification

    return checkout_verification.record_binding(
        org, session_id, Decimal(amount_usd), livemode, None
    )


def _paid_checkout_read(session_id, **fields):
    """Stripe's answer for a complete, paid, test-mode USD 25.00 session: the
    landing page's own verification read, stood in (Stripe is never called)."""
    from agentcc.services.checkout_verification import CheckoutSessionState

    return CheckoutSessionState(
        **{
            "id": session_id,
            "mode": "payment",
            "status": "complete",
            "payment_status": "paid",
            "livemode": False,
            "currency": "usd",
            "amount_total": 2500,
            **fields,
        }
    )


# Synthetic Stripe Checkout Session identifiers. The status route answers only
# cs_test_ or cs_live_ followed by at least 24 letters or digits; any other
# value is a 404 before the view runs.
SESSION_CREATED = "cs_test_SyntheticCreatedSession00000"
SESSION_PAID = "cs_test_SyntheticPaidSession00000000"
SESSION_UNPAID = "cs_test_SyntheticUnpaidSession00000"
SESSION_OPEN = "cs_test_SyntheticOpenSession00000000"
SESSION_THEIRS = "cs_test_SyntheticTheirSession0000000"
SESSION_STATUS = "cs_test_SyntheticStatusSession00000"

CHECKOUT_BODY = {
    "amount_usd": "25.00",
    "success_url": "https://console.example.test/dashboard/gateway/billing?topup=success",
    "cancel_url": "https://console.example.test/dashboard/gateway/billing?topup=cancel",
}


@pytest.mark.integration
class TestCheckout:
    def test_checkout_delegates(self, ledger, organization, admin_client, gateway):
        create_session = MagicMock(
            return_value={
                "checkout_url": "https://checkout.test/s/cs_test_SyntheticDelegatedSession0000",
                "session_id": "cs_test_SyntheticDelegatedSession0000",
            }
        )
        with _install_checkout(create_session):
            response = admin_client.post(
                f"{BASE}checkout/", CHECKOUT_BODY, format="json"
            )
            summary = _result(admin_client.get(BASE))

        assert response.status_code == 200, response.content
        assert response.json()["result"] == {
            "checkout_url": "https://checkout.test/s/cs_test_SyntheticDelegatedSession0000",
            "session_id": "cs_test_SyntheticDelegatedSession0000",
        }
        args, kwargs = create_session.call_args
        org, user, amount, success, cancel = args
        assert org == organization
        assert user.is_authenticated
        assert amount == Decimal("25.00")
        assert (success, cancel) == (
            CHECKOUT_BODY["success_url"],
            CHECKOUT_BODY["cancel_url"],
        )
        assert summary["billing_available"] is True

    def test_checkout_takes_the_session_shape_bl_stripe_exports(
        self, ledger, organization, admin_client, gateway
    ):
        """The ledger interface's seam: cloud billing's
        ``create_session(org, user, amount_usd, success_url, cancel_url)``
        returns ``{url, session_id}``. The console answers it as its own
        ``{checkout_url, session_id}``; nothing is credited."""
        create_session = MagicMock(
            return_value={
                "url": "https://checkout.test/s/cs_test_SyntheticExportedSession00000",
                "session_id": "cs_test_SyntheticExportedSession00000",
            }
        )
        with _install_checkout(create_session):
            response = admin_client.post(
                f"{BASE}checkout/", CHECKOUT_BODY, format="json"
            )

        assert response.status_code == 200, response.content
        assert response.json()["result"] == {
            "checkout_url": "https://checkout.test/s/cs_test_SyntheticExportedSession00000",
            "session_id": "cs_test_SyntheticExportedSession00000",
        }
        assert ledger.ledger_entries(organization).count() == 0

    def test_checkout_amount_out_of_range_is_400(
        self, ledger, organization, admin_client
    ):
        class TopupAmountOutOfRange(ValueError):
            pass

        def refuse(*args):
            raise TopupAmountOutOfRange("$24 is below the $25 minimum")

        with _install_checkout(refuse, TopupAmountOutOfRange):
            response = admin_client.post(
                f"{BASE}checkout/", {**CHECKOUT_BODY, "amount_usd": "24"}, format="json"
            )
        assert response.status_code == 400
        assert response.json()["code"] == "topup_amount_out_of_range"

    def test_checkout_without_cloud_billing_is_a_truthful_503(
        self, ledger, organization, admin_client, gateway
    ):
        """Until cloud billing is installed, a top-up answers 503 billing_unavailable:
        never a session URL, never a credit."""
        with stand_in_modules({CHECKOUT_MODULE: None}):
            response = admin_client.post(
                f"{BASE}checkout/", CHECKOUT_BODY, format="json"
            )
            summary = _result(admin_client.get(BASE))
        assert response.status_code == 503
        assert response.json()["code"] == "billing_unavailable"
        assert "checkout_url" not in response.content.decode()
        assert summary["billing_available"] is False
        assert ledger.ledger_entries(organization).count() == 0

    @pytest.mark.parametrize(
        "returned",
        [
            None,
            {},
            {"session_id": "cs_x"},
            {"checkout_url": "", "session_id": "x"},
            {"url": "", "session_id": "x"},
            {"url": "https://checkout.test/s/cs_y", "session_id": ""},
        ],
    )
    def test_checkout_never_reports_a_session_it_did_not_get(
        self, ledger, organization, admin_client, gateway, returned
    ):
        with _install_checkout(MagicMock(return_value=returned)):
            response = admin_client.post(
                f"{BASE}checkout/", CHECKOUT_BODY, format="json"
            )
        assert response.status_code == 503, response.content
        assert response.json()["code"] == "billing_unavailable"
        assert ledger.ledger_entries(organization).count() == 0

    def test_checkout_creates_no_credit(
        self, ledger, organization, admin_client, gateway
    ):
        """Starting a Checkout session credits nothing: credit comes only
        from fulfilment once Stripe says the session is paid."""
        create_session = MagicMock(
            return_value={
                "checkout_url": "https://checkout.test/s/" + SESSION_CREATED,
                "session_id": SESSION_CREATED,
            }
        )
        with _install_checkout(create_session):
            response = admin_client.post(
                f"{BASE}checkout/", CHECKOUT_BODY, format="json"
            )
            status_ = _result(admin_client.get(f"{BASE}checkout/{SESSION_CREATED}/"))
            # Another shape names no checkout session: 404 before the view.
            other_shape = admin_client.get(f"{BASE}checkout/cs_2/")
        assert response.status_code == 200
        assert status_["credited"] is False
        assert other_shape.status_code == 404
        assert ledger.ledger_entries(organization).count() == 0
        assert ledger.balance(organization)["granted_total_micros"] == 0

    def test_landing_page_fulfils_through_cloud_billing(
        self, ledger, organization, admin_client, gateway
    ):
        """The landing page's status read runs cloud billing's fulfil for a
        session bound to the org that Stripe says is paid, which credits
        through fulfil_topup once, with the session's PaymentIntent. A session
        that is unbound or not paid never reaches fulfil (MUST-FIX G), and a
        fulfil that refuses leaves the session pending."""
        from agentcc.services import checkout_verification

        _bind_checkout(organization, SESSION_PAID)
        _bind_checkout(organization, SESSION_UNPAID)

        def read(session_id):
            if session_id == SESSION_PAID:
                return _paid_checkout_read(session_id)
            return _paid_checkout_read(session_id, payment_status="unpaid")

        def fulfil(org, session_id):
            if session_id == SESSION_PAID:
                _fulfil_session(ledger, org, session_id, 25 * MICROS)
                return
            raise ValueError("session is not paid")

        fulfilment = MagicMock(side_effect=fulfil)
        with (
            _install_checkout(MagicMock(), fulfil=fulfilment),
            patch.object(
                checkout_verification, "read_checkout_session", side_effect=read
            ),
        ):
            paid = _result(admin_client.get(f"{BASE}checkout/{SESSION_PAID}/"))
            again = _result(admin_client.get(f"{BASE}checkout/{SESSION_PAID}/"))
            unpaid = _result(admin_client.get(f"{BASE}checkout/{SESSION_UNPAID}/"))
            # Another shape names no checkout session: 404, no fulfilment.
            other_shape = admin_client.get(f"{BASE}checkout/cs_paid/")

        assert paid == {
            "session_id": SESSION_PAID,
            "status": "complete",
            "credited": True,
        }
        assert again["credited"] is True
        assert unpaid == {
            "session_id": None,
            "status": "pending",
            "credited": False,
        }
        # The session's PaymentIntent succeeding afterwards
        # (payment_intent.succeeded) is the same money and never credits again.
        replay = ledger.fulfil_topup(
            organization,
            payment=ledger.TopupPayment(
                source=ledger.TopupSource.PAYMENT_INTENT,
                object_id=f"pi_{SESSION_PAID}",
                amount_micros=25 * MICROS,
                mode=ledger.StripeMode.TEST,
            ),
            stripe_event_id="evt_pi_cs_paid",
        )
        assert replay.created is False
        assert other_shape.status_code == 404
        assert [call.args[1] for call in fulfilment.call_args_list] == [
            SESSION_PAID,
        ]
        topups = ledger.ledger_entries(organization).filter(kind="topup")
        assert [(e.stripe_ref, e.amount_micros) for e in topups] == [
            (SESSION_PAID, 25 * MICROS)
        ]

    def test_no_route_an_org_admin_reaches_writes_credit(
        self, ledger, organization, admin_client, gateway
    ):
        """Credit is written only by fulfilment (webhook or landing page) and
        staff: every route an org Owner or Admin's browser can call, with any
        body, leaves the ledger without a credit entry."""
        _opt_in(organization)
        bodies = [
            {"amount_usd": "1000", "note": "x", "idempotency_key": "k"},
            {"granted_total_micros": 10**12, "version": 99},
            CHECKOUT_BODY,
        ]
        create_session = MagicMock(
            return_value={
                "checkout_url": "https://checkout.test/s/" + SESSION_STATUS,
                "session_id": SESSION_STATUS,
            }
        )
        statuses = {}
        with _install_checkout(create_session):
            for path in (
                "",
                "entries/",
                "statement/",
                "checkout/",
                f"checkout/{SESSION_STATUS}/",
                "checkout/cs_3/",
                "settings/",
                "grant/",
                "adjust/",
                "refund/",
            ):
                for method in ("put", "post", "patch"):
                    for body in bodies:
                        response = getattr(admin_client, method)(
                            f"{BASE}{path}", body, format="json"
                        )
                        statuses.setdefault(path, set()).add(response.status_code)
        # The status route takes no writes; another shape is answered 404
        # before the view runs.
        assert statuses[f"checkout/{SESSION_STATUS}/"] == {405}
        assert statuses["checkout/cs_3/"] == {404}
        credits = ledger.ledger_entries(organization).filter(
            kind__in=["topup", "grant", "adjustment", "refund"]
        )
        assert credits.count() == 0
        assert ledger.balance(organization)["granted_total_micros"] == 0
        assert [push[2]["granted_total_micros"] for push in gateway.pushes()] in (
            [],
            [0],
        )

    def test_checkout_status_reads_the_org_ledger(
        self, ledger, organization, other_org, admin_client, gateway
    ):
        _fulfil_session(ledger, organization, SESSION_PAID, 25 * MICROS)
        _fulfil_session(ledger, other_org, SESSION_THEIRS, 25 * MICROS)
        _fulfil_session(ledger, organization, "cs_paid", 25 * MICROS)

        paid = _result(admin_client.get(f"{BASE}checkout/{SESSION_PAID}/"))
        open_ = _result(admin_client.get(f"{BASE}checkout/{SESSION_OPEN}/"))
        theirs = _result(admin_client.get(f"{BASE}checkout/{SESSION_THEIRS}/"))
        # Another shape names no checkout session, even one with ledger
        # history: 404 before the view reads the ledger.
        other_shape = admin_client.get(f"{BASE}checkout/cs_paid/")

        assert paid == {
            "session_id": SESSION_PAID,
            "status": "complete",
            "credited": True,
        }
        assert open_ == {
            "session_id": None,
            "status": "pending",
            "credited": False,
        }
        assert theirs["credited"] is False
        assert other_shape.status_code == 404
        assert "cs_paid" not in other_shape.content.decode()


@pytest.mark.integration
class TestRoles:
    @pytest.mark.parametrize(
        "path",
        [
            "",
            "entries/",
            "statement/?month=2026-10",
            f"checkout/{SESSION_STATUS}/",
            "settings/",
        ],
    )
    def test_member_reads_nothing(
        self, ledger, organization, member_client, gateway, path
    ):
        """billing-ledger-read-v1 section 2: balances and entries are Owner
        and Admin reads; a Member gets 403 and the gateway is never asked."""
        ledger.grant(organization, MICROS, idempotency_key="g", actor=None, note="n")

        response = member_client.get(f"{BASE}{path}")

        assert response.status_code == 403, response.content
        assert "1.000000" not in response.content.decode()
        if path.startswith("checkout/"):
            # Another shape is answered 404 before permissions are checked.
            other_shape = member_client.get(f"{BASE}checkout/cs_1/")
            assert other_shape.status_code == 404
            assert "1.000000" not in other_shape.content.decode()
        assert gateway.calls == []

    @pytest.mark.parametrize("level", [Level.OWNER, Level.ADMIN, Level.MEMBER])
    @pytest.mark.parametrize("opted_in", [False, True])
    def test_summary_role_checks_unchanged(
        self, ledger, organization, make_client, gateway, level, opted_in
    ):
        if opted_in:
            _opt_in(organization)
        client = make_client(organization, level)
        allowed = level in (Level.OWNER, Level.ADMIN)
        assert client.get(BASE).status_code == (200 if allowed else 403)
        assert client.get(f"{BASE}entries/").status_code == (200 if allowed else 403)
        assert ledger.get_account(organization) is None
        assert gateway.pushes() == []
        if not allowed:
            assert gateway.calls == []


@pytest.mark.integration
class TestSettings:
    def test_member_cannot_checkout_or_patch(
        self, ledger, organization, admin_client, member_client, gateway
    ):
        create_session = MagicMock()
        with _install_checkout(create_session):
            checkout = member_client.post(
                f"{BASE}checkout/", CHECKOUT_BODY, format="json"
            )
        settings_put = member_client.put(
            f"{BASE}settings/",
            {"low_balance_threshold_usd": "5", "auto_reload": {"enabled": False}},
            format="json",
        )

        assert checkout.status_code == 403
        assert settings_put.status_code == 403
        create_session.assert_not_called()
        account = ledger.get_account(organization)
        assert account is None or account.low_balance_threshold_micros is None
        assert admin_client.get(f"{BASE}settings/").status_code == 200

    def test_admin_saves_settings(
        self, ledger, organization, admin_client, gateway, auto_reload_available
    ):
        ledger.ManagedCreditAccount.objects.create(
            organization=organization,
            autoreload_payment_method_id="pm_synthetic_settings",
        )
        _opt_in(organization)
        body = {
            "low_balance_threshold_usd": "5.5",
            "auto_reload": {
                "enabled": True,
                "threshold_usd": "10",
                "amount_usd": "50",
                "consent_version": AUTO_RELOAD_CONSENT_VERSION,
            },
        }

        saved = _result(admin_client.put(f"{BASE}settings/", body, format="json"))

        assert saved["low_balance_threshold_usd"] == 5.5
        assert saved["auto_reload"] == {
            "enabled": True,
            "threshold_usd": 10.0,
            "amount_usd": 50.0,
            "disabled_reason": "",
            "consent_version": AUTO_RELOAD_CONSENT_VERSION,
            "consent_terms": AUTO_RELOAD_CONSENT_TERMS,
        }
        account = ledger.get_account(organization)
        assert account.low_balance_threshold_micros == 5_500_000
        assert account.autoreload_threshold_micros == 10 * MICROS
        assert account.autoreload_amount_micros == 50 * MICROS
        assert account.granted_total_micros == 0 and account.version == 0
        assert _result(admin_client.get(BASE))["auto_reload"]["enabled"] is True

    def test_settings_put_pushes_the_account_it_creates(
        self, ledger, organization, other_org, admin_client, make_client, gateway
    ):
        # An opted-in org the gateway holds no account for. The PUT creates the
        # org's ledger account at version 0, which GET / then reads as in step
        # with the gateway's zeros, so the PUT pushes the account itself.
        _opt_in(organization)
        body = {"low_balance_threshold_usd": "5", "auto_reload": {"enabled": False}}

        _result(admin_client.put(f"{BASE}settings/", body, format="json"))

        pushes = gateway.pushes()
        assert [push[1] for push in pushes] == [
            f"/-/orgs/{organization.pk}/credits/granted"
        ]
        assert pushes[0][2]["granted_total_micros"] == 0
        assert gateway.account is not None
        # The account exists now: a second PUT and the summary push nothing.
        _result(admin_client.put(f"{BASE}settings/", body, format="json"))
        assert _result(admin_client.get(BASE))["gateway_warning"] is None
        assert len(gateway.pushes()) == 1

        # An org that has not opted in gets its settings row and no push.
        other_admin = make_client(other_org, Level.ADMIN)
        _result(other_admin.put(f"{BASE}settings/", body, format="json"))
        assert ledger.get_account(other_org).low_balance_threshold_micros == 5 * MICROS
        assert len(gateway.pushes()) == 1

    def test_settings_put_push_coalesces_with_the_one_in_flight(
        self, ledger, organization, admin_client, gateway
    ):
        """One granted push in flight per org (``request_push``).
        While another worker holds the org's push lock, the PUT that creates
        the account marks the org dirty for that worker instead of sending a
        second, concurrent push of its own."""
        from ee.usage.services import gateway_push

        _opt_in(organization)
        r = gateway_push._redis()
        lock = gateway_push._LOCK.format(org=str(organization.pk))
        dirty = gateway_push._DIRTY.format(org=str(organization.pk))
        r.set(lock, "another-worker", px=30_000)
        body = {"low_balance_threshold_usd": "5", "auto_reload": {"enabled": False}}
        try:
            saved = _result(admin_client.put(f"{BASE}settings/", body, format="json"))

            assert saved["low_balance_threshold_usd"] == 5.0
            assert ledger.get_account(organization) is not None
            assert gateway.pushes() == []
            assert r.exists(dirty)
        finally:
            r.delete(lock, dirty)

    def test_settings_put_saves_when_the_push_fails(
        self, ledger, organization, admin_client, gateway
    ):
        # Saving a threshold never depends on the gateway: the push fails open.
        _opt_in(organization)
        gateway.error = GatewayClientError("Cannot connect to gateway")
        body = {"low_balance_threshold_usd": "7", "auto_reload": {"enabled": False}}

        saved = _result(admin_client.put(f"{BASE}settings/", body, format="json"))

        assert saved["low_balance_threshold_usd"] == 7.0
        assert ledger.get_account(organization).low_balance_threshold_micros == (
            7 * MICROS
        )
        assert len(gateway.pushes()) == 1
        assert gateway.account is None
        assert _result(admin_client.get(BASE))["gateway_warning"] == "unreachable"

    def test_auto_reload_needs_threshold_and_minimum_amount(
        self, ledger, organization, admin_client, auto_reload_available
    ):
        _opt_in(organization)
        missing = {"low_balance_threshold_usd": None, "auto_reload": {"enabled": True}}
        too_small = {
            "low_balance_threshold_usd": None,
            "auto_reload": {
                "enabled": True,
                "threshold_usd": "1",
                "amount_usd": "24.99",
                "consent_version": AUTO_RELOAD_CONSENT_VERSION,
            },
        }
        assert (
            admin_client.put(f"{BASE}settings/", missing, format="json").status_code
            == 400
        )
        response = admin_client.put(f"{BASE}settings/", too_small, format="json")
        assert response.status_code == 400
        assert response.json()["code"] == "topup_amount_out_of_range"


# --- staff writes ----------------------------------------------------------------


@pytest.mark.integration
class TestStaffWrites:
    def test_non_staff_cannot_grant(
        self, ledger, organization, admin_client, member_client, gateway
    ):
        body = {"amount_usd": "10", "note": "pilot", "idempotency_key": "k1"}
        for client in (admin_client, member_client):
            assert client.post(f"{BASE}grant/", body, format="json").status_code == 403
            assert (
                client.post(
                    f"{BASE}adjust/",
                    {"amount_usd": "-1", "reason": "x", "idempotency_key": "k2"},
                    format="json",
                ).status_code
                == 403
            )
            assert (
                client.post(
                    f"{BASE}refund/", {"request_id": "r", "reason": "x"}, format="json"
                ).status_code
                == 403
            )
        wrong_token = APIClient()
        wrong_token.credentials(HTTP_AUTHORIZATION="Bearer not-the-admin-token")
        with patch.object(gateway_permissions, "AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
            response = wrong_token.post(
                f"{BASE}grant/", {**body, "org_id": str(organization.pk)}, format="json"
            )
        assert response.status_code in (401, 403)
        assert ledger.balance(organization)["granted_total_micros"] == 0

    def test_staff_grant_is_idempotent_and_shows_the_key_sent(
        self, ledger, organization, staff_client, gateway
    ):
        body = {"amount_usd": "12.5", "note": "pilot", "idempotency_key": "launch-1"}

        first = staff_client.post(f"{BASE}grant/", body, format="json")
        again = staff_client.post(f"{BASE}grant/", body, format="json")
        reused = staff_client.post(
            f"{BASE}grant/", {**body, "amount_usd": "99"}, format="json"
        )

        assert first.status_code == 201, first.content
        made = first.json()["result"]
        assert made["created"] is True
        assert made["idempotency_key"] == "launch-1"
        assert made["entry"]["kind"] == "grant"
        assert made["entry"]["amount_usd"] == 12.5
        assert again.status_code == 200
        assert again.json()["result"]["created"] is False
        assert again.json()["result"]["entry"]["id"] == made["entry"]["id"]
        assert reused.status_code == 409
        assert reused.json()["code"] == "idempotency_key_reused"
        assert ledger.balance(organization)["granted_total_micros"] == 12_500_000

    @pytest.mark.parametrize("credential", ["staff", "admin"])
    @pytest.mark.parametrize("debt_source", ["spend", "receivable"])
    @pytest.mark.parametrize("amount_usd", [1, 2, 3])
    def test_staff_grant_repays_debt_and_reports_the_allocation(
        self,
        ledger,
        organization,
        staff_client,
        token_client,
        gateway,
        credential,
        debt_source,
        amount_usd,
    ):
        """An authorized grant pays any debt first, then credits the remainder."""
        if debt_source == "receivable":
            ledger.fulfil_topup(
                organization,
                payment=ledger.TopupPayment(
                    source=ledger.TopupSource.PAYMENT_INTENT,
                    object_id="pi_staff_grant_receivable",
                    amount_micros=2 * MICROS,
                    mode=ledger.StripeMode.TEST,
                ),
            )
        _spend(ledger, organization, "staff-grant-debt", 2 * MICROS)
        if debt_source == "receivable":
            ledger.reverse_topup(
                organization,
                refund_id="re_staff_grant_receivable",
                payment_intent_id="pi_staff_grant_receivable",
                amount_micros=2 * MICROS,
                mode=ledger.StripeMode.TEST,
            )
        account = ledger.ManagedCreditAccount.objects.get(organization=organization)
        assert account.debt_micros == 2 * MICROS
        assert ledger.freeze_reason(organization) == ledger.RECEIVABLE_OPEN
        client = staff_client if credential == "staff" else token_client
        body = {
            "org_id": str(organization.pk),
            "amount_usd": str(amount_usd),
            "note": "staff debt repayment",
            "idempotency_key": "staff-debt-repayment",
        }

        response = client.post(f"{BASE}grant/", body, format="json")

        assert response.status_code == 201, response.content
        result = response.json()["result"]
        assert result["created"] is True
        assert result["debt_repaid_usd"] == min(amount_usd, 2)
        assert result["entry"]["kind"] == "grant"
        assert result["entry"]["amount_usd"] == amount_usd
        entry = ledger.ManagedCreditEntry.objects.get(pk=result["entry"]["id"])
        assert entry.funded_debt_micros == min(amount_usd, 2) * MICROS
        assert entry.funded_promo_micros == max(amount_usd - 2, 0) * MICROS
        assert entry.funded_commit_micros == entry.funded_purchased_micros == 0
        account.refresh_from_db()
        assert account.debt_micros == max(2 - amount_usd, 0) * MICROS
        assert account.promo_balance_micros == max(amount_usd - 2, 0) * MICROS
        expected_freeze = ledger.RECEIVABLE_OPEN if amount_usd < 2 else ""
        assert ledger.freeze_reason(organization) == expected_freeze
        before_replay = (
            account.version,
            account.entry_seq,
            account.granted_total_micros,
        )
        count = ledger.ledger_entries(organization).count()

        replay = client.post(f"{BASE}grant/", body, format="json")

        assert replay.status_code == 200, replay.content
        repeated = replay.json()["result"]
        assert repeated["created"] is False
        assert repeated["entry"]["id"] == result["entry"]["id"]
        assert repeated["debt_repaid_usd"] == result["debt_repaid_usd"]
        account.refresh_from_db()
        assert (
            account.version,
            account.entry_seq,
            account.granted_total_micros,
        ) == before_replay
        assert ledger.ledger_entries(organization).count() == count

    def test_staff_amounts_are_validated(
        self, ledger, organization, staff_client, gateway
    ):
        too_big = {
            "amount_usd": "1000000000.000001",
            "note": "n",
            "idempotency_key": "b",
        }
        too_precise = {**too_big, "amount_usd": "1.0000001"}
        zero_adjust = {"amount_usd": "0", "reason": "r", "idempotency_key": "z"}
        for path, body in (
            ("grant/", too_big),
            ("grant/", too_precise),
            ("adjust/", zero_adjust),
            ("adjust/", {**zero_adjust, "amount_usd": "-1000000000.5"}),
        ):
            response = staff_client.post(f"{BASE}{path}", body, format="json")
            assert response.status_code == 400, (path, body)
        assert ledger.ledger_entries(organization).count() == 0

    def test_admin_token_names_the_org(
        self, ledger, organization, token_client, gateway
    ):
        body = {"amount_usd": "3", "note": "ops", "idempotency_key": "t-1"}

        missing = token_client.post(f"{BASE}grant/", body, format="json")
        unknown = token_client.post(
            f"{BASE}grant/", {**body, "org_id": str(uuid.uuid4())}, format="json"
        )
        granted = token_client.post(
            f"{BASE}grant/", {**body, "org_id": str(organization.pk)}, format="json"
        )
        adjusted = token_client.post(
            f"{BASE}adjust/",
            {
                "org_id": str(organization.pk),
                "amount_usd": "-0.25",
                "reason": "goodwill reversal",
                "idempotency_key": "t-2",
            },
            format="json",
        )

        assert missing.status_code == 400
        assert missing.json()["code"] == "organization_required"
        assert unknown.status_code == 404
        assert granted.status_code == 201, granted.content
        assert adjusted.status_code == 201, adjusted.content
        assert adjusted.json()["result"]["entry"]["amount_usd"] == -0.25
        assert ledger.balance(organization)["granted_total_micros"] == 2_750_000

    @pytest.mark.parametrize(
        "credential", ["staff-jwt", "admin-token", "session-cookie"]
    )
    @pytest.mark.parametrize("action", ["grant", "adjust", "refund"])
    def test_staff_write_credentials_match_the_published_bearer_scheme(
        self, ledger, organization, gateway, token_client, credential, action
    ):
        user, _ = _user_in(organization, Level.MEMBER, staff=True)
        client = token_client if credential == "admin-token" else APIClient()
        if credential == "staff-jwt":
            login = client.post(
                "/accounts/token/",
                {"email": user.email, "password": "testpassword123"},
                format="json",
            )
            assert login.status_code == 200
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
        elif credential == "session-cookie":
            client.force_login(user)
        _spend(ledger, organization, "credential-refund", MICROS)
        before = list(ledger.ledger_entries(organization).order_by("id").values())
        body = {"amount_usd": "1", "org_id": str(organization.pk)}
        if action == "refund":
            body.update(request_id="credential-refund", reason="credential test")
        else:
            body["idempotency_key"] = "credential-write"
            body["note" if action == "grant" else "reason"] = "credential test"
        response = client.post(f"{BASE}{action}/", body, format="json")
        # 4b5348a0 added the ApiKey challenge; d576d93d still excludes cookies.
        expected = 401 if credential == "session-cookie" else 201
        assert response.status_code == expected, response.content
        if credential == "session-cookie":
            assert response["WWW-Authenticate"] == "ApiKey"
            assert response.json()["code"] == "not_authenticated"
            assert (
                list(ledger.ledger_entries(organization).order_by("id").values())
                == before
            )
        else:
            assert ledger.ledger_entries(organization).count() == len(before) + 1

    @pytest.mark.parametrize("same_org", [False, True], ids=["cross-org", "same-org"])
    @pytest.mark.parametrize("key_type", ["system", "user", "system-without-user"])
    @pytest.mark.parametrize("action", ["grant", "adjust", "refund"])
    def test_org_api_keys_cannot_mint_credit(
        self, ledger, organization, other_org, gateway, same_org, key_type, action
    ):
        """Retired keys fail authentication; personal keys cannot mint any tenant's credit."""
        from accounts.api_key_policy import authenticate_api_key
        from accounts.models.user import OrgApiKey
        from accounts.tests.personal_keys import personal_key_for
        from tfc.middleware.workspace_context import clear_workspace_context

        key_org = organization if same_org else other_org
        staff_user = staff_workspace = None
        if key_type != "system-without-user":
            staff_user, staff_workspace = _user_in(key_org, Level.OWNER, staff=True)
        clear_workspace_context()
        fields = {
            "name": f"d4-{key_type}",
            "api_key": f"api-d4-{uuid.uuid4().hex[:8]}",
            "secret_key": f"secret-d4-{uuid.uuid4().hex[:8]}",
        }
        if key_type == "user":
            key = personal_key_for(
                staff_user, key_org, workspace=staff_workspace, **fields
            )
            admitted = authenticate_api_key(key.api_key, key.secret_key)
            assert admitted.user_id == staff_user.pk
            assert admitted.user.is_staff
        else:
            key = OrgApiKey.objects.create(
                organization=key_org, type="system", user=None, **fields
            )
        # A real spend ensures an accidentally authorized refund could write.
        _spend(ledger, organization, "key-refund", MICROS)
        before = {
            org.pk: list(ledger.ledger_entries(org).order_by("id").values())
            for org in (organization, other_org)
        }
        body = {"amount_usd": "1", "org_id": str(organization.pk)}
        if action == "refund":
            body.update(request_id="key-refund", reason="x")
        else:
            body["idempotency_key"] = "key-mint"
            body["note" if action == "grant" else "reason"] = "x"
        client = APIClient()
        headers = {"HTTP_X_API_KEY": key.api_key, "HTTP_X_SECRET_KEY": key.secret_key}
        if staff_workspace is not None:
            headers["HTTP_X_WORKSPACE_ID"] = str(staff_workspace.pk)
        client.credentials(**headers)
        clear_workspace_context()
        response = client.post(f"{BASE}{action}/", body, format="json")
        for org in (organization, other_org):
            assert (
                list(ledger.ledger_entries(org).order_by("id").values())
                == before[org.pk]
            )
        # Preserve 3cb17834/4b5348a0's contract, disconnected by 5a9cfed0.
        expected = 403 if key_type == "user" else 401
        assert response.status_code == expected, response.content
        if key_type == "user":
            assert response.json()["code"] == "permission_denied"
            assert "WWW-Authenticate" not in response
            assert response.json().get("code") != "api_key_retired"
            assert "create_url" not in response.json()
        else:
            assert response["WWW-Authenticate"] == "ApiKey"
            assert response.json()["code"] == "api_key_retired"
            assert response.json()["create_url"] == "/dashboard/keys"
        clear_workspace_context()

    def test_retired_and_personal_keys_cannot_mint_credit(
        self, ledger, organization, other_org, gateway
    ):
        """Retired system keys fail authentication; a valid staff-owned personal
        key authenticates but cannot grant, adjust or refund credit.
        """
        from accounts.api_key_policy import authenticate_api_key
        from accounts.models.user import OrgApiKey
        from accounts.tests.personal_keys import personal_key_for
        from tfc.middleware.workspace_context import clear_workspace_context

        staff_user, staff_workspace = _user_in(other_org, Level.OWNER, staff=True)
        clear_workspace_context()
        keys = [
            OrgApiKey.objects.create(
                name="d4-system",
                api_key=f"api-d4-sys-{uuid.uuid4().hex[:8]}",
                secret_key=f"secret-d4-sys-{uuid.uuid4().hex[:8]}",
                organization=other_org,
                type="system",
            ),
            personal_key_for(
                staff_user,
                other_org,
                workspace=staff_workspace,
                name="d4-user",
                api_key=f"api-d4-usr-{uuid.uuid4().hex[:8]}",
                secret_key=f"secret-d4-usr-{uuid.uuid4().hex[:8]}",
            ),
        ]
        body = {
            "amount_usd": "1000",
            "note": "x",
            "idempotency_key": "key-mint",
            "org_id": str(organization.pk),
        }
        personal = keys[1]
        admitted = authenticate_api_key(personal.api_key, personal.secret_key)
        assert admitted.user_id == staff_user.pk
        assert admitted.user.is_staff
        for key in keys:
            client = APIClient()
            client.credentials(
                HTTP_X_API_KEY=key.api_key,
                HTTP_X_SECRET_KEY=key.secret_key,
                HTTP_X_WORKSPACE_ID=str(staff_workspace.pk),
            )
            for path, payload in (
                ("grant/", body),
                ("adjust/", {**body, "reason": "x"}),
                ("refund/", {"request_id": "r", "reason": "x"}),
            ):
                clear_workspace_context()
                response = client.post(f"{BASE}{path}", payload, format="json")
                expected = 401 if key.type == "system" else 403
                assert response.status_code == expected, (
                    key.type,
                    path,
                    response.content,
                )
                if key.type == "system":
                    assert response["WWW-Authenticate"] == "ApiKey"
                    assert response.json()["code"] == "api_key_retired"
                    assert response.json()["create_url"] == "/dashboard/keys"
                else:
                    assert response.json()["code"] == "permission_denied"
                    assert "WWW-Authenticate" not in response
                    assert response.json().get("code") != "api_key_retired"
                    assert "create_url" not in response.json()
        clear_workspace_context()
        assert ledger.balance(organization)["granted_total_micros"] == 0
        assert ledger.balance(other_org)["granted_total_micros"] == 0
        assert ledger.ledger_entries(organization).count() == 0
        assert ledger.ledger_entries(other_org).count() == 0

    @pytest.mark.parametrize("action", ["grant", "adjust", "refund"])
    @pytest.mark.parametrize(
        "credential",
        [
            "missing",
            "api-only",
            "secret-only",
            "wrong-secret",
            "unknown-key",
            "bad-bearer",
        ],
    )
    def test_unproven_keys_cannot_disclose_retirement_or_mint_credit(
        self, ledger, organization, other_org, gateway, action, credential
    ):
        """d576d93d's refusal survives key verification, including bearer precedence."""
        from accounts.models.user import OrgApiKey
        from tfc.middleware.workspace_context import clear_workspace_context

        clear_workspace_context()
        key = OrgApiKey.objects.create(
            name="d4-retired-proof",
            api_key=f"api-d4-{uuid.uuid4().hex}",
            secret_key=f"secret-d4-{uuid.uuid4().hex}",
            organization=other_org,
            type="system",
        )
        headers = {"HTTP_X_API_KEY": key.api_key, "HTTP_X_SECRET_KEY": key.secret_key}
        if credential == "missing":
            headers = {}
        elif credential == "api-only":
            headers.pop("HTTP_X_SECRET_KEY")
        elif credential == "secret-only":
            headers.pop("HTTP_X_API_KEY")
        elif credential == "wrong-secret":
            headers["HTTP_X_SECRET_KEY"] = f"wrong-{uuid.uuid4().hex}"
        elif credential == "unknown-key":
            headers["HTTP_X_API_KEY"] = f"unknown-{uuid.uuid4().hex}"
        else:
            # A bad bearer must not fall back to a proven retired key pair.
            headers["HTTP_AUTHORIZATION"] = "Bearer not-a-valid-session"
        _spend(ledger, organization, "unproven-refund", MICROS)
        before = {
            org.pk: list(ledger.ledger_entries(org).order_by("id").values())
            for org in (organization, other_org)
        }
        body = {"org_id": str(organization.pk), "amount_usd": "1"}
        if action == "refund":
            body.update(request_id="unproven-refund", reason="proof test")
        else:
            body["idempotency_key"] = "unproven-write"
            body["note" if action == "grant" else "reason"] = "proof test"
        client = APIClient()
        client.credentials(**headers)
        try:
            clear_workspace_context()
            response = client.post(f"{BASE}{action}/", body, format="json")
            assert response.status_code == 401, response.content
            assert response["WWW-Authenticate"] == "ApiKey"
            assert response.json().get("code") != "api_key_retired"
            assert "create_url" not in response.json()
            for org in (organization, other_org):
                assert (
                    list(ledger.ledger_entries(org).order_by("id").values())
                    == before[org.pk]
                )
        finally:
            clear_workspace_context()

    def test_non_ascii_bearer_is_refused_not_a_server_error(
        self, ledger, organization, gateway
    ):
        """A bearer with a non-ASCII character is not the admin token: refused,
        not a TypeError from hmac.compare_digest."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer \xff")
        body = {"amount_usd": "1", "note": "x", "idempotency_key": "k-ascii"}
        with patch.object(gateway_permissions, "AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
            response = client.post(
                f"{BASE}grant/", {**body, "org_id": str(organization.pk)}, format="json"
            )
        assert response.status_code in (401, 403), response.content
        assert ledger.balance(organization)["granted_total_micros"] == 0

    def test_refund_over_spend_rejected(
        self, ledger, organization, staff_client, gateway
    ):
        _spend(ledger, organization, "req-over", 1_000_000)

        over = staff_client.post(
            f"{BASE}refund/",
            {"request_id": "req-over", "amount_usd": "1.000001", "reason": "x"},
            format="json",
        )
        partial = staff_client.post(
            f"{BASE}refund/",
            {"request_id": "req-over", "amount_usd": "0.4", "reason": "bad answer"},
            format="json",
        )

        assert over.status_code == 400
        assert over.json()["code"] == "refund_exceeds_spend"
        assert partial.status_code == 201, partial.content
        assert partial.json()["result"]["entry"]["amount_usd"] == 0.4
        assert partial.json()["result"]["entry"]["request_id"] == "req-over"

    def test_refund_amount_mismatch_reported(
        self, ledger, organization, staff_client, gateway
    ):
        _spend(ledger, organization, "req-twice", 2_000_000)
        body = {"request_id": "req-twice", "amount_usd": "0.5", "reason": "x"}

        first = staff_client.post(f"{BASE}refund/", body, format="json")
        replay = staff_client.post(f"{BASE}refund/", body, format="json")
        other = staff_client.post(
            f"{BASE}refund/", {**body, "amount_usd": "1.5"}, format="json"
        )
        full = staff_client.post(
            f"{BASE}refund/", {"request_id": "req-twice", "reason": "x"}, format="json"
        )

        assert first.status_code == 201
        assert replay.status_code == 200
        assert replay.json()["result"]["created"] is False
        for response in (other, full):
            assert response.status_code == 409, response.content
            body_json = response.json()
            assert body_json["code"] == "already_refunded"
            assert body_json["result"]["refunded_usd"] == 0.5
        refunds = ledger.ledger_entries(organization).filter(kind="refund")
        assert [e.amount_micros for e in refunds] == [500_000]

    def test_refund_conflict_hides_other_org(
        self, ledger, organization, other_org, staff_client, gateway
    ):
        _spend(ledger, other_org, "req-theirs", 1_000_000)
        ledger.refund(other_org, "req-theirs", reason="theirs", actor=None)

        response = staff_client.post(
            f"{BASE}refund/",
            {"request_id": "req-theirs", "reason": "mine?"},
            format="json",
        )

        assert response.status_code == 409
        assert response.json()["code"] == "ledger_key_conflict"
        assert str(other_org.pk) not in response.content.decode()
        assert ledger.ledger_entries(organization).count() == 0

    def test_refund_capability_spend_by_entry_request_id(
        self, ledger, organization, staff_client, admin_client, gateway
    ):
        rid = "req-cap-7"
        _spend(ledger, organization, f"{rid}.1", 300_000, model="search/brave-web")
        _spend(ledger, organization, f"{rid}.2", 200_000, model="search/brave-web")

        # The org's Admin reads the entries (staff without an org role cannot).
        entries = _result(admin_client.get(f"{BASE}entries/", {"kind": "spend"}))
        assert {e["request_id"] for e in entries["entries"]} == {f"{rid}.1", f"{rid}.2"}

        bare = staff_client.post(
            f"{BASE}refund/", {"request_id": rid, "reason": "x"}, format="json"
        )
        first = staff_client.post(
            f"{BASE}refund/", {"request_id": f"{rid}.1", "reason": "x"}, format="json"
        )

        assert bare.status_code == 404
        assert bare.json()["code"] == "spend_not_found"
        assert first.status_code == 201, first.content
        assert first.json()["result"]["entry"]["request_id"] == f"{rid}.1"
        assert first.json()["result"]["entry"]["amount_usd"] == 0.3
        refunds = ledger.ledger_entries(organization).filter(kind="refund")
        assert [(e.request_id, e.amount_micros) for e in refunds] == [
            (f"{rid}.1", 300_000)
        ]


@pytest.mark.integration
class TestResponseContracts:
    """Every response validates against the serializer its route documents
    (test settings run with DEBUG off, where validated_request skips this)."""

    @pytest.mark.parametrize(
        "result",
        [
            {"session_id": None, "status": "pending", "credited": False},
            {"session_id": "cs_pending", "status": "complete", "credited": True},
        ],
        ids=["pending-null", "complete-reference"],
    )
    def test_checkout_status_contract_accepts_pending_null_and_completed_reference(
        self, result
    ):
        from agentcc.serializers.managed_credits import (
            ManagedCreditCheckoutStatusResponseSerializer,
        )

        serializer = ManagedCreditCheckoutStatusResponseSerializer(
            data={"status": True, "result": result}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["result"] == result

    def test_responses_match_their_serializers(
        self,
        ledger,
        organization,
        admin_client,
        staff_client,
        gateway,
        auto_reload_available,
    ):
        from agentcc.serializers import contracts
        from agentcc.serializers import managed_credits as shapes

        _opt_in(organization)
        _spend(ledger, organization, "req-shape", 1_000_000)
        # f1838b7b binds consent to a saved method, including this success fixture.
        ledger.ManagedCreditAccount.objects.filter(organization=organization).update(
            autoreload_payment_method_id="pm_synthetic_response_contract"
        )
        create_session = MagicMock(
            return_value={
                "checkout_url": "https://checkout.test/s/" + SESSION_STATUS,
                "session_id": SESSION_STATUS,
            }
        )
        settings_body = {
            "low_balance_threshold_usd": "2",
            "auto_reload": {
                "enabled": True,
                "threshold_usd": "1",
                "amount_usd": "30",
                "consent_version": AUTO_RELOAD_CONSENT_VERSION,
            },
        }
        with _install_checkout(create_session):
            calls = [
                (
                    admin_client.get(BASE),
                    shapes.ManagedCreditSummaryResponseSerializer,
                    200,
                ),
                (
                    admin_client.get(f"{BASE}entries/", {"limit": 1}),
                    shapes.ManagedCreditEntriesResponseSerializer,
                    200,
                ),
                (
                    admin_client.get(f"{BASE}statement/", {"month": "2026-10"}),
                    shapes.ManagedCreditStatementResponseSerializer,
                    200,
                ),
                (
                    admin_client.post(f"{BASE}checkout/", CHECKOUT_BODY, format="json"),
                    shapes.ManagedCreditCheckoutResponseSerializer,
                    200,
                ),
                (
                    admin_client.get(f"{BASE}checkout/{SESSION_STATUS}/"),
                    shapes.ManagedCreditCheckoutStatusResponseSerializer,
                    200,
                ),
                # Another shape: the documented error envelope, status 404.
                (
                    other_shape := admin_client.get(f"{BASE}checkout/cs_9/"),
                    contracts.AgentccErrorResponseSerializer,
                    404,
                ),
                (
                    admin_client.put(f"{BASE}settings/", settings_body, format="json"),
                    shapes.ManagedCreditSettingsResponseSerializer,
                    200,
                ),
                (
                    staff_client.post(
                        f"{BASE}grant/",
                        {"amount_usd": "1", "note": "n", "idempotency_key": "s1"},
                        format="json",
                    ),
                    shapes.ManagedCreditWriteResponseSerializer,
                    201,
                ),
                (
                    staff_client.post(
                        f"{BASE}refund/",
                        {"request_id": "req-shape", "reason": "r"},
                        format="json",
                    ),
                    shapes.ManagedCreditWriteResponseSerializer,
                    201,
                ),
                (
                    staff_client.post(
                        f"{BASE}refund/",
                        {"request_id": "req-none", "reason": "r"},
                        format="json",
                    ),
                    contracts.AgentccErrorResponseSerializer,
                    404,
                ),
            ]
        for response, serializer_class, expected_status in calls:
            assert response.status_code == expected_status, response.content
            serializer = serializer_class(data=response.json())
            assert serializer.is_valid(), (
                response.request["PATH_INFO"],
                serializer.errors,
            )
        assert other_shape.status_code == 404
        assert other_shape.json()["code"] == "checkout_session_not_found"


@pytest.mark.integration
class TestOrganizationRequired:
    def test_org_less_request_reaches_nothing(self, ledger, organization, gateway):
        user, _ = _user_in(organization, Level.ADMIN)
        client = APIClient()
        client.force_authenticate(user=user)  # no workspace, so no organization
        for path in (BASE, f"{BASE}entries/", f"{BASE}settings/"):
            response = client.get(path)
            assert response.status_code in (400, 403), path
        assert gateway.calls == []
