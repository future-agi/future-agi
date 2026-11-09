"""BL-LEDGER-2A-c (ruling 492.4, 513.3): a refund's repaid debt share goes home.

A spend that overdrew the wallet leaves debt. A later credit repays it. When
that spend is refunded, the part of its debt a credit already repaid is the
credit's money again, so it goes back to the bucket of the credit that repaid
it: a promo grant's share to promo (an expiring grant stays expiring), a
commit grant's to commit, a top-up's to purchased. Before 2A-c every repaid
share went to purchased, which turned expiring promo into refundable cash.

The rule (services/funding.py, ``debt_lot`` and ``refund_repaid_by``): debt
is one balance, but every entry that added debt is a lot, and every
repayment closes the oldest open lots first (first in, first out: "every
credit repays debt first", in the order the debt was made). Debt seeded by
usage/0032 from entries without funding columns is the oldest lot. A refund
gives back exactly what closed its spend's lot, each part to the bucket of
the credit that paid it. Like every credit it repays outstanding debt first:
its own spend's unpaid part, then other debt, paid with its spend's oldest
repayments (which then belong to the lots they close). Each bucket is
capped by what its credits repaid net of the shares earlier refunds already
returned to it; anything no repayment covers goes to purchased, as before.

The 2A-c verify (wf_d3276edc-ef9) found the first rule ("the credits written
after the spend, oldest first") gave a grant that had repaid OLDER debt to
a later spend's refund, turning paid money into promo or commit. Its rows
(p1, p1b, p3, p5) and its first-in-first-out oracle are below, with a
conservation fuzz that holds under any lot order: once every spend is
refunded, each bucket holds exactly what its credits brought in.

``test_v4_v4_refund_after_a_grant_repaid_the_spends_debt`` is verify4's row
(wf_f5e2d127-009, test_adv4_blledger_reverify.py), copied with the helpers it
uses; since ruling 971bd25c its grant is the correction below (``_correct``).

Clawback follows the recorded lot (ruling 529.2). When Stripe takes back a
top-up (a refund of the charge, or a dispute hold a lost dispute leaves
standing), the reversal opens its own debt lot. A credit that later repays
that lot repays the reversal's debt; it does not relabel the repayment
already recorded on a spend's lot. So a refund of that spend returns the
bucket that repaid its lot, not whatever covers the net position now.
``test_v8_attr_must_529_2_clawback_follows_the_recorded_lot`` pins this. It
is the 2A-c independent verify's row (test_adv8_blledger_2c_independent.py),
adopted with the helper names changed and, since ruling 971bd25c, its grant
made a correction (``_correct``). The 2A-c verify's two question
rows (``test_v7_question_a_repayment_stripe_took_back_still_counts``, in
test_adv7_blledger_2c_verify.py) assert the net-position answer, which 529.2
rules against: they stay red and are not counted as passing.

``test_v9_skew_must_one_grants_repayment_comes_home_once`` is the same
verify's clock-skew row (test_adv9_blledger_2c_skew.py), adopted with the
helper names changed and, since ruling 971bd25c, its grant made a correction.
A writer whose clock runs behind is simulated at
insert time, so the per-bucket cap in ``managed_credits._debt_repayers`` is
the only thing that keeps one grant's repayment from coming home twice.

Ruling 971bd25c (MAIN-RULING-ONEKEY-F3-RECEIVABLE-ANY-DEBT-r1) amends the rows
above for managed one-key orgs: ``grant()`` is refused while the org has any
debt or an open dispute, so a staff promo or commit grant no longer repays an
overdraft. Each row that granted into debt now shows that grant refused, with
nothing written, and makes the person's correction with ``adjust()``
(``_correct``). A positive adjustment fills the same bucket after repaying
debt first and is attributed exactly like a grant (it is a debt-repaying
credit, its ``funding`` names the bucket, and it counts towards the bucket's
gross), so every attribution these rows pin is unchanged. The fuzz histories
do the same whenever the org is frozen (``_credit``). An expiring promo grant
cannot be a correction (``adjust()`` has no expiry), so that row now lands the
grant after the refund clears the debt. The 492.4 refund replay (debt first)
is unchanged.
"""

import random
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from accounts.models.organization import Organization
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.services import funding, ledger_reads, reconcile
from ee.usage.services import managed_credits as mc
from ee.usage.services.funding import Allocation, Bucket, Buckets
from ee.usage.tests.ledger_support import FakeGateway, patched_gateway, spend_record

Kind = ManagedCreditEntry.Kind
pytestmark = pytest.mark.integration


# --- verify4's helpers, unchanged -------------------------------------------------


def _org(tag):
    return Organization.objects.create(name=f"v4 {tag} {uuid.uuid4().hex[:6]}")


def _cs(object_id, amount, pi=None):
    return mc.TopupPayment(
        mc.TopupSource.CHECKOUT_SESSION, object_id, amount, mc.StripeMode.TEST,
        payment_intent_id=f"pi_{object_id}" if pi is None else pi,
    )


def _ok(*checks):
    for check in checks:
        assert check.ok, (check.name, check.failures)


def _correct(org, micros, key, bucket=Bucket.PROMO):
    """Credit that repays open debt, after ruling 971bd25c.

    The promo or commit ``grant()`` is refused while the org is frozen and
    writes nothing; the person's correction is ``adjust()`` into the same
    bucket. Returns the adjustment.
    """
    assert mc.freeze_reason(org), "a correction row needs an open receivable or dispute"
    count = ManagedCreditEntry.objects.filter(organization=org).count()
    with pytest.raises(mc.ManagedCreditError):
        mc.grant(org, micros, idempotency_key=key, actor=None, note="", bucket=bucket)
    assert ManagedCreditEntry.objects.filter(organization=org).count() == count
    entry, created = mc.adjust(
        org, micros, idempotency_key=key, reason="correction", actor=None, bucket=bucket
    )
    assert created and entry.kind == Kind.ADJUSTMENT
    return entry


# --- verify4 V4 -------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("bucket", [Bucket.PROMO, Bucket.COMMIT], ids=["promo", "commit"])
def test_v4_v4_refund_after_a_grant_repaid_the_spends_debt(bucket):
    org = _org("v4")
    mc.fulfil_topup(org, payment=_cs("cs_test_v4v4", 5_000))
    mc.settle_spend(spend_record(org, "req_v4_v4", billed=10_000))  # purchased 5_000 + debt 5_000
    _correct(org, 5_000, "v4-grant", bucket)  # covers the overshoot (grant refused)
    bal = mc.ledger_balance(org)
    assert (bal.balance_micros, bal.debt_micros) == (0, 0)
    entry, created = mc.refund(org, "req_v4_v4", reason="failed request", actor=None)
    assert created and entry.amount_micros == 10_000
    bal = mc.ledger_balance(org)
    assert bal.balance_micros == 10_000  # the balance itself must be exact
    _ok(reconcile.check_i2(org=org), reconcile.check_i5(org))
    granted_bucket = bal.promo_micros if bucket == Bucket.PROMO else bal.commit_micros
    # SAFE attribution: only the 5_000 the org paid comes back as purchased; the
    # correction's 5_000 that repaid the debt goes back to its own bucket.
    assert (bal.purchased_micros, granted_bucket) == (5_000, 5_000), (
        "purchased", bal.purchased_micros, bucket.value, granted_bucket,
    )


# --- the lane's rows --------------------------------------------------------------


def _buckets(org):
    bal = mc.ledger_balance(org)
    return (bal.promo_micros, bal.commit_micros, bal.purchased_micros, bal.debt_micros)


def _funded(entry):
    return (
        entry.funded_promo_micros,
        entry.funded_commit_micros,
        entry.funded_purchased_micros,
        entry.funded_debt_micros,
    )


def _never_above_gross(org):
    """No bucket ever holds more than its credits ever brought in."""
    a = ManagedCreditAccount.objects.get(organization=org)
    assert a.promo_balance_micros <= a.promo_total_micros, (a.promo_balance_micros, a.promo_total_micros)
    assert a.commit_balance_micros <= a.commit_total_micros, (a.commit_balance_micros, a.commit_total_micros)


@pytest.mark.django_db
def test_two_credits_that_repaid_one_spends_debt_each_get_their_share_back():
    org = _org("two")
    mc.settle_spend(spend_record(org, "req_two", billed=5_000))  # all debt
    _correct(org, 3_000, "two-promo")  # repays 3_000
    mc.fulfil_topup(org, payment=_cs("cs_test_two", 2_000))  # repays 2_000
    assert _buckets(org) == (0, 0, 0, 0)

    entry, created = mc.refund(org, "req_two", reason="failed request", actor=None)

    assert created and _funded(entry) == (3_000, 0, 2_000, 0)
    assert _buckets(org) == (3_000, 0, 2_000, 0)
    _never_above_gross(org)
    _ok(reconcile.check_i2(org=org), reconcile.check_i5(org))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "micros, split",
    [(3_000, (3_000, 0, 0, 0)), (4_000, (3_000, 0, 1_000, 0)), (1, (1, 0, 0, 0))],
    ids=["exactly_the_oldest", "oldest_then_next", "one_micro"],
)
def test_a_partial_refund_returns_the_oldest_repayment_first(micros, split):
    org = _org("part")
    mc.settle_spend(spend_record(org, "req_part", billed=5_000))  # all debt
    _correct(org, 3_000, "part-promo")  # repays first
    mc.fulfil_topup(org, payment=_cs("cs_test_part", 2_000))  # repays second

    entry, _ = mc.refund(org, "req_part", micros, reason="partial", actor=None)

    assert _funded(entry) == split
    assert _buckets(org) == split
    _ok(reconcile.check_i2(org=org), reconcile.check_i5(org))


@pytest.mark.django_db
def test_two_indebted_spends_never_both_claim_one_grants_repayment():
    org = _org("cap")
    mc.settle_spend(spend_record(org, "req_cap_a", billed=5_000))  # debt 5_000
    mc.settle_spend(spend_record(org, "req_cap_b", billed=5_000))  # debt 10_000
    _correct(org, 5_000, "cap-promo")  # debt 5_000
    mc.fulfil_topup(org, payment=_cs("cs_test_cap", 5_000))  # debt 0

    first, _ = mc.refund(org, "req_cap_a", reason="failed request", actor=None)
    second, _ = mc.refund(org, "req_cap_b", reason="failed request", actor=None)

    # The correction's 5_000 goes home once; the top-up's 5_000 is the rest.
    assert _funded(first) == (5_000, 0, 0, 0)
    assert _funded(second) == (0, 0, 5_000, 0)
    assert _buckets(org) == (5_000, 0, 5_000, 0)
    _never_above_gross(org)
    _ok(reconcile.check_i2(org=org), reconcile.check_i5(org))


@pytest.mark.django_db
def test_a_credit_written_before_the_spend_never_takes_its_refund():
    org = _org("before")
    mc.settle_spend(spend_record(org, "req_before_x", billed=5_000))  # debt 5_000
    _correct(org, 5_000, "before-promo")  # repays x
    mc.settle_spend(spend_record(org, "req_before_s", billed=5_000))  # debt 5_000
    mc.fulfil_topup(org, payment=_cs("cs_test_before", 5_000))  # repays s

    entry, _ = mc.refund(org, "req_before_s", reason="failed request", actor=None)

    assert _funded(entry) == (0, 0, 5_000, 0)
    assert _buckets(org) == (0, 0, 5_000, 0)
    _ok(reconcile.check_i2(org=org), reconcile.check_i5(org))


@pytest.mark.django_db
def test_a_spend_that_drew_promo_and_debt_gets_both_parts_home():
    org = _org("mix")
    mc.grant(org, 2_000, idempotency_key="mix-promo", actor=None, note="")
    mc.settle_spend(spend_record(org, "req_mix", billed=5_000))  # promo 2_000 + debt 3_000
    _correct(org, 3_000, "mix-commit", Bucket.COMMIT)

    entry, _ = mc.refund(org, "req_mix", reason="failed request", actor=None)

    assert _funded(entry) == (2_000, 3_000, 0, 0)
    assert _buckets(org) == (2_000, 3_000, 0, 0)
    _never_above_gross(org)
    _ok(reconcile.check_i2(org=org), reconcile.check_i5(org))


@pytest.mark.django_db
def test_an_expiring_promo_grant_is_allowed_after_a_refund_clears_the_debt():
    """Rewritten for ruling 971bd25c: an expiring promo grant can no longer
    repay debt (``grant()`` is refused while the org owes, and ``adjust()``
    has no expiry). The grant is refused while the overdraft is open and
    writes nothing; the refund cancels the spend's unpaid debt and gives its
    purchased part back; the same grant then lands as expiring promo. The
    balance it ends with is the one this row pinned before the ruling."""
    org = _org("exp")
    expires = datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)
    mc.fulfil_topup(org, payment=_cs("cs_test_exp", 5_000))
    mc.settle_spend(spend_record(org, "req_exp", billed=10_000))  # purchased 5_000 + debt 5_000
    with pytest.raises(mc.ManagedCreditError):
        mc.grant(org, 5_000, idempotency_key="exp-promo", actor=None, note="", expires_at=expires)
    assert not ManagedCreditEntry.objects.filter(organization=org, kind=Kind.GRANT).exists()

    mc.refund(org, "req_exp", reason="failed request", actor=None)
    assert _buckets(org) == (0, 0, 5_000, 0)
    mc.grant(org, 5_000, idempotency_key="exp-promo", actor=None, note="", expires_at=expires)

    with patched_gateway(FakeGateway()):
        body = ledger_reads.balance_payload(org)
    assert body["remaining"] == {
        "promotional_micros": 5_000,
        "commit_micros": 0,
        "purchased_micros": 5_000,
    }
    assert body["promotional_expiring"] == [
        {"amount_micros": 5_000, "expires_at": ledger_reads.utc(expires)}
    ]
    _ok(reconcile.check_i2(org=org), reconcile.check_i5(org))


@pytest.mark.django_db
def test_a_top_up_that_repaid_the_debt_still_gets_purchased_back():
    org = _org("ctl")
    mc.grant(org, 1_000, idempotency_key="ctl-promo", actor=None, note="")  # before the spend
    mc.settle_spend(spend_record(org, "req_ctl", billed=4_000))  # promo 1_000 + debt 3_000
    mc.fulfil_topup(org, payment=_cs("cs_test_ctl", 3_000))  # repays it

    entry, _ = mc.refund(org, "req_ctl", reason="failed request", actor=None)

    assert _funded(entry) == (1_000, 0, 3_000, 0)
    assert _buckets(org) == (1_000, 0, 3_000, 0)
    _ok(reconcile.check_i2(org=org), reconcile.check_i5(org))


# --- debt as first-in-first-out lots (the 2A-c verify's finding) ------------------


def _spend(org, req, billed):
    entry, created = mc.settle_spend(spend_record(org, req, billed=billed))
    assert created
    return entry


def _grant(org, micros, key, bucket=Bucket.PROMO):
    return mc.grant(org, micros, idempotency_key=key, actor=None, note="", bucket=bucket)[0]


def _credit(org, micros, key, bucket=Bucket.PROMO):
    """A grant, or (ruling 971bd25c) while the org is frozen the refused grant
    and its correction. Consumes no randomness, so a seed's history is the
    same as before the ruling."""
    if mc.freeze_reason(org):
        return _correct(org, micros, key, bucket)
    return _grant(org, micros, key, bucket)


def _top_up(org, tag, micros):
    return mc.fulfil_topup(org, payment=_cs(f"cs_test_{tag}", micros)).entry


def _refund(org, req, micros=None):
    entry, created = mc.refund(org, req, micros, reason="failed request", actor=None)
    assert created
    return entry


def _checked(org, ctx=""):
    _never_above_gross(org)
    for check in (reconcile.check_i2(org=org), reconcile.check_i5(org)):
        assert check.ok, (ctx, check.name, check.failures)


@pytest.mark.django_db
def test_a_promo_grant_that_repaid_the_later_spends_debt_gets_it_back():
    """S_a then S_b overdraw; a top-up repays the older debt (S_a's), then a
    promo credit repays S_b's (the verify's p1; since ruling 971bd25c the
    grant is refused and the credit is a correction). Refunding S_b gives the
    correction's 5_000 back to promo; purchased would turn promo into cash."""
    org = _org("lot1")
    _spend(org, "req_lot1_a", 5_000)
    _spend(org, "req_lot1_b", 5_000)
    _top_up(org, "lot1", 5_000)
    _correct(org, 5_000, "lot1-promo")
    assert _buckets(org) == (0, 0, 0, 0)

    assert _funded(_refund(org, "req_lot1_b")) == (5_000, 0, 0, 0)
    assert _funded(_refund(org, "req_lot1_a")) == (0, 0, 5_000, 0)
    assert _buckets(org) == (5_000, 0, 5_000, 0)
    _checked(org)


@pytest.mark.django_db
def test_the_later_spend_refunded_first_gets_the_credit_that_repaid_it():
    """test_two_indebted_spends_never_both_claim_one_grants_repayment with the
    refunds the other way round (the verify's p1b): the promo correction
    repaid S_a and the top-up S_b, whatever order the refunds come in."""
    org = _org("lot2")
    _spend(org, "req_lot2_a", 5_000)
    _spend(org, "req_lot2_b", 5_000)
    _correct(org, 5_000, "lot2-promo")
    _top_up(org, "lot2", 5_000)

    assert _funded(_refund(org, "req_lot2_b")) == (0, 0, 5_000, 0)
    assert _funded(_refund(org, "req_lot2_a")) == (5_000, 0, 0, 0)
    assert _buckets(org) == (5_000, 0, 5_000, 0)
    _checked(org)


@pytest.mark.django_db
def test_debt_older_than_the_spend_is_repaid_first():
    """A negative adjustment's 5_000 of debt is older than S's: the promo
    correction repays it and the top-up repays S's (the verify's p3). Refunding
    S gives purchased back; promo would turn paid money into promo credit."""
    org = _org("lot3")
    mc.adjust(org, -5_000, idempotency_key="lot3-adj", reason="older debt", actor=None)
    _spend(org, "req_lot3", 5_000)
    _correct(org, 5_000, "lot3-promo")
    _top_up(org, "lot3", 5_000)
    assert _buckets(org) == (0, 0, 0, 0)

    assert _funded(_refund(org, "req_lot3")) == (0, 0, 5_000, 0)
    assert _buckets(org) == (0, 0, 5_000, 0)
    _checked(org)


@pytest.mark.django_db
def test_debt_from_entries_without_funding_columns_is_the_oldest_lot():
    """usage/0032 seeded the account's debt from entries written before the
    funding columns (here one legacy spend). That debt is older than any
    funded spend, so the promo correction repays it and the top-up repays S's."""
    org = _org("lot4")
    ManagedCreditEntry.objects.create(
        organization=org,
        kind=Kind.SPEND,
        amount_micros=-5_000,
        idempotency_key=f"spend:req_lot4_legacy_{uuid.uuid4().hex[:8]}",
    )
    ManagedCreditAccount.objects.create(
        organization=org, settled_spend_micros=5_000, debt_micros=5_000, entry_seq=1
    )
    _checked(org, "the seeded legacy account")
    _spend(org, "req_lot4", 5_000)
    _correct(org, 5_000, "lot4-promo")
    _top_up(org, "lot4", 5_000)
    assert _buckets(org) == (0, 0, 0, 0)

    assert _funded(_refund(org, "req_lot4")) == (0, 0, 5_000, 0)
    assert _buckets(org) == (0, 0, 5_000, 0)
    _checked(org)


@pytest.mark.django_db
def test_a_refund_that_repays_other_debt_passes_its_credits_bucket_on():
    """S1's debt is repaid by a promo correction; S2 overdraws; S1's refund
    repays S2's debt with the correction's money (the verify's p5, the first
    rule's disclosed limit). So S2's refund gives promo back: the correction
    ends unspent, in promo."""
    org = _org("lot5")
    _spend(org, "req_lot5_1", 5_000)
    _correct(org, 5_000, "lot5-promo")
    _spend(org, "req_lot5_2", 5_000)

    assert _funded(_refund(org, "req_lot5_1")) == (0, 0, 0, 5_000)
    assert _funded(_refund(org, "req_lot5_2")) == (5_000, 0, 0, 0)
    assert _buckets(org) == (5_000, 0, 0, 0)
    _checked(org)


@pytest.mark.django_db
def test_a_refund_repays_other_debt_with_its_spends_oldest_repayment():
    """S1's debt was repaid by a promo correction (3_000), then a top-up
    (2_000); then S2 overdraws 3_000. Refunding S1 repays that debt first, with
    the oldest repayment (the correction's), and gives the top-up's 2_000 back.
    S2's refund then gives the correction's 3_000 back, so each bucket ends
    holding exactly what its credits brought in."""
    org = _org("lot6")
    _spend(org, "req_lot6_1", 5_000)
    _correct(org, 3_000, "lot6-promo")
    _top_up(org, "lot6", 2_000)
    _spend(org, "req_lot6_2", 3_000)

    assert _funded(_refund(org, "req_lot6_1")) == (0, 0, 2_000, 3_000)
    assert _funded(_refund(org, "req_lot6_2")) == (3_000, 0, 0, 0)
    assert _buckets(org) == (3_000, 0, 2_000, 0)
    _checked(org)


@pytest.mark.django_db
def test_a_refund_cancels_its_own_unpaid_debt_before_any_other():
    """S1 is partly repaid (a promo correction, 2_000 of 5_000) when S2
    overdraws 4_000. S1's refund cancels its own unpaid 3_000 first, then
    repays 2_000 of S2's debt with the correction's money; nothing is left to
    give back. S2's refund cancels its unpaid 2_000 and gives the correction's
    2_000 back to promo."""
    org = _org("lot7")
    _spend(org, "req_lot7_1", 5_000)
    _correct(org, 2_000, "lot7-promo")
    _spend(org, "req_lot7_2", 4_000)
    assert _buckets(org) == (0, 0, 0, 7_000)

    assert _funded(_refund(org, "req_lot7_1")) == (0, 0, 0, 5_000)
    assert _funded(_refund(org, "req_lot7_2")) == (2_000, 0, 0, 2_000)
    assert _buckets(org) == (2_000, 0, 0, 0)
    _checked(org)


def _fifo_repaid(org):
    """The verify's oracle: spend pk -> [(bucket, micros)], first in first out
    over the ledger's own funded debt column, oldest lot first."""
    lots = deque()
    repaid = defaultdict(list)
    rows = ManagedCreditEntry.objects.filter(organization=org).order_by("created_at", "id")
    for e in rows:
        debt = e.funded_debt_micros or 0
        if debt < 0:
            lots.append([e.pk, -debt])
        elif debt > 0:
            assert e.kind != Kind.REFUND, "the oracle covers credits only"
            left = debt
            while left:
                lot = lots[0]
                take = min(lot[1], left)
                repaid[lot[0]].append((e.funding, take))
                lot[1] -= take
                left -= take
                if not lot[1]:
                    lots.popleft()
    assert not lots, "the history must end with no debt"
    return repaid


def _overdraft_history(org, rnd, tag):
    kinds = ["spend", "spend"] + [
        rnd.choice(["spend", "spend", "promo", "promo", "commit", "topup", "topup", "adjust"])
        for _ in range(rnd.randint(3, 8))
    ]
    rnd.shuffle(kinds)
    spends = []
    for i, kind in enumerate(kinds):
        amt = rnd.randint(1, 8) * 1_000
        if kind == "spend":
            _spend(org, f"req_{tag}_{i}", amt)
            spends.append(f"req_{tag}_{i}")
        elif kind == "promo":
            _credit(org, amt, f"{tag}-g{i}")
        elif kind == "commit":
            _credit(org, amt, f"{tag}-c{i}", bucket=Bucket.COMMIT)
        elif kind == "topup":
            _top_up(org, f"{tag}_{i}", amt)
        else:
            mc.adjust(org, -amt, idempotency_key=f"{tag}-a{i}", reason="other debt", actor=None)
    debt = mc.ledger_balance(org).debt_micros
    if debt:
        _top_up(org, f"{tag}_end", debt)
    return spends


@pytest.mark.django_db
def test_every_refund_after_the_debt_is_repaid_matches_first_in_first_out():
    """The verify's fuzz (adv7): 40 overdraft histories with other debt mixed
    in, repaid in full; a random subset of spends refunded in random order.
    Each refund returns exactly the first-in-first-out repayments of its own
    lot, plus what the spend drew from the wallet."""
    misses = []
    for seed in range(40):
        rnd = random.Random(7000 + seed)
        org = _org(f"fifo{seed}")
        spends = _overdraft_history(org, rnd, f"fifo{seed}")
        oracle = _fifo_repaid(org)
        for req in rnd.sample(spends, rnd.randint(1, len(spends))):
            spend = ManagedCreditEntry.objects.get(idempotency_key=f"spend:{req}")
            want = {
                "promo": -spend.funded_promo_micros,
                "commit": -spend.funded_commit_micros,
                "purchased": -spend.funded_purchased_micros,
            }
            for bucket, take in oracle.get(spend.pk, []):
                want[bucket] += take
            got = _refund(org, req)
            have = {
                "promo": got.funded_promo_micros,
                "commit": got.funded_commit_micros,
                "purchased": got.funded_purchased_micros,
            }
            assert got.funded_debt_micros == 0
            if have != want:
                misses.append((seed, req, want, have))
        _checked(org, f"seed {seed}")
    assert misses == [], misses[:4]


def _refund_everything_history(org, rnd, tag):
    """Spends, promo and commit grants, top-ups and full refunds, interleaved;
    then every spend not yet refunded is refunded. Returns each bucket's gross."""
    open_, gross = [], {"promo": 0, "commit": 0, "purchased": 0}
    for i in range(rnd.randint(8, 16)):
        op = rnd.choice(["spend", "spend", "spend", "promo", "commit", "topup", "refund", "refund"])
        amt = rnd.randint(1, 8) * 1_000
        if op == "spend":
            _spend(org, f"req_{tag}_{i}", amt)
            open_.append(f"req_{tag}_{i}")
        elif op == "promo":
            _credit(org, amt, f"{tag}-g{i}")
            gross["promo"] += amt
        elif op == "commit":
            _credit(org, amt, f"{tag}-c{i}", bucket=Bucket.COMMIT)
            gross["commit"] += amt
        elif op == "topup":
            _top_up(org, f"{tag}_{i}", amt)
            gross["purchased"] += amt
        elif not open_:
            continue  # nothing to refund yet
        else:
            _refund(org, open_.pop(rnd.randrange(len(open_))))
        _checked(org, (tag, i, op))
    rnd.shuffle(open_)
    for req in open_:
        _refund(org, req)
        _checked(org, (tag, req))
    return gross


@pytest.mark.django_db
def test_once_every_spend_is_refunded_each_bucket_holds_exactly_its_credits():
    """Rule-independent: whichever lot a repayment closed, refunding every
    spend (at any time, with debt outstanding or not) must hand every credit's
    money back to its own bucket and leave no debt. The first rule left promo
    in purchased (p5) or the other way round."""
    for seed in range(30):
        rnd = random.Random(5100 + seed)
        org = _org(f"all{seed}")
        gross = _refund_everything_history(org, rnd, f"all{seed}")
        assert _buckets(org) == (gross["promo"], gross["commit"], gross["purchased"], 0), seed


# --- clawback follows the recorded lot (ruling 529.2) -----------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("clawback", ["reverse_topup", "dispute_hold_lost"])
def test_v8_attr_must_529_2_clawback_follows_the_recorded_lot(clawback):
    """Ruling 529.2 pinned: the top-up repaid S's lot, Stripe took it back
    (its own debt lot), a promo credit repaid that lot (a correction: ruling
    971bd25c refuses the grant while the clawback's debt is open). S's refund
    follows the recorded lot (purchased), not the net position (promo)."""
    org = _org("claw")
    _spend(org, "req_v8claw", 5_000)
    pi = "pi_v8claw"
    mc.fulfil_topup(org, payment=_cs("cs_test_v8claw", 5_000, pi=pi))
    if clawback == "reverse_topup":
        mc.reverse_topup(
            org, refund_id="re_v8claw", payment_intent_id=pi, amount_micros=5_000,
            mode=mc.StripeMode.TEST,
        )
    else:
        mc.hold_dispute(
            org, dispute_id="dp_v8claw", payment_intent_id=pi, amount_micros=5_000,
            mode=mc.StripeMode.TEST,
        )
    assert _buckets(org) == (0, 0, 0, 5_000)
    _correct(org, 5_000, "v8claw-promo")
    assert _funded(_refund(org, "req_v8claw")) == (0, 0, 5_000, 0)
    assert _buckets(org) == (0, 0, 5_000, 0)
    _checked(org)


# --- the per-bucket cap under writer clock skew -----------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("bucket", [Bucket.PROMO, Bucket.COMMIT], ids=["promo", "commit"])
def test_v9_skew_must_one_grants_repayment_comes_home_once(bucket):
    """S1 overdraws 5_000; a correction repays it (ruling 971bd25c refuses
    the grant while S1's debt is open); S1's refund brings the correction's
    5_000 home. S2 (10_000) then draws that 5_000 and overdraws 5_000, which a
    top-up repays; S2 is written by a writer whose clock is 1 s behind S1's.
    The replay now sees S2's lot as the one the correction closed, but its
    repayment already came home once: the cap must send that share to
    purchased, so the bucket ends at exactly its gross 5_000."""
    org = _org(f"skew-{bucket.value}")
    s1 = _spend(org, f"req_v9sk1_{bucket.value}", 5_000)
    _correct(org, 5_000, f"v9sk-{bucket.value}", bucket)
    r1 = _refund(org, f"req_v9sk1_{bucket.value}")
    want_r1 = (5_000, 0, 0, 0) if bucket == Bucket.PROMO else (0, 5_000, 0, 0)
    assert _funded(r1) == want_r1
    behind = s1.created_at - timedelta(seconds=1)
    with mock.patch("django.utils.timezone.now", return_value=behind):
        s2 = _spend(org, f"req_v9sk2_{bucket.value}", 10_000)
    assert s2.created_at < s1.created_at, (s2.created_at, s1.created_at)
    mc.fulfil_topup(
        org,
        payment=mc.TopupPayment(
            mc.TopupSource.CHECKOUT_SESSION, f"cs_test_v9sk{bucket.value}", 5_000, mc.StripeMode.TEST,
            payment_intent_id=f"pi_v9sk{bucket.value}",
        ),
    )
    assert _buckets(org) == (0, 0, 0, 0)

    r2 = _refund(org, f"req_v9sk2_{bucket.value}")

    a = ManagedCreditAccount.objects.get(organization=org)
    i2, i5 = reconcile.check_i2(org=org), reconcile.check_i5(org)
    assert i2.ok and i5.ok, (i2.failures, i5.failures)
    assert a.promo_balance_micros <= a.promo_total_micros, ("promo above gross", _funded(r2))
    assert a.commit_balance_micros <= a.commit_total_micros, ("commit above gross", _funded(r2))
    want = (5_000, 0, 5_000, 0) if bucket == Bucket.PROMO else (0, 5_000, 5_000, 0)
    assert _buckets(org) == want, (_buckets(org), _funded(r2))
    assert ManagedCreditEntry.objects.filter(organization=org).count() == 6


# --- debt lots, pure ----------------------------------------------------------------


def test_debt_lots_close_oldest_first_and_name_who_repaid_a_spend():
    rows = [
        ("s1", -5_000, None, None),
        ("s2", -5_000, None, None),
        ("t", 5_000, Bucket.PURCHASED, None),  # repays s1
        ("g", 3_000, Bucket.PROMO, None),  # repays 3_000 of s2
    ]
    assert funding.debt_lot(rows, 0, "s2") == (2_000, [(Bucket.PROMO, 3_000)])
    assert funding.debt_lot(rows, 0, "s1") == (0, [(Bucket.PURCHASED, 5_000)])
    # Debt from before the funding columns is the oldest lot: the top-up repays it.
    assert funding.debt_lot(rows, 5_000, "s1") == (2_000, [(Bucket.PROMO, 3_000)])
    assert funding.debt_lot(rows, 5_000, "s2") == (5_000, [])


def test_a_refund_in_the_replay_cancels_its_own_debt_then_pays_with_its_repayments():
    rows = [
        ("s1", -5_000, None, None),
        ("g", 2_000, Bucket.PROMO, None),  # repays 2_000 of s1
        ("s2", -4_000, None, None),
        ("r1", 5_000, None, "s1"),  # s1's 3_000 unpaid, then 2_000 of s2 with the grant's money
    ]
    assert funding.debt_lot(rows, 0, "s2", {"s1"}) == (2_000, [(Bucket.PROMO, 2_000)])
    # A refund whose spend the replay cannot see pays with purchased (the old
    # rule): 3_000 closes the rest of s1's lot, 2_000 part of s2's.
    rows[-1] = ("r1", 5_000, None, "unknown")
    assert funding.debt_lot(rows, 0, "s2", {"unknown"}) == (2_000, [(Bucket.PURCHASED, 2_000)])


def test_refund_repaid_by_skips_what_repays_other_debt_and_honours_the_caps():
    spend = Allocation(debt=-5_000)
    repayments = [(Bucket.PROMO, 3_000), (Bucket.PURCHASED, 2_000)]
    # No debt outstanding: every repayment goes back, oldest first.
    assert funding.refund_repaid_by(Buckets(), 5_000, spend, 0, repayments) == (
        (Bucket.PROMO, 3_000),
        (Bucket.PURCHASED, 2_000),
    )
    # 3_000 of other debt: the oldest repayment repays it, the rest goes back.
    assert funding.refund_repaid_by(Buckets(debt=3_000), 5_000, spend, 0, repayments) == (
        (Bucket.PURCHASED, 2_000),
    )
    # The spend's own unpaid part is cancelled before any repayment is used.
    assert funding.refund_repaid_by(
        Buckets(debt=1_000), 5_000, spend, 1_000, [(Bucket.PROMO, 4_000)]
    ) == ((Bucket.PROMO, 4_000),)
    # A partial refund gives back the oldest repayments first.
    assert funding.refund_repaid_by(Buckets(), 4_000, spend, 0, repayments) == (
        (Bucket.PROMO, 3_000),
        (Bucket.PURCHASED, 1_000),
    )
    # A cap holds a bucket to what its credits still have to give back.
    capped = {Bucket.PROMO: 1_000, Bucket.COMMIT: 0, Bucket.PURCHASED: 9_000}
    assert funding.refund_repaid_by(Buckets(), 5_000, spend, 0, repayments, capped) == (
        (Bucket.PROMO, 1_000),
        (Bucket.PURCHASED, 2_000),
    )


# --- the pure allocator -----------------------------------------------------------


SPEND = Allocation(promo=-1_000, commit=0, purchased=-2_000, debt=-5_000)


def test_allocator_returns_the_repaid_share_to_the_named_buckets_in_order():
    alloc = funding.allocate_spend_refund(
        Buckets(), 8_000, SPEND, repaid_by=((Bucket.COMMIT, 3_000), (Bucket.PROMO, 4_000))
    )
    # 5_000 repaid share: commit 3_000, then promo 2_000; then the spend's own draws.
    assert alloc == Allocation(promo=2_000 + 1_000, commit=3_000, purchased=2_000, debt=0)


def test_allocator_sends_what_the_named_buckets_cannot_take_to_purchased():
    alloc = funding.allocate_spend_refund(Buckets(), 8_000, SPEND, repaid_by=((Bucket.PROMO, 1_500),))
    assert alloc == Allocation(promo=1_500 + 1_000, commit=0, purchased=3_500 + 2_000, debt=0)


def test_allocator_repays_outstanding_debt_before_any_bucket_gets_a_share():
    alloc = funding.allocate_spend_refund(
        Buckets(debt=4_000), 8_000, SPEND, repaid_by=((Bucket.PROMO, 5_000),)
    )
    assert alloc == Allocation(promo=1_000 + 1_000, commit=0, purchased=2_000, debt=4_000)


def test_allocator_without_repayers_keeps_the_purchased_rule():
    assert funding.allocate_spend_refund(Buckets(), 8_000, SPEND) == funding.allocate_spend_refund(
        Buckets(), 8_000, SPEND, repaid_by=()
    )
    assert funding.allocate_spend_refund(Buckets(), 8_000, SPEND) == Allocation(
        promo=1_000, commit=0, purchased=7_000, debt=0
    )


@pytest.mark.parametrize(
    "repaid_by",
    [((Bucket.DEBT, 1),), ((Bucket.PROMO, -1),), ((Bucket.PROMO, True),), (("promo", 1.5),)],
    ids=["debt_bucket", "negative", "bool", "float"],
)
def test_allocator_refuses_a_repayer_it_cannot_take(repaid_by):
    with pytest.raises(funding.FundingError):
        funding.allocate_spend_refund(Buckets(), 8_000, SPEND, repaid_by=repaid_by)
