"""Managed credit ledger service: the only writer of ``ManagedCreditEntry``.

Every credit and debit is one idempotent entry, in integer micros. A write
locks the org's ``ManagedCreditAccount`` (``SELECT ... FOR UPDATE``), inserts
its entry with ``INSERT ... ON CONFLICT DO NOTHING``, and changes the account
only when that row was inserted. A duplicate key returns ``(existing, False)``.

Phase 2a (BL-LEDGER, PLAN 1.2 and 1.4) makes this the one org ledger:

- every balance-changing entry carries its wallet funding split
  (``services/funding.py``): promo, commit, purchased, then debt. The account
  keeps the bucket balances, so ``balance = F - S - P`` is read, not summed;
- ``_write`` routes by kind: ``spend`` adds to ``settled_spend_micros`` (gateway
  only); a wallet ``usage_debit`` adds to ``platform_spend_micros`` and bumps the
  version; every credit-side kind bumps the version and recomputes
  ``granted_total_micros`` (F); an invoice-funded ``usage_debit`` changes no
  balance;
- on commit the balance cache is written, ``balance_changed`` is sent, and a
  change of the pushed value ``max(F - P, 0)`` requests the coalesced gateway
  push (``services/gateway_push.py``);
- while the org is frozen (``services/money_freeze.py``: a receivable or an
  open dispute) the cached and signalled balance is 0 and the pushed value is
  capped at S, so a settled spend that moves S bumps the version and pushes
  too. The stored buckets are never changed by the freeze.

The gateway's Redis is only the hot path; this ledger is the system of record.
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from functools import cache, partial
from typing import Any

import structlog
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import QuerySet, Sum
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.user import User
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.services import funding, money_freeze
from ee.usage.services.auto_topup import (  # re-exported: BL-STRIPE's interface
    claim_auto_topup as claim_auto_topup,
)
from ee.usage.services.auto_topup import (
    clear_claim_on_fulfil,
)
from ee.usage.services.auto_topup import (
    configure_auto_topup as configure_auto_topup,
)
from ee.usage.services.auto_topup import (
    finish_auto_topup as finish_auto_topup,
)
from ee.usage.services.funding import Allocation, Bucket, Buckets
from ee.usage.services.gateway_push import (  # re-exported: GW-WALLET's interface
    ensure_account_pushed as ensure_account_pushed,
)
from ee.usage.services.gateway_push import (
    granted_value,
    request_push,
)
from ee.usage.services.gateway_push import (
    push_granted as push_granted,
)
from ee.usage.services.gateway_push import (
    pushed_value as pushed_value,
)
from ee.usage.services.gateway_push import (
    sweep_unacked_pushes as sweep_unacked_pushes,
)
from ee.usage.services.money_freeze import (  # re-exported: BL-STRIPE's interface
    DISPUTE_OPEN as DISPUTE_OPEN,
)
from ee.usage.services.money_freeze import (
    RECEIVABLE_OPEN as RECEIVABLE_OPEN,
)
from ee.usage.services.money_freeze import (
    freeze_reason as freeze_reason,
)
from ee.usage.services.money_freeze import (
    freeze_reason_for as freeze_reason_for,
)

logger = structlog.get_logger(__name__)

Kind = ManagedCreditEntry.Kind
CREDIT_KINDS = frozenset(
    {
        Kind.TOPUP,
        Kind.GRANT,
        Kind.REFUND,
        Kind.ADJUSTMENT,
        Kind.EXPIRY,
        Kind.TOPUP_REVERSAL,
        Kind.DISPUTE_HOLD,
        Kind.DISPUTE_RELEASE,
    }
)
WALLET_FUNDING = "wallet"
USAGE_FUNDINGS = frozenset({WALLET_FUNDING, "invoice", "marketplace", "free"})
MANAGED_SPEND = "managed_spend"
SPEND_EVENT_TYPE = "gateway_managed_spend"
_KEY_MAX_LENGTH = ManagedCreditEntry._meta.get_field("idempotency_key").max_length

# managed-settlement-v1 integer fields and the entry columns they fill.
_RECORD_INTS = (
    ("upstream_micros", "upstream_cost_micros"),
    ("landed_micros", "landed_cost_micros"),
    ("markup_micros", "markup_micros"),
    ("markup_bps", "markup_bps"),
    ("upstream_fee_bps", "upstream_fee_bps"),
    ("processing_fee_bps", "processing_fee_bps"),
)
# String fields copied to the entry column of the same name. The record's
# generation_id (always the request id) and hold_micros have no column.
_RECORD_STRS = (
    "model",
    "provider",
    "endpoint_id",
    "served_upstream_provider",
    "upstream_key_id",
    "cost_source",
    "catalog_snapshot_id",
    "policy_version",
    "api_key_id",
    "agent_run_id",
    "agent_id",
)


class ManagedCreditError(ValueError):
    """A ledger write was refused; nothing was written."""


class IdempotencyConflict(ManagedCreditError):
    """The key already belongs to an entry of another org or kind."""


class RefundExceedsSpend(ManagedCreditError):
    """A refund asked for more than the request's settled spend."""


class InvalidSettlementRecord(ManagedCreditError):
    """A managed-settlement-v1 record that cannot be settled."""


class TopupMismatch(IdempotencyConflict):
    """A payment object already credited once arrived again with another amount,
    org, purpose or mode. Nothing is written; the caller alerts (PLAN 2.2)."""


class LiveModeRefused(ManagedCreditError):
    """A live-mode Stripe object reached a ledger whose settings do not accept
    live money (``BILLING_LEDGER_ACCEPT_LIVE`` is off, the default)."""


class AutoTopupNotClaimed(ManagedCreditError):
    """``finish_auto_topup`` named a sequence that is not the one in flight."""


class DoubleMeteredCall(IdempotencyConflict):
    """A second ledger debit for a model call that already has one (UMP guard e)."""


# --- phase 2a interface (BL-LEDGER) -------------------------------------------
#
# GW-WALLET (gateway drain and agentcc views) calls: settle_spend, refund,
# push_granted, ensure_account_pushed, pushed_value, request_push,
# sweep_unacked_pushes, balance, ledger_balance and reconcile.check_gateway.
# BL-STRIPE (private ee/cloud billing) calls: fulfil_topup, reverse_topup,
# hold_dispute, release_dispute, configure_auto_topup, claim_auto_topup,
# finish_auto_topup, freeze_reason, and stripe_records.record_stripe_event /
# mark_stripe_event / upsert_invoice_mirror. Nothing here imports Stripe:
# callers pass plain values.


class TopupSource(StrEnum):
    """The Stripe object a top-up is paid by; it decides the entry's key."""

    CHECKOUT_SESSION = "checkout_session"  # key stripe:<cs_...> (R's apply_topup)
    PAYMENT_INTENT = "payment_intent"  # key stripe_pi:<pi_...> (R's apply_topup_pi)
    INVOICE = "invoice"  # key stripe_inv:<in_...> (one-off top-up invoice, tax on)


class TopupPurpose(StrEnum):
    TOPUP = "topup"  # a customer's Checkout or "Add $25"
    AUTO_TOPUP = "auto_topup"  # an auto top-up off-session PaymentIntent


class StripeMode(StrEnum):
    TEST = "test"
    LIVE = "live"


_TOPUP_KEY_PREFIX = {
    TopupSource.CHECKOUT_SESSION: "stripe",
    TopupSource.PAYMENT_INTENT: "stripe_pi",
    TopupSource.INVOICE: "stripe_inv",
}
# A Stripe PaymentIntent id: "pi_" and at least one id character.
# Stripe documents ids as opaque (length and format may change, up to 255
# characters, case-sensitive; docs.stripe.com/api-versions) with no character
# set, so this is not narrowed further. It documents the client secret's shape,
# "<pi id>_secret_<suffix>" (docs.stripe.com/api/payment_intents/object), which
# this pattern also matches; _CLIENT_SECRET refuses it.
_PAYMENT_INTENT_ID = re.compile(r"pi_[A-Za-z0-9_]+")
_CLIENT_SECRET = re.compile(r"_secret_", re.IGNORECASE)


@dataclass(frozen=True)
class TopupPayment:
    """One paid Stripe object, as plain values (no Stripe import in this tree).

    ``amount_micros`` is the pre-tax ``amount_subtotal`` (cents x 10_000) BL-STRIPE
    already checked against the quoted intent (PLAN 2.2 rule 4).
    ``payment_intent_id`` is the PaymentIntent behind a session or an invoice,
    required for those two sources (``fulfil_topup`` refuses an empty one, and
    any value that is not a ``pi_...`` id): a second credit for the same
    PaymentIntent, under any object, is refused as a duplicate. ``auto_topup_seq`` is the claim's
    sequence for an auto top-up (``claim_auto_topup``), else None.
    """

    source: TopupSource
    object_id: str
    amount_micros: int
    mode: StripeMode
    payment_intent_id: str = ""
    purpose: TopupPurpose = TopupPurpose.TOPUP
    intent_ref: str = ""
    payment_fee_micros: int = 0
    payment_fee_estimated: bool = False
    auto_topup_seq: int | None = None


@dataclass(frozen=True)
class FulfilResult:
    """``created`` is False when this payment (or this event id) was already
    credited; ``entry`` is then the existing entry."""

    entry: ManagedCreditEntry
    created: bool


@dataclass(frozen=True)
class GrantedPush:
    """The one pushed value (PLAN 1.2 fix 3), the body of credits-granted-push-v1."""

    granted_total_micros: int
    version: int
    settled_spend_floor_micros: int

    def as_request(self) -> dict[str, int]:
        return {
            "granted_total_micros": self.granted_total_micros,
            "version": self.version,
            "settled_spend_floor_micros": self.settled_spend_floor_micros,
        }


@dataclass(frozen=True)
class LedgerBalance:
    """An org's ledger balance (billing-ledger-read-v1 section 4).

    ``credit_total_micros`` is F, ``settled_spend_micros`` S (gateway spend) and
    ``platform_spend_micros`` P (wallet-funded usage debits);
    ``balance_micros = F - S - P``, negative when the org owes (``debt_micros``).
    ``promo_micros``, ``commit_micros`` and ``purchased_micros`` are what is left in
    each wallet bucket.
    """

    org_id: str
    credit_total_micros: int
    settled_spend_micros: int
    platform_spend_micros: int
    balance_micros: int
    debt_micros: int
    promo_micros: int
    commit_micros: int
    purchased_micros: int
    version: int
    gateway_acked_version: int
    gateway_push_state: str


@dataclass(frozen=True)
class AutoTopupClaim:
    """One auto top-up charge the caller may now make, exactly once.

    ``idempotency_key`` is the Stripe Idempotency-Key to send
    (``pi:autotopup:<org>:<seq>``, PLAN 2.3), and the PaymentIntent's metadata
    should carry ``seq`` so its fulfilment clears this claim.
    """

    org_id: str
    seq: int
    amount_micros: int
    payment_method_id: str
    idempotency_key: str


# --- writes: credits ----------------------------------------------------------


def grant(
    org: Organization,
    micros: int,
    *,
    idempotency_key: str,
    actor: User | None,
    note: str,
    bucket: Bucket = Bucket.PROMO,
    expires_at: datetime | None = None,
    category: str = "",
    staff_authorized: bool = False,
) -> tuple[ManagedCreditEntry, bool]:
    """Grant ``micros`` of credit. The key is scoped to the org and kind.

    ``bucket`` is ``promo`` (staff and promotional credit, the default) or
    ``commit`` (contract credit); ``expires_at`` and ``category`` describe a
    promotional grant (PLAN 1.2).

    Automatic grants are refused (``ManagedCreditError``, nothing written)
    while the org has a receivable or open dispute. The staff/admin API may
    pass ``staff_authorized=True`` only after authorizing the caller: that
    grant repays debt first, then fills its bucket, under the account lock.
    An open dispute still refuses new grants. A replay returns the original
    entry and its recorded allocation without writing again.
    """
    if Bucket(bucket) not in (Bucket.PROMO, Bucket.COMMIT):
        raise ManagedCreditError("a grant fills the promo or commit bucket")
    amount = _positive(micros)
    key = _caller_key(Kind.GRANT, org, idempotency_key)
    with transaction.atomic():
        account = _locked_account(org)
        existing = _existing(key, org, Kind.GRANT)
        if existing is not None:
            return existing, False
        reason = freeze_reason_for(
            org.pk, debt_micros=0 if staff_authorized else account.debt_micros
        )
        if reason:
            raise ManagedCreditError(f"no new grant while the org is frozen ({reason})")
        return _write(
            org,
            Kind.GRANT,
            amount,
            key,
            bucket=Bucket(bucket),
            created_by=actor,
            note=note,
            expires_at=expires_at,
            category=str(category or "")[:32],
        )


def apply_topup(
    org: Organization,
    micros: int,
    *,
    stripe_session_id: str,
    payment_intent_id: str = "",
    payment_fee_micros: int = 0,
    payment_fee_estimated: bool = False,
    mode: StripeMode = StripeMode.TEST,
) -> tuple[ManagedCreditEntry, bool]:
    """Credit a paid Checkout session through ``fulfil_topup`` (R's pre-2a hook).

    Internal: adapters and views call ``fulfil_topup``. This hook
    takes exactly its rules and its key (``stripe:<session id>``): the session's
    ``payment_intent_id`` is required and must be a ``pi_...`` id, a session and
    its PaymentIntent credit once between them, and a live session is refused
    unless the ledger accepts live money (pass ``mode=StripeMode.LIVE``; the
    default is a test session). ``payment_fee_micros`` is Stripe's processing
    fee, recorded beside the credited amount and never subtracted from it.
    Returns ``(entry, created)``.
    """
    result = fulfil_topup(
        org,
        payment=TopupPayment(
            source=TopupSource.CHECKOUT_SESSION,
            object_id=stripe_session_id,
            amount_micros=micros,
            mode=mode,
            payment_intent_id=payment_intent_id,
            payment_fee_micros=payment_fee_micros,
            payment_fee_estimated=payment_fee_estimated,
        ),
    )
    return result.entry, result.created


def apply_topup_pi(
    org: Organization,
    micros: int,
    *,
    payment_intent_id: str,
    mode: StripeMode = StripeMode.TEST,
) -> tuple[ManagedCreditEntry, bool]:
    """Credit an off-session PaymentIntent through ``fulfil_topup`` (R's pre-2a hook).

    Internal, like ``apply_topup``: keyed ``stripe_pi:<id>``, the id must be a
    ``pi_...`` id, and a session that already credited this PaymentIntent is
    returned with ``created=False``. Returns ``(entry, created)``.
    """
    result = fulfil_topup(
        org,
        payment=TopupPayment(
            source=TopupSource.PAYMENT_INTENT,
            object_id=payment_intent_id,
            amount_micros=micros,
            mode=mode,
        ),
    )
    return result.entry, result.created


def fulfil_topup(
    org: Organization,
    *,
    payment: TopupPayment,
    stripe_event_id: str = "",
) -> FulfilResult:
    """Credit one paid Stripe object to the org's wallet, exactly once.

    The ONE function BL-STRIPE calls for a top-up: from
    ``checkout.session.completed``, ``checkout.session.async_payment_succeeded``,
    the landing page, ``payment_intent.succeeded`` (repeat and auto top-ups) and
    a top-up invoice's ``invoice.paid``. Any number of calls, concurrent or not,
    with or without an event id, write one ``topup`` entry per payment object:

    - keyed ``stripe:<cs>``, ``stripe_pi:<pi>`` or ``stripe_inv:<in>`` by source;
    - refused as a duplicate when another top-up already holds the same
      ``payment_intent_id`` (a session and its PaymentIntent never both credit);
    - ``stripe_event_id`` (``evt_...``) is recorded on the entry and in
      ``StripeEventLog``; replaying an event returns the first result.

    The credit fills the ``purchased`` bucket after repaying any debt, bumps the
    account version and schedules the coalesced gateway push on commit. An auto
    top-up's fulfilment also clears its in-flight claim.

    Raises ``TopupMismatch`` when the object was credited with another amount,
    org, purpose or mode; ``LiveModeRefused`` for a live object unless
    ``settings.BILLING_LEDGER_ACCEPT_LIVE`` is true; ``ManagedCreditError`` for a
    non-positive or non-integer amount, an empty object id, a
    ``checkout_session`` or ``invoice`` payment without its ``payment_intent_id``,
    or a PaymentIntent id (``payment_intent_id``, or a ``payment_intent``
    top-up's object id) that is not a ``pi_...`` id or is a client secret
    (``pi_..._secret_...``). These are checked after the live-mode refusal,
    before anything is read or written.
    """
    source, mode, purpose = _payment_enums(payment)
    _require_mode_allowed(mode)
    amount = _positive(payment.amount_micros)
    if not _is_micros(payment.payment_fee_micros) or payment.payment_fee_micros < 0:
        raise ManagedCreditError("payment_fee_micros is a non-negative integer")
    key = _system_key(_TOPUP_KEY_PREFIX[source], payment.object_id)
    pi_id = _topup_payment_intent(payment, source)
    event_id = _bounded_ref(stripe_event_id, "stripe_event_id")
    with transaction.atomic():
        _locked_account(org)
        event_row = (
            _event_row(event_id, mode, payment.object_id, org) if event_id else None
        )
        if event_row is not None and event_row.entry_id:
            existing = event_row.entry
            same_money = existing.stripe_ref == payment.object_id or (
                pi_id and existing.stripe_payment_intent_id == pi_id
            )
            if not same_money:
                raise TopupMismatch(
                    f"event {event_id} already credited {existing.stripe_ref!r}"
                )
            _same_topup(existing, org, amount, mode, purpose)
            return FulfilResult(_record_payment_intent(existing, pi_id), False)
        try:
            entry, created = _write(
                org,
                Kind.TOPUP,
                amount,
                key,
                bucket=Bucket.PURCHASED,
                conflict=partial(_topup_by_payment_intent, pi_id) if pi_id else None,
                stripe_ref=payment.object_id,
                stripe_payment_intent_id=pi_id,
                stripe_event_id=event_id,
                stripe_mode=mode.value,
                category=purpose.value,
                source_kind=f"stripe_{source.value}",
                source_ref=payment.object_id,
                payment_fee_micros=payment.payment_fee_micros,
                payment_fee_estimated=bool(payment.payment_fee_estimated),
                note=f"intent:{payment.intent_ref}" if payment.intent_ref else "",
            )
        except TopupMismatch:
            raise
        except IdempotencyConflict as exc:
            raise TopupMismatch(str(exc)) from exc
        if not created:
            _same_topup(entry, org, amount, mode, purpose)
            entry = _record_payment_intent(entry, pi_id)
        if event_row is not None:
            _link_event(event_row, entry, org)
        if purpose == TopupPurpose.AUTO_TOPUP:
            clear_claim_on_fulfil(org, payment.auto_topup_seq)
    return FulfilResult(entry, created)


def reverse_topup(
    org: Organization,
    *,
    refund_id: str,
    payment_intent_id: str,
    amount_micros: int,
    mode: StripeMode,
    stripe_event_id: str = "",
) -> tuple[ManagedCreditEntry, bool]:
    """Debit a refunded top-up (``charge.refunded``), keyed ``stripe_refund:<re_...>``.

    Kind ``topup_reversal``, the full refunded amount, drawn from ``purchased``;
    any shortfall becomes debt (PLAN 4.5, finding 15). ``payment_intent_id``
    must name a credited top-up of this org; refunds never exceed it in total.
    """
    mode = _mode(mode)
    _require_mode_allowed(mode)
    amount = _positive(amount_micros)
    key = _system_key("stripe_refund", refund_id)
    with transaction.atomic():
        _locked_account(org)
        existing = _existing(key, org, Kind.TOPUP_REVERSAL)
        if existing is not None:
            if existing.amount_micros != -amount:
                raise TopupMismatch(
                    f"refund {refund_id} was reversed with another amount"
                )
            return existing, False
        topup = _credited_topup(org, payment_intent_id)
        reversed_so_far = -(
            ManagedCreditEntry.objects.filter(
                organization=org, kind=Kind.TOPUP_REVERSAL, related_entry=topup
            ).aggregate(total=Sum("amount_micros"))["total"]
            or 0
        )
        if reversed_so_far + amount > topup.amount_micros:
            raise ManagedCreditError(
                f"refunds of {reversed_so_far + amount} micros exceed the "
                f"{topup.amount_micros} micros top-up {topup.stripe_ref!r}"
            )
        return _write(
            org,
            Kind.TOPUP_REVERSAL,
            -amount,
            key,
            bucket=Bucket.PURCHASED,
            related_entry=topup,
            stripe_ref=refund_id,
            stripe_payment_intent_id=topup.stripe_payment_intent_id,
            stripe_event_id=_bounded_ref(stripe_event_id, "stripe_event_id"),
            stripe_mode=mode.value,
        )


def hold_dispute(
    org: Organization,
    *,
    dispute_id: str,
    payment_intent_id: str,
    amount_micros: int,
    mode: StripeMode,
    stripe_event_id: str = "",
) -> tuple[ManagedCreditEntry, bool]:
    """Freeze a disputed charge's credit (``charge.dispute.created``).

    Kind ``dispute_hold``, keyed ``dispute_hold:<dp_...>``, drawn like a reversal.
    A lost dispute leaves the hold standing.
    """
    mode = _mode(mode)
    _require_mode_allowed(mode)
    amount = _positive(amount_micros)
    key = _system_key("dispute_hold", dispute_id)
    with transaction.atomic():
        _locked_account(org)
        existing = _existing(key, org, Kind.DISPUTE_HOLD)
        if existing is not None:
            if existing.amount_micros != -amount:
                raise TopupMismatch(
                    f"dispute {dispute_id} was held with another amount"
                )
            return existing, False
        topup = _credited_topup(org, payment_intent_id)
        if amount > topup.amount_micros:
            raise ManagedCreditError("a dispute hold exceeds its top-up")
        return _write(
            org,
            Kind.DISPUTE_HOLD,
            -amount,
            key,
            bucket=Bucket.PURCHASED,
            related_entry=topup,
            stripe_ref=dispute_id,
            stripe_payment_intent_id=topup.stripe_payment_intent_id,
            stripe_event_id=_bounded_ref(stripe_event_id, "stripe_event_id"),
            stripe_mode=mode.value,
        )


def release_dispute(
    org: Organization,
    *,
    dispute_id: str,
    mode: StripeMode,
    stripe_event_id: str = "",
) -> tuple[ManagedCreditEntry, bool]:
    """Give back a won dispute's hold, keyed ``dispute_release:<dp_...>``.

    Kind ``dispute_release``, the hold's exact amount. Raises
    ``ManagedCreditError`` when the dispute has no hold for this org.
    """
    mode = _mode(mode)
    _require_mode_allowed(mode)
    hold_key = _system_key("dispute_hold", dispute_id)
    key = _system_key("dispute_release", dispute_id)
    with transaction.atomic():
        _locked_account(org)
        hold = _existing(hold_key, org, Kind.DISPUTE_HOLD)
        if hold is None:
            raise ManagedCreditError(f"dispute {dispute_id!r} has no hold for this org")
        return _write(
            org,
            Kind.DISPUTE_RELEASE,
            -hold.amount_micros,
            key,
            bucket=Bucket.PURCHASED,
            related_entry=hold,
            stripe_ref=dispute_id,
            stripe_payment_intent_id=hold.stripe_payment_intent_id,
            stripe_event_id=_bounded_ref(stripe_event_id, "stripe_event_id"),
            stripe_mode=mode.value,
        )


# --- writes: spend and adjustments -------------------------------------------


def settle_spend(
    record: dict[str, Any], *, workspace_id: str | None = None
) -> tuple[ManagedCreditEntry, bool]:
    """Write one ``kind=spend`` managed-settlement-v1 record, keyed ``spend:<request_id>``.

    The record's values may be the outbox's strings or plain ints. A ``void``
    record is refused: it settles nothing. Exactly once per request id, however
    often and from however many drain workers it is called.

    Phase 2a (BL-LEDGER) adds, in the same transaction as the entry:

    - the entry's wallet funding split (promo, commit, purchased, then debt),
      ``agent_id`` from the record, ``workspace_id`` (the drain takes it from the
      request-log row, usage-event-v1 section 2), ``product=gateway``,
      ``dimension=managed_spend``, ``source_kind=model_call`` with
      ``source_ref=<request_id>`` (one meter per model call) and
      ``price_catalog_version`` of the catalog in force at ``ts_ms`` (null when
      none is);
    - the ``gateway_managed_spend`` display event in ``UsageEventLog``, event id
      ``uuid5(NAMESPACE_URL, "agentcc:spend:" + request_id)``, quantity
      ``billed_micros``, already rated to this entry (I1). The drain must not
      also ``emit()`` it; if it does, the same event id is a duplicate.
    """
    from ee.usage.services import rating

    org_id, billed, fields = _spend_fields(record)
    try:
        org = Organization.objects.get(pk=org_id)
    except (Organization.DoesNotExist, ValidationError) as exc:
        raise InvalidSettlementRecord(f"unknown org_id {org_id!r}") from exc
    request_id = fields["request_id"]
    occurred = fields["occurred_at"]
    fields.update(
        product="gateway",
        dimension=MANAGED_SPEND,
        unit="usd_micros",
        quantity=Decimal(billed),
        period=_period(occurred),
        source_kind="model_call",
        source_ref=request_id,
        workspace_id=_workspace_in_org(org, workspace_id),
        price_catalog_version=rating.catalog_version_at(occurred),
    )
    key = _system_key("spend", request_id)
    with transaction.atomic():
        entry, created = _write(
            org,
            Kind.SPEND,
            -billed,
            key,
            conflict=partial(_model_call_conflict, request_id),
            **fields,
        )
        if -entry.amount_micros > 0:
            _ensure_spend_event(entry)
    return entry, created


def refund(
    org: Organization,
    request_id: str,
    micros: int | None = None,
    *,
    reason: str,
    actor: User | None,
) -> tuple[ManagedCreditEntry, bool]:
    """Credit back a settled request, keyed ``refund:<request_id>``.

    ``micros`` defaults to the request's whole spend and may not exceed it. A
    request is refunded once: a second call returns the first refund. The
    money goes back to the buckets the spend drew from, debt first; the part
    of its debt that credits already repaid goes back to those credits'
    buckets (``_debt_repayers``: debt is replayed as
    first-in-first-out lots), so a promo grant that covered the overdraft
    gets its promo back, expiry and all, and a top-up's money stays
    purchased.
    """
    key = _system_key("refund", request_id)
    with transaction.atomic():
        account = _locked_account(org)
        existing = ManagedCreditEntry.objects.filter(idempotency_key=key).first()
        if existing is not None:
            return _verified(existing, org, Kind.REFUND), False
        spend = ManagedCreditEntry.objects.filter(
            organization=org, idempotency_key=_system_key("spend", request_id)
        ).first()
        spent = -spend.amount_micros if spend is not None else 0
        amount = spent if micros is None else _positive(micros)
        if amount <= 0 or amount > spent:
            raise RefundExceedsSpend("The refund exceeds the request's settled spend.")
        now = timezone.now()
        return _write(
            org,
            Kind.REFUND,
            amount,
            key,
            spend_alloc=_allocation_of(spend),
            repaid_by=_debt_repayers(org, spend, amount, _buckets(account)),
            related_entry=spend,
            request_id=request_id,
            note=reason,
            created_by=actor,
            product="gateway",
            dimension=MANAGED_SPEND,
            unit="usd_micros",
            quantity=Decimal(amount),
            period=_period(now),
            workspace_id=spend.workspace_id,
            agent_id=spend.agent_id,
            agent_run_id=spend.agent_run_id,
            api_key_id=spend.api_key_id,
            occurred_at=now,
        )


def adjust(
    org: Organization,
    signed_micros: int,
    *,
    idempotency_key: str,
    reason: str,
    actor: User | None,
    bucket: Bucket = Bucket.PROMO,
) -> tuple[ManagedCreditEntry, bool]:
    """Add (positive) or remove (negative) credit by hand.

    A positive adjustment fills ``bucket`` after repaying debt; a negative one
    draws the wallet like a debit.
    """
    if not _is_micros(signed_micros) or signed_micros == 0:
        raise ManagedCreditError("an adjustment is a non-zero integer of micros")
    if Bucket(bucket) == Bucket.DEBT:
        raise ManagedCreditError("an adjustment cannot fill the debt bucket")
    key = _caller_key(Kind.ADJUSTMENT, org, idempotency_key)
    return _write(
        org,
        Kind.ADJUSTMENT,
        signed_micros,
        key,
        bucket=Bucket(bucket),
        note=reason,
        created_by=actor,
    )


def expire(
    org: Organization, micros: int, *, idempotency_key: str
) -> tuple[ManagedCreditEntry, bool]:
    """Remove ``micros`` of promotional credit; a negative ``expiry`` entry."""
    key = _caller_key(Kind.EXPIRY, org, idempotency_key)
    return _write(org, Kind.EXPIRY, -_positive(micros), key, bucket=Bucket.PROMO)


def debit_usage(
    org: Organization,
    *,
    dimension: str,
    product: str,
    unit: str,
    quantity: Decimal,
    amount_micros: int,
    period: str,
    seq: int,
    funding_source: str,
    occurred_at: datetime,
    price_catalog_version: str,
    workspace_id: str | None = None,
    agent_id: str = "",
    agent_run_id: str = "",
    api_key_id: str = "",
    purpose: str = "",
    source_kind: str = "",
    source_ref: str = "",
    late_of_period: str = "",
) -> tuple[list[ManagedCreditEntry], bool]:
    """Write one rating commit's ``usage_debit`` rows (the rater's primitive).

    ``funding_source`` ``wallet`` draws the wallet (one row per bucket touched,
    keyed ``rate:<org>:<dim>:<period>:<seq>:<bucket>``), adds to
    ``platform_spend_micros`` and bumps the version; ``invoice``, ``marketplace``
    or ``free`` write one unfunded row keyed ``...:<seq>:<funding>`` that
    changes no balance. A replay of the same ``seq`` returns the rows written
    first. Phase 2a writes these only in tests (I11's P): platform rating is 2b.
    """
    if funding_source not in USAGE_FUNDINGS:
        raise ManagedCreditError(
            f"funding_source must be one of {sorted(USAGE_FUNDINGS)}"
        )
    amount = _positive(amount_micros)
    quantity = Decimal(quantity) if quantity is not None else None
    conflict = (
        partial(_model_call_conflict, source_ref, Kind.USAGE_DEBIT)
        if source_kind == "model_call"
        else None
    )
    if conflict is not None and len(source_ref or "") == 0:
        raise ManagedCreditError("a model_call debit needs its request id")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise ManagedCreditError("seq is a non-negative integer")
    base = _bounded(f"rate:{org.pk}:{dimension}:{period}:{seq}")
    common = {
        "product": product,
        "dimension": dimension,
        "unit": unit,
        "period": period,
        "late_of_period": late_of_period,
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "agent_run_id": agent_run_id,
        "api_key_id": api_key_id,
        "purpose": purpose,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "price_catalog_version": price_catalog_version,
        "occurred_at": occurred_at,
    }
    with transaction.atomic():
        account = _locked_account(org)
        done = list(
            ManagedCreditEntry.objects.filter(
                organization=org,
                kind=Kind.USAGE_DEBIT,
                idempotency_key__startswith=base + ":",
            ).order_by("idempotency_key")
        )
        if done:
            return done, False
        if funding_source != WALLET_FUNDING:
            entry, _ = _write(
                org,
                Kind.USAGE_DEBIT,
                -amount,
                _bounded(f"{base}:{funding_source}"),
                usage_funding=funding_source,
                conflict=conflict,
                quantity=quantity,
                **common,
            )
            return [entry], True
        split = funding.allocate_debit(_buckets(account), amount)
        rows = []
        for bucket in (Bucket.PROMO, Bucket.COMMIT, Bucket.PURCHASED, Bucket.DEBT):
            part = -getattr(split, bucket.value)
            if part <= 0:
                continue
            share = (quantity * part / amount) if quantity is not None else None
            if rows and source_kind == "model_call":
                # One meter per model call: only the first bucket's row holds it.
                common = {**common, "source_kind": "model_call_part"}
            entry, _ = _write(
                org,
                Kind.USAGE_DEBIT,
                -part,
                _bounded(f"{base}:{bucket.value}"),
                usage_funding=WALLET_FUNDING,
                conflict=conflict,
                quantity=share.quantize(Decimal("0.000001"))
                if share is not None
                else None,
                **common,
            )
            rows.append(entry)
        return rows, True


# --- reads ------------------------------------------------------------------


def get_account(org: Organization) -> ManagedCreditAccount | None:
    return ManagedCreditAccount.objects.filter(organization=org).first()


def balance(org: Organization) -> dict[str, int]:
    """The gateway-side totals R's callers read (granted, settled spend, their gap)."""
    account = get_account(org)
    granted = account.granted_total_micros if account else 0
    spent = account.settled_spend_micros if account else 0
    return {
        "granted_total_micros": granted,
        "settled_spend_micros": spent,
        "ledger_available_micros": granted - spent,
    }


def ledger_balance(org: Organization) -> LedgerBalance:
    """The org's balance from Postgres, never the gateway or Stripe."""
    account = get_account(org)
    if account is None:
        return LedgerBalance(str(org.pk), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "")
    buckets = _buckets(account)
    return LedgerBalance(
        org_id=str(org.pk),
        credit_total_micros=account.granted_total_micros,
        settled_spend_micros=account.settled_spend_micros,
        platform_spend_micros=account.platform_spend_micros,
        balance_micros=buckets.balance,
        debt_micros=buckets.debt,
        promo_micros=buckets.promo,
        commit_micros=buckets.commit,
        purchased_micros=buckets.purchased,
        version=account.version,
        gateway_acked_version=account.gateway_acked_version,
        gateway_push_state=account.gateway_push_state,
    )


_MARKETPLACE_CHANNELS = {
    "gcp_marketplace": "gcp_marketplace",
    "aws_marketplace": "aws_marketplace",
}
_INVOICED_METHODS = frozenset({"invoice_30", "invoice_60", "wire"})


def billing_profile_for_account(
    account: ManagedCreditAccount | None, org_id=None
) -> dict:
    """Funding, channel, path and cutover of an org (PLAN 1.1).

    A value set on the account wins; otherwise it is derived from the org's
    subscription: marketplace methods bill through the marketplace, invoice and
    wire through Stripe invoices, a paid card plan is self-serve postpaid, and
    an org with no paid plan is self-serve prepaid-only (decision 10).
    """
    from ee.usage.models.usage import OrganizationSubscription

    org_id = account.organization_id if account is not None else org_id
    sub = (
        OrganizationSubscription.objects.filter(organization_id=org_id, deleted=False)
        .values("plan", "billing_method")
        .first()
        or {}
    )
    method = sub.get("billing_method") or ""
    plan = sub.get("plan") or "free"
    if method in _MARKETPLACE_CHANNELS:
        channel = _MARKETPLACE_CHANNELS[method]
    elif method in _INVOICED_METHODS:
        channel = "stripe_invoiced"
    elif plan != "free":
        channel = "stripe_selfserve"
    else:
        channel = "none"
    derived_funding = "prepaid_only" if channel == "none" else "prepaid_then_postpaid"
    return {
        "billing_channel": (account.billing_channel if account else "") or channel,
        "funding": (account.funding if account else "") or derived_funding,
        "billing_path": (account.billing_path if account else "") or "legacy",
        "cutover_at": account.cutover_at if account else None,
    }


def ledger_entries(
    org: Organization,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> QuerySet[ManagedCreditEntry]:
    """The org's entries with ``start <= occurred_at < end``, oldest first."""
    entries = ManagedCreditEntry.objects.filter(organization=org)
    if start is not None:
        entries = entries.filter(occurred_at__gte=start)
    if end is not None:
        entries = entries.filter(occurred_at__lt=end)
    return entries.order_by("occurred_at", "id")


# --- internals: the one write path -------------------------------------------


def _write(
    org: Organization,
    kind: str,
    amount_micros: int,
    key: str,
    *,
    bucket: Bucket | None = None,
    spend_alloc: Allocation | None = None,
    repaid_by: tuple[tuple[Bucket, int], ...] = (),
    usage_funding: str | None = None,
    conflict=None,
    **fields: Any,
) -> tuple[ManagedCreditEntry, bool]:
    with transaction.atomic():
        account = _locked_account(org)
        before = _buckets(account)
        try:
            alloc = _allocate(
                kind,
                amount_micros,
                before,
                bucket,
                spend_alloc,
                usage_funding,
                repaid_by,
            )
            after = funding.apply(before, alloc) if alloc is not None else before
        except funding.FundingError as exc:
            # A refused write, like every other ledger refusal: nothing is written.
            raise ManagedCreditError(f"entry {key!r} cannot be funded: {exc}") from exc
        entry = ManagedCreditEntry(
            organization=org,
            kind=kind,
            amount_micros=amount_micros,
            idempotency_key=key,
            funding=_funding_label(kind, alloc, bucket, usage_funding),
            **(alloc.as_columns() if alloc is not None else {}),
            **fields,
        )
        # INSERT ... ON CONFLICT DO NOTHING; the row is ours only if our id landed.
        ManagedCreditEntry.objects.bulk_create([entry], ignore_conflicts=True)
        if not ManagedCreditEntry.objects.filter(pk=entry.pk).exists():
            existing = ManagedCreditEntry.objects.filter(idempotency_key=key).first()
            if existing is None and conflict is not None:
                existing = conflict()
            if existing is None:
                raise ManagedCreditError(
                    f"entry {key!r} conflicts with another unique entry"
                )
            return _verified(existing, org, kind), False

        changed = ["entry_seq"]
        account.entry_seq += 1
        if alloc is not None:
            account.promo_balance_micros = after.promo
            account.commit_balance_micros = after.commit
            account.purchased_balance_micros = after.purchased
            account.debt_micros = after.debt
            changed += [
                "promo_balance_micros",
                "commit_balance_micros",
                "purchased_balance_micros",
                "debt_micros",
            ]
        push = False

        @cache
        def disputed() -> bool:
            # Read once, under the account lock, after this entry's insert.
            return money_freeze.dispute_open(org.pk)

        def frozen(buckets: Buckets) -> bool:
            return buckets.debt > 0 or disputed()

        if kind == Kind.SPEND:
            settled_before = account.settled_spend_micros
            account.settled_spend_micros -= amount_micros
            changed.append("settled_spend_micros")
            # While frozen the pushed value is capped at S, so a settled spend
            # can move it; an overdraft never does (F - P is already below S).
            was, now = frozen(before), frozen(after)
            if (was or now) and granted_value(
                account.granted_total_micros,
                account.platform_spend_micros,
                settled_before,
                was,
            ) != granted_value(
                account.granted_total_micros,
                account.platform_spend_micros,
                account.settled_spend_micros,
                now,
            ):
                account.version += 1
                changed.append("version")
                push = True
        elif kind == Kind.USAGE_DEBIT:
            if usage_funding == WALLET_FUNDING:
                account.platform_spend_micros -= amount_micros
                account.version += 1
                changed += ["platform_spend_micros", "version"]
                push = True
        else:
            account.version += 1
            account.granted_total_micros = _credit_total(org)
            changed += ["version", "granted_total_micros"]
            push = True
            if kind == Kind.TOPUP:
                account.purchased_total_micros += amount_micros
                changed.append("purchased_total_micros")
            elif kind in (Kind.GRANT, Kind.ADJUSTMENT) and amount_micros > 0:
                if bucket == Bucket.COMMIT:
                    account.commit_total_micros += amount_micros
                    changed.append("commit_total_micros")
                elif bucket == Bucket.PROMO:
                    account.promo_total_micros += amount_micros
                    changed.append("promo_total_micros")
        account.save(update_fields=[*changed, "updated_at"])
        profile = billing_profile_for_account(account)
        mode = (
            "lp"
            if profile["funding"] == "prepaid_only"
            and profile["billing_path"] == "ledger"
            else ""
        )
        transaction.on_commit(
            partial(
                _after_commit,
                str(org.pk),
                push,
                money_freeze.spendable(after.balance, frozen(after)),
                account.entry_seq,
                mode,
            ),
            robust=True,
        )
        threshold = account.low_balance_threshold_micros
        if threshold is not None and before.balance >= threshold > after.balance:
            from ee.usage.services.low_balance_alerts import send_low_balance_alert

            # Publish the balance and request the gateway grant first: commit
            # callbacks run in order, and synchronous mail may block.
            # The account lock and unique ledger insert admit one callback per
            # crossing. Commit-only delivery excludes rollbacks and replays.
            transaction.on_commit(
                partial(send_low_balance_alert, org, after.balance, threshold),
                robust=True,
            )
    return entry, True


def _allocate(
    kind, amount, buckets, bucket, spend_alloc, usage_funding, repaid_by=()
) -> Allocation | None:
    if kind == Kind.SPEND:
        return funding.allocate_debit(buckets, -amount) if amount < 0 else Allocation()
    if kind == Kind.USAGE_DEBIT:
        if usage_funding != WALLET_FUNDING:
            return None
        return funding.allocate_debit(buckets, -amount)
    if kind in (Kind.TOPUP, Kind.DISPUTE_RELEASE):
        return funding.allocate_credit(buckets, amount, Bucket.PURCHASED)
    if kind == Kind.GRANT:
        return funding.allocate_credit(buckets, amount, bucket or Bucket.PROMO)
    if kind == Kind.REFUND:
        return funding.allocate_spend_refund(buckets, amount, spend_alloc, repaid_by)
    if kind == Kind.ADJUSTMENT:
        return funding.allocate_signed(buckets, amount, bucket or Bucket.PROMO)
    if kind == Kind.EXPIRY:
        return funding.allocate_reversal(buckets, -amount, Bucket.PROMO)
    if kind in (Kind.TOPUP_REVERSAL, Kind.DISPUTE_HOLD):
        return funding.allocate_reversal(buckets, -amount, Bucket.PURCHASED)
    raise ManagedCreditError(f"unknown entry kind {kind!r}")


def _funding_label(kind, alloc, bucket, usage_funding) -> str:
    if kind == Kind.USAGE_DEBIT and usage_funding != WALLET_FUNDING:
        return usage_funding or ""
    if alloc is None:
        return ""
    if alloc.total > 0 and kind != Kind.REFUND:
        return (bucket or Bucket.PURCHASED).value
    single = alloc.single_bucket
    return single.value if single is not None else ""


def _after_commit(
    org_id: str, push: bool, balance_micros: int, seq: int, mode: str
) -> None:
    from ee.usage.services import balance_cache

    balance_cache.write(org_id, balance_micros=balance_micros, entry_seq=seq, mode=mode)
    balance_cache.balance_changed.send_robust(
        sender=None, org_id=org_id, balance_micros=balance_micros, entry_seq=seq
    )
    if push:
        request_push(org_id)


def _buckets(account: ManagedCreditAccount) -> Buckets:
    return Buckets(
        promo=account.promo_balance_micros,
        commit=account.commit_balance_micros,
        purchased=account.purchased_balance_micros,
        debt=account.debt_micros,
    )


def _allocation_of(entry: ManagedCreditEntry | None) -> Allocation | None:
    if entry is None or entry.funded_promo_micros is None:
        return None
    return Allocation(
        promo=entry.funded_promo_micros,
        commit=entry.funded_commit_micros,
        purchased=entry.funded_purchased_micros,
        debt=entry.funded_debt_micros,
    )


# Credit kinds that fill a wallet bucket, so may have repaid debt first. Their
# ``funding`` column names that bucket (``_funding_label``).
_DEBT_REPAYING_KINDS = (Kind.TOPUP, Kind.GRANT, Kind.ADJUSTMENT, Kind.DISPUTE_RELEASE)
_WALLET_BUCKET_NAMES = tuple(b.value for b in funding.WALLET_ORDER)


def _debt_repayers(
    org: Organization, spend: ManagedCreditEntry | None, amount: int, buckets: Buckets
) -> tuple:
    """``repaid_by`` for a refund of ``amount`` of ``spend``, as ``(bucket, micros)``.

    A refund returns the repaid share of the spend's debt to the
    bucket of the credit that repaid it. Debt is one balance, so this is a
    rule, applied under the account lock ``refund`` holds (``buckets`` is the
    locked account's wallet):

    - ``funding.debt_lot`` replays this org's debt as first-in-first-out lots
      over the stored ``funded_debt_micros`` in write order (``created_at``,
      then ``id``), with the debt usage/0032 seeded from entries without
      funding columns as the oldest lot, and names who repaid the spend's lot;
    - ``funding.refund_repaid_by`` takes the repayments the refund gives back:
      its repayment of outstanding debt cancels the spend's unpaid part, then
      uses the oldest repayments; the next ones go home;
    - each bucket gives back at most what its credits repaid, minus what
      earlier refunds already returned to it as repaid shares. With one
      writer clock this never binds (a lot is closed once); it keeps clock
      skew between writers, which can reorder ``created_at``, from returning
      one grant's repayment twice. ``allocate_spend_refund`` sends anything
      not covered to purchased.
    """
    spend_alloc = _allocation_of(spend)
    if spend_alloc is None or spend_alloc.debt >= 0:
        return ()
    if funding.refund_debt_split(buckets, amount, spend_alloc)[1] <= 0:
        return ()  # nothing a credit repaid comes back
    rows = ManagedCreditEntry.objects.filter(
        organization=org, funded_debt_micros__isnull=False
    ).exclude(funded_debt_micros=0)
    # debt now = opening debt - every funded debt change, so:
    opening = buckets.debt + (
        rows.aggregate(total=Sum("funded_debt_micros"))["total"] or 0
    )
    refunded = set(
        rows.filter(kind=Kind.REFUND, funded_debt_micros__gt=0).values_list(
            "related_entry_id", flat=True
        )
    )
    unpaid, repayments = funding.debt_lot(_debt_rows(rows), opening, spend.pk, refunded)
    return funding.refund_repaid_by(
        buckets, amount, spend_alloc, unpaid, repayments, caps=_repaid_pool(org)
    )


def _debt_rows(rows: QuerySet):
    """``funding.debt_lot``'s rows: each debt change in write order."""
    ordered = rows.order_by("created_at", "id").values_list(
        "pk", "kind", "funding", "funded_debt_micros", "related_entry_id"
    )
    for pk, kind, label, debt, related in ordered.iterator():
        if kind == Kind.REFUND:
            yield pk, debt, None, related
        else:
            bucket = (
                Bucket(label) if label in _WALLET_BUCKET_NAMES else Bucket.PURCHASED
            )
            yield pk, debt, bucket, None


def _repaid_pool(org: Organization) -> dict[Bucket, int]:
    """Per bucket: what its credits repaid of debt, net of repaid shares refunds returned."""
    credits = ManagedCreditEntry.objects.filter(
        organization=org,
        kind__in=_DEBT_REPAYING_KINDS,
        amount_micros__gt=0,
        funded_debt_micros__gt=0,
        funding__in=_WALLET_BUCKET_NAMES,
    )
    pool = dict.fromkeys(funding.WALLET_ORDER, 0)
    for row in (
        credits.order_by().values("funding").annotate(total=Sum("funded_debt_micros"))
    ):
        pool[Bucket(row["funding"])] += row["total"]
    for bucket, micros in _returned_repaid_shares(org):
        pool[bucket] -= micros
    return pool


def _returned_repaid_shares(org: Organization):
    """``(bucket, micros)`` each earlier refund returned as a repaid debt share.

    Not stored, but exact: ``allocate_spend_refund`` repays outstanding debt
    (the refund's ``funded_debt_micros``), then hands back the repaid share
    ``min(spend debt, amount) - that``, then gives the rest to purchased,
    commit and promo, each at most what the spend drew. So the repaid share
    in each bucket is what the refund put there minus that last give.
    """
    rows = ManagedCreditEntry.objects.filter(
        organization=org,
        kind=Kind.REFUND,
        funded_debt_micros__isnull=False,
        related_entry__funded_debt_micros__lt=0,
    ).values_list(
        "amount_micros",
        "funded_promo_micros",
        "funded_commit_micros",
        "funded_purchased_micros",
        "funded_debt_micros",
        "related_entry__funded_promo_micros",
        "related_entry__funded_commit_micros",
        "related_entry__funded_purchased_micros",
        "related_entry__funded_debt_micros",
    )
    for (
        amount,
        promo,
        commit,
        purchased,
        repay,
        s_promo,
        s_commit,
        s_purchased,
        s_debt,
    ) in rows:
        repaid = max(min(-s_debt, amount) - repay, 0)
        rest = amount - repay - repaid
        given = {}
        for bucket, drew in (
            (Bucket.PURCHASED, -s_purchased),
            (Bucket.COMMIT, -s_commit),
            (Bucket.PROMO, -s_promo),
        ):
            given[bucket] = max(min(drew, rest), 0)
            rest -= given[bucket]
        for bucket, back in (
            (Bucket.PROMO, promo),
            (Bucket.COMMIT, commit),
            (Bucket.PURCHASED, purchased),
        ):
            if back - given[bucket] > 0:
                yield bucket, back - given[bucket]


def _locked_account(org: Organization) -> ManagedCreditAccount:
    ManagedCreditAccount.objects.get_or_create(organization=org)
    return ManagedCreditAccount.objects.select_for_update().get(organization=org)


def _credit_total(org: Organization) -> int:
    total = ManagedCreditEntry.objects.filter(
        organization=org, kind__in=CREDIT_KINDS
    ).aggregate(total=Sum("amount_micros"))["total"]
    return total or 0


def _verified(
    entry: ManagedCreditEntry, org: Organization, kind: str
) -> ManagedCreditEntry:
    if entry.organization_id != org.pk or entry.kind != kind:
        raise IdempotencyConflict(
            "The reference already belongs to another ledger entry."
        )
    return entry


def _existing(key: str, org: Organization, kind: str) -> ManagedCreditEntry | None:
    entry = ManagedCreditEntry.objects.filter(idempotency_key=key).first()
    return _verified(entry, org, kind) if entry is not None else None


# --- internals: top-ups -------------------------------------------------------


def _mode(mode) -> StripeMode:
    try:
        return StripeMode(mode)
    except ValueError as exc:
        raise ManagedCreditError(f"mode must be test or live, got {mode!r}") from exc


def _payment_enums(
    payment: TopupPayment,
) -> tuple[TopupSource, StripeMode, TopupPurpose]:
    if not isinstance(payment, TopupPayment):
        raise ManagedCreditError("payment must be a TopupPayment")
    try:
        source = TopupSource(payment.source)
        purpose = TopupPurpose(payment.purpose)
    except ValueError as exc:
        raise ManagedCreditError(str(exc)) from exc
    if not isinstance(payment.object_id, str) or not payment.object_id:
        raise ManagedCreditError("a top-up needs its Stripe object id")
    return source, _mode(payment.mode), purpose


def _topup_payment_intent(payment: TopupPayment, source: TopupSource) -> str:
    """The id of the PaymentIntent that paid a top-up; every source has one.

    A paid payment-mode session and a paid top-up invoice always have a
    PaymentIntent. Without it the one-credit-per-PaymentIntent guard cannot
    see that a later ``payment_intent.succeeded`` is the same money, and a
    value that is not a PaymentIntent id (a blank, whitespace, the session's or
    the invoice's own id) blinds that guard the same way. So does
    the PaymentIntent's client secret, ``<pi id>_secret_<suffix>``, which is
    refused without being echoed.
    """
    pi_id = payment.payment_intent_id
    if source == TopupSource.PAYMENT_INTENT:
        pi_id = pi_id or payment.object_id
        if pi_id != payment.object_id:
            raise ManagedCreditError("a PaymentIntent top-up names its own id")
    if not pi_id:
        raise ManagedCreditError(
            f"a {source.value} top-up needs its payment_intent_id "
            "(the PaymentIntent that paid it)"
        )
    # Before the charset check, whose message echoes the value: a client
    # secret with a stray character must not reach it.
    _refuse_client_secret(pi_id)
    if (
        not isinstance(pi_id, str)
        or len(pi_id) > 255
        or not _PAYMENT_INTENT_ID.fullmatch(pi_id)
    ):
        raise ManagedCreditError(
            f"payment_intent_id must be a PaymentIntent id (pi_...), got {str(pi_id)[:80]!r}"
        )
    return pi_id


def _refuse_client_secret(value: Any) -> None:
    """Refuse a PaymentIntent's client secret named as its id.

    Never echoed: Stripe says a client secret is not to be stored or logged.
    """
    if isinstance(value, str) and _CLIENT_SECRET.search(value):
        raise ManagedCreditError(
            "payment_intent_id must be a PaymentIntent id, not its client secret "
            "(pi_..._secret_...)"
        )


def _require_mode_allowed(mode: StripeMode) -> None:
    if mode == StripeMode.LIVE and not getattr(
        settings, "BILLING_LEDGER_ACCEPT_LIVE", False
    ):
        raise LiveModeRefused(
            "a live Stripe object reached a ledger that does not accept live "
            "money (BILLING_LEDGER_ACCEPT_LIVE is off)"
        )


def _bounded_ref(value: str, name: str) -> str:
    value = str(value or "")
    if len(value) > 255:
        raise ManagedCreditError(f"{name} is longer than 255")
    return value


def _same_topup(entry, org, amount, mode: StripeMode, purpose: TopupPurpose) -> None:
    if entry.organization_id != org.pk or entry.kind != Kind.TOPUP:
        raise TopupMismatch(f"{entry.stripe_ref!r} was credited to another org or kind")
    if entry.amount_micros != amount:
        raise TopupMismatch(
            f"{entry.stripe_ref!r} was credited {entry.amount_micros} micros, not {amount}"
        )
    if entry.stripe_mode and entry.stripe_mode != mode.value:
        raise TopupMismatch(
            f"{entry.stripe_ref!r} was credited in {entry.stripe_mode} mode"
        )
    if entry.category and entry.category != purpose.value:
        raise TopupMismatch(f"{entry.stripe_ref!r} was credited as {entry.category}")


def _record_payment_intent(entry: ManagedCreditEntry, pi_id: str) -> ManagedCreditEntry:
    """Make a replayed top-up carry the PaymentIntent the replay names.

    Called under the account lock with the existing entry of a replay. A top-up
    credited without its PaymentIntent (R's pre-2a ``apply_topup``) gets it
    here, so that PaymentIntent's own event then finds this entry instead of
    crediting the money again. A replay naming another
    PaymentIntent than the recorded one is a ``TopupMismatch``: that one would
    stay free to credit the same money. So is a PaymentIntent another top-up
    already holds (the money was credited twice before the link was known):
    the caller alerts, and nothing is written.
    """
    recorded = entry.stripe_payment_intent_id
    if recorded == pi_id:
        return entry
    if recorded:
        raise TopupMismatch(
            f"{entry.stripe_ref!r} was credited for payment intent {recorded!r}, not {pi_id!r}"
        )
    try:
        with transaction.atomic():
            filled = ManagedCreditEntry.objects.filter(pk=entry.pk).fill_payment_intent(
                pi_id
            )
    except IntegrityError as exc:
        raise TopupMismatch(
            f"payment intent {pi_id!r} already credited another top-up; "
            f"{entry.stripe_ref!r} was credited without it"
        ) from exc
    if filled != 1:
        raise ManagedCreditError(
            f"top-up {entry.pk} did not take payment intent {pi_id!r}"
        )
    entry.stripe_payment_intent_id = pi_id
    logger.info(
        "managed_credits_topup_payment_intent_recorded",
        org_id=str(entry.organization_id),
        entry_id=str(entry.pk),
        stripe_ref=entry.stripe_ref,
        payment_intent_id=pi_id,
    )
    return entry


def _topup_by_payment_intent(pi_id: str) -> ManagedCreditEntry | None:
    if not pi_id:
        return None
    return ManagedCreditEntry.objects.filter(
        kind=Kind.TOPUP, stripe_payment_intent_id=pi_id
    ).first()


def _credited_topup(org: Organization, payment_intent_id: str) -> ManagedCreditEntry:
    if not isinstance(payment_intent_id, str) or not payment_intent_id:
        raise ManagedCreditError("payment_intent_id is required")
    # Refused before the lookup, whose miss message echoes the id.
    _refuse_client_secret(payment_intent_id)
    topup = (
        ManagedCreditEntry.objects.filter(
            organization=org,
            kind=Kind.TOPUP,
            stripe_payment_intent_id=payment_intent_id,
        ).first()
        or ManagedCreditEntry.objects.filter(
            organization=org, kind=Kind.TOPUP, stripe_ref=payment_intent_id
        ).first()
    )
    if topup is None:
        raise ManagedCreditError(
            f"no credited top-up of this org for payment intent {payment_intent_id!r}"
        )
    return topup


def _event_row(event_id: str, mode: StripeMode, object_id: str, org: Organization):
    from ee.usage.models.billing_ledger import StripeEventLog

    row, _ = StripeEventLog.objects.select_for_update().get_or_create(
        event_id=event_id,
        defaults={
            "event_type": "ledger.fulfil_topup",
            "mode": mode.value,
            "object_id": object_id,
            "organization": org,
        },
    )
    if row.organization_id not in (None, org.pk):
        raise TopupMismatch(f"event {event_id} belongs to another org")
    return row


def _link_event(row, entry: ManagedCreditEntry, org: Organization) -> None:
    from ee.usage.models.billing_ledger import StripeEventLog

    StripeEventLog.objects.filter(pk=row.pk).update(
        entry=entry,
        organization=org,
        status="processed",
        attempts=row.attempts + 1,
        processed_at=timezone.now(),
    )


# --- internals: spend --------------------------------------------------------


def _model_call_conflict(
    request_id: str, writing_kind: str = Kind.SPEND
) -> ManagedCreditEntry | None:
    other = ManagedCreditEntry.objects.filter(
        source_kind="model_call", source_ref=request_id
    ).first()
    if other is not None and other.kind != writing_kind:
        raise DoubleMeteredCall(
            f"model call {request_id!r} already has a {other.kind} entry "
            f"{other.idempotency_key!r} (one payer per call)"
        )
    return other


def _workspace_in_org(org: Organization, workspace_id) -> uuid.UUID | None:
    if not workspace_id:
        return None
    from accounts.models.workspace import Workspace

    try:
        ws = uuid.UUID(str(workspace_id))
    except ValueError:
        logger.warning("managed_spend_workspace_invalid", org_id=str(org.pk))
        return None
    if not Workspace.no_workspace_objects.filter(pk=ws, organization=org).exists():
        logger.warning("managed_spend_workspace_not_in_org", org_id=str(org.pk))
        return None
    return ws


def _period(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y-%m")


def _utc_text(instant: datetime) -> str:
    instant = instant.astimezone(UTC)
    text = instant.strftime("%Y-%m-%dT%H:%M:%S")
    if instant.microsecond:
        text += f".{instant.microsecond:06d}"
    return text + "Z"


def spend_event_id(request_id: str) -> uuid.UUID:
    """The managed-spend display event id (usage-event-v1 section 3, kept from I)."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"agentcc:spend:{request_id}")


def spend_event_payload(entry: ManagedCreditEntry) -> dict[str, Any]:
    """The usage-event-v1 event of a SPEND entry, in its one canonical form."""
    billed = -entry.amount_micros
    return {
        "v": "1",
        "event_id": str(spend_event_id(entry.request_id)),
        "org_id": str(entry.organization_id),
        "workspace_id": str(entry.workspace_id) if entry.workspace_id else None,
        "product": "gateway",
        "dimension": MANAGED_SPEND,
        "event_type": SPEND_EVENT_TYPE,
        "quantity": str(billed),
        "unit": "usd_micros",
        "occurred_at": _utc_text(entry.occurred_at),
        "source": {"system": "fa_gateway_settlement", "ref": entry.request_id},
        "attribution": {
            "agent_id": entry.agent_id or None,
            "agent_run_id": entry.agent_run_id or None,
            "api_key_id": entry.api_key_id or None,
            "purpose": None,
        },
        "attributes": {
            "credential_type": "managed",
            "request_id": entry.request_id,
            **({"model": entry.model[:255]} if entry.model else {}),
        },
    }


def _ensure_spend_event(entry: ManagedCreditEntry) -> None:
    """Write (or heal) the SPEND entry's display event, rated to the entry."""
    from ee.usage.models.billing_ledger import UsageEventRecord
    from ee.usage.models.usage import UsageEventLog

    payload = spend_event_payload(entry)
    event_uuid = uuid.UUID(payload["event_id"])
    billed = -entry.amount_micros
    log = UsageEventLog.objects.filter(event_id=event_uuid).first()
    if log is None:
        try:
            with transaction.atomic():
                log = UsageEventLog.objects.create(
                    event_id=event_uuid,
                    organization_id=entry.organization_id,
                    workspace_id=entry.workspace_id,
                    event_type=SPEND_EVENT_TYPE,
                    dimension=MANAGED_SPEND,
                    amount_raw=Decimal(billed),
                    amount_display=(Decimal(billed) / Decimal(1_000_000)).quantize(
                        Decimal("0.000001")
                    ),
                    period=_period(entry.occurred_at),
                    timestamp=entry.occurred_at,
                    properties={
                        **payload["attributes"],
                        "source": "fa_gateway_settlement",
                        "source_id": entry.request_id,
                    },
                )
        except IntegrityError:
            log = UsageEventLog.objects.get(event_id=event_uuid)
    if log.organization_id != entry.organization_id:
        logger.error("managed_spend_event_org_mismatch", request_id=entry.request_id)
        return
    sha = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    record = UsageEventRecord.objects.filter(event=log).first()
    if record is None:
        try:
            with transaction.atomic():
                UsageEventRecord.objects.create(
                    event=log,
                    organization_id=entry.organization_id,
                    event_uuid=event_uuid,
                    product="gateway",
                    unit="usd_micros",
                    source_system="fa_gateway_settlement",
                    source_ref=entry.request_id[:255],
                    agent_id=entry.agent_id,
                    agent_run_id=entry.agent_run_id,
                    api_key_id=entry.api_key_id,
                    request_id=entry.request_id[:255],
                    payload_sha256=sha,
                    schema_version="1",
                    occurred_at=entry.occurred_at,
                    period=_period(entry.occurred_at),
                    booked_period=_period(entry.occurred_at),
                    rated_entry=entry,
                    rated_at=timezone.now(),
                )
            return
        except IntegrityError:
            record = UsageEventRecord.objects.get(event=log)
    UsageEventRecord.objects.filter(
        pk=record.pk, rated_entry__isnull=True, not_billable_reason=""
    ).update(
        rated_entry=entry,
        booked_period=_period(entry.occurred_at),
        rated_at=timezone.now(),
    )


# --- internals: keys and validation -------------------------------------------


def _system_key(prefix: str, ref: str) -> str:
    if not isinstance(ref, str) or not ref:
        raise ManagedCreditError(f"a {prefix} entry needs its reference id")
    return _bounded(f"{prefix}:{ref}")


def _caller_key(kind: str, org: Organization, idempotency_key: str) -> str:
    # The key column is unique across orgs; scoping it keeps one org's key from
    # ever resolving to another org's entry.
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ManagedCreditError("idempotency_key is required")
    return _bounded(f"{kind}:{org.pk}:{idempotency_key}")


def _bounded(key: str) -> str:
    if len(key) > _KEY_MAX_LENGTH:
        raise ManagedCreditError(f"idempotency key longer than {_KEY_MAX_LENGTH}")
    return key


def _is_micros(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive(micros: Any) -> int:
    if not _is_micros(micros) or micros <= 0:
        raise ManagedCreditError("amount must be a positive integer of micros")
    return micros


def _record_int(record: dict[str, Any], name: str) -> int | None:
    value = record.get(name)
    if value is None or value == "":
        return None
    if _is_micros(value):
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        number = int(value)
    else:
        raise InvalidSettlementRecord(
            f"{name} is not a non-negative integer: {value!r}"
        )
    if number < 0:
        raise InvalidSettlementRecord(f"{name} is negative: {value!r}")
    return number


def _spend_fields(record: dict[str, Any]) -> tuple[str, int, dict[str, Any]]:
    """Validate a spend record; return its org id, billed micros and entry fields."""
    if str(record.get("v")) != "1" or record.get("kind") != "spend":
        raise InvalidSettlementRecord(
            f"not a managed-settlement-v1 spend: v={record.get('v')!r} "
            f"kind={record.get('kind')!r}"
        )
    request_id, org_id = record.get("request_id"), record.get("org_id")
    billed = _record_int(record, "billed_micros")
    if (
        not isinstance(request_id, str)
        or not request_id
        or not org_id
        or billed is None
    ):
        raise InvalidSettlementRecord(
            "request_id, org_id and billed_micros are required"
        )
    if len(request_id) > 255:
        raise InvalidSettlementRecord("request_id is longer than 255")

    fields: dict[str, Any] = {"request_id": request_id}
    for name, column in _RECORD_INTS:
        fields[column] = _record_int(record, name)
    for name in _RECORD_STRS:
        value = str(record.get(name) or "")
        limit = ManagedCreditEntry._meta.get_field(name).max_length
        if len(value) > limit:
            raise InvalidSettlementRecord(f"{name} is longer than {limit}")
        fields[name] = value

    estimated = record.get("usage_estimated", "0")
    if estimated not in ("0", "1", 0, 1):  # False == 0 and True == 1 also match
        raise InvalidSettlementRecord(f"usage_estimated must be 0 or 1: {estimated!r}")
    fields["usage_estimated"] = estimated in ("1", 1)

    attempts = record.get("attempts") or []
    if isinstance(attempts, str):
        try:
            attempts = json.loads(attempts)
        except json.JSONDecodeError as exc:
            raise InvalidSettlementRecord("attempts is not JSON") from exc
    if not isinstance(attempts, list) or not all(isinstance(a, dict) for a in attempts):
        raise InvalidSettlementRecord("attempts must be a list of attempt objects")
    fields["attempts"] = attempts
    fields["attempt_count"] = len(attempts)

    ts_ms = _record_int(record, "ts_ms")
    fields["occurred_at"] = (
        datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
        + timedelta(milliseconds=ts_ms % 1000)
        if ts_ms is not None
        else timezone.now()
    )
    return str(org_id), billed, fields
