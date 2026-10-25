"""Managed credit ledger service: the only writer of ``ManagedCreditEntry``.

Every credit and debit is one idempotent entry, in integer micros. A write
locks the org's ``ManagedCreditAccount`` (``SELECT ... FOR UPDATE``), inserts
its entry with ``INSERT ... ON CONFLICT DO NOTHING``, and changes the account
only when that row was inserted: a credit-side kind bumps ``version`` and
recomputes ``granted_total_micros`` from the entries, a spend adds to
``settled_spend_micros``. A duplicate key returns ``(existing, False)``.

When a credit-side write commits, ``push_granted`` sends the gateway
``credits-granted-push-v1``. The gateway's Redis is only the
hot path; this ledger is the system of record.
"""

import json
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import httpx
import structlog
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet, Sum
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.user import User
from agentcc.services.gateway_admin import credits as gateway_credits
from agentcc.services.gateway_client import GatewayClientError
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry

logger = structlog.get_logger(__name__)

Kind = ManagedCreditEntry.Kind
CREDIT_KINDS = frozenset(
    {Kind.TOPUP, Kind.GRANT, Kind.REFUND, Kind.ADJUSTMENT, Kind.EXPIRY}
)
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
# generation_id (always the request id), hold_micros and agent_id have no column.
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
)


class ManagedCreditError(ValueError):
    """A ledger write was refused; nothing was written."""


class IdempotencyConflict(ManagedCreditError):
    """The key already belongs to an entry of another org or kind."""


class RefundExceedsSpend(ManagedCreditError):
    """A refund asked for more than the request's settled spend."""


class InvalidSettlementRecord(ManagedCreditError):
    """A managed-settlement-v1 record that cannot be settled."""


# --- writes -----------------------------------------------------------------


def grant(
    org: Organization,
    micros: int,
    *,
    idempotency_key: str,
    actor: User | None,
    note: str,
) -> tuple[ManagedCreditEntry, bool]:
    """Grant ``micros`` of credit. The key is scoped to the org and kind."""
    key = _caller_key(Kind.GRANT, org, idempotency_key)
    return _write(org, Kind.GRANT, _positive(micros), key, created_by=actor, note=note)


def apply_topup(
    org: Organization,
    micros: int,
    *,
    stripe_session_id: str,
    payment_fee_micros: int = 0,
    payment_fee_estimated: bool = False,
) -> tuple[ManagedCreditEntry, bool]:
    """Credit a paid Checkout session, keyed ``stripe:<session id>``.

    ``payment_fee_micros`` is Stripe's processing fee, recorded beside the
    credited amount and never subtracted from it.
    """
    if not _is_micros(payment_fee_micros) or payment_fee_micros < 0:
        raise ManagedCreditError("payment_fee_micros is a non-negative integer")
    return _write(
        org,
        Kind.TOPUP,
        _positive(micros),
        _system_key("stripe", stripe_session_id),
        stripe_ref=stripe_session_id,
        payment_fee_micros=payment_fee_micros,
        payment_fee_estimated=payment_fee_estimated,
    )


def apply_topup_pi(
    org: Organization, micros: int, *, payment_intent_id: str
) -> tuple[ManagedCreditEntry, bool]:
    """Credit an off-session PaymentIntent, keyed ``stripe_pi:<id>``."""
    return _write(
        org,
        Kind.TOPUP,
        _positive(micros),
        _system_key("stripe_pi", payment_intent_id),
        stripe_ref=payment_intent_id,
    )


def settle_spend(record: dict[str, Any]) -> tuple[ManagedCreditEntry, bool]:
    """Write one ``kind=spend`` managed-settlement-v1 record, keyed ``spend:<request_id>``.

    The record's values may be the outbox's strings or plain ints. A ``void``
    record is refused: it settles nothing.
    """
    org_id, billed, fields = _spend_fields(record)
    try:
        org = Organization.objects.get(pk=org_id)
    except (Organization.DoesNotExist, ValidationError) as exc:
        raise InvalidSettlementRecord(f"unknown org_id {org_id!r}") from exc
    key = _system_key("spend", fields["request_id"])
    return _write(org, Kind.SPEND, -billed, key, **fields)


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
    request is refunded once: a second call returns the first refund.
    """
    key = _system_key("refund", request_id)
    with transaction.atomic():
        _locked_account(org)
        existing = ManagedCreditEntry.objects.filter(idempotency_key=key).first()
        if existing is not None:
            return _verified(existing, org, Kind.REFUND), False
        spend = ManagedCreditEntry.objects.filter(
            organization=org, idempotency_key=_system_key("spend", request_id)
        ).first()
        spent = -spend.amount_micros if spend is not None else 0
        amount = spent if micros is None else _positive(micros)
        if amount <= 0 or amount > spent:
            raise RefundExceedsSpend(
                f"refund of {amount} micros exceeds the {spent} micros "
                f"settled for request {request_id!r}"
            )
        return _write(
            org,
            Kind.REFUND,
            amount,
            key,
            request_id=request_id,
            note=reason,
            created_by=actor,
        )


def adjust(
    org: Organization,
    signed_micros: int,
    *,
    idempotency_key: str,
    reason: str,
    actor: User | None,
) -> tuple[ManagedCreditEntry, bool]:
    """Add (positive) or remove (negative) credit by hand."""
    if not _is_micros(signed_micros) or signed_micros == 0:
        raise ManagedCreditError("an adjustment is a non-zero integer of micros")
    key = _caller_key(Kind.ADJUSTMENT, org, idempotency_key)
    return _write(
        org, Kind.ADJUSTMENT, signed_micros, key, note=reason, created_by=actor
    )


def expire(
    org: Organization, micros: int, *, idempotency_key: str
) -> tuple[ManagedCreditEntry, bool]:
    """Remove ``micros`` of credit; written as a negative ``expiry`` entry."""
    key = _caller_key(Kind.EXPIRY, org, idempotency_key)
    return _write(org, Kind.EXPIRY, -_positive(micros), key)


# --- reads ------------------------------------------------------------------


def get_account(org: Organization) -> ManagedCreditAccount | None:
    return ManagedCreditAccount.objects.filter(organization=org).first()


def balance(org: Organization) -> dict[str, int]:
    account = get_account(org)
    granted = account.granted_total_micros if account else 0
    spent = account.settled_spend_micros if account else 0
    return {
        "granted_total_micros": granted,
        "settled_spend_micros": spent,
        "ledger_available_micros": granted - spent,
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


# --- gateway push -----------------------------------------------------------


def push_granted(org: Organization) -> dict[str, Any] | None:
    """Send the gateway the granted total, its version and the spent floor.

    Returns the gateway's account state, or None when the org has no account or
    the push failed. A failure is logged and never raised: the account's
    ``gateway_acked_version`` stays behind its ``version`` until a later push
    (the next credit-side write, or ``ensure_account_pushed``) sends the full
    total again.
    """
    account = get_account(org)
    if account is None:
        logger.warning("managed_credits_push_no_account", org_id=str(org.pk))
        return None
    version = account.version
    try:
        state = gateway_credits.put_granted(
            str(org.pk),
            # The contract has no negative grant; adjustments can go below zero.
            granted_total_micros=max(account.granted_total_micros, 0),
            version=version,
            settled_spend_floor_micros=account.settled_spend_micros,
        )
    except (GatewayClientError, httpx.HTTPError, ValueError):
        logger.warning(
            "managed_credits_push_failed",
            org_id=str(org.pk),
            version=version,
            exc_info=True,
        )
        return None
    acked = state.get("granted_version") if isinstance(state, dict) else None
    if isinstance(acked, int) and acked >= version:
        ManagedCreditAccount.objects.filter(
            organization=org, gateway_acked_version__lt=version
        ).update(gateway_acked_version=version, updated_at=timezone.now())
    else:
        logger.warning(
            "managed_credits_push_not_acked",
            org_id=str(org.pk),
            version=version,
            gateway_version=acked,
        )
    return state


def ensure_account_pushed(org: Organization) -> dict[str, Any] | None:
    """Create the org's account with granted 0 if it is missing, then push it.

    The push runs now, not on commit, and the result is ``push_granted``'s
    (None when it failed). ``config_push`` calls it after a config push for an
    org whose managed models are on; a failed push is not re-queued.
    """
    ManagedCreditAccount.objects.get_or_create(organization=org)
    return push_granted(org)


# --- internals --------------------------------------------------------------


def _write(
    org: Organization, kind: str, amount_micros: int, key: str, **fields: Any
) -> tuple[ManagedCreditEntry, bool]:
    with transaction.atomic():
        account = _locked_account(org)
        entry = ManagedCreditEntry(
            organization=org,
            kind=kind,
            amount_micros=amount_micros,
            idempotency_key=key,
            **fields,
        )
        # INSERT ... ON CONFLICT DO NOTHING; the row is ours only if our id landed.
        ManagedCreditEntry.objects.bulk_create([entry], ignore_conflicts=True)
        if not ManagedCreditEntry.objects.filter(pk=entry.pk).exists():
            existing = ManagedCreditEntry.objects.get(idempotency_key=key)
            return _verified(existing, org, kind), False
        if kind == Kind.SPEND:
            account.settled_spend_micros -= amount_micros
            changed = ["settled_spend_micros"]
        else:
            account.version += 1
            account.granted_total_micros = _credit_total(org)
            changed = ["version", "granted_total_micros"]
            transaction.on_commit(partial(push_granted, org), robust=True)
        account.save(update_fields=[*changed, "updated_at"])
    return entry, True


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
            f"key {entry.idempotency_key!r} is a {entry.kind} entry of org "
            f"{entry.organization_id}, not a {kind} of org {org.pk}"
        )
    return entry


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
        raise ManagedCreditError(
            f"amount must be a positive integer of micros, got {micros!r}"
        )
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
