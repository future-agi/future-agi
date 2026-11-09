"""The ledger's money freeze and the NULL-cap refusal (one-key F2, F3, F4).

The ee Stripe adapter (BL-STRIPE, ``money_freeze_reason``) assumes the ledger
itself enforces what a receivable or an open dispute means for the org's
money. Its rule, mirrored here without importing ee.cloud:

- **receivable_open**: the account owes (its debt bucket is above zero);
- **dispute_open**: a ``dispute_hold`` with no ``dispute_release`` of the same
  dispute. A lost dispute leaves its hold standing, so it stays frozen.

F3: no new automatic grant while frozen (ruling 971bd25c: ANY debt, or an
unmatched dispute hold). Authorized staff/admin grants may repay debt first;
the API tests cover this exception. An open dispute still refuses new grants.
Top-ups and a person's ``adjust()`` corrections still write and are audited.
F4: while frozen the spendable balance the ledger caches
and sends is 0 (never negative, never promo or commit during an open dispute)
and the gateway's granted push leaves it no spendable money; the stored buckets
are not changed by the freeze. F2: ``claim_auto_topup`` refuses a NULL daily
count or amount cap (it used to read NULL as unlimited), checked under the
account row lock. It also refuses while the org is frozen, checked under the
same lock (the adapter's own freeze check runs before the claim, outside it).

Stripe is never called: the adapter's calls are made with synthetic values.
"""

import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from django.db import connection, transaction

from accounts.models.organization import Organization
from accounts.models.user import User
from agentcc.auto_reload_terms import AUTO_RELOAD_CONSENT_VERSION
from agentcc.services.auto_reload_consent import has_current_auto_reload_consent
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.services import auto_topup, balance_cache, reconcile
from ee.usage.services import managed_credits as mc
from ee.usage.services.funding import Bucket
from ee.usage.tests.ledger_support import (
    FakeGateway,
    migrate_usage_to_latest,
    patched_gateway,
    spend_record,
    with_catalog,
)

Kind = ManagedCreditEntry.Kind
pytestmark = pytest.mark.integration


# --- helpers ----------------------------------------------------------------------


def _pi(pi_id, amount):
    return mc.TopupPayment(
        source=mc.TopupSource.PAYMENT_INTENT,
        object_id=pi_id,
        amount_micros=amount,
        mode=mc.StripeMode.TEST,
    )


def _top_up(org, pi_id, amount):
    return mc.fulfil_topup(org, payment=_pi(pi_id, amount))


def _grant(org, key, micros=1_000, bucket=Bucket.PROMO):
    return mc.grant(
        org, micros, idempotency_key=key, actor=None, note="f3", bucket=bucket
    )


def _spend(org, request_id, billed):
    return mc.settle_spend(spend_record(org, request_id, billed=billed))


def _hold(org, dispute_id, pi_id, amount):
    return mc.hold_dispute(
        org,
        dispute_id=dispute_id,
        payment_intent_id=pi_id,
        amount_micros=amount,
        mode=mc.StripeMode.TEST,
    )


def _release(org, dispute_id):
    return mc.release_dispute(org, dispute_id=dispute_id, mode=mc.StripeMode.TEST)


def _reverse(org, refund_id, pi_id, amount):
    return mc.reverse_topup(
        org,
        refund_id=refund_id,
        payment_intent_id=pi_id,
        amount_micros=amount,
        mode=mc.StripeMode.TEST,
    )


def _account(org):
    return ManagedCreditAccount.objects.get(organization=org)


def _buckets(org):
    a = _account(org)
    return (
        a.promo_balance_micros,
        a.commit_balance_micros,
        a.purchased_balance_micros,
        a.debt_micros,
    )


def _snapshot(org):
    """Everything a refused write must leave as it was."""
    a = _account(org)
    return (
        _buckets(org),
        a.granted_total_micros,
        a.version,
        a.entry_seq,
        ManagedCreditEntry.objects.filter(organization=org).count(),
    )


def _receivable(org, tag="recv"):
    """A refund of a part-spent top-up: purchased 0, debt 8_000 (a receivable)."""
    _top_up(org, f"pi_{tag}", 10_000)
    _spend(org, f"req_{tag}", 8_000)
    _reverse(org, f"re_{tag}", f"pi_{tag}", 10_000)
    assert _buckets(org) == (0, 0, 0, 8_000)


def _open_dispute(org, tag="disp"):
    """Hold 4_000 from a 10_000 top-up without assuming other buckets are empty."""
    _top_up(org, f"pi_{tag}", 10_000)
    promo, commit, purchased, debt = _buckets(org)
    held_purchased = min(purchased, 4_000)
    _hold(org, f"dp_{tag}", f"pi_{tag}", 4_000)
    assert _buckets(org) == (
        promo, commit, purchased - held_purchased, debt + 4_000 - held_purchased
    )


def _gateway_checks_ok(org):
    results = {r.name: r for r in reconcile.check_gateway(org)}
    for name in ("I5", "I6", "I11"):
        assert results[name].ok, (name, results[name].failures)
    assert not results["I6"].exempt and not results["I11"].exempt


@pytest.fixture
def ledger_io(django_capture_on_commit_callbacks):
    """Run a write and its on-commit work (cache, signal, push) against a fake gateway."""
    fake = FakeGateway()
    seen = []

    def receiver(sender, **kwargs):
        seen.append(kwargs["balance_micros"])

    def run(call, *args, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return call(*args, **kwargs)

    balance_cache.balance_changed.connect(receiver)
    try:
        with patched_gateway(fake):
            yield SimpleNamespace(run=run, fake=fake, seen=seen)
    finally:
        balance_cache.balance_changed.disconnect(receiver)


# --- the freeze predicate (the adapter's money_freeze_reason, ledger-native) ------


class TestFreezeReason:
    def test_a_clean_org_is_not_frozen(self, organization):
        assert mc.freeze_reason(organization) == ""
        _top_up(organization, "pi_f_clean", 1_000)
        assert mc.freeze_reason(organization) == ""

    def test_any_debt_is_a_receivable(self, organization):
        _receivable(organization)
        assert mc.freeze_reason(organization) == mc.RECEIVABLE_OPEN == "receivable_open"

    def test_a_hold_without_its_release_is_an_open_dispute(self, organization):
        _open_dispute(organization)
        assert mc.freeze_reason(organization) == mc.DISPUTE_OPEN == "dispute_open"
        _release(organization, "dp_disp")
        assert mc.freeze_reason(organization) == ""

    @pytest.mark.parametrize("bucket", [Bucket.PROMO, Bucket.COMMIT])
    def test_open_dispute_preserves_existing_grant_buckets(self, organization, bucket):
        _grant(organization, "dispute-existing-grant", micros=2_000, bucket=bucket)

        _open_dispute(organization)

        expected = (2_000, 0, 6_000, 0) if bucket == Bucket.PROMO else (0, 2_000, 6_000, 0)
        assert _buckets(organization) == expected
        assert mc.freeze_reason(organization) == mc.DISPUTE_OPEN

    def test_a_lost_dispute_keeps_its_hold_and_the_freeze(self, organization):
        # Lost: the adapter writes the hold again (idempotent) and never a release.
        _open_dispute(organization)
        _, created = _hold(organization, "dp_disp", "pi_disp", 4_000)
        assert created is False
        assert mc.freeze_reason(organization) == "dispute_open"

    def test_the_receivable_is_named_before_the_dispute(self, organization):
        # A dispute of the whole charge after 8_000 was spent: debt and a hold.
        _top_up(organization, "pi_f_both", 10_000)
        _spend(organization, "req_f_both", 8_000)
        _hold(organization, "dp_f_both", "pi_f_both", 10_000)
        assert _buckets(organization) == (0, 0, 0, 8_000)
        assert mc.freeze_reason(organization) == "receivable_open"

    def test_releasing_another_dispute_does_not_lift_this_one(self, organization):
        _top_up(organization, "pi_f_a", 10_000)
        _top_up(organization, "pi_f_b", 10_000)
        _hold(organization, "dp_f_a", "pi_f_a", 1_000)
        _hold(organization, "dp_f_b", "pi_f_b", 1_000)
        _release(organization, "dp_f_b")
        assert mc.freeze_reason(organization) == "dispute_open"
        _release(organization, "dp_f_a")
        assert mc.freeze_reason(organization) == ""

    def test_another_orgs_dispute_does_not_freeze_this_org(self, organization):
        other = Organization.objects.create(name="Freeze: another org")
        _open_dispute(other, tag="other")
        assert mc.freeze_reason(other) == "dispute_open"
        assert mc.freeze_reason(organization) == ""
        _top_up(organization, "pi_f_mine", 1_000)
        assert mc.freeze_reason(organization) == ""


# --- F3: no automatic grant while frozen; what still writes -----------------------

# Ruling 971bd25c still refuses automatic grants while the org has any debt or
# an unmatched dispute_hold. The authorized staff/admin debt-repayment exception
# is exercised through the API; these helper calls are not staff-authorized.
# The historical 492.4 / 529.2 correction rows continue to use adjust().


class TestF3Grants:
    @pytest.mark.parametrize("bucket", [Bucket.PROMO, Bucket.COMMIT])
    def test_a_grant_is_refused_while_the_org_has_a_receivable(
        self, organization, bucket
    ):
        _receivable(organization)
        before = _snapshot(organization)
        with pytest.raises(mc.ManagedCreditError):
            _grant(organization, f"f3-recv-{bucket.value}", bucket=bucket)
        assert _snapshot(organization) == before
        assert not ManagedCreditEntry.objects.filter(
            organization=organization, kind=Kind.GRANT
        ).exists()

    @pytest.mark.parametrize("bucket", [Bucket.PROMO, Bucket.COMMIT])
    def test_a_grant_is_refused_while_a_dispute_is_open(self, organization, bucket):
        _open_dispute(organization)
        before = _snapshot(organization)
        with pytest.raises(mc.ManagedCreditError):
            _grant(organization, f"f3-disp-{bucket.value}", bucket=bucket)
        assert _snapshot(organization) == before

    def test_a_lost_dispute_refuses_grants(self, organization):
        _open_dispute(organization)
        _hold(organization, "dp_disp", "pi_disp", 4_000)  # lost: the hold stands
        with pytest.raises(mc.ManagedCreditError):
            _grant(organization, "f3-lost")

    def test_a_grant_is_allowed_after_the_dispute_is_released(self, organization):
        _open_dispute(organization)
        _release(organization, "dp_disp")
        entry, created = _grant(organization, "f3-released", micros=2_000)
        assert created and entry.kind == Kind.GRANT
        assert _buckets(organization) == (2_000, 0, 10_000, 0)

    def test_a_debt_repaying_top_up_is_written_and_lifts_the_freeze(self, organization):
        _receivable(organization)
        result = _top_up(organization, "pi_f3_repay", 8_000)
        assert result.created
        assert (
            result.entry.funded_debt_micros,
            result.entry.funded_purchased_micros,
        ) == (8_000, 0)
        assert _buckets(organization) == (0, 0, 0, 0)
        assert mc.freeze_reason(organization) == ""
        _, created = _grant(organization, "f3-after-repay")
        assert created

    def test_a_partly_repaying_top_up_is_written_and_the_freeze_stays(
        self, organization
    ):
        _receivable(organization)
        result = _top_up(organization, "pi_f3_part", 3_000)
        assert result.created and result.entry.funded_debt_micros == 3_000
        assert _buckets(organization) == (0, 0, 0, 5_000)
        assert mc.freeze_reason(organization) == "receivable_open"

    def test_an_adjustment_is_written_while_frozen_and_repays_the_receivable_first(
        self, organization
    ):
        # A person's correction is not a grant: it writes, repaying debt first.
        _receivable(organization)
        entry, created = mc.adjust(
            organization, 8_000, idempotency_key="f3-adj-recv", reason="t", actor=None
        )
        assert created and entry.kind == Kind.ADJUSTMENT
        assert (entry.funded_debt_micros, entry.funded_promo_micros) == (8_000, 0)
        assert _buckets(organization) == (0, 0, 0, 0)
        assert mc.freeze_reason(organization) == ""

    def test_replaying_a_grant_written_before_the_freeze_returns_it(self, organization):
        entry, created = _grant(organization, "f3-replay", micros=2_000)
        _open_dispute(organization)
        again, created_again = _grant(organization, "f3-replay", micros=2_000)
        assert (created, created_again) == (True, False)
        assert again.pk == entry.pk

    def test_other_kinds_still_write_while_a_dispute_is_open(self, organization):
        _open_dispute(organization)
        written = [
            _spend(organization, "req_f3_kinds", 1_000)[1],
            _top_up(organization, "pi_f3_kinds", 2_000).created,
            mc.refund(organization, "req_f3_kinds", reason="failed", actor=None)[1],
            mc.adjust(
                organization, 500, idempotency_key="f3-adj+", reason="t", actor=None
            )[1],
            mc.adjust(
                organization, -200, idempotency_key="f3-adj-", reason="t", actor=None
            )[1],
            mc.expire(organization, 100, idempotency_key="f3-exp")[1],
        ]
        assert written == [True] * 6
        assert mc.freeze_reason(organization) == "dispute_open"


# --- F4: no spendable money while frozen; the buckets are untouched ---------------


class TestF4Spendable:
    def test_an_open_dispute_caches_and_pushes_no_spendable_money(
        self, organization, ledger_io
    ):
        org_id = str(organization.pk)
        ledger_io.run(_grant, organization, "f4-promo", micros=5_000)
        ledger_io.run(_top_up, organization, "pi_f4_disp", 10_000)
        assert balance_cache.read(org_id) == 15_000

        ledger_io.run(_hold, organization, "dp_f4_disp", "pi_f4_disp", 4_000)

        # The stored buckets and the ledger balance are not changed by the freeze.
        assert _buckets(organization) == (5_000, 0, 6_000, 0)
        assert mc.ledger_balance(organization).balance_micros == 11_000
        # What is cached and signalled as spendable is 0, promo included.
        assert balance_cache.read(org_id) == 0
        assert ledger_io.seen[-1] == 0
        # The gateway is left nothing to spend: granted capped at the settled spend.
        assert mc.pushed_value(_account(organization)).granted_total_micros == 0
        state = ledger_io.fake.state(organization.pk)
        assert (state["granted_total_micros"], state["available_micros"]) == (0, 0)
        _gateway_checks_ok(organization)

        ledger_io.run(_release, organization, "dp_f4_disp")

        assert balance_cache.read(org_id) == 15_000
        assert ledger_io.seen[-1] == 15_000
        state = ledger_io.fake.state(organization.pk)
        assert (state["granted_total_micros"], state["available_micros"]) == (
            15_000,
            15_000,
        )
        _gateway_checks_ok(organization)

    def test_a_receivable_next_to_promo_is_not_spendable(self, organization, ledger_io):
        org_id = str(organization.pk)
        ledger_io.run(_top_up, organization, "pi_f4_recv", 10_000)
        ledger_io.run(_spend, organization, "req_f4_recv", 8_000)
        ledger_io.run(_grant, organization, "f4-recv-promo", micros=5_000)
        # A refund of 6_000: purchased gives its 2_000, 4_000 becomes a receivable,
        # while 5_000 of promo is still in its bucket.
        ledger_io.run(_reverse, organization, "re_f4_recv", "pi_f4_recv", 6_000)

        assert _buckets(organization) == (5_000, 0, 0, 4_000)
        assert mc.ledger_balance(organization).balance_micros == 1_000
        assert balance_cache.read(org_id) == 0
        assert ledger_io.seen[-1] == 0
        state = ledger_io.fake.state(organization.pk)
        assert (
            state["granted_total_micros"],
            state["spent_micros"],
            state["available_micros"],
        ) == (8_000, 8_000, 0)
        _gateway_checks_ok(organization)

        # Paying the receivable restores the promo as spendable money.
        ledger_io.run(_top_up, organization, "pi_f4_repay", 4_000)

        assert _buckets(organization) == (5_000, 0, 0, 0)
        assert balance_cache.read(org_id) == 5_000
        assert ledger_io.seen[-1] == 5_000
        state = ledger_io.fake.state(organization.pk)
        assert (state["granted_total_micros"], state["available_micros"]) == (
            13_000,
            5_000,
        )
        _gateway_checks_ok(organization)

    def test_an_overdraft_caches_zero_never_a_negative_balance(
        self, organization, ledger_io
    ):
        org_id = str(organization.pk)
        ledger_io.run(_spend, organization, "req_f4_over", 5_000)

        assert _buckets(organization) == (0, 0, 0, 5_000)
        assert mc.ledger_balance(organization).balance_micros == -5_000
        assert balance_cache.read(org_id) == 0
        assert ledger_io.seen[-1] == 0
        # An overdraft leaves the pushed value as it was: no version, no push.
        account = _account(organization)
        assert account.version == 0
        assert mc.pushed_value(account).granted_total_micros == 0
        assert ledger_io.fake.pushes_for(organization) == []

    def test_a_spend_settled_during_a_dispute_moves_the_cap_and_is_pushed(
        self, organization, ledger_io
    ):
        ledger_io.run(_grant, organization, "f4-sd-promo", micros=5_000)
        ledger_io.run(_top_up, organization, "pi_f4_sd", 10_000)
        ledger_io.run(_hold, organization, "dp_f4_sd", "pi_f4_sd", 4_000)
        version = _account(organization).version

        # A call the gateway admitted before the hold, settled after it.
        ledger_io.run(_spend, organization, "req_f4_sd", 1_000)

        account = _account(organization)
        assert account.version == version + 1
        assert account.gateway_acked_version == account.version
        state = ledger_io.fake.state(organization.pk)
        assert (
            state["granted_total_micros"],
            state["spent_micros"],
            state["available_micros"],
        ) == (1_000, 1_000, 0)
        assert balance_cache.read(str(organization.pk)) == 0
        _gateway_checks_ok(organization)

    def test_a_cache_miss_refills_zero_while_frozen(self, organization):
        from ee.usage.services.emitter import get_redis

        org_id = str(organization.pk)
        key = balance_cache.CACHE_KEY.format(org_id=org_id)
        _grant(organization, "f4-fill-promo", micros=5_000)
        _top_up(organization, "pi_f4_fill", 10_000)
        _hold(organization, "dp_f4_fill", "pi_f4_fill", 4_000)
        get_redis().delete(key)
        with with_catalog():
            refusal = balance_cache.admission_refusal(org_id, "gateway_managed_spend")
        assert refusal is not None and refusal.error_code == balance_cache.ERROR_CODE
        assert balance_cache.read(org_id) == 0

        _release(organization, "dp_f4_fill")
        get_redis().delete(key)
        with with_catalog():
            assert (
                balance_cache.admission_refusal(org_id, "gateway_managed_spend") is None
            )
        assert balance_cache.read(org_id) == 15_000


# --- the refusal names the freeze, not the generic "add credits" ------------------


def _refusal(org):
    """The ledger's admission answer for managed spend, refilled from Postgres."""
    from ee.usage.services.emitter import get_redis

    org_id = str(org.pk)
    get_redis().delete(balance_cache.CACHE_KEY.format(org_id=org_id))
    with with_catalog():
        return balance_cache.admission_refusal(org_id, "gateway_managed_spend")


class TestFreezeIsNamed:
    def test_a_receivable_names_what_is_owed_and_where_to_pay(self, organization):
        _receivable(organization)
        refusal = _refusal(organization)
        assert (refusal.allowed, refusal.error_code) == (
            False,
            balance_cache.ERROR_CODE,
        )
        assert refusal.reason == balance_cache.FROZEN_REASONS[mc.RECEIVABLE_OPEN]
        assert refusal.reason != balance_cache.EXHAUSTED_REASON
        assert "owes" in refusal.reason
        assert balance_cache.ADD_CREDITS_PATH in refusal.reason  # a top-up pays it

    def test_an_open_dispute_names_the_dispute_not_add_credits(self, organization):
        _open_dispute(organization)  # 6_000 still in purchased, all frozen
        refusal = _refusal(organization)
        assert refusal.error_code == balance_cache.ERROR_CODE
        assert refusal.reason == balance_cache.FROZEN_REASONS[mc.DISPUTE_OPEN]
        assert "dispute" in refusal.reason
        assert "Add credits" not in refusal.reason  # a top-up does not lift it
        assert balance_cache.ADD_CREDITS_PATH not in refusal.reason

        _release(organization, "dp_disp")
        assert _refusal(organization) is None

    def test_an_unfrozen_empty_org_keeps_the_add_credits_text(self, organization):
        refusal = _refusal(organization)
        assert refusal.reason == balance_cache.EXHAUSTED_REASON
        assert balance_cache.ADD_CREDITS_PATH in refusal.reason

    def test_every_freeze_reason_has_its_text_and_summary_value(self):
        from agentcc.serializers.managed_credits import FREEZE_REASONS

        reasons = {mc.RECEIVABLE_OPEN, mc.DISPUTE_OPEN}
        assert set(balance_cache.FROZEN_REASONS) == reasons
        assert set(FREEZE_REASONS) == reasons


def test_a_freeze_reason_read_error_never_drops_a_balance_refusal(organization):
    from unittest.mock import patch

    from django.db import OperationalError

    from ee.usage.services.metering import check_usage

    org_id = str(organization.pk)
    balance_cache.write(org_id, balance_micros=0, entry_seq=0)
    assert balance_cache.read(org_id) == 0

    with (
        with_catalog(),
        patch(
            "ee.usage.services.money_freeze.freeze_reason_of",
            side_effect=OperationalError("freeze reason unavailable"),
        ) as freeze_reason,
    ):
        refusal = check_usage(org_id, "gateway_managed_spend")

    freeze_reason.assert_called_once_with(org_id)
    assert (refusal.allowed, refusal.error_code) == (False, balance_cache.ERROR_CODE)
    assert refusal.reason == balance_cache.EXHAUSTED_REASON


# --- F2: a NULL daily cap is refused, under the account row lock ------------------

CAPS = ("autoreload_daily_count_cap", "autoreload_daily_amount_cap_micros")


def _enable_auto_topup(org, actor):
    return mc.configure_auto_topup(
        org,
        enabled=True,
        threshold_micros=1_000,
        amount_micros=5_000,
        daily_count_cap=2,
        daily_amount_cap_micros=20_000,
        payment_method_id="pm_card_visa",
        consent_text_version=AUTO_RELOAD_CONSENT_VERSION,
        actor=actor,
    )


class TestF2NullCaps:
    def test_both_caps_set_still_claims(self, organization, user):
        _enable_auto_topup(organization, user)
        claim = mc.claim_auto_topup(organization)
        assert claim is not None and claim.seq == 1

    @pytest.mark.parametrize("cap", CAPS)
    def test_a_null_cap_is_refused_not_read_as_unlimited(self, organization, user, cap):
        _enable_auto_topup(organization, user)
        ManagedCreditAccount.objects.filter(organization=organization).update(
            **{cap: None}
        )
        # The receipt binds threshold and amount, not the caps: consent is current.
        assert has_current_auto_reload_consent(_account(organization))
        assert mc.claim_auto_topup(organization) is None
        account = _account(organization)
        assert (account.autoreload_seq, account.autoreload_in_flight_seq) == (0, None)


def _a_session_waits_for_a_lock(timeout=10.0):
    deadline = time.monotonic() + timeout
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database()"
                " AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
            )
            if cursor.fetchone()[0] > 0:
                return True
            time.sleep(0.02)
    return False


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cap", CAPS)
def test_a_null_cap_saved_while_the_claim_waits_for_the_lock_is_refused(cap):
    """A settings save holds the account row and clears a cap; a claim waits
    for the row. The claim must read the cap after it gets the lock, so it
    sees NULL and refuses. A check made before the lock would have read the
    old cap and charged."""
    from tfc.constants.roles import OrganizationRoles

    migrate_usage_to_latest()
    org = Organization.objects.create(name=f"F2 lock {cap}")
    actor = User.objects.create_user(
        email=f"f2-lock-{uuid.uuid4().hex[:12]}@futureagi.com",
        password=uuid.uuid4().hex,
        name="F2",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    _enable_auto_topup(org, actor)
    held, go = threading.Event(), threading.Event()
    out = {}

    def save_settings():
        try:
            with transaction.atomic():
                ManagedCreditAccount.objects.select_for_update().get(organization=org)
                ManagedCreditAccount.objects.filter(organization=org).update(
                    **{cap: None}
                )
                held.set()
                go.wait(timeout=15)
        except Exception as exc:  # surfaced below
            out["save_error"] = exc
        finally:
            held.set()
            connection.close()

    def claim():
        try:
            out["claim"] = auto_topup.claim_auto_topup(org)
        except Exception as exc:  # surfaced below
            out["claim_error"] = exc
        finally:
            connection.close()

    saver = threading.Thread(target=save_settings)
    saver.start()
    try:
        assert held.wait(timeout=10)
        claimer = threading.Thread(target=claim)
        claimer.start()
        try:
            assert _a_session_waits_for_a_lock(), "the claim never waited for the row"
        finally:
            go.set()
        claimer.join(timeout=20)
    finally:
        go.set()
        saver.join(timeout=20)
    assert "save_error" not in out and "claim_error" not in out, out
    assert out["claim"] is None
    account = ManagedCreditAccount.objects.get(organization=org)
    assert getattr(account, cap) is None
    assert (account.autoreload_seq, account.autoreload_in_flight_seq) == (0, None)


# --- auto top-up: a frozen org is never charged, checked under the row lock -------


class TestAutoTopupWhileFrozen:
    """``claim_auto_topup`` refuses while the org is frozen (a receivable or an
    open dispute). Each org here is below the 1_000 threshold with both caps
    set, so the freeze is the only thing between the claim and a charge."""

    def test_a_receivable_refuses_the_claim_until_it_is_paid(self, organization, user):
        _enable_auto_topup(organization, user)
        _receivable(organization)  # balance -8_000
        assert mc.claim_auto_topup(organization) is None
        account = _account(organization)
        assert (account.autoreload_seq, account.autoreload_in_flight_seq) == (0, None)

        # Paid: the balance (0) is still below the threshold, now unfrozen.
        _top_up(organization, "pi_at_repay", 8_000)
        assert mc.freeze_reason(organization) == ""
        claim = mc.claim_auto_topup(organization)
        assert claim is not None and claim.seq == 1

    def test_an_open_dispute_refuses_the_claim_until_it_is_released(
        self, organization, user
    ):
        _enable_auto_topup(organization, user)
        _top_up(organization, "pi_at_disp", 10_000)
        # The whole charge is disputed: balance 0, below the threshold.
        _hold(organization, "dp_at_disp", "pi_at_disp", 10_000)
        assert _buckets(organization) == (0, 0, 0, 0)
        assert mc.claim_auto_topup(organization) is None
        account = _account(organization)
        assert (account.autoreload_seq, account.autoreload_in_flight_seq) == (0, None)

        _release(organization, "dp_at_disp")
        _spend(organization, "req_at_disp", 9_500)  # balance 500, unfrozen
        claim = mc.claim_auto_topup(organization)
        assert claim is not None and claim.seq == 1


@pytest.mark.django_db(transaction=True)
def test_a_dispute_held_while_the_claim_waits_for_the_lock_refuses_it():
    """A dispute hold of the whole charge is written while it holds the
    account row; a claim waits for the row. The adapter's own freeze check
    runs before it claims, outside this lock, and saw an unfrozen org. The
    claim must read the freeze after it gets the lock, so it sees the hold
    and refuses. Without that, it reads the balance the hold left (0, below
    the threshold) and charges a disputed org."""
    from tfc.constants.roles import OrganizationRoles

    migrate_usage_to_latest()
    org = Organization.objects.create(name="Freeze claim lock")
    actor = User.objects.create_user(
        email=f"freeze-lock-{uuid.uuid4().hex[:12]}@futureagi.com",
        password=uuid.uuid4().hex,
        name="Freeze",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    held, go = threading.Event(), threading.Event()
    out = {}

    def hold_the_dispute():
        try:
            with transaction.atomic():
                ManagedCreditAccount.objects.select_for_update().get(organization=org)
                _hold(org, "dp_at_lock", "pi_at_lock", 10_000)
                held.set()
                go.wait(timeout=15)
        except Exception as exc:  # surfaced below
            out["hold_error"] = exc
        finally:
            held.set()
            connection.close()

    def claim():
        try:
            out["claim"] = auto_topup.claim_auto_topup(org)
        except Exception as exc:  # surfaced below
            out["claim_error"] = exc
        finally:
            connection.close()

    with patched_gateway(FakeGateway()):
        _enable_auto_topup(org, actor)
        _top_up(org, "pi_at_lock", 10_000)
        # What a freeze check made before the claim sees.
        assert mc.freeze_reason(org) == ""
        holder = threading.Thread(target=hold_the_dispute)
        holder.start()
        try:
            assert held.wait(timeout=10)
            claimer = threading.Thread(target=claim)
            claimer.start()
            try:
                assert _a_session_waits_for_a_lock(), (
                    "the claim never waited for the row"
                )
            finally:
                go.set()
            claimer.join(timeout=20)
        finally:
            go.set()
            holder.join(timeout=20)
    assert "hold_error" not in out and "claim_error" not in out, out
    assert mc.freeze_reason(org) == "dispute_open"
    assert _buckets(org) == (0, 0, 0, 0)
    assert out["claim"] is None
    account = ManagedCreditAccount.objects.get(organization=org)
    assert (account.autoreload_seq, account.autoreload_in_flight_seq) == (0, None)
