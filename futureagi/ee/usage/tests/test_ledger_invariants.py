"""BL-LEDGER phase 2a: invariants I1, I2, I5, I6 and I11 (PLAN 4.4).

Each invariant is shown holding on a mixed history and failing on a ledger
corrupted the one way it guards against, so a check that always passes would
fail here.
"""

from datetime import UTC, datetime, timedelta

import pytest

from accounts.tests.hard_erase_fixture import migration_fixture_cursor
from ee.usage.models.billing_ledger import ReconciliationRun, UsageEventRecord
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.models.usage import UsageEventLog
from ee.usage.services import managed_credits as mc
from ee.usage.services import rating, reconcile
from ee.usage.tests.ledger_support import (
    FakeGateway,
    patched_gateway,
    spend_record,
    with_catalog,
)

pytestmark = pytest.mark.integration


def _topup(org, object_id, amount):
    return mc.fulfil_topup(
        org,
        payment=mc.TopupPayment(
            source=mc.TopupSource.CHECKOUT_SESSION,
            object_id=object_id,
            amount_micros=amount,
            mode=mc.StripeMode.TEST,
            payment_intent_id=f"pi_{object_id}",
        ),
    )


def _platform_debit(org, seq, amount):
    return mc.debit_usage(
        org,
        dimension="agent_runs",
        product="agents",
        unit="runs",
        quantity=1,
        amount_micros=amount,
        period="2026-10",
        seq=seq,
        funding_source="wallet",
        occurred_at=datetime.now(UTC),
        price_catalog_version="price-catalog.v0",
    )


def _settle_both(fake, org, request_id, billed):
    """The gateway counts the spend on its hot path; the drain settles it."""
    fake.settle(org.pk, billed)
    mc.settle_spend(spend_record(org, request_id, billed=billed))


def _push(org):
    assert mc.push_granted(org) is not None


class TestI11MixedTraffic:
    def test_credits_above_platform_spend(self, organization):
        fake = FakeGateway()
        with patched_gateway(fake):
            _topup(organization, "cs_test_i11a", 100_000)
            _platform_debit(organization, 0, 30_000)
            _settle_both(fake, organization, "req_i11_a", 20_000)
            fake.hold(organization.pk, 5_000)
            _push(organization)
            results = reconcile.check_gateway(organization)
        names = {r.name: r for r in results}
        assert all(r.ok for r in results), [r.failures for r in results]
        assert not names["I6"].exempt and names["I11"].checked == 2
        # F - P - S - H on the gateway; F - P - S in the ledger.
        assert (
            fake.state(organization.pk)["available_micros"]
            == 100_000 - 30_000 - 20_000 - 5_000
        )
        assert (
            mc.ledger_balance(organization).balance_micros == 100_000 - 30_000 - 20_000
        )
        assert ReconciliationRun.objects.filter(organization=organization).count() == 3

    def test_platform_spend_above_credits_is_postgres_debt(self, organization):
        fake = FakeGateway()
        with patched_gateway(fake):
            _topup(organization, "cs_test_i11b", 10_000)
            _platform_debit(organization, 0, 25_000)
            _settle_both(fake, organization, "req_i11_b", 2_000)
            fake.hold(organization.pk, 1_000)
            _push(organization)
            i6, i11 = (
                reconcile.check_i6(
                    organization,
                    fake.request("GET", f"/-/orgs/{organization.pk}/credits"),
                ),
                reconcile.check_i11(
                    organization,
                    fake.request("GET", f"/-/orgs/{organization.pk}/credits"),
                ),
            )
        assert i6.ok and i11.ok, (i6.failures, i11.failures)
        # The gateway sees granted 0 (the clamp): -S - H; the ledger shows the debt.
        assert fake.state(organization.pk)["available_micros"] == -2_000 - 1_000
        led = mc.ledger_balance(organization)
        assert (led.balance_micros, led.debt_micros) == (
            10_000 - 25_000 - 2_000,
            17_000,
        )

    def test_i11_fails_when_the_gateway_was_pushed_the_wrong_value(self, organization):
        fake = FakeGateway()
        with patched_gateway(fake):
            _topup(organization, "cs_test_i11c", 50_000)
            _platform_debit(organization, 0, 10_000)
            _push(organization)
            # A push that forgot P (the double-count of finding 1, inverted).
            account = ManagedCreditAccount.objects.get(organization=organization)
            fake.request(
                "PUT",
                f"/-/orgs/{organization.pk}/credits/granted",
                json={
                    "granted_total_micros": 50_000,
                    "version": account.version + 1,
                    "settled_spend_floor_micros": 0,
                },
            )
            state = fake.request("GET", f"/-/orgs/{organization.pk}/credits")
        assert not reconcile.check_i11(organization, state).ok

    def test_managed_off_is_exempt(self, organization):
        fake = FakeGateway(managed_off=True)
        with patched_gateway(fake):
            _topup(organization, "cs_test_off", 5_000)
            mc.push_granted(organization)
            results = {r.name: r for r in reconcile.check_gateway(organization)}
        assert results["I6"].exempt and results["I11"].exempt
        assert results["I5"].ok

    def test_unreachable_gateway_fails_i6_and_i11(self, organization):
        from agentcc.services.gateway_client import GatewayClientError

        class Down(FakeGateway):
            def request(self, *a, **k):
                raise GatewayClientError("Cannot connect", status_code=None)

        _topup(organization, "cs_test_down", 5_000)
        with patched_gateway(Down()):
            results = {r.name: r for r in reconcile.check_gateway(organization)}
        assert not results["I6"].ok and not results["I11"].ok


class TestI6:
    def test_holds_after_push_and_fails_on_a_stale_floor(self, organization):
        fake = FakeGateway()
        with patched_gateway(fake):
            _topup(organization, "cs_test_i6", 40_000)
            _platform_debit(organization, 0, 15_000)
            mc.settle_spend(
                spend_record(organization, "req_i6", billed=4_000)
            )  # not on the gateway yet
            _push(organization)
            state = fake.request("GET", f"/-/orgs/{organization.pk}/credits")
        result = reconcile.check_i6(organization, state)
        assert result.ok, result.failures
        assert (
            state["granted_total_micros"] == 25_000 and state["spent_micros"] == 4_000
        )
        stale = {**state, "spent_micros": 3_999}
        assert not reconcile.check_i6(organization, stale).ok
        ahead = {**state, "granted_total_micros": 40_000}
        assert not reconcile.check_i6(organization, ahead).ok


class TestI5AndI2:
    def test_hold_on_a_full_history_and_catch_a_drifted_account(self, organization):
        mc.grant(organization, 3_000, idempotency_key="g", actor=None, note="")
        _topup(organization, "cs_test_i5", 10_000)
        mc.settle_spend(spend_record(organization, "req_i5a", billed=6_000))
        mc.settle_spend(spend_record(organization, "req_i5b", billed=9_000))
        mc.refund(organization, "req_i5a", 1_000, reason="", actor=None)
        mc.adjust(organization, -500, idempotency_key="a", reason="", actor=None)
        _platform_debit(organization, 0, 700)
        assert reconcile.check_i5(organization).ok
        assert reconcile.check_i2(org=organization).ok
        ManagedCreditAccount.objects.filter(organization=organization).update(
            purchased_balance_micros=1
        )
        failed = reconcile.check_i5(organization)
        assert not failed.ok and any(
            "purchased_balance_micros" in f for f in failed.failures
        )

    def test_i2_catches_a_bucket_of_the_wrong_sign(self, organization):
        mc.settle_spend(spend_record(organization, "req_i2", billed=100))
        assert reconcile.check_i2(org=organization).ok
        # Bypass the append-only manager the way only a corrupting script could.
        from django.db import connection

        with migration_fixture_cursor(connection) as cursor:
            cursor.execute(
                "UPDATE usage_managedcreditentry SET funded_promo_micros = 50, "
                "funded_debt_micros = -150 WHERE request_id = %s",
                ["req_i2"],
            )
        assert not reconcile.check_i2(org=organization).ok

    def test_i5_replays_rows_from_before_the_funding_columns(self, organization):
        # A pre-0031 row (no split) and its seeded account, then a 2a write.
        account = ManagedCreditAccount.objects.create(
            organization=organization,
            granted_total_micros=4_000,
            version=1,
            promo_balance_micros=4_000,
            promo_total_micros=4_000,
            entry_seq=1,
        )
        ManagedCreditEntry.objects.bulk_create(
            [
                ManagedCreditEntry(
                    organization=organization,
                    kind="grant",
                    amount_micros=4_000,
                    idempotency_key=f"grant:{organization.pk}:legacy",
                )
            ]
        )
        mc.settle_spend(spend_record(organization, "req_legacy", billed=1_000))
        account.refresh_from_db()
        assert account.promo_balance_micros == 3_000
        assert reconcile.check_i5(organization).ok


class TestI1:
    def test_rater_links_spend_and_marks_legacy_platform_events(self, organization):
        now = datetime.now(UTC)
        mc.settle_spend(spend_record(organization, "req_i1"))
        # A legacy-consumer row (no v1 record) and an EE row, both legacy path.
        legacy = UsageEventLog.objects.create(
            event_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            organization=organization,
            event_type="turing_large_evaluator",
            dimension="ai_credits",
            amount_raw=3,
            amount_display=3,
            period=now.strftime("%Y-%m"),
            timestamp=now,
            properties={"source": "eval", "source_id": "cell-1"},
        )
        ee = UsageEventLog.objects.create(
            event_id="6ba7b811-9dad-11d1-80b4-00c04fd430c8",
            organization=organization,
            event_type="task_run",
            dimension="agent_runs",
            amount_raw=1,
            amount_display=1,
            period=now.strftime("%Y-%m"),
            timestamp=now,
            properties={},
        )
        with with_catalog():
            result = rating.rate_pending()
        assert (result.not_billable, result.pending_ledger_path) == (2, 0)
        assert (
            UsageEventRecord.objects.get(event=legacy).not_billable_reason
            == "legacy_period"
        )
        ee_record = UsageEventRecord.objects.get(event=ee)
        assert (ee_record.product, ee_record.not_billable_reason) == (
            "agents",
            "legacy_ee",
        )
        again = rating.rate_pending()
        assert (again.linked, again.not_billable, again.waiting) == (0, 0, 0)
        assert reconcile.check_i1(org=organization).ok

    def test_a_managed_spend_event_waits_for_its_spend_then_links(self, organization):
        now = datetime.now(UTC)
        event_id = mc.spend_event_id("req_late")
        UsageEventLog.objects.create(
            event_id=event_id,
            organization=organization,
            event_type="gateway_managed_spend",
            dimension="managed_spend",
            amount_raw=11_815,
            amount_display="0.011815",
            period=now.strftime("%Y-%m"),
            timestamp=now,
            properties={
                "source": "fa_gateway_settlement",
                "source_id": "req_late",
                "request_id": "req_late",
            },
        )
        first = rating.rate_pending()
        assert first.waiting == 1
        waiting = UsageEventRecord.objects.get(event_uuid=event_id)
        assert (
            rating.rated_through() < waiting.received_at
        )  # the watermark stops before it
        mc.settle_spend(spend_record(organization, "req_late"))
        record = UsageEventRecord.objects.get(event_uuid=event_id)
        assert (
            record.rated_entry is not None
            and record.rated_entry.request_id == "req_late"
        )
        assert reconcile.check_i1(org=organization).ok

    def test_i1_fails_on_a_stuck_event_and_a_ledger_path_event(self, organization):
        old = datetime.now(UTC) - timedelta(hours=3)
        UsageEventLog.objects.create(
            event_id=mc.spend_event_id("req_never"),
            organization=organization,
            event_type="gateway_managed_spend",
            dimension="managed_spend",
            amount_raw=5,
            amount_display="0.000005",
            period=old.strftime("%Y-%m"),
            timestamp=old,
            properties={"request_id": "req_never"},
        )
        rating.rate_pending()
        UsageEventRecord.objects.filter(request_id="req_never").update(received_at=old)
        assert not reconcile.check_i1(org=organization).ok

        ManagedCreditAccount.objects.create(
            organization=organization, cutover_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        now = datetime.now(UTC)
        UsageEventLog.objects.create(
            event_id="6ba7b812-9dad-11d1-80b4-00c04fd430c8",
            organization=organization,
            event_type="turing_large_evaluator",
            dimension="ai_credits",
            amount_raw=1,
            amount_display=1,
            period=now.strftime("%Y-%m"),
            timestamp=now,
            properties={},
        )
        assert (
            rating.rate_pending().pending_ledger_path == 1
        )  # phase 2b: loud, not dropped
