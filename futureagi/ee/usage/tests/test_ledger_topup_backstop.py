"""BL-LEDGER-2A-b (ruling 492): the ledger's own one-credit-per-payment backstop.

One payment credits once, whichever ledger door it comes through:

- ``apply_topup`` and ``apply_topup_pi`` (R's pre-2a hooks) take the rules of
  ``fulfil_topup``: a session without its PaymentIntent is refused there too;
- a ``fulfil_topup`` replay that names a PaymentIntent records it on a top-up
  that was credited without one, so that PaymentIntent never credits again;
- ``payment_intent_id`` must be a PaymentIntent id (``pi_...``): a blank, a
  whitespace or a session or invoice id is refused.

V2, V3a and V3b are verify4's rows (wf_f5e2d127-009,
test_adv4_blledger_reverify.py), with its helpers. V2 is copied unchanged.
V3a and V3b open with a PI-less ``apply_topup``, which ruling 492.1 refuses, so
each keeps its assertions and changes only its first write: V3a catches the
refusal the way V2 does, and V3b seeds the row R's pre-2a ``apply_topup``
wrote (key ``stripe:<cs>``, no PaymentIntent) instead of calling the hook.
"""

import uuid
from functools import partial

import pytest
from django.db import IntegrityError, transaction

from accounts.models.organization import Organization
from ee.usage.models.billing_ledger import StripeEventLog
from ee.usage.models.managed_credits import (
    ImmutableLedgerEntryError,
    ManagedCreditAccount,
    ManagedCreditEntry,
)
from ee.usage.services import balance_cache, reconcile
from ee.usage.services import managed_credits as mc
from ee.usage.services.funding import Bucket
from ee.usage.tests.ledger_support import (
    FakeGateway,
    migrate_usage_to_latest,
    patched_gateway,
    run_concurrently,
)

Kind = ManagedCreditEntry.Kind
pytestmark = pytest.mark.integration

# Values that are not a PaymentIntent id, each with an ASCII test id.
NOT_A_PAYMENT_INTENT = [
    (" ", "blank"),
    ("\t", "tab"),
    ("cs_test_b3own", "session_id"),
    ("in_1B3own", "invoice_id"),
    ("pi", "pi_no_underscore"),
    ("pi_", "pi_prefix_only"),
    ("PI_1B3", "upper_case"),
    (" pi_1B3", "leading_space"),
    ("pi_1B3 ", "trailing_space"),
    ("pi_1B3\n", "trailing_newline"),
    ("pi_3B3own_secret_B3abc", "client_secret"),  # 2A-c, ruling 513.3
]


@pytest.fixture
def schema_current(transactional_db):
    migrate_usage_to_latest()


def _org(tag):
    return Organization.objects.create(name=f"v4 {tag} {uuid.uuid4().hex[:6]}")


def _cs(object_id, amount, pi=None):
    return mc.TopupPayment(
        mc.TopupSource.CHECKOUT_SESSION, object_id, amount, mc.StripeMode.TEST,
        payment_intent_id=f"pi_{object_id}" if pi is None else pi,
    )


def _inv(object_id, amount, pi=None):
    return mc.TopupPayment(
        mc.TopupSource.INVOICE, object_id, amount, mc.StripeMode.TEST,
        payment_intent_id=f"pi_{object_id}" if pi is None else pi,
    )


def _pi(pi_id, amount):
    return mc.TopupPayment(mc.TopupSource.PAYMENT_INTENT, pi_id, amount, mc.StripeMode.TEST)


def _topups(org):
    return ManagedCreditEntry.objects.filter(organization=org, kind=Kind.TOPUP).count()


def _state(org):
    a = ManagedCreditAccount.objects.get(organization=org)
    return (
        a.version, a.entry_seq, a.granted_total_micros, a.settled_spend_micros,
        a.platform_spend_micros, a.promo_balance_micros, a.commit_balance_micros,
        a.purchased_balance_micros, a.debt_micros,
    )


def _legacy_session_topup(org, session_id, amount):
    """The row R's pre-2a ``apply_topup`` wrote, through the same ``_write``
    call: key ``stripe:<cs>``, purchased bucket, no PaymentIntent, no mode."""
    entry, created = mc._write(
        org,
        Kind.TOPUP,
        amount,
        mc._system_key("stripe", session_id),
        bucket=Bucket.PURCHASED,
        stripe_ref=session_id,
        category=mc.TopupPurpose.TOPUP.value,
    )
    assert created and entry.stripe_payment_intent_id == ""
    return entry


def _assert_nothing_written(org, callbacks, event_id=None):
    assert callbacks == []  # no cache write, no balance signal, no push
    assert not ManagedCreditEntry.objects.filter(organization=org).exists()
    assert not ManagedCreditAccount.objects.filter(organization=org).exists()
    if event_id is not None:
        assert not StripeEventLog.objects.filter(event_id=event_id).exists()
    assert balance_cache.read(str(org.pk)) is None


# --- verify4 V2, V3a, V3b ---------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("bad_pi", [" ", "cs_test_v4v2"], ids=["blank", "session_id"])
def test_v4_v2_a_session_naming_no_real_payment_intent_credits_its_payment_once(bad_pi):
    """Hardening probe: the ledger checks only that payment_intent_id is non-empty."""
    org = _org("v2")
    try:
        mc.fulfil_topup(org, payment=_cs("cs_test_v4v2", 5_000_000, pi=bad_pi))
    except mc.ManagedCreditError:
        pass  # a refusal is the safe outcome
    mc.fulfil_topup(org, payment=_pi("pi_test_v4v2", 5_000_000))
    assert _topups(org) == 1


@pytest.mark.django_db
def test_v4_v3a_apply_topup_session_then_its_pi_through_apply_topup_pi():
    """Seam probe: BL-STRIPE 2c08270 ledger_adapter credits sessions through
    apply_topup and PaymentIntents through apply_topup_pi."""
    org = _org("v3a")
    try:  # adapted (ruling 492.1 refuses this call): V2's own pattern
        mc.apply_topup(org, 5_000_000, stripe_session_id="cs_test_v4v3a")
    except mc.ManagedCreditError:
        pass  # a refusal is the safe outcome
    mc.apply_topup_pi(org, 5_000_000, payment_intent_id="pi_test_v4v3a")
    assert _topups(org) == 1


@pytest.mark.django_db
def test_v4_v3b_apply_topup_session_then_fulfil_session_and_pi():
    """Seam probe: a session credited by apply_topup (no PI stored), then the
    same session and its PI through fulfil_topup."""
    org = _org("v3b")
    # adapted (ruling 492.1 refuses a PI-less apply_topup): the row it wrote
    _legacy_session_topup(org, "cs_test_v4v3b", 5_000_000)
    again = mc.fulfil_topup(org, payment=_cs("cs_test_v4v3b", 5_000_000, pi="pi_test_v4v3b"))
    assert not again.created
    mc.fulfil_topup(org, payment=_pi("pi_test_v4v3b", 5_000_000))
    assert _topups(org) == 1


# --- item 1: the pre-2a hooks take fulfil_topup's rules -----------------------------


@pytest.mark.django_db
def test_apply_topup_without_its_payment_intent_writes_nothing_at_all(
    django_capture_on_commit_callbacks,
):
    org = _org("b1")
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(mc.ManagedCreditError, match="payment_intent_id") as info:
            mc.apply_topup(org, 5_000_000, stripe_session_id="cs_test_b1")

    assert type(info.value) is mc.ManagedCreditError
    _assert_nothing_written(org, callbacks)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad_id", [value for value, _ in NOT_A_PAYMENT_INTENT], ids=[i for _, i in NOT_A_PAYMENT_INTENT]
)
def test_apply_topup_pi_refuses_an_id_that_is_not_a_payment_intent(
    bad_id, django_capture_on_commit_callbacks
):
    org = _org("b1pi")
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(mc.ManagedCreditError):
            mc.apply_topup_pi(org, 5_000_000, payment_intent_id=bad_id)

    _assert_nothing_written(org, callbacks)


@pytest.mark.django_db
@pytest.mark.parametrize("order", ["session_first", "payment_intent_first"])
def test_apply_topup_and_apply_topup_pi_credit_one_payment_once(order):
    org = _org("b1once")

    def session():
        return mc.apply_topup(
            org, 5_000_000, stripe_session_id="cs_test_b1once", payment_intent_id="pi_test_b1once"
        )

    def by_pi():
        return mc.apply_topup_pi(org, 5_000_000, payment_intent_id="pi_test_b1once")

    first, second = (session, by_pi) if order == "session_first" else (by_pi, session)
    entry, created = first()
    again, created_again = second()

    assert (created, created_again) == (True, False) and again.pk == entry.pk
    assert entry.stripe_payment_intent_id == "pi_test_b1once"
    assert entry.stripe_mode == mc.StripeMode.TEST.value
    assert _topups(org) == 1
    assert mc.ledger_balance(org).balance_micros == 5_000_000


@pytest.mark.django_db
def test_the_pre_2a_hooks_refuse_live_money_like_fulfil_topup():
    org = _org("b1live")
    with pytest.raises(mc.LiveModeRefused):
        mc.apply_topup(
            org, 5_000_000, stripe_session_id="cs_live_b1", payment_intent_id="pi_live_b1",
            mode=mc.StripeMode.LIVE,
        )
    with pytest.raises(mc.LiveModeRefused):
        mc.apply_topup_pi(org, 5_000_000, payment_intent_id="pi_live_b1", mode=mc.StripeMode.LIVE)
    assert not ManagedCreditEntry.objects.filter(organization=org).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("round_", range(3))
def test_a_session_and_its_payment_intent_racing_through_every_door_credit_once(
    schema_current, round_
):
    org = Organization.objects.create(name=f"b1 race {round_} {uuid.uuid4().hex[:6]}")
    session, pi = f"cs_test_b1race{round_}", f"pi_test_b1race{round_}"
    calls = [
        partial(
            mc.fulfil_topup, org, payment=_cs(session, 5_000_000, pi=pi),
            stripe_event_id=f"evt_b1race{round_}_cs",
        ),
        partial(mc.fulfil_topup, org, payment=_cs(session, 5_000_000, pi=pi)),
        partial(
            mc.fulfil_topup, org, payment=_pi(pi, 5_000_000),
            stripe_event_id=f"evt_b1race{round_}_pi",
        ),
        partial(mc.fulfil_topup, org, payment=_pi(pi, 5_000_000)),
        partial(mc.apply_topup, org, 5_000_000, stripe_session_id=session, payment_intent_id=pi),
        partial(mc.apply_topup_pi, org, 5_000_000, payment_intent_id=pi),
    ]

    with patched_gateway(FakeGateway()):
        results, errors = run_concurrently(calls)

    assert errors == []
    outcomes = [(r.entry, r.created) if isinstance(r, mc.FulfilResult) else r for r in results]
    assert sorted(created for _, created in outcomes) == [False] * 5 + [True]
    assert len({entry.pk for entry, _ in outcomes}) == 1
    assert _topups(org) == 1
    account = ManagedCreditAccount.objects.get(organization=org)
    assert (account.purchased_balance_micros, account.version) == (5_000_000, 1)
    assert reconcile.check_i2(org=org).ok and reconcile.check_i5(org).ok


# --- item 2: a replay records the PaymentIntent a top-up was credited without -----


@pytest.mark.django_db
def test_a_session_replay_records_its_payment_intent_on_a_legacy_top_up():
    org = _org("b2")
    legacy = _legacy_session_topup(org, "cs_test_b2", 5_000_000)
    after_credit = _state(org)

    again = mc.fulfil_topup(org, payment=_cs("cs_test_b2", 5_000_000, pi="pi_test_b2"))
    by_pi = mc.fulfil_topup(org, payment=_pi("pi_test_b2", 5_000_000))

    assert not again.created and again.entry.pk == legacy.pk
    assert again.entry.stripe_payment_intent_id == "pi_test_b2"
    legacy.refresh_from_db()
    assert legacy.stripe_payment_intent_id == "pi_test_b2"
    assert not by_pi.created and by_pi.entry.pk == legacy.pk
    # Recording the PaymentIntent is no credit: the account is as it was.
    assert _state(org) == after_credit
    assert _topups(org) == 1
    assert mc.ledger_balance(org).balance_micros == 5_000_000


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["checkout_session", "invoice"])
def test_a_redelivered_event_records_the_payment_intent_on_its_legacy_top_up(source):
    """The event already links a top-up credited without its PaymentIntent (a
    pre-2a session row, or an invoice c0d41f724 took without one; eedc243 linked
    a replay to it unchanged); the event's redelivery names the PaymentIntent."""
    org = _org("b2evt")
    object_id = "cs_test_b2evt" if source == "checkout_session" else "in_1B2evt"
    if source == "checkout_session":
        legacy = _legacy_session_topup(org, object_id, 5_000_000)
        payment = _cs(object_id, 5_000_000, pi="pi_test_b2evt")
    else:
        legacy, _ = mc._write(
            org, Kind.TOPUP, 5_000_000, mc._system_key("stripe_inv", object_id),
            bucket=Bucket.PURCHASED, stripe_ref=object_id,
            category=mc.TopupPurpose.TOPUP.value,
        )
        payment = _inv(object_id, 5_000_000, pi="pi_test_b2evt")
    row = mc._event_row("evt_b2evt", mc.StripeMode.TEST, object_id, org)
    mc._link_event(row, legacy, org)

    again = mc.fulfil_topup(org, payment=payment, stripe_event_id="evt_b2evt")
    by_pi = mc.fulfil_topup(org, payment=_pi("pi_test_b2evt", 5_000_000))

    assert not again.created and again.entry.pk == legacy.pk
    legacy.refresh_from_db()
    assert legacy.stripe_payment_intent_id == "pi_test_b2evt"
    assert not by_pi.created and by_pi.entry.pk == legacy.pk
    assert _topups(org) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("with_event", [False, True], ids=["no_event", "same_event"])
def test_a_replay_naming_another_payment_intent_is_a_mismatch(
    with_event, django_capture_on_commit_callbacks
):
    """A session has one PaymentIntent. A replay naming another one would leave
    that one free to credit the same money again, so it is refused and alerted
    (TopupMismatch), and nothing changes."""
    org = _org("b2mm")
    event_id = "evt_b2mm" if with_event else ""
    first = mc.fulfil_topup(
        org, payment=_cs("cs_test_b2mm", 5_000_000, pi="pi_test_b2mm_a"), stripe_event_id=event_id
    )
    before = _state(org)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(mc.TopupMismatch):
            mc.fulfil_topup(
                org,
                payment=_cs("cs_test_b2mm", 5_000_000, pi="pi_test_b2mm_b"),
                stripe_event_id=event_id,
            )

    assert callbacks == []
    first.entry.refresh_from_db()
    assert first.entry.stripe_payment_intent_id == "pi_test_b2mm_a"
    assert _state(org) == before
    assert _topups(org) == 1


@pytest.mark.django_db
def test_a_replay_whose_payment_intent_already_credited_another_top_up_is_a_mismatch(
    django_capture_on_commit_callbacks,
):
    """The PaymentIntent credited before any replay linked it to the legacy
    session row: the ledger could not know it was the same money. The replay
    that would link them is refused and alerted (TopupMismatch), never silent."""
    org = _org("b2dup")
    legacy = _legacy_session_topup(org, "cs_test_b2dup", 5_000_000)
    assert mc.fulfil_topup(org, payment=_pi("pi_test_b2dup", 5_000_000)).created
    before = _state(org)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(mc.TopupMismatch):
            mc.fulfil_topup(org, payment=_cs("cs_test_b2dup", 5_000_000, pi="pi_test_b2dup"))

    assert callbacks == []
    legacy.refresh_from_db()
    assert legacy.stripe_payment_intent_id == ""
    assert _state(org) == before


@pytest.mark.django_db
def test_fill_payment_intent_fills_a_blank_top_up_id_once_and_nothing_else():
    org = _org("b2fill")
    legacy = _legacy_session_topup(org, "cs_test_b2fill", 5_000_000)
    other = _legacy_session_topup(org, "cs_test_b2fill2", 2_000_000)
    named = mc.fulfil_topup(org, payment=_cs("cs_test_b2named", 1_000_000)).entry
    grant, _ = mc.grant(org, 1_000_000, idempotency_key="g-b2fill", actor=None, note="")
    rows = ManagedCreditEntry.objects.filter(organization=org)

    with pytest.raises(ImmutableLedgerEntryError):
        rows.filter(pk=legacy.pk).update(stripe_payment_intent_id="pi_test_b2fill")
    with pytest.raises(ImmutableLedgerEntryError):
        rows.filter(pk=legacy.pk).fill_payment_intent("")
    assert rows.filter(pk=named.pk).fill_payment_intent("pi_test_b2x") == 0
    assert rows.filter(pk=grant.pk).fill_payment_intent("pi_test_b2x") == 0
    assert rows.filter(pk=legacy.pk).fill_payment_intent("pi_test_b2fill") == 1
    assert rows.filter(pk=legacy.pk).fill_payment_intent("pi_test_b2again") == 0
    with pytest.raises(IntegrityError), transaction.atomic():
        rows.filter(pk=other.pk).fill_payment_intent("pi_test_b2fill")

    assert dict(rows.values_list("pk", "stripe_payment_intent_id")) == {
        legacy.pk: "pi_test_b2fill",
        other.pk: "",
        named.pk: "pi_cs_test_b2named",
        grant.pk: "",
    }
    assert dict(rows.values_list("pk", "amount_micros")) == {
        legacy.pk: 5_000_000,
        other.pk: 2_000_000,
        named.pk: 1_000_000,
        grant.pk: 1_000_000,
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("round_", range(3))
def test_a_legacy_top_up_replay_racing_its_payment_intent_is_never_silent(schema_current, round_):
    """Not exactly-once by nature: a top-up credited without its PaymentIntent
    has no link to it until a replay names it. If the replay records it first,
    the PaymentIntent finds that entry (1 credit). If the PaymentIntent credits
    first, the replay's record is refused as a TopupMismatch (2 credits, and the
    caller alerts). Never 2 credits in silence."""
    with patched_gateway(FakeGateway()):
        org = Organization.objects.create(name=f"b2 race {round_} {uuid.uuid4().hex[:6]}")
        session, pi = f"cs_test_b2race{round_}", f"pi_test_b2race{round_}"
        _legacy_session_topup(org, session, 5_000_000)
        results, errors = run_concurrently(
            [
                partial(mc.fulfil_topup, org, payment=_cs(session, 5_000_000, pi=pi)),
                partial(mc.fulfil_topup, org, payment=_pi(pi, 5_000_000)),
            ]
        )

    topups = ManagedCreditEntry.objects.filter(organization=org, kind=Kind.TOPUP)
    if topups.count() == 1:
        assert errors == [] and len(results) == 2
        assert topups.get().stripe_payment_intent_id == pi
        assert {r.created for r in results} == {False}
    else:
        assert topups.count() == 2
        assert len(errors) == 1 and isinstance(errors[0], mc.TopupMismatch), errors
    assert reconcile.check_i2(org=org).ok and reconcile.check_i5(org).ok


# --- item 3: payment_intent_id is a PaymentIntent id ----------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["checkout_session", "invoice"])
@pytest.mark.parametrize(
    "bad_pi", [value for value, _ in NOT_A_PAYMENT_INTENT], ids=[i for _, i in NOT_A_PAYMENT_INTENT]
)
def test_a_session_or_invoice_must_name_a_payment_intent_id(
    source, bad_pi, django_capture_on_commit_callbacks
):
    org = _org("b3")
    payment = (
        _cs("cs_test_b3own", 5_000_000, pi=bad_pi)
        if source == "checkout_session"
        else _inv("in_1B3own", 5_000_000, pi=bad_pi)
    )
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(mc.ManagedCreditError, match="payment_intent_id") as info:
            mc.fulfil_topup(org, payment=payment, stripe_event_id="evt_b3")

    assert type(info.value) is mc.ManagedCreditError
    _assert_nothing_written(org, callbacks, event_id="evt_b3")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad_id", [value for value, _ in NOT_A_PAYMENT_INTENT], ids=[i for _, i in NOT_A_PAYMENT_INTENT]
)
def test_a_payment_intent_top_up_must_be_a_payment_intent_id(
    bad_id, django_capture_on_commit_callbacks
):
    org = _org("b3pi")
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(mc.ManagedCreditError) as info:
            mc.fulfil_topup(org, payment=_pi(bad_id, 5_000_000), stripe_event_id="evt_b3pi")

    assert type(info.value) is mc.ManagedCreditError
    _assert_nothing_written(org, callbacks, event_id="evt_b3pi")


@pytest.mark.django_db
def test_live_mode_is_still_refused_before_the_payment_intent_format():
    org = _org("b3live")
    with pytest.raises(mc.LiveModeRefused):
        mc.fulfil_topup(
            org,
            payment=mc.TopupPayment(
                mc.TopupSource.CHECKOUT_SESSION, "cs_live_b3", 5_000_000, mc.StripeMode.LIVE,
                payment_intent_id=" ",
            ),
        )
    assert not ManagedCreditEntry.objects.filter(organization=org).exists()
