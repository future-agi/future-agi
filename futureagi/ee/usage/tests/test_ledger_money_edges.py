"""BL-LEDGER phase 2a: money edges found by the independent verify of c0d41f724.

- A refund of a spend that drew debt which a later credit already repaid
  (verifier A14, A14b; A14c is the control with no debt).
- A funding error is a refused ledger write (``ManagedCreditError``), never an
  unhandled ``ValueError``, and it writes nothing.
- A session or invoice top-up must name its PaymentIntent, so one payment can
  never credit twice through two objects (verifier A2).
- CSV exports never hand a spreadsheet a formula (verifier A15).
- An invoice row names no statement route that phase 2a does not serve.
"""

import csv
import io
import re
import time
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest

from ee.usage.models.billing_ledger import StripeEventLog
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.services import funding, reconcile, stripe_records
from ee.usage.services import managed_credits as mc
from ee.usage.tests.ledger_support import spend_record

Kind = ManagedCreditEntry.Kind
pytestmark = pytest.mark.integration
BASE = "/usage/ledger/v1"
_PLAIN_NUMBER = re.compile(r"[+-]?\d+(\.\d+)?")


def _session(object_id, amount, pi=None):
    return mc.TopupPayment(
        source=mc.TopupSource.CHECKOUT_SESSION,
        object_id=object_id,
        amount_micros=amount,
        mode=mc.StripeMode.TEST,
        payment_intent_id=f"pi_{object_id}" if pi is None else pi,
    )


def _pi(pi_id, amount):
    return mc.TopupPayment(
        source=mc.TopupSource.PAYMENT_INTENT,
        object_id=pi_id,
        amount_micros=amount,
        mode=mc.StripeMode.TEST,
    )


def _ok(*checks):
    for check in checks:
        assert check.ok, (check.name, check.failures)


def _account_state(org):
    a = ManagedCreditAccount.objects.get(organization=org)
    return (
        a.version,
        a.entry_seq,
        a.granted_total_micros,
        a.promo_balance_micros,
        a.commit_balance_micros,
        a.purchased_balance_micros,
        a.debt_micros,
    )


class TestRefundOfASpendWhoseDebtWasRepaid:
    def test_debt_fully_repaid_by_a_later_top_up(self, organization):
        mc.fulfil_topup(organization, payment=_session("cs_test_me14a", 5_000))
        mc.settle_spend(spend_record(organization, "req_me14", billed=10_000))  # 5_000 + 5_000 debt
        assert mc.ledger_balance(organization).debt_micros == 5_000
        mc.fulfil_topup(organization, payment=_session("cs_test_me14b", 20_000))  # repays the debt
        assert mc.ledger_balance(organization).balance_micros == 15_000

        entry, created = mc.refund(organization, "req_me14", reason="failed request", actor=None)

        assert created and entry.amount_micros == 10_000
        # The spend's purchased part and its repaid debt part both go back to purchased.
        assert (entry.funded_purchased_micros, entry.funded_debt_micros) == (10_000, 0)
        bal = mc.ledger_balance(organization)
        assert (bal.balance_micros, bal.debt_micros, bal.purchased_micros) == (25_000, 0, 25_000)
        _ok(reconcile.check_i2(org=organization), reconcile.check_i5(organization))

    def test_debt_partly_repaid_by_a_later_top_up(self, organization):
        mc.fulfil_topup(organization, payment=_session("cs_test_me14c", 5_000))
        mc.settle_spend(spend_record(organization, "req_me14b", billed=10_000))  # 5_000 debt
        mc.fulfil_topup(organization, payment=_session("cs_test_me14d", 3_000))  # debt now 2_000

        entry, created = mc.refund(organization, "req_me14b", reason="failed request", actor=None)

        assert created and entry.amount_micros == 10_000
        # 2_000 repays the debt still outstanding; 5_000 + 3_000 go back to purchased.
        assert (entry.funded_purchased_micros, entry.funded_debt_micros) == (8_000, 2_000)
        bal = mc.ledger_balance(organization)
        assert (bal.balance_micros, bal.debt_micros, bal.purchased_micros) == (8_000, 0, 8_000)
        _ok(reconcile.check_i2(org=organization), reconcile.check_i5(organization))

    def test_partial_refund_returns_the_debt_part_first(self, organization):
        mc.grant(organization, 2_000, idempotency_key="me-promo", actor=None, note="")
        mc.settle_spend(spend_record(organization, "req_me14p", billed=6_000))  # promo 2_000 + debt 4_000
        mc.fulfil_topup(organization, payment=_session("cs_test_me14p", 1_000))  # debt now 3_000

        entry, _ = mc.refund(organization, "req_me14p", 3_500, reason="partial", actor=None)

        # Debt still outstanding first (3_000), then the repaid debt share to
        # purchased (500); the promo part is untouched by this partial refund.
        assert (entry.funded_debt_micros, entry.funded_purchased_micros, entry.funded_promo_micros) == (
            3_000,
            500,
            0,
        )
        bal = mc.ledger_balance(organization)
        assert (bal.balance_micros, bal.debt_micros, bal.purchased_micros, bal.promo_micros) == (
            500,
            0,
            500,
            0,
        )
        _ok(reconcile.check_i2(org=organization), reconcile.check_i5(organization))

    def test_control_no_debt_involved(self, organization):
        mc.fulfil_topup(organization, payment=_session("cs_test_me14e", 20_000))
        mc.settle_spend(spend_record(organization, "req_me14c", billed=10_000))

        entry, created = mc.refund(organization, "req_me14c", reason="failed request", actor=None)

        assert created and entry.amount_micros == 10_000
        assert mc.ledger_balance(organization).balance_micros == 20_000
        _ok(reconcile.check_i2(org=organization), reconcile.check_i5(organization))


def test_a_funding_error_is_a_refused_write_that_changes_nothing(organization):
    mc.fulfil_topup(organization, payment=_session("cs_test_mefe", 20_000))
    mc.settle_spend(spend_record(organization, "req_mefe", billed=10_000))
    before = _account_state(organization)

    with patch.object(funding, "allocate_spend_refund", side_effect=funding.FundingError("no bucket")):
        with pytest.raises(mc.ManagedCreditError, match="no bucket"):
            mc.refund(organization, "req_mefe", reason="failed request", actor=None)

    assert not ManagedCreditEntry.objects.filter(organization=organization, kind=Kind.REFUND).exists()
    assert _account_state(organization) == before


class TestTopupNamesItsPaymentIntent:
    @pytest.mark.parametrize(
        "payment",
        [
            mc.TopupPayment(mc.TopupSource.CHECKOUT_SESSION, "cs_test_menopi", 25_000_000, mc.StripeMode.TEST),
            mc.TopupPayment(mc.TopupSource.INVOICE, "in_1MeNoPi", 25_000_000, mc.StripeMode.TEST),
        ],
        ids=["checkout_session", "invoice"],
    )
    def test_session_or_invoice_without_its_payment_intent_is_refused(self, organization, payment):
        with pytest.raises(mc.ManagedCreditError, match="payment_intent_id"):
            mc.fulfil_topup(organization, payment=payment, stripe_event_id="evt_me_nopi")

        assert not ManagedCreditEntry.objects.filter(organization=organization).exists()
        assert not StripeEventLog.objects.filter(event_id="evt_me_nopi").exists()

    def test_one_payment_credits_once_whichever_object_arrives(self, organization):
        # Verifier A2: the session first without its PaymentIntent, then the PaymentIntent.
        with pytest.raises(mc.ManagedCreditError):
            mc.fulfil_topup(organization, payment=_session("cs_test_mea2", 25_000_000, pi=""))
        first = mc.fulfil_topup(organization, payment=_pi("pi_test_mea2", 25_000_000))
        again = mc.fulfil_topup(
            organization, payment=_session("cs_test_mea2", 25_000_000, pi="pi_test_mea2")
        )

        assert first.created and not again.created and again.entry.pk == first.entry.pk
        assert ManagedCreditEntry.objects.filter(organization=organization, kind=Kind.TOPUP).count() == 1
        assert mc.ledger_balance(organization).balance_micros == 25_000_000

    def test_live_mode_is_refused_before_the_payment_intent_check(self, organization):
        for source, object_id in (
            (mc.TopupSource.CHECKOUT_SESSION, "cs_live_menopi"),
            (mc.TopupSource.INVOICE, "in_liveMeNoPi"),
        ):
            with pytest.raises(mc.LiveModeRefused):
                mc.fulfil_topup(
                    organization,
                    payment=mc.TopupPayment(source, object_id, 1_000_000, mc.StripeMode.LIVE),
                )
        assert not ManagedCreditEntry.objects.filter(organization=organization).exists()


class TestCsvNeverCarriesAFormula:
    FORMULA = '=HYPERLINK("https://x.invalid","x")'
    RUN = "+cmd|' /C calc'!A0"
    KEY = "@SUM(A1:A9)"

    def _settle(self, organization):
        now_ms = int(time.time() * 1000)
        mc.settle_spend(
            spend_record(
                organization, "req_mecsv", billed=1_000, ts_ms=now_ms,
                agent_id=self.FORMULA, agent_run_id=self.RUN, api_key_id=self.KEY,
            )
        )
        return datetime.fromtimestamp(now_ms / 1000, tz=UTC).strftime("%Y-%m")

    @staticmethod
    def _rows(response):
        assert response.status_code == 200, response.content
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        header, body = rows[0], rows[1:]
        assert body
        for row in body:
            for cell in row:
                if cell[:1] in ("=", "+", "-", "@", "\t", "\r"):
                    assert _PLAIN_NUMBER.fullmatch(cell), (cell, row)
        return [dict(zip(header, row, strict=True)) for row in body]

    def test_entries_csv(self, organization, auth_client):
        self._settle(organization)

        (row,) = self._rows(auth_client.get(f"{BASE}/entries/?format=csv"))

        assert (row["agent_id"], row["agent_run_id"], row["api_key_id"]) == (
            "'" + self.FORMULA,
            "'" + self.RUN,
            "'" + self.KEY,
        )
        assert row["amount_micros"] == "-1000"  # a negative number stays a number

    def test_usage_csv(self, organization, auth_client):
        period = self._settle(organization)

        rows = self._rows(
            auth_client.get(f"{BASE}/usage/?format=csv&period={period}&group_by=agent,api_key")
        )

        (row,) = [r for r in rows if r["product"] == "gateway"]
        assert (row["agent_id"], row["api_key_id"]) == ("'" + self.FORMULA, "'" + self.KEY)
        assert row["amount_micros"] == "1000"


def test_invoice_rows_name_no_statement_route_until_it_exists(organization, auth_client):
    stripe_records.upsert_invoice_mirror(
        organization, mode="test", stripe_invoice_id="in_1MeStmt", kind="usage", status="paid",
        period_start=date(2026, 10, 1), period_end=date(2026, 10, 31), subtotal_cents=1_200,
        tax_cents=0, total_cents=1_200, amount_due_cents=0,
        hosted_invoice_url="https://invoice.stripe.example/stmt",
        invoice_pdf_url="https://invoice.stripe.example/stmt.pdf",
        stripe_created_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    response = auth_client.get(f"{BASE}/invoices/")

    assert response.status_code == 200, response.content
    (invoice,) = response.json()["invoices"]
    assert (invoice["kind"], invoice["statement_url"]) == ("usage", None)
    # The route the link used to name is not served in phase 2a.
    assert auth_client.get(f"{BASE}/statements/2026-10/").status_code == 404
