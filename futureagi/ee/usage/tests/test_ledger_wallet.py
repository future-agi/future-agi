"""BL-LEDGER phase 2a: the prepaid wallet (top-ups, spend, buckets, push, 402).

Stripe is never called: BL-STRIPE's calls are made here with plain values,
as the private billing code makes them.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.test import override_settings
from jsonschema import Draft202012Validator

from accounts.models.organization import Organization
from agentcc.auto_reload_terms import AUTO_RELOAD_CONSENT_VERSION
from agentcc.models.auto_reload_consent import AgentccAutoReloadConsent
from agentcc.services.auto_reload_consent import has_current_auto_reload_consent
from ee.usage.models.billing_ledger import StripeEventLog, UsageEventRecord
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.models.usage import UsageEventLog
from ee.usage.services import balance_cache, gateway_push, reconcile, stripe_records
from ee.usage.services import managed_credits as mc
from ee.usage.services.auto_topup import AutoTopupError
from ee.usage.services.funding import Bucket
from ee.usage.services.metering import check_usage
from ee.usage.tests.ledger_support import (
    FakeGateway,
    golden,
    new_id,
    patched_gateway,
    spend_record,
    with_catalog,
)

Kind = ManagedCreditEntry.Kind
pytestmark = pytest.mark.integration


def _session(
    amount=25_000_000, object_id=None, pi="", purpose=mc.TopupPurpose.TOPUP, seq=None
):
    # A paid payment-mode session always has a PaymentIntent, and the ledger
    # requires it (one payment never credits through two objects).
    object_id = object_id or f"cs_test_{new_id()}"
    return mc.TopupPayment(
        source=mc.TopupSource.CHECKOUT_SESSION,
        object_id=object_id,
        amount_micros=amount,
        mode=mc.StripeMode.TEST,
        payment_intent_id=pi or f"pi_{object_id}",
        purpose=purpose,
        auto_topup_seq=seq,
    )


def _pi(pi_id, amount=10_000_000, purpose=mc.TopupPurpose.TOPUP, seq=None):
    return mc.TopupPayment(
        source=mc.TopupSource.PAYMENT_INTENT,
        object_id=pi_id,
        amount_micros=amount,
        mode=mc.StripeMode.TEST,
        purpose=purpose,
        auto_topup_seq=seq,
    )


def _account(org):
    return ManagedCreditAccount.objects.get(organization=org)


def _ok(result):
    assert result.ok, result.failures
    return result


class TestFulfilTopup:
    def test_replays_and_landing_page_give_one_entry(self, organization):
        payment = _session(pi="pi_test_s1")
        first = mc.fulfil_topup(organization, payment=payment, stripe_event_id="evt_1")
        second = mc.fulfil_topup(organization, payment=payment, stripe_event_id="evt_1")
        third = mc.fulfil_topup(organization, payment=payment, stripe_event_id="evt_2")
        landing = mc.fulfil_topup(organization, payment=payment)

        assert [r.created for r in (first, second, third, landing)] == [
            True,
            False,
            False,
            False,
        ]
        assert {r.entry.pk for r in (first, second, third, landing)} == {first.entry.pk}
        entry = first.entry
        assert entry.idempotency_key == f"stripe:{payment.object_id}"
        assert (entry.kind, entry.amount_micros, entry.funding) == (
            Kind.TOPUP,
            25_000_000,
            "purchased",
        )
        assert (entry.stripe_mode, entry.category, entry.stripe_event_id) == (
            "test",
            "topup",
            "evt_1",
        )
        assert ManagedCreditEntry.objects.filter(organization=organization).count() == 1
        assert set(
            StripeEventLog.objects.filter(entry=entry).values_list(
                "event_id", flat=True
            )
        ) == {"evt_1", "evt_2"}
        account = _account(organization)
        assert (
            account.purchased_balance_micros,
            account.granted_total_micros,
            account.version,
        ) == (
            25_000_000,
            25_000_000,
            1,
        )

    def test_session_and_its_payment_intent_never_both_credit(self, organization):
        session = _session(pi="pi_test_same")
        first = mc.fulfil_topup(organization, payment=session)
        again = mc.fulfil_topup(
            organization,
            payment=_pi("pi_test_same", amount=25_000_000),
            stripe_event_id="evt_pi",
        )

        assert (first.created, again.created) == (True, False)
        assert again.entry.pk == first.entry.pk
        # Replaying the PaymentIntent's event finds the same money, not a mismatch.
        replay = mc.fulfil_topup(
            organization,
            payment=_pi("pi_test_same", amount=25_000_000),
            stripe_event_id="evt_pi",
        )
        assert (replay.created, replay.entry.pk) == (False, first.entry.pk)
        assert _account(organization).purchased_balance_micros == 25_000_000

    def test_invoice_and_payment_intent_keys(self, organization):
        inv = mc.fulfil_topup(
            organization,
            payment=mc.TopupPayment(
                source=mc.TopupSource.INVOICE,
                object_id="in_1Test1",
                amount_micros=5_000_000,
                mode=mc.StripeMode.TEST,
                payment_intent_id="pi_test_inv",
            ),
        )
        pi = mc.fulfil_topup(organization, payment=_pi("pi_test_x"))
        assert inv.entry.idempotency_key == "stripe_inv:in_1Test1"
        assert pi.entry.idempotency_key == "stripe_pi:pi_test_x"
        assert pi.entry.stripe_payment_intent_id == "pi_test_x"

    @pytest.mark.parametrize(
        "change",
        [{"amount_micros": 1}, {"purpose": mc.TopupPurpose.AUTO_TOPUP}],
        ids=["amount", "purpose"],
    )
    def test_same_object_other_terms_is_a_mismatch(self, organization, change):
        payment = _session()
        mc.fulfil_topup(organization, payment=payment)
        other = mc.TopupPayment(**{**payment.__dict__, **change})

        with pytest.raises(mc.TopupMismatch):
            mc.fulfil_topup(organization, payment=other)
        assert ManagedCreditEntry.objects.filter(organization=organization).count() == 1

    def test_another_org_cannot_take_a_paid_object(self, organization):
        other = Organization.objects.create(name="Other org")
        payment = _session()
        mc.fulfil_topup(organization, payment=payment)

        with pytest.raises(mc.TopupMismatch):
            mc.fulfil_topup(other, payment=payment)
        assert not ManagedCreditEntry.objects.filter(organization=other).exists()

    def test_event_id_of_another_object_is_refused(self, organization):
        mc.fulfil_topup(organization, payment=_session(), stripe_event_id="evt_x")
        with pytest.raises(mc.TopupMismatch):
            mc.fulfil_topup(organization, payment=_session(), stripe_event_id="evt_x")

    def test_live_mode_is_refused_by_default(self, organization):
        live = mc.TopupPayment(
            source=mc.TopupSource.CHECKOUT_SESSION,
            object_id="cs_live_1",
            amount_micros=1_000_000,
            mode=mc.StripeMode.LIVE,
            payment_intent_id="pi_live_1",
        )
        with pytest.raises(mc.LiveModeRefused):
            mc.fulfil_topup(organization, payment=live)
        with pytest.raises(mc.LiveModeRefused):
            mc.reverse_topup(
                organization,
                refund_id="re_1",
                payment_intent_id="pi",
                amount_micros=1,
                mode=mc.StripeMode.LIVE,
            )
        assert not ManagedCreditEntry.objects.exists()
        with override_settings(BILLING_LEDGER_ACCEPT_LIVE=True):
            assert mc.fulfil_topup(organization, payment=live).created

    @pytest.mark.parametrize(
        "payment",
        [
            mc.TopupPayment(
                mc.TopupSource.CHECKOUT_SESSION, "cs_1", 0, mc.StripeMode.TEST
            ),
            mc.TopupPayment(
                mc.TopupSource.CHECKOUT_SESSION, "cs_1", 1.5, mc.StripeMode.TEST
            ),
            mc.TopupPayment(mc.TopupSource.CHECKOUT_SESSION, "", 5, mc.StripeMode.TEST),
            mc.TopupPayment(
                mc.TopupSource.PAYMENT_INTENT, "pi_a", 5, mc.StripeMode.TEST, "pi_b"
            ),
            mc.TopupPayment(mc.TopupSource.CHECKOUT_SESSION, "cs_1", 5, "sandbox"),
        ],
        ids=["zero", "float", "no-id", "pi-mismatch", "bad-mode"],
    )
    def test_invalid_payments_write_nothing(self, organization, payment):
        with pytest.raises(mc.ManagedCreditError):
            mc.fulfil_topup(organization, payment=payment)
        assert not ManagedCreditEntry.objects.exists()


class TestWalletBuckets:
    def test_spend_draws_promo_then_purchased_then_debt(self, organization):
        mc.grant(
            organization,
            2_000,
            idempotency_key="g",
            actor=None,
            note="",
            bucket=Bucket.PROMO,
        )
        mc.fulfil_topup(organization, payment=_session(amount=3_000))

        spend, _ = mc.settle_spend(spend_record(organization, "req_b1", billed=7_000))

        assert (
            spend.funded_promo_micros,
            spend.funded_purchased_micros,
            spend.funded_debt_micros,
        ) == (
            -2_000,
            -3_000,
            -2_000,
        )
        assert spend.funding == ""  # split across buckets
        led = mc.ledger_balance(organization)
        assert (
            led.balance_micros,
            led.debt_micros,
            led.promo_micros,
            led.purchased_micros,
        ) == (
            -2_000,
            2_000,
            0,
            0,
        )
        # The next top-up repays the debt first.
        topup = mc.fulfil_topup(organization, payment=_session(amount=5_000)).entry
        assert (topup.funded_debt_micros, topup.funded_purchased_micros) == (
            2_000,
            3_000,
        )
        assert mc.ledger_balance(organization).balance_micros == 3_000
        _ok(reconcile.check_i2(org=organization))
        _ok(reconcile.check_i5(organization))

    def test_refund_returns_to_the_spends_buckets(self, organization):
        mc.grant(organization, 1_000, idempotency_key="g", actor=None, note="")
        mc.fulfil_topup(organization, payment=_session(amount=1_000))
        mc.settle_spend(spend_record(organization, "req_r", billed=3_000))

        refund, _ = mc.refund(organization, "req_r", reason="bad", actor=None)

        assert (
            refund.funded_debt_micros,
            refund.funded_purchased_micros,
            refund.funded_promo_micros,
        ) == (
            1_000,
            1_000,
            1_000,
        )
        assert refund.related_entry_id is not None and refund.product == "gateway"
        led = mc.ledger_balance(organization)
        assert (
            led.balance_micros,
            led.promo_micros,
            led.purchased_micros,
            led.debt_micros,
        ) == (
            2_000,
            1_000,
            1_000,
            0,
        )
        _ok(reconcile.check_i5(organization))

    def test_reversal_and_dispute_shortfalls_become_debt(self, organization):
        mc.fulfil_topup(organization, payment=_pi("pi_test_r", amount=10_000))
        mc.settle_spend(spend_record(organization, "req_d", billed=8_000))

        reversal, created = mc.reverse_topup(
            organization,
            refund_id="re_1",
            payment_intent_id="pi_test_r",
            amount_micros=6_000,
            mode=mc.StripeMode.TEST,
            stripe_event_id="evt_re",
        )
        _, again = mc.reverse_topup(
            organization,
            refund_id="re_1",
            payment_intent_id="pi_test_r",
            amount_micros=6_000,
            mode=mc.StripeMode.TEST,
        )
        assert (created, again) == (True, False)
        assert (reversal.kind, reversal.amount_micros) == (Kind.TOPUP_REVERSAL, -6_000)
        assert (reversal.funded_purchased_micros, reversal.funded_debt_micros) == (
            -2_000,
            -4_000,
        )
        with pytest.raises(mc.ManagedCreditError):
            mc.reverse_topup(
                organization,
                refund_id="re_2",
                payment_intent_id="pi_test_r",
                amount_micros=5_000,
                mode=mc.StripeMode.TEST,
            )
        hold, _ = mc.hold_dispute(
            organization,
            dispute_id="dp_1",
            payment_intent_id="pi_test_r",
            amount_micros=4_000,
            mode=mc.StripeMode.TEST,
        )
        assert (hold.kind, hold.funded_debt_micros) == (Kind.DISPUTE_HOLD, -4_000)
        assert mc.ledger_balance(organization).debt_micros == 8_000
        release, _ = mc.release_dispute(
            organization, dispute_id="dp_1", mode=mc.StripeMode.TEST
        )
        assert (release.amount_micros, release.funded_debt_micros) == (4_000, 4_000)
        with pytest.raises(mc.ManagedCreditError):
            mc.release_dispute(
                organization, dispute_id="dp_none", mode=mc.StripeMode.TEST
            )
        led = mc.ledger_balance(organization)
        assert led.balance_micros == 10_000 - 8_000 - 6_000
        _ok(reconcile.check_i2(org=organization))
        _ok(reconcile.check_i5(organization))

    def test_reversal_needs_a_credited_top_up_of_the_org(self, organization):
        with pytest.raises(mc.ManagedCreditError):
            mc.reverse_topup(
                organization,
                refund_id="re_x",
                payment_intent_id="pi_unknown",
                amount_micros=1,
                mode=mc.StripeMode.TEST,
            )

    def test_commit_grant_and_promo_expiry(self, organization):
        mc.grant(
            organization,
            5_000,
            idempotency_key="c",
            actor=None,
            note="",
            bucket=Bucket.COMMIT,
        )
        mc.grant(
            organization,
            2_000,
            idempotency_key="p",
            actor=None,
            note="",
            expires_at=datetime(2026, 12, 1, tzinfo=UTC),
            category="signup",
        )
        mc.expire(organization, 500, idempotency_key="e")
        led = mc.ledger_balance(organization)
        assert (led.commit_micros, led.promo_micros) == (5_000, 1_500)
        account = _account(organization)
        assert (account.commit_total_micros, account.promo_total_micros) == (
            5_000,
            2_000,
        )
        with pytest.raises(mc.ManagedCreditError):
            mc.grant(
                organization,
                1,
                idempotency_key="d",
                actor=None,
                note="",
                bucket=Bucket.PURCHASED,
            )
        _ok(reconcile.check_i5(organization))


class TestSpendEntry:
    def test_spend_carries_ledger_columns_and_its_display_event(
        self, organization, workspace
    ):
        with with_catalog():
            entry, created = mc.settle_spend(
                spend_record(organization, "req_ev"), workspace_id=str(workspace.pk)
            )
        assert created
        assert (entry.product, entry.dimension, entry.unit) == (
            "gateway",
            "managed_spend",
            "usd_micros",
        )
        assert (entry.source_kind, entry.source_ref) == ("model_call", "req_ev")
        assert entry.agent_id == "researcher" and entry.workspace_id == workspace.pk
        assert entry.quantity == 11_815 and entry.period == entry.occurred_at.strftime(
            "%Y-%m"
        )
        # The fixture catalog is unconfirmed and the flag is off: none is in force.
        assert entry.price_catalog_version == ""

        event_id = mc.spend_event_id("req_ev")
        assert str(event_id) == str(
            uuid.uuid5(uuid.NAMESPACE_URL, "agentcc:spend:req_ev")
        )
        log = UsageEventLog.objects.get(event_id=event_id)
        assert (log.event_type, log.dimension, log.amount_raw) == (
            "gateway_managed_spend",
            "managed_spend",
            11_815,
        )
        assert log.workspace_id == workspace.pk
        record = UsageEventRecord.objects.get(event=log)
        assert record.rated_entry_id == entry.pk and record.not_billable_reason == ""
        schema = golden("managed-settlement-v1.schema.json")  # the record stays frozen
        assert "workspace_id" not in schema["properties"]

        # A replay writes nothing new and keeps the link.
        _, again = mc.settle_spend(spend_record(organization, "req_ev"))
        assert again is False
        assert UsageEventLog.objects.filter(event_id=event_id).count() == 1
        _ok(reconcile.check_i1(org=organization))

    def test_catalog_version_is_stamped_under_the_test_flag(self, organization):
        with with_catalog(
            BILLING_CATALOG_ALLOW_UNCONFIRMED=True, STRIPE_SECRET_KEY="sk_test_x"
        ):
            entry, _ = mc.settle_spend(spend_record(organization, "req_flag"))
        assert entry.price_catalog_version == "price-catalog.v0"

    def test_workspace_of_another_org_is_dropped_not_refused(self, organization, user):
        other = Organization.objects.create(name="Other org")
        from accounts.models.workspace import Workspace

        foreign = Workspace.no_workspace_objects.create(
            name="w", organization=other, created_by=user
        )
        entry, _ = mc.settle_spend(
            spend_record(organization, "req_ws"), workspace_id=str(foreign.pk)
        )
        assert entry.workspace_id is None

    def test_one_payer_per_model_call(self, organization):
        mc.settle_spend(spend_record(organization, "req_one"))
        with pytest.raises(mc.DoubleMeteredCall):
            mc.debit_usage(
                organization,
                dimension="ai_credits",
                product="evals",
                unit="credits",
                quantity=1,
                amount_micros=10,
                period="2026-10",
                seq=0,
                funding_source="invoice",
                occurred_at=datetime.now(UTC),
                price_catalog_version="",
                source_kind="model_call",
                source_ref="req_one",
            )

    def test_zero_billed_spend_writes_no_display_event(self, organization):
        entry, _ = mc.settle_spend(spend_record(organization, "req_zero", billed=0))
        assert entry.amount_micros == 0 and entry.funded_promo_micros == 0
        assert not UsageEventLog.objects.filter(
            event_id=mc.spend_event_id("req_zero")
        ).exists()


class TestPushValue:
    def test_pushed_value_is_credits_minus_platform_spend_clamped(self, organization):
        mc.fulfil_topup(organization, payment=_session(amount=10_000))
        mc.debit_usage(
            organization,
            dimension="agent_runs",
            product="agents",
            unit="runs",
            quantity=2,
            amount_micros=4_000,
            period="2026-10",
            seq=0,
            funding_source="wallet",
            occurred_at=datetime.now(UTC),
            price_catalog_version="price-catalog.v0",
        )
        mc.settle_spend(spend_record(organization, "req_pv", billed=3_000))
        value = mc.pushed_value(_account(organization))
        assert value.as_request() == {
            "granted_total_micros": 6_000,
            "version": 2,
            "settled_spend_floor_micros": 3_000,
        }
        schema = golden("credits-granted-push-v1.schema.json")["$defs"]["Request"]
        Draft202012Validator(schema).validate(value.as_request())
        mc.debit_usage(
            organization,
            dimension="agent_runs",
            product="agents",
            unit="runs",
            quantity=9,
            amount_micros=9_000,
            period="2026-10",
            seq=1,
            funding_source="wallet",
            occurred_at=datetime.now(UTC),
            price_catalog_version="price-catalog.v0",
        )
        assert mc.pushed_value(_account(organization)).granted_total_micros == 0
        _ok(reconcile.check_i5(organization))

    def test_invoice_funded_debit_changes_no_balance(self, organization):
        mc.fulfil_topup(organization, payment=_session(amount=10_000))
        rows, created = mc.debit_usage(
            organization,
            dimension="ai_credits",
            product="evals",
            unit="credits",
            quantity=5,
            amount_micros=700,
            period="2026-10",
            seq=3,
            funding_source="invoice",
            occurred_at=datetime.now(UTC),
            price_catalog_version="price-catalog.v0",
        )
        again, created_again = mc.debit_usage(
            organization,
            dimension="ai_credits",
            product="evals",
            unit="credits",
            quantity=5,
            amount_micros=700,
            period="2026-10",
            seq=3,
            funding_source="invoice",
            occurred_at=datetime.now(UTC),
            price_catalog_version="price-catalog.v0",
        )
        assert (created, created_again) == (True, False) and rows[0].pk == again[0].pk
        assert rows[0].funding == "invoice" and rows[0].funded_promo_micros is None
        account = _account(organization)
        assert (account.platform_spend_micros, account.version) == (0, 1)
        assert mc.ledger_balance(organization).balance_micros == 10_000
        _ok(reconcile.check_i5(organization))

    def test_404_managed_off_is_not_applicable_and_not_retried(
        self, organization, django_capture_on_commit_callbacks
    ):
        fake = FakeGateway(managed_off=True)
        with patched_gateway(fake):
            with django_capture_on_commit_callbacks(execute=True):
                mc.fulfil_topup(organization, payment=_session(amount=1_000))
            account = _account(organization)
            assert account.gateway_push_state == gateway_push.STATE_NA_MANAGED_OFF
            assert (account.version, account.gateway_acked_version) == (1, 0)
            # Another credit within NA_RECHECK does not try again, nor does the sweep.
            with django_capture_on_commit_callbacks(execute=True):
                mc.fulfil_topup(organization, payment=_session(amount=1_000))
            assert mc.sweep_unacked_pushes() == 0
            # Managed models turned on: config_push's ensure_account_pushed sends one full push.
            fake.managed_off = False
            state = mc.ensure_account_pushed(organization)
        account = _account(organization)
        assert (
            state["granted_version"] == 2
            and fake.pushes_for(organization)[-1]["granted_total_micros"] == 2_000
        )
        assert (account.gateway_push_state, account.gateway_acked_version) == ("ok", 2)

    def test_sweep_pushes_accounts_behind_their_version(self, organization):
        fake = FakeGateway()
        mc.fulfil_topup(
            organization, payment=_session(amount=1_000)
        )  # on_commit never runs here
        with patched_gateway(fake):
            assert mc.sweep_unacked_pushes() >= 1
        assert _account(organization).gateway_acked_version == 1


class TestBalanceCacheAndAdmission:
    def test_zero_balance_refuses_managed_spend_only(
        self, organization, django_capture_on_commit_callbacks
    ):
        org_id = str(organization.pk)
        with with_catalog():
            refusal = check_usage(org_id, "gateway_managed_spend")
            assert (refusal.allowed, refusal.error_code) == (
                False,
                balance_cache.ERROR_CODE,
            )
            assert balance_cache.ADD_CREDITS_PATH in refusal.reason
            with patched_gateway(FakeGateway()):
                with django_capture_on_commit_callbacks(execute=True):
                    mc.fulfil_topup(organization, payment=_session(amount=1_000))
            assert balance_cache.read(org_id) == 1_000
            assert (
                balance_cache.admission_refusal(org_id, "gateway_managed_spend") is None
            )
            with django_capture_on_commit_callbacks(execute=True):
                mc.settle_spend(
                    spend_record(organization, "req_zero_bal", billed=1_000)
                )
            assert balance_cache.read(org_id) == 0
            assert (
                balance_cache.admission_refusal(org_id, "gateway_managed_spend")
                is not None
            )
            # Platform admission is unchanged in phase 2a (legacy path).
            assert (
                balance_cache.admission_refusal(org_id, "turing_large_evaluator")
                is None
            )

    def test_ledger_path_prepaid_org_is_refused_on_every_type(self, organization):
        org_id = str(organization.pk)
        ManagedCreditAccount.objects.create(
            organization=organization, funding="prepaid_only", billing_path="ledger"
        )
        balance_cache.write(
            org_id, balance_micros=0, entry_seq=-1
        )  # stale entry loses to the DB
        from ee.usage.services.emitter import get_redis

        get_redis().delete(balance_cache.CACHE_KEY.format(org_id=org_id))
        assert (
            balance_cache.admission_refusal(org_id, "turing_large_evaluator")
            is not None
        )

    def test_older_sequence_never_overwrites_a_newer_balance(self, organization):
        org_id = str(organization.pk)
        balance_cache.write(org_id, balance_micros=500, entry_seq=7)
        balance_cache.write(org_id, balance_micros=900, entry_seq=6)
        assert balance_cache.read(org_id) == 500
        balance_cache.write(org_id, balance_micros=100, entry_seq=8)
        assert balance_cache.read(org_id) == 100

    def test_balance_changed_signal_on_commit(
        self, organization, django_capture_on_commit_callbacks
    ):
        seen = []

        def receiver(sender, **kwargs):
            seen.append(kwargs)

        balance_cache.balance_changed.connect(receiver)
        try:
            with patched_gateway(FakeGateway()):
                with django_capture_on_commit_callbacks(execute=True):
                    mc.fulfil_topup(organization, payment=_session(amount=1_234))
        finally:
            balance_cache.balance_changed.disconnect(receiver)
        assert seen[-1]["org_id"] == str(organization.pk)
        assert (seen[-1]["balance_micros"], seen[-1]["entry_seq"]) == (1_234, 1)


class TestAutoTopup:
    @pytest.fixture(autouse=True)
    def _actor(self, user):
        # Every enabled save is a signed-in user acknowledging the current terms.
        self.actor = user

    def _enable(self, org, **overrides):
        args = {
            "enabled": True,
            "threshold_micros": 1_000,
            "amount_micros": 5_000,
            "daily_count_cap": 2,
            "daily_amount_cap_micros": 8_000,
            "payment_method_id": "pm_card_visa",
            "consent_text_version": AUTO_RELOAD_CONSENT_VERSION,
            "actor": self.actor,
        }
        args.update(overrides)
        return mc.configure_auto_topup(org, **args)

    def test_off_by_default_and_enabling_needs_every_limit(self, organization):
        assert mc.claim_auto_topup(organization) is None
        with pytest.raises(ValueError):
            mc.configure_auto_topup(
                organization,
                enabled=True,
                threshold_micros=1,
                amount_micros=None,
                daily_count_cap=1,
                daily_amount_cap_micros=1,
                payment_method_id="pm",
                consent_text_version="v",
            )
        account = self._enable(organization)
        assert account.autoreload_consent_version == AUTO_RELOAD_CONSENT_VERSION
        assert account.autoreload_consent_at

    def test_enabling_records_the_actors_receipt_for_the_saved_settings(
        self, organization
    ):
        account = self._enable(organization)
        receipt = AgentccAutoReloadConsent.objects.get(organization=organization)
        assert (receipt.actor_id, receipt.actor_identifier) == (
            self.actor.pk,
            self.actor.pk,
        )
        assert receipt.consent_version == AUTO_RELOAD_CONSENT_VERSION
        assert receipt.accepted_at == account.autoreload_consent_at
        assert (receipt.threshold_micros, receipt.amount_micros) == (1_000, 5_000)
        assert has_current_auto_reload_consent(_account(organization))

    @pytest.mark.parametrize(
        "overrides",
        [{"consent_text_version": "v1"}, {"actor": None}],
        ids=["outdated_terms", "no_actor"],
    )
    def test_enabling_without_current_terms_or_actor_changes_nothing(
        self, organization, overrides
    ):
        with pytest.raises(AutoTopupError):
            self._enable(organization, **overrides)
        assert not AgentccAutoReloadConsent.objects.filter(
            organization=organization
        ).exists()
        assert not ManagedCreditAccount.objects.filter(
            organization=organization, autoreload_enabled=True
        ).exists()
        assert mc.claim_auto_topup(organization) is None

    def test_a_failed_receipt_write_rolls_the_settings_back(
        self, organization, monkeypatch
    ):
        from django.db import DatabaseError

        def refuse(**kwargs):
            raise DatabaseError("synthetic receipt failure")

        monkeypatch.setattr(AgentccAutoReloadConsent.objects, "create", refuse)
        with pytest.raises(DatabaseError):
            self._enable(organization)
        account = ManagedCreditAccount.objects.filter(organization=organization).first()
        assert account is None or not account.autoreload_enabled

    @pytest.mark.parametrize(
        "tamper", ["receipt_deleted", "amount_changed", "threshold_changed"]
    )
    def test_claim_without_a_matching_current_receipt_never_charges(
        self, organization, tamper
    ):
        # Without its receipt the row is a legacy "enabled" flag, version and time: not consent.
        self._enable(organization)
        account = ManagedCreditAccount.objects.filter(organization=organization)
        if tamper == "receipt_deleted":
            AgentccAutoReloadConsent.objects.filter(organization=organization).delete()
        elif tamper == "amount_changed":
            account.update(autoreload_amount_micros=6_000)
        else:
            account.update(autoreload_threshold_micros=2_000)
        assert mc.claim_auto_topup(organization) is None
        account = _account(organization)
        assert (account.autoreload_in_flight_seq, account.autoreload_seq) == (None, 0)

    def test_one_charge_in_flight_and_daily_caps(self, organization):
        self._enable(organization)
        claim = mc.claim_auto_topup(organization)
        assert (
            claim.seq == 1
            and claim.idempotency_key == f"pi:autotopup:{organization.pk}:1"
        )
        assert mc.claim_auto_topup(organization) is None  # one in flight
        mc.fulfil_topup(
            organization,
            payment=_pi(
                "pi_auto_1", amount=5_000, purpose=mc.TopupPurpose.AUTO_TOPUP, seq=1
            ),
        )
        assert _account(organization).autoreload_in_flight_seq is None
        assert mc.claim_auto_topup(organization) is None  # balance 5000 above threshold
        mc.settle_spend(spend_record(organization, "req_auto", billed=4_500))
        # The amount cap (8000) leaves no room for a second 5000 today.
        assert mc.claim_auto_topup(organization) is None
        self._enable(organization, daily_amount_cap_micros=20_000)
        second = mc.claim_auto_topup(organization)
        assert second.seq == 2
        mc.finish_auto_topup(
            organization, seq=2, outcome="failed", reason="card_declined"
        )
        account = _account(organization)
        assert (account.autoreload_enabled, account.autoreload_disabled_reason) == (
            False,
            "card_declined",
        )
        with pytest.raises(mc.AutoTopupNotClaimed):
            mc.finish_auto_topup(organization, seq=9, outcome="failed")

    def test_count_cap(self, organization):
        self._enable(organization, daily_count_cap=1, daily_amount_cap_micros=50_000)
        claim = mc.claim_auto_topup(organization)
        mc.fulfil_topup(
            organization,
            payment=_pi(
                "pi_auto_c",
                amount=5_000,
                purpose=mc.TopupPurpose.AUTO_TOPUP,
                seq=claim.seq,
            ),
        )
        mc.settle_spend(spend_record(organization, "req_cc", billed=5_000))
        assert mc.claim_auto_topup(organization) is None


class TestStripeRecords:
    def test_event_log_is_unique_and_marked(self, organization):
        row, created = stripe_records.record_stripe_event(
            event_id="evt_s1",
            event_type="checkout.session.completed",
            mode="test",
            object_id="cs_test_1",
            org=organization,
        )
        _, again = stripe_records.record_stripe_event(
            event_id="evt_s1",
            event_type="checkout.session.completed",
            mode="test",
        )
        assert (created, again) == (True, False)
        stripe_records.mark_stripe_event("evt_s1", status="processed")
        row.refresh_from_db()
        assert (row.status, row.attempts) == ("processed", 1)
        with pytest.raises(stripe_records.StripeRecordError):
            stripe_records.record_stripe_event(
                event_id="evt_s2", event_type="x", mode="sandbox"
            )

    def test_invoice_mirror_status_only_moves_forward(self, organization):
        common = {
            "mode": "test",
            "stripe_invoice_id": "in_1Test9",
            "kind": "topup",
            "period_start": None,
            "period_end": None,
            "subtotal_cents": 2500,
            "tax_cents": 0,
            "total_cents": 2500,
            "amount_due_cents": 0,
            "hosted_invoice_url": "https://invoice.example/i",
            "stripe_created_at": datetime.now(UTC) - timedelta(minutes=1),
        }
        row, created = stripe_records.upsert_invoice_mirror(
            organization, status="open", **common
        )
        stripe_records.upsert_invoice_mirror(organization, status="paid", **common)
        row, created_again = stripe_records.upsert_invoice_mirror(
            organization, status="open", **common
        )
        assert (created, created_again, row.status) == (True, False, "paid")
        with pytest.raises(stripe_records.StripeRecordError):
            stripe_records.upsert_invoice_mirror(
                organization,
                status="open",
                **{**common, "hosted_invoice_url": "http://x"},
            )


def test_billing_profile_derivation(organization):
    profile = mc.billing_profile_for_account(None, org_id=organization.pk)
    assert (
        profile["billing_channel"],
        profile["funding"],
        profile["billing_path"],
    ) == (
        "none",
        "prepaid_only",
        "legacy",
    )
    account = ManagedCreditAccount.objects.create(
        organization=organization,
        funding="prepaid_then_postpaid",
        billing_channel="stripe_invoiced",
    )
    profile = mc.billing_profile_for_account(account)
    assert (profile["billing_channel"], profile["funding"]) == (
        "stripe_invoiced",
        "prepaid_then_postpaid",
    )
