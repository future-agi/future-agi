"""BL-LEDGER phase 2a: billing-ledger-read-v1 views (balance, receipts, gateway spend).

Every 200 body is validated against the signed schema (vendored byte for byte
in ``ee/usage/contracts``), and the RA properties of contract section 6 that
phase 2a serves are tested: RA1, RA2, RA3, RA4, RA5, RA6, RA7, RA8, RA9, RA10.
"""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import WorkspaceAwareAPIClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from ee.usage.models.managed_credits import ManagedCreditEntry
from ee.usage.services import managed_credits as mc
from ee.usage.services import stripe_records
from ee.usage.tests.ledger_support import CONTRACTS_DIR, FakeGateway, patched_gateway, spend_record
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.integration
BASE = "/usage/ledger/v1"
INTERNAL = (
    "landed_cost_micros", "upstream_fee_bps", "processing_fee_bps", "upstream_key_id",
    "payment_fee_micros", "note", "upstream_cost_micros", "served_upstream_provider", "created_by",
)


def _schema():
    root = json.loads((CONTRACTS_DIR / "billing-ledger-read-v1.schema.json").read_text())
    public = json.loads((CONTRACTS_DIR / "price-catalog-public-v1.schema.json").read_text())
    registry = Registry().with_resources(
        [
            (public["$id"], Resource.from_contents(public)),
            (root["$id"], Resource.from_contents(root)),
        ]
    )
    return root, registry


def _validate(defn, body):
    root, registry = _schema()
    Draft202012Validator(
        {"$ref": f"{root['$id']}#/$defs/{defn}"}, registry=registry
    ).validate(body)


def _topup(org, object_id, amount):
    return mc.fulfil_topup(
        org,
        payment=mc.TopupPayment(
            source=mc.TopupSource.CHECKOUT_SESSION,
            object_id=object_id,
            amount_micros=amount,
            mode=mc.StripeMode.TEST,
            payment_intent_id=f"pi_{object_id}",
            payment_fee_micros=123,
        ),
    )


def _member(org, workspace, *, org_level, ws_level=None):
    user = User.objects.create_user(
        email=f"m-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Member",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=org, role=OrganizationRoles.MEMBER, level=org_level, is_active=True
    )
    if ws_level is not None:
        WorkspaceMembership.no_workspace_objects.create(
            user=user, workspace=workspace, role="Workspace Admin", level=ws_level,
            is_active=True, organization_membership=membership,
        )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return client


@pytest.fixture
def history(organization, workspace):
    """A wallet with top-ups, managed spend (two keys), a refund and a receipt invoice."""
    _topup(organization, "cs_test_r1", 20_000_000)
    _topup(organization, "cs_test_r2", 5_000_000)
    for i in range(4):
        mc.settle_spend(
            spend_record(organization, f"req_ra_{i}", billed=1_000_000 + i, api_key_id=f"key_{i % 2}"),
            workspace_id=str(workspace.pk) if i % 2 == 0 else None,
        )
    mc.refund(organization, "req_ra_1", 500_000, reason="retry", actor=None)
    stripe_records.upsert_invoice_mirror(
        organization, mode="test", stripe_invoice_id="in_1TestR1", kind="topup", status="paid",
        period_start=None, period_end=None, subtotal_cents=500, tax_cents=0, total_cents=500,
        amount_due_cents=0, hosted_invoice_url="https://invoice.stripe.example/r1",
        invoice_pdf_url="https://invoice.stripe.example/r1.pdf",
        stripe_created_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    return organization


class TestBalance:
    def test_balance_shape_and_ra1(self, auth_client, history):
        with patched_gateway(FakeGateway()):
            mc.push_granted(history)
            response = auth_client.get(f"{BASE}/balance/")
        assert response.status_code == 200, response.content
        body = response.json()
        _validate("Balance", body)
        led = mc.ledger_balance(history)
        spent = sum(1_000_000 + i for i in range(4)) - 500_000
        assert body["balance_micros"] == led.balance_micros == 25_000_000 - spent
        assert body["remaining"]["purchased_micros"] == 25_000_000 - spent
        assert body["gateway"]["state"] == "ok"
        assert body["refusing"] is False and body["funding"] == "prepaid_only"

    def test_managed_off_gives_na_and_nulls_ra8(self, auth_client, history):
        with patched_gateway(FakeGateway(managed_off=True)):
            body = auth_client.get(f"{BASE}/balance/").json()
        _validate("Balance", body)
        assert body["gateway"] == {
            "state": "na_managed_off",
            "granted_total_micros": None,
            "granted_version": None,
            "spent_micros": None,
            "held_micros": None,
            "available_micros": None,
            "markup_percent": None,
        }

    def test_unreachable_gateway_still_answers_200(self, auth_client, history):
        from agentcc.services.gateway_client import GatewayClientError

        class Down(FakeGateway):
            def request(self, *a, **k):
                raise GatewayClientError("Cannot connect")

        with patched_gateway(Down()):
            response = auth_client.get(f"{BASE}/balance/")
        assert response.status_code == 200 and response.json()["gateway"]["state"] == "unreachable"


class TestEntries:
    def test_receipts_and_gateway_spend_ra9(self, auth_client, history):
        response = auth_client.get(f"{BASE}/entries/", {"kind": "topup"})
        assert response.status_code == 200
        body = response.json()
        _validate("EntriesPage", body)
        assert {e["stripe_ref"] for e in body["entries"]} == {"cs_test_r1", "cs_test_r2"}
        spend = auth_client.get(f"{BASE}/entries/", {"product": "gateway", "kind": "spend"}).json()
        _validate("EntriesPage", spend)
        assert len(spend["entries"]) == 4
        assert all(e["dimension"] == "managed_spend" and e["request_id"] for e in spend["entries"])
        raw = json.dumps(body) + json.dumps(spend)
        for name in INTERNAL:
            assert f'"{name}"' not in raw

    def test_pagination_each_entry_once_in_order_ra7(self, auth_client, history):
        seen, cursor = [], None
        while True:
            params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
            page = auth_client.get(f"{BASE}/entries/", params).json()
            _validate("EntriesPage", page)
            seen.extend(page["entries"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        keys = [(e["occurred_at"], e["id"]) for e in seen]
        assert len(keys) == len(set(keys)) == ManagedCreditEntry.objects.filter(organization=history).count()
        assert keys == sorted(keys, reverse=True)

    def test_bad_queries(self, auth_client, history):
        first = auth_client.get(f"{BASE}/entries/", {"limit": 1}).json()
        reused = auth_client.get(f"{BASE}/entries/", {"limit": 1, "kind": "spend", "cursor": first["next_cursor"]})
        assert reused.status_code == 400 and reused.json()["error"]["code"] == "invalid_cursor"
        for params, code in (
            ({"nope": "1"}, "invalid_query"),
            ({"period": "2026-13"}, "invalid_period"),
            ({"limit": 201}, "invalid_query"),
            ({"kind": "bogus"}, "invalid_query"),
        ):
            response = auth_client.get(f"{BASE}/entries/", params)
            assert response.status_code == 400
            _validate("Error", response.json())
            assert response.json()["error"]["code"] == code

    def test_csv_has_every_row(self, auth_client, history):
        response = auth_client.get(f"{BASE}/entries/", {"format": "csv"})
        assert response.status_code == 200 and response["Content-Type"].startswith("text/csv")
        lines = response.content.decode().strip().splitlines()
        assert lines[0].startswith("id,kind,amount_micros")
        assert len(lines) - 1 == ManagedCreditEntry.objects.filter(organization=history).count()


class TestUsage:
    def test_gateway_spend_sums_ra2_ra3(self, auth_client, history):
        response = auth_client.get(f"{BASE}/usage/", {"group_by": "dimension,api_key"})
        assert response.status_code == 200, response.content
        body = response.json()
        _validate("Usage", body)
        expected = -sum(
            e.amount_micros
            for e in ManagedCreditEntry.objects.filter(
                organization=history, kind__in=["spend", "usage_debit", "refund"]
            )
        )
        assert body["totals"]["amount_micros"] == expected == sum(1_000_000 + i for i in range(4)) - 500_000
        assert sum(r["amount_micros"] for r in body["rows"]) == expected
        for row in [*body["rows"], body["totals"]]:
            assert sum(row["funded"].values()) == row["amount_micros"]
        assert {r["api_key_id"] for r in body["rows"]} == {"key_0", "key_1"}
        assert all(r["amount_source"] == "ledger" for r in body["rows"])

    def test_workspace_admin_reads_own_workspace_ra4_ra10(self, auth_client, history, workspace):
        owner = auth_client.get(f"{BASE}/usage/", {"group_by": "workspace"}).json()
        ws_admin = _member(history, workspace, org_level=Level.MEMBER, ws_level=Level.WORKSPACE_ADMIN)
        mine = ws_admin.get(f"{BASE}/workspaces/{workspace.pk}/usage/", {"group_by": "workspace"})
        assert mine.status_code == 200, mine.content
        _validate("Usage", mine.json())
        owner_rows = [r for r in owner["rows"] if r["workspace_id"] == str(workspace.pk)]
        assert mine.json()["rows"] == owner_rows
        entries = ws_admin.get(f"{BASE}/workspaces/{workspace.pk}/entries/").json()
        _validate("EntriesPage", entries)
        assert {e["kind"] for e in entries["entries"]} <= {"spend", "usage_debit", "refund"}
        for route in ("balance", "entries", "usage", "invoices"):
            response = ws_admin.get(f"{BASE}/{route}/")
            assert response.status_code == 403, route
            assert response.json()["error"]["code"] == "forbidden"

    def test_member_and_viewer_are_refused_ra10(self, history, workspace):
        for level in (Level.MEMBER, Level.VIEWER):
            client = _member(history, workspace, org_level=level)
            for route in ("balance/", "entries/", "usage/", "invoices/", f"workspaces/{workspace.pk}/usage/"):
                assert client.get(f"{BASE}/{route}").status_code == 403, (level, route)

    def test_other_orgs_never_appear_ra5(self, auth_client, history, user):
        other = Organization.objects.create(name="Org B")
        other_ws = Workspace.no_workspace_objects.create(name="b", organization=other, created_by=user)
        _topup(other, "cs_test_orgb", 7_000_000)
        mc.settle_spend(spend_record(other, "req_orgb"), workspace_id=str(other_ws.pk))
        bodies = [
            auth_client.get(f"{BASE}/{route}/").content.decode()
            for route in ("entries", "usage", "invoices")
        ]
        for text in bodies:
            assert "cs_test_orgb" not in text and "req_orgb" not in text and str(other.pk) not in text
        assert auth_client.get(f"{BASE}/workspaces/{other_ws.pk}/usage/").status_code == 404


class TestInvoices:
    def test_receipt_invoice_mirror(self, auth_client, history):
        response = auth_client.get(f"{BASE}/invoices/")
        assert response.status_code == 200
        body = response.json()
        _validate("InvoicesPage", body)
        (invoice,) = body["invoices"]
        assert (invoice["kind"], invoice["status"], invoice["total_cents"]) == ("topup", "paid", 500)


def test_unauthenticated_is_401():
    from rest_framework.test import APIClient

    response = APIClient().get(f"{BASE}/balance/")
    assert response.status_code == 401


def test_no_stripe_import_in_the_ledger_ra6():
    """No Stripe import in this tree (BL-LEDGER acceptance); reads never call Stripe."""
    root = Path(__file__).resolve().parents[1]
    pattern = re.compile(r"^\s*(import stripe|from stripe\b)", re.M)
    # The ledger's code (the owner-only switch_stripe_collection_method command
    # predates it and is not a ledger path).
    files = [
        *root.joinpath("services").glob("*.py"),
        *root.joinpath("models").glob("*.py"),
        *root.joinpath("views").glob("*.py"),
        *root.joinpath("migrations").glob("003*.py"),
        root / "urls.py",
    ]
    assert len(files) > 20
    offenders = [
        str(p.relative_to(root)) for p in files if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
