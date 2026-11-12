"""BL-LEDGER-2A-c: a PaymentIntent's client secret is not its id.

Stripe documents object ids as opaque strings whose length and format may
change (prefixes included), up to 255 characters and case-sensitive, and
documents no character set (docs.stripe.com/api-versions, "Compatible changes
and breaking changes"). So the ledger keeps its pi_[A-Za-z0-9_]+ and
does not narrow it. What Stripe does document is the client secret's shape:
the PaymentIntent object's client_secret is the id, then "_secret_", then a
suffix (docs.stripe.com/api/payment_intents/object). That matches the pattern,
so a session or invoice naming it plus the real PaymentIntent credited one
payment twice (the 2A-b verify's adv5 probe, copied here). The ledger now
refuses any id containing "_secret_" (any case), and never echoes it: Stripe
says a client secret should not be stored or logged.
"""

import uuid

import pytest

from accounts.models.organization import Organization
from ee.usage.models.billing_ledger import StripeEventLog
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.services import balance_cache
from ee.usage.services import managed_credits as mc

Kind = ManagedCreditEntry.Kind
pytestmark = pytest.mark.integration
AMOUNT = 5_000_000
REAL = "pi_3V5sec"
# The documented shape, a lower-case variant on an underscore-style test id,
# and an upper-case variant (refused too: no PaymentIntent id carries it).
SECRET_SHAPES = [
    (f"{REAL}_secret_V5abc", "documented_shape"),
    ("pi_test_c2_secret_V5abc", "test_style_id"),
    (f"{REAL}_SECRET_V5abc", "upper_case"),
]
# Ids that must still be accepted: Stripe's own example id, a "secret" with no
# underscores around it, the lane's underscore-style test ids, and the longest
# id whose stripe_pi:<id> key fits the 200-character key column.
ACCEPTED = [
    ("pi_3MtwBwLkdIwHu7ix28a3tqPa", "stripe_docs_example"),
    ("pi_1secret2", "secret_without_underscores"),
    ("pi_test_secretive_1", "secretive"),
    ("pi_cs_test_c2ok", "underscore_test_id"),
    ("pi_" + "a" * 187, "length_190"),
]


def _org(tag):
    return Organization.objects.create(name=f"c2 {tag} {uuid.uuid4().hex[:6]}")


def _cs(object_id, amount, pi):
    return mc.TopupPayment(
        mc.TopupSource.CHECKOUT_SESSION, object_id, amount, mc.StripeMode.TEST,
        payment_intent_id=pi,
    )


def _inv(object_id, amount, pi):
    return mc.TopupPayment(
        mc.TopupSource.INVOICE, object_id, amount, mc.StripeMode.TEST, payment_intent_id=pi
    )


def _pi(pi_id, amount):
    return mc.TopupPayment(mc.TopupSource.PAYMENT_INTENT, pi_id, amount, mc.StripeMode.TEST)


def _topups(org):
    return ManagedCreditEntry.objects.filter(organization=org, kind=Kind.TOPUP).count()


def _assert_refused_and_nothing_written(org, info, callbacks, event_id):
    assert type(info.value) is mc.ManagedCreditError
    assert "V5abc" not in str(info.value)  # the secret is never echoed
    assert callbacks == []
    assert not ManagedCreditEntry.objects.filter(organization=org).exists()
    assert not ManagedCreditAccount.objects.filter(organization=org).exists()
    if event_id:
        assert not StripeEventLog.objects.filter(event_id=event_id).exists()
    assert balance_cache.read(str(org.pk)) is None


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["checkout_session", "invoice"])
@pytest.mark.parametrize("named", [v for v, _ in SECRET_SHAPES], ids=[i for _, i in SECRET_SHAPES])
def test_a_session_or_invoice_naming_a_client_secret_is_refused(
    source, named, django_capture_on_commit_callbacks
):
    org = _org("ref")
    payment = (
        _cs("cs_test_c2ref", AMOUNT, named)
        if source == "checkout_session"
        else _inv("in_1C2ref", AMOUNT, named)
    )
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with pytest.raises(mc.ManagedCreditError, match="payment_intent_id") as info:
            mc.fulfil_topup(org, payment=payment, stripe_event_id="evt_c2_ref")
    _assert_refused_and_nothing_written(org, info, callbacks, "evt_c2_ref")


@pytest.mark.django_db
@pytest.mark.parametrize("named", [v for v, _ in SECRET_SHAPES], ids=[i for _, i in SECRET_SHAPES])
def test_a_payment_intent_top_up_whose_id_is_a_client_secret_is_refused(
    named, django_capture_on_commit_callbacks
):
    org = _org("pio")
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with pytest.raises(mc.ManagedCreditError) as info:
            mc.fulfil_topup(org, payment=_pi(named, AMOUNT), stripe_event_id="evt_c2_pio")
    _assert_refused_and_nothing_written(org, info, callbacks, "evt_c2_pio")


@pytest.mark.django_db
@pytest.mark.parametrize("hook", ["apply_topup", "apply_topup_pi"])
def test_the_pre_2a_hooks_refuse_a_client_secret(hook, django_capture_on_commit_callbacks):
    org = _org("hook")
    named = SECRET_SHAPES[0][0]
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with pytest.raises(mc.ManagedCreditError) as info:
            if hook == "apply_topup":
                mc.apply_topup(org, AMOUNT, stripe_session_id="cs_test_c2hook", payment_intent_id=named)
            else:
                mc.apply_topup_pi(org, AMOUNT, payment_intent_id=named)
    _assert_refused_and_nothing_written(org, info, callbacks, None)


@pytest.mark.django_db
@pytest.mark.parametrize("tail", ["\n", " ", "-x"], ids=["trailing_newline", "trailing_space", "dash"])
@pytest.mark.parametrize("source", ["checkout_session", "payment_intent"])
def test_a_client_secret_that_also_breaks_the_id_charset_is_never_echoed(
    source, tail, django_capture_on_commit_callbacks
):
    """The 2A-c verify's LOW: the charset check ran first and echoed the
    first 80 characters, so a client secret with a stray character leaked."""
    org = _org("tail")
    named = SECRET_SHAPES[0][0] + tail
    payment = _cs("cs_test_c2tail", AMOUNT, named) if source == "checkout_session" else _pi(named, AMOUNT)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with pytest.raises(mc.ManagedCreditError, match="client secret") as info:
            mc.fulfil_topup(org, payment=payment, stripe_event_id="evt_c2_tail")
    _assert_refused_and_nothing_written(org, info, callbacks, "evt_c2_tail")


@pytest.mark.django_db
@pytest.mark.parametrize("door", ["reverse_topup", "hold_dispute"])
@pytest.mark.parametrize("named", [v for v, _ in SECRET_SHAPES], ids=[i for _, i in SECRET_SHAPES])
def test_a_reversal_or_dispute_naming_a_client_secret_is_refused_unechoed(
    door, named, django_capture_on_commit_callbacks
):
    """The 2A-c verify's LOW: reverse_topup and hold_dispute looked the id up
    and echoed it in full in 'no credited top-up ... for payment intent'."""
    org = _org("rev")
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with pytest.raises(mc.ManagedCreditError, match="client secret") as info:
            if door == "reverse_topup":
                mc.reverse_topup(
                    org, refund_id="re_c2rev", payment_intent_id=named, amount_micros=AMOUNT,
                    mode=mc.StripeMode.TEST,
                )
            else:
                mc.hold_dispute(
                    org, dispute_id="dp_c2rev", payment_intent_id=named, amount_micros=AMOUNT,
                    mode=mc.StripeMode.TEST,
                )
    _assert_refused_and_nothing_written(org, info, callbacks, None)


@pytest.mark.django_db
@pytest.mark.parametrize("door", ["reverse_topup", "hold_dispute"])
def test_a_credited_top_ups_client_secret_never_reverses_or_holds_it(door):
    org = _org("revc")
    real = "pi_3V5revc"
    mc.fulfil_topup(org, payment=_cs("cs_test_c2revc", AMOUNT, real))
    before = sorted(ManagedCreditEntry.objects.filter(organization=org).values_list("pk", "amount_micros"))
    with pytest.raises(mc.ManagedCreditError) as info:
        if door == "reverse_topup":
            mc.reverse_topup(
                org, refund_id="re_c2revc", payment_intent_id=real + "_secret_V5abc",
                amount_micros=AMOUNT, mode=mc.StripeMode.TEST,
            )
        else:
            mc.hold_dispute(
                org, dispute_id="dp_c2revc", payment_intent_id=real + "_secret_V5abc",
                amount_micros=AMOUNT, mode=mc.StripeMode.TEST,
            )
    assert "V5abc" not in str(info.value)
    assert sorted(ManagedCreditEntry.objects.filter(organization=org).values_list("pk", "amount_micros")) == before
    # The real id still reverses (control).
    if door == "reverse_topup":
        entry, created = mc.reverse_topup(
            org, refund_id="re_c2revc", payment_intent_id=real, amount_micros=AMOUNT,
            mode=mc.StripeMode.TEST,
        )
    else:
        entry, created = mc.hold_dispute(
            org, dispute_id="dp_c2revc", payment_intent_id=real, amount_micros=AMOUNT,
            mode=mc.StripeMode.TEST,
        )
    assert created and entry.amount_micros == -AMOUNT


@pytest.mark.django_db
@pytest.mark.parametrize("order", ["object_first", "payment_intent_first"])
@pytest.mark.parametrize("source", ["checkout_session", "invoice"])
def test_v5_probe_a_client_secret_is_not_a_payment_intent_id(source, order):
    """The 2A-b verify's adv5 probe, unchanged: a session or an invoice naming
    the client secret, plus the real PaymentIntent, is one payment."""
    org = _org("sec")
    real = "pi_3V5sec"
    named = real + "_secret_V5abc"
    obj = (
        _cs("cs_test_v5sec", AMOUNT, named)
        if source == "checkout_session"
        else _inv("in_1V5sec", AMOUNT, named)
    )

    def by_object():
        try:
            mc.fulfil_topup(org, payment=obj)
        except mc.ManagedCreditError:
            pass  # a refusal is the safe outcome

    def by_pi():
        mc.fulfil_topup(org, payment=_pi(real, AMOUNT))

    for step in (by_object, by_pi) if order == "object_first" else (by_pi, by_object):
        step()
    assert _topups(org) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("pi", [v for v, _ in ACCEPTED], ids=[i for _, i in ACCEPTED])
def test_a_real_payment_intent_id_still_credits_once_through_every_object(pi):
    org = _org("ok")
    first = mc.fulfil_topup(org, payment=_cs("cs_test_c2ok", AMOUNT, pi), stripe_event_id="evt_c2_ok")
    assert first.created and first.entry.stripe_payment_intent_id == pi
    again = [
        mc.fulfil_topup(org, payment=_inv("in_1C2ok", AMOUNT, pi)),
        mc.fulfil_topup(org, payment=_pi(pi, AMOUNT)),
        mc.fulfil_topup(org, payment=_cs("cs_test_c2ok", AMOUNT, pi)),
    ]
    assert {(r.entry.pk, r.created) for r in again} == {(first.entry.pk, False)}
    assert _topups(org) == 1
