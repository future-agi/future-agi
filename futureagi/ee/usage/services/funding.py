"""Funding allocator for the one billing ledger (PLAN section 1.4).

Pure functions, no database. The ledger service (``managed_credits._write``)
calls them under the org's account row lock and stores the result on the new
entry as four signed columns, ``funded_{promo,commit,purchased,debt}_micros``,
whose sum the database holds equal to ``amount_micros`` (invariant I2, the
``managed_credit_entry_funded_sum`` check constraint).

The model:

- The wallet has three buckets, each never below zero: ``promo`` (promotional
  credit), ``commit`` (contract credit) and ``purchased`` (top-ups). ``debt`` is
  what a prepaid-only org spent beyond them, never below zero either.
- ``balance = promo + commit + purchased - debt``, which equals
  ``F - S - P`` of billing-ledger-read-v1 section 4 (credit total minus gateway
  spend minus wallet-funded platform debits).
- A debit takes promo first, then commit, then purchased; what is left becomes
  debt (prepaid-only: "the wallet takes it all and the balance goes negative").
- Every credit repays debt first, then fills its own bucket ("the next top-up
  repays the debt first, simply because the balance arithmetic includes it").
- A reversal (a refunded top-up, a dispute hold, an expiry) takes its own
  bucket first and turns any shortfall into debt, never into another bucket.
- A refund of a gateway spend returns money to the buckets that spend drew
  from, debt first. The part of its debt that credits already repaid goes
  back to the buckets of those credits (ruling 492.4): a promo grant's share
  to promo, so an expiring grant stays expiring; a commit grant's to commit;
  a top-up's to purchased. Debt is one balance, so which credit repaid which
  spend is a rule (``debt_lot``): every entry that added debt is a lot, and
  every repayment closes the oldest open lots first (first in, first out).
  A refund, like every credit, repays outstanding debt first: its own
  spend's unpaid part, then other debt, paid with its spend's oldest
  repayments; the next ones go back to their buckets (``refund_repaid_by``),
  each bucket capped by what its credits repaid net of the shares earlier
  refunds already returned to it (``managed_credits._debt_repayers``). A
  share no repayment covers goes to purchased, the rule before 2A-c.
  Clawback follows the recorded lot, not the net position (ruling 529.2):
  a reversal or a lost dispute's hold opens its own debt lot, and a credit
  that repays that lot never relabels the repayment already recorded on a
  spend's lot, so the spend's refund still returns that repayment's bucket.

The ``debt`` column of an ``Allocation`` is the change of the debt bucket's
contribution to the balance, which is ``-debt``: a debit that adds 5 of debt
has ``debt=-5``; a credit that repays 5 has ``debt=+5``. So every
``Allocation`` sums to the entry's signed amount.

Promotional credit is one bucket here. PLAN section 1.4 orders promo grants
"earliest expiry first"; the read API derives each grant's unspent part from
that order (``promotional_expiring``), so nothing per grant is stored.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum


class Bucket(StrEnum):
    """A wallet funding bucket. ``DEBT`` is the overdraft, not a source."""

    PROMO = "promo"
    COMMIT = "commit"
    PURCHASED = "purchased"
    DEBT = "debt"


# The order a debit draws the wallet in (PLAN section 1.4).
WALLET_ORDER = (Bucket.PROMO, Bucket.COMMIT, Bucket.PURCHASED)
# The buckets a credit may fill.
CREDIT_BUCKETS = frozenset(WALLET_ORDER)


class FundingError(ValueError):
    """An allocation was asked for with an amount or bucket it cannot take."""


@dataclass(frozen=True)
class Buckets:
    """An org's wallet state. Every field is a non-negative integer of micros."""

    promo: int = 0
    commit: int = 0
    purchased: int = 0
    debt: int = 0

    def __post_init__(self):
        for name in ("promo", "commit", "purchased", "debt"):
            value = getattr(self, name)
            if not _is_int(value) or value < 0:
                raise FundingError(f"bucket {name} must be a non-negative int, got {value!r}")

    @property
    def balance(self) -> int:
        return self.promo + self.commit + self.purchased - self.debt

    def get(self, bucket: Bucket) -> int:
        return getattr(self, bucket.value)


@dataclass(frozen=True)
class Allocation:
    """Signed per-bucket changes of one entry. ``total`` is the entry's amount."""

    promo: int = 0
    commit: int = 0
    purchased: int = 0
    debt: int = 0

    @property
    def total(self) -> int:
        return self.promo + self.commit + self.purchased + self.debt

    def as_columns(self) -> dict[str, int]:
        """The entry columns this allocation fills."""
        return {
            "funded_promo_micros": self.promo,
            "funded_commit_micros": self.commit,
            "funded_purchased_micros": self.purchased,
            "funded_debt_micros": self.debt,
        }

    @property
    def single_bucket(self) -> Bucket | None:
        """The one bucket this allocation touches, or None when it touches several."""
        touched = [b for b in Bucket if getattr(self, b.value) != 0]
        return touched[0] if len(touched) == 1 else None


def apply(buckets: Buckets, allocation: Allocation) -> Buckets:
    """The wallet after ``allocation``. Raises if a bucket would go negative."""
    return Buckets(
        promo=buckets.promo + allocation.promo,
        commit=buckets.commit + allocation.commit,
        purchased=buckets.purchased + allocation.purchased,
        # allocation.debt is the change of -debt.
        debt=buckets.debt - allocation.debt,
    )


def allocate_debit(buckets: Buckets, micros: int) -> Allocation:
    """Draw ``micros`` (> 0) from the wallet: promo, commit, purchased, then debt."""
    _require_positive(micros)
    remaining = micros
    taken: dict[str, int] = {}
    for bucket in WALLET_ORDER:
        take = min(buckets.get(bucket), remaining)
        taken[bucket.value] = -take
        remaining -= take
    return Allocation(**taken, debt=-remaining)


def allocate_credit(buckets: Buckets, micros: int, bucket: Bucket) -> Allocation:
    """Credit ``micros`` (> 0) to ``bucket``, repaying any debt first."""
    _require_positive(micros)
    if bucket not in CREDIT_BUCKETS:
        raise FundingError(f"a credit cannot fill the {bucket} bucket")
    repay = min(buckets.debt, micros)
    return Allocation(**{bucket.value: micros - repay}, debt=repay)


def allocate_reversal(buckets: Buckets, micros: int, bucket: Bucket) -> Allocation:
    """Take ``micros`` (> 0) back from ``bucket``; any shortfall becomes debt."""
    _require_positive(micros)
    if bucket not in CREDIT_BUCKETS:
        raise FundingError(f"a reversal cannot draw the {bucket} bucket")
    take = min(buckets.get(bucket), micros)
    return Allocation(**{bucket.value: -take}, debt=-(micros - take))


def allocate_spend_refund(
    buckets: Buckets,
    micros: int,
    spend: Allocation | None,
    repaid_by: Iterable[tuple[Bucket, int]] = (),
) -> Allocation:
    """Return ``micros`` (> 0) of a refunded spend to the buckets it drew from.

    The spend's debt part comes back first (``refund_debt_split``): the share
    that repays outstanding debt, then the share credits already repaid,
    which is their money again. ``repaid_by`` names those credits as
    ``(bucket, micros)`` pairs in the order they take it back (the ledger
    passes ``refund_repaid_by``: the spend's repayments, first in first out,
    capped); what they do not cover goes to ``purchased``, as it all did
    before ruling 492.4. Then purchased, commit and promo, each at most what
    the spend took from it. A spend written before funding columns existed
    (``spend`` is None) is refunded like a top-up: debt, then purchased.
    """
    _require_positive(micros)
    sources = _repayers(repaid_by)
    if spend is None:
        return allocate_credit(buckets, micros, Bucket.PURCHASED)
    remaining = micros
    back: dict[str, int] = dict.fromkeys((b.value for b in WALLET_ORDER), 0)
    repay, repaid = refund_debt_split(buckets, remaining, spend)
    remaining -= repay + repaid
    for bucket, available in sources:
        give = min(available, repaid)
        back[bucket.value] += give
        repaid -= give
    back[Bucket.PURCHASED.value] += repaid
    for bucket in reversed(WALLET_ORDER):
        give = min(-getattr(spend, bucket.value), remaining)
        back[bucket.value] += give
        remaining -= give
    if remaining:
        # Only reachable when the refund exceeds what the spend drew, which the
        # ledger refuses before allocating (RefundExceedsSpend).
        raise FundingError(f"refund exceeds its spend by {remaining} micros")
    return Allocation(**back, debt=repay)


def refund_debt_split(buckets: Buckets, micros: int, spend: Allocation) -> tuple[int, int]:
    """``(repay, repaid)``: how a refund of ``micros`` returns ``spend``'s debt part.

    ``repay`` repays outstanding debt (a refund, like every credit, repays debt
    first), at most the spend's debt part. ``repaid`` is the rest of the debt
    part this refund covers: credits already repaid it, so it is theirs.
    """
    spend_debt = -spend.debt
    repay = min(spend_debt, buckets.debt, micros)
    return repay, min(spend_debt - repay, micros - repay)


def debt_lot(
    rows: Iterable[tuple[object, int, Bucket | None, object]],
    opening_debt: int,
    spend_id: object,
    refunded: Iterable[object] = (),
) -> tuple[int, list[tuple[Bucket, int]]]:
    """Replay debt as lots: ``spend_id``'s unpaid part, and who repaid the rest.

    Debt is one balance, but every entry that added debt is a lot, and every
    repayment closes the oldest open lots first: first in, first out, so
    "every credit repays debt first" in the order the debt was made.
    ``opening_debt`` (debt from entries without funding columns, usage/0032)
    is the oldest lot. A credit repays with its own bucket. A refund repays
    with the money it hands back: first it cancels its own spend's unpaid
    part, then pays with that spend's repayments, oldest first, exactly as
    ``refund_repaid_by`` chose them; a part neither covers counts as
    purchased, the rule before ruling 492.4.

    ``rows`` are ``(entry id, funded debt micros, the bucket a credit fills or
    None, the spend a refund returns or None)`` in write order; rows with no
    debt change may be left out. ``refunded`` names the spends whose refunds
    are in ``rows``: only their repayments and ``spend_id``'s are kept.
    Returns ``(unpaid micros, [(bucket, micros), ...])``, repayments in the
    order they came.
    """
    watched = {spend_id, *refunded}
    watched.discard(None)
    lots: deque[list] = deque()
    if opening_debt > 0:
        lots.append([None, opening_debt])
    own: dict[object, list] = {}
    repaid_by: dict[object, list[tuple[Bucket, int]]] = defaultdict(list)
    for entry_id, debt, bucket, refunded_spend in rows:
        if debt < 0:
            lot = [entry_id, -debt]
            lots.append(lot)
            if entry_id in watched:
                own[entry_id] = lot
        elif debt > 0:
            if refunded_spend is None:
                payers = [(Bucket(bucket) if bucket else Bucket.PURCHASED, debt)]
            else:
                payers = _refund_payers(
                    own.get(refunded_spend), repaid_by.pop(refunded_spend, []), debt
                )
            _close_oldest(lots, payers, repaid_by, watched)
    lot = own.get(spend_id)
    return (lot[1] if lot is not None else 0), list(repaid_by.get(spend_id, ()))


def refund_repaid_by(
    buckets: Buckets,
    micros: int,
    spend: Allocation,
    unpaid: int,
    repayments: Iterable[tuple[Bucket, int]],
    caps: Mapping[Bucket, int] | None = None,
) -> tuple[tuple[Bucket, int], ...]:
    """``repaid_by`` for a refund of ``micros`` of ``spend`` (ruling 492.4).

    ``unpaid`` and ``repayments`` are ``debt_lot``'s answer for the spend. The
    refund's repayment of outstanding debt (``refund_debt_split``) cancels the
    spend's own unpaid part first; beyond it, it is paid with the oldest
    repayments, which then stay in the debt they repay. The next repayments,
    up to the repaid share, go back to their buckets, each bucket at most its
    ``caps`` entry when caps are given. What they do not cover is left for
    ``allocate_spend_refund`` to send to purchased.
    """
    repay, repaid = refund_debt_split(buckets, micros, spend)
    skip = max(repay - unpaid, 0)
    left = dict(caps) if caps is not None else None
    sources: list[tuple[Bucket, int]] = []
    for bucket, available in repayments:
        if repaid <= 0:
            break
        used = min(skip, available)
        skip -= used
        take = min(available - used, repaid)
        if left is not None:
            take = min(take, max(left.get(bucket, 0), 0))
            left[bucket] = left.get(bucket, 0) - take
        if take > 0:
            sources.append((bucket, take))
            repaid -= take
    return tuple(sources)


def _refund_payers(
    lot: list | None, repayments: list[tuple[Bucket, int]], debt: int
) -> list[tuple[Bucket, int]]:
    """What a refund's ``debt`` repayment is paid with, in ``debt_lot``'s replay."""
    cancel = min(lot[1], debt) if lot is not None else 0
    if lot is not None:
        lot[1] -= cancel
    left = debt - cancel
    payers = []
    for bucket, micros in repayments:
        take = min(micros, left)
        if take > 0:
            payers.append((bucket, take))
            left -= take
    if left > 0:
        payers.append((Bucket.PURCHASED, left))
    return payers


def _close_oldest(
    lots: deque[list],
    payers: list[tuple[Bucket, int]],
    repaid_by: dict[object, list[tuple[Bucket, int]]],
    watched: set,
) -> None:
    """Close the oldest open lots with ``payers``, recording watched lots' payers."""
    for bucket, micros in payers:
        while micros > 0 and lots:
            lot = lots[0]  # first in, first out
            take = min(lot[1], micros)
            lot[1] -= take
            micros -= take
            if take > 0 and lot[0] in watched:
                repaid_by[lot[0]].append((bucket, take))
            if lot[1] == 0:
                lots.popleft()


def allocate_signed(buckets: Buckets, signed_micros: int, bucket: Bucket) -> Allocation:
    """An adjustment: positive credits ``bucket``; negative draws like a debit."""
    if not _is_int(signed_micros) or signed_micros == 0:
        raise FundingError("an adjustment is a non-zero integer of micros")
    if signed_micros > 0:
        return allocate_credit(buckets, signed_micros, bucket)
    debit = allocate_debit(buckets, -signed_micros)
    return debit


def legacy_allocation(buckets: Buckets, kind: str, amount: int) -> Allocation:
    """The rule usage/0032 applied to entries written before funding columns.

    A grant or positive adjustment fills promo, any other credit purchased
    (each repaying debt first); an expiry draws promo; any other debit draws
    promo, commit, purchased, then debt.
    """
    if amount == 0:
        return Allocation()
    if amount > 0:
        bucket = Bucket.PROMO if kind in ("grant", "adjustment") else Bucket.PURCHASED
        return allocate_credit(buckets, amount, bucket)
    if kind == "expiry":
        return allocate_reversal(buckets, -amount, Bucket.PROMO)
    return allocate_debit(buckets, -amount)


def promotional_remaining(
    grants: list[tuple[int, object]], consumed: int
) -> list[tuple[int, object]]:
    """Each promo grant's unspent part, spending ``consumed`` earliest expiry first.

    ``grants`` is ``[(amount_micros, expires_at), ...]`` in that order (None
    expiry last). Returns the grants still holding money, in the same order.
    """
    left = []
    for amount, expires_at in grants:
        use = min(amount, consumed)
        consumed -= use
        if amount - use > 0:
            left.append((amount - use, expires_at))
    return left


def _repayers(repaid_by: Iterable[tuple[Bucket, int]]) -> list[tuple[Bucket, int]]:
    """``repaid_by`` checked: wallet buckets only, each a non-negative int."""
    sources = []
    for bucket, micros in repaid_by:
        try:
            bucket = Bucket(bucket)
        except ValueError as exc:
            raise FundingError(f"a repaid share cannot go to {bucket!r}") from exc
        if bucket not in CREDIT_BUCKETS:
            raise FundingError(f"a repaid share cannot go to the {bucket} bucket")
        if not _is_int(micros) or micros < 0:
            raise FundingError(f"a repaid share is a non-negative int, got {micros!r}")
        sources.append((bucket, micros))
    return sources


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_positive(micros: object) -> None:
    if not _is_int(micros) or micros <= 0:
        raise FundingError(f"amount must be a positive integer of micros, got {micros!r}")
