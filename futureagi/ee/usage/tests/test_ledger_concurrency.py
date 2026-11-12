"""BL-LEDGER phase 2a: two workers writing the same ledger at once.

Each worker is a thread with its own database connection and committing
transactions, so the account row lock, the unique keys and the coalesced push
are exercised as two drain or webhook workers would exercise them.
"""

import copy
from functools import partial

import pytest

from accounts.models.organization import Organization
from accounts.models.user import User
from agentcc.auto_reload_terms import AUTO_RELOAD_CONSENT_VERSION
from ee.usage.models.billing_ledger import StripeEventLog
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.models.usage import UsageEventLog
from ee.usage.services import managed_credits as mc
from ee.usage.services import reconcile
from ee.usage.tests.ledger_support import (
    FakeGateway,
    migrate_usage_to_latest,
    new_id,
    patched_gateway,
    run_concurrently,
    spend_record,
)

Kind = ManagedCreditEntry.Kind


@pytest.fixture
def schema_current(transactional_db):
    migrate_usage_to_latest()


def _payment(object_id, amount, pi=""):
    return mc.TopupPayment(
        source=mc.TopupSource.CHECKOUT_SESSION,
        object_id=object_id,
        amount_micros=amount,
        mode=mc.StripeMode.TEST,
        payment_intent_id=pi or f"pi_{object_id}",
    )


def _two_workers(work):
    """Run ``work(worker_index)`` on two workers at once; return (results, errors)."""
    return run_concurrently([partial(work, 0), partial(work, 1)])


@pytest.mark.django_db(transaction=True)
def test_two_workers_fulfil_the_same_session_once(schema_current):
    org = Organization.objects.create(name="Two workers, one session")
    payment = _payment("cs_test_tw", 25_000_000, pi="pi_test_tw")
    fake = FakeGateway()

    def worker(i):
        # Worker 0 is the webhook (with replays), worker 1 the landing page and
        # the PaymentIntent event for the same money.
        out = []
        if i == 0:
            for n in range(3):
                out.append(
                    mc.fulfil_topup(
                        org, payment=payment, stripe_event_id=f"evt_tw_{n % 2}"
                    )
                )
        else:
            out.append(mc.fulfil_topup(org, payment=payment))
            pi = mc.TopupPayment(
                source=mc.TopupSource.PAYMENT_INTENT,
                object_id="pi_test_tw",
                amount_micros=25_000_000,
                mode=mc.StripeMode.TEST,
            )
            out.append(mc.fulfil_topup(org, payment=pi, stripe_event_id="evt_tw_pi"))
        return out

    with patched_gateway(fake):
        results, errors = _two_workers(worker)

    assert errors == []
    flat = [r for batch in results for r in batch]
    assert sum(r.created for r in flat) == 1
    assert len({r.entry.pk for r in flat}) == 1
    assert ManagedCreditEntry.objects.filter(organization=org).count() == 1
    assert StripeEventLog.objects.filter(entry__organization=org).count() == 3
    account = ManagedCreditAccount.objects.get(organization=org)
    assert (
        account.purchased_balance_micros,
        account.version,
        account.gateway_acked_version,
    ) == (
        25_000_000,
        1,
        1,
    )
    assert reconcile.check_i5(org).ok and reconcile.check_i2(org=org).ok


@pytest.mark.django_db(transaction=True)
def test_two_drain_workers_settle_and_credit_without_a_lost_update(schema_current):
    org = Organization.objects.create(name="Two drain workers")
    fake = FakeGateway(push_delay=0.02)
    records = [spend_record(org, f"req_tw_{i}", billed=1_000 + i) for i in range(20)]

    def worker(i):
        # Both workers drain the same 20 records (the outbox redelivers), and
        # each also credits its own top-ups.
        out = []
        for n, record in enumerate(records if i == 0 else list(reversed(records))):
            out.append(mc.settle_spend(copy.deepcopy(record)))
            if n % 5 == 0:
                out.append(
                    mc.fulfil_topup(org, payment=_payment(f"cs_test_w{i}_{n}", 3_000))
                )
        return out

    with patched_gateway(fake):
        results, errors = _two_workers(worker)

    assert errors == []
    spends = ManagedCreditEntry.objects.filter(organization=org, kind=Kind.SPEND)
    assert spends.count() == 20
    assert (
        UsageEventLog.objects.filter(
            organization=org, event_type="gateway_managed_spend"
        ).count()
        == 20
    )
    account = ManagedCreditAccount.objects.get(organization=org)
    billed = sum(1_000 + i for i in range(20))
    assert account.settled_spend_micros == billed
    assert account.granted_total_micros == 8 * 3_000
    assert mc.ledger_balance(org).balance_micros == 8 * 3_000 - billed
    # The coalesced push: never two in flight for the org, and the last one is current.
    assert fake.max_in_flight.get(str(org.pk), 0) == 1
    assert fake.state(org.pk)["granted_version"] == account.version == 8
    assert account.gateway_acked_version == 8
    for check in (
        reconcile.check_i1(org=org),
        reconcile.check_i2(org=org),
        reconcile.check_i5(org),
    ):
        assert check.ok, check.failures


@pytest.mark.django_db(transaction=True)
def test_two_workers_claim_auto_topup_once(schema_current):
    org = Organization.objects.create(name="Two auto top-up workers")
    from tfc.constants.roles import OrganizationRoles

    actor = User.objects.create_user(
        email=f"auto-topup-{new_id()}@futureagi.com",
        password="testpassword123",
        name="W",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    mc.configure_auto_topup(
        org,
        enabled=True,
        threshold_micros=0,
        amount_micros=5_000,
        daily_count_cap=3,
        daily_amount_cap_micros=15_000,
        payment_method_id="pm_card_visa",
        consent_text_version=AUTO_RELOAD_CONSENT_VERSION,
        actor=actor,
    )
    results, errors = _two_workers(lambda i: mc.claim_auto_topup(org))
    assert errors == []
    claims = [c for c in results if c is not None]
    assert len(claims) == 1 and claims[0].seq == 1
