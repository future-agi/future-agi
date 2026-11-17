"""The granted push to the gateway (credits-granted-push-v1, PLAN 1.2 fixes 3-6).

- ``pushed_value`` is the one value every caller sends:
  ``granted = max(F - P, 0)`` (the contract has no negative grant), the
  account version, and the settled-spend floor S. Once platform debits pass the
  credits the gateway sees 0 and the overdraft lives only in Postgres. While
  the org is frozen (``money_freeze``: a receivable or an open dispute) the
  granted value is capped at S (``granted_value``), so the gateway has nothing
  left to spend.
- ``push_granted`` sends it now. A 404 whose body names
  ``managed_models_disabled`` (no managed provider on the gateway) marks the
  account ``na_managed_off``: not applicable, never retried in a loop. Any
  other failure (of any exception type) is logged value-free and left for the
  sweep; it never propagates to the on-commit caller.
- ``request_push`` is what a ledger commit calls: one push in flight per org,
  coalesced through Redis. A caller first marks the org dirty, then tries the
  lock; the holder clears the mark before each read-and-push and pushes again
  while it finds the mark set, including once more after it unlocks. So a
  commit is either seen by the holder or takes the lock itself. With Redis down
  it pushes directly (versions make a duplicate push harmless).
- ``sweep_unacked_pushes`` (the 60 s schedule) pushes every account whose ack
  is behind its version; ``na_managed_off`` accounts are re-tried at most once
  per ``NA_RECHECK`` so a gateway that turned managed models on gets one full
  push without a retry loop.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import structlog
from django.db.models import F, Q
from django.utils import timezone

from accounts.models.organization import Organization
from agentcc.services.gateway_admin import credits as gateway_credits
from agentcc.services.gateway_client import GatewayClientError
from ee.usage.models.managed_credits import ManagedCreditAccount
from ee.usage.services.money_freeze import freeze_reason_for

logger = structlog.get_logger(__name__)

STATE_OK = "ok"
STATE_NA_MANAGED_OFF = "na_managed_off"
MANAGED_OFF_CODE = "managed_models_disabled"
NA_RECHECK = timedelta(hours=1)
LOCK_TTL_MS = 30_000
MAX_ROUNDS = 8
_LOCK = "mc:push:lock:{org}"
_DIRTY = "mc:push:dirty:{org}"
_UNLOCK_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) else return 0 end"
)


def granted_value(
    credit_total: int, platform_spend: int, settled_spend: int, frozen: bool
) -> int:
    """The granted total to push: ``max(F - P, 0)``, at most S while frozen.

    The gateway's available is ``granted - spent - held`` and its spent is at
    least the pushed floor S, so a frozen org (``money_freeze``: a receivable
    or an open dispute) is left no spendable money, promo and commit included.
    For an overdraft (every bucket empty) F - P is already below S, so the
    value is the same as unfrozen.
    """
    granted = max(credit_total - platform_spend, 0)
    return min(granted, max(settled_spend, 0)) if frozen else granted


def pushed_value(account: ManagedCreditAccount):
    """The ``GrantedPush`` for ``account``: ``granted_value``, version, floor S."""
    from ee.usage.services.managed_credits import GrantedPush

    frozen = bool(
        freeze_reason_for(account.organization_id, debt_micros=account.debt_micros)
    )
    return GrantedPush(
        granted_total_micros=granted_value(
            account.granted_total_micros,
            account.platform_spend_micros,
            account.settled_spend_micros,
            frozen,
        ),
        version=account.version,
        settled_spend_floor_micros=max(account.settled_spend_micros, 0),
    )


def is_managed_off(exc: Exception) -> bool:
    """True for the gateway's 404 ``managed_models_disabled`` answer."""
    return (
        isinstance(exc, GatewayClientError)
        and getattr(exc, "status_code", None) == 404
        and MANAGED_OFF_CODE in str(exc)
    )


def push_granted(org: Organization) -> dict[str, Any] | None:
    """Send the gateway the pushed value now; the gateway's state, or None.

    None when the org has no account, the push failed (logged
    ``managed_credits_push_failed``, never raised) or the gateway answered 404
    ``managed_models_disabled`` (the account becomes ``na_managed_off``).
    """
    account = ManagedCreditAccount.objects.filter(organization=org).first()
    if account is None:
        logger.warning("managed_credits_push_no_account", org_id=str(org.pk))
        return None
    value = pushed_value(account)
    try:
        state = gateway_credits.put_granted(str(org.pk), **value.as_request())
    except Exception as exc:
        # Every ordinary failure of the push is nonfatal and logged value-free
        # here, so the robust on-commit caller never receives a value-bearing
        # exception chain.
        if is_managed_off(exc):
            _set_state(org, STATE_NA_MANAGED_OFF)
            logger.info(
                "managed_credits_push_not_applicable",
                org_id=str(org.pk),
                version=value.version,
                reason=MANAGED_OFF_CODE,
            )
            return None
        logger.warning(
            "managed_credits_push_failed",
            org_id=str(org.pk),
            version=value.version,
            exception_type=type(exc).__name__,
            reason="gateway_push_failed",
        )
        return None
    acked = state.get("granted_version") if isinstance(state, dict) else None
    now = timezone.now()
    if (
        isinstance(acked, int)
        and not isinstance(acked, bool)
        and acked >= value.version
    ):
        ManagedCreditAccount.objects.filter(
            organization=org, gateway_acked_version__lt=value.version
        ).update(gateway_acked_version=value.version, updated_at=now)
    else:
        logger.warning(
            "managed_credits_push_not_acked",
            org_id=str(org.pk),
            version=value.version,
            gateway_version=acked if type(acked) is int else None,
            reason="gateway_push_not_acknowledged",
        )
    _set_state(org, STATE_OK)
    return state


def ensure_account_pushed(org: Organization) -> dict[str, Any] | None:
    """Create the org's account with granted 0 if it is missing, then push it.

    The push runs now and ignores ``na_managed_off``: its caller
    (``config_push``) runs when an org's managed models are turned on, so this
    is the one full push PLAN 1.2 fix 6 asks for.
    """
    ManagedCreditAccount.objects.get_or_create(organization=org)
    return push_granted(org)


def request_push(org_id: str) -> None:
    """Coalesced push for one org (see the module docstring). Never raises."""
    try:
        _request_push(str(org_id))
    except Exception:
        logger.exception("managed_credits_request_push_failed", org_id=str(org_id))


def sweep_unacked_pushes(*, limit: int = 500) -> int:
    """Push accounts whose ack is behind their version; returns how many."""
    recheck_before = timezone.now() - NA_RECHECK
    org_ids = list(
        ManagedCreditAccount.objects.filter(gateway_acked_version__lt=F("version"))
        .filter(
            ~Q(gateway_push_state=STATE_NA_MANAGED_OFF)
            | Q(gateway_push_state_at__lt=recheck_before)
            | Q(gateway_push_state_at__isnull=True)
        )
        .order_by("updated_at")
        .values_list("organization_id", flat=True)[:limit]
    )
    for org_id in org_ids:
        request_push(str(org_id))
    return len(org_ids)


# --- internals --------------------------------------------------------------


def _set_state(org: Organization, state: str) -> None:
    ManagedCreditAccount.objects.filter(organization=org).exclude(
        gateway_push_state=state
    ).update(gateway_push_state=state, gateway_push_state_at=timezone.now())
    if state == STATE_NA_MANAGED_OFF:
        # Stamp the time of every not-applicable answer: NA_RECHECK counts from it.
        ManagedCreditAccount.objects.filter(organization=org).update(
            gateway_push_state_at=timezone.now()
        )


def _skips(account: ManagedCreditAccount | None) -> bool:
    if account is None:
        return True
    if account.gateway_push_state != STATE_NA_MANAGED_OFF:
        return False
    stamped = account.gateway_push_state_at
    return stamped is not None and stamped > timezone.now() - NA_RECHECK


def _redis():
    from ee.usage.services.emitter import get_redis

    return get_redis()


def _request_push(org_id: str) -> None:
    org = Organization.objects.filter(pk=org_id).first()
    if org is None:
        return
    if _skips(ManagedCreditAccount.objects.filter(organization=org).first()):
        return
    try:
        r = _redis()
        r.set(_DIRTY.format(org=org_id), "1", px=LOCK_TTL_MS * 2)
    except Exception:
        logger.warning("managed_credits_push_lock_unavailable", org_id=org_id)
        push_granted(org)
        return
    for _ in range(MAX_ROUNDS):
        token = uuid.uuid4().hex
        try:
            got = r.set(_LOCK.format(org=org_id), token, nx=True, px=LOCK_TTL_MS)
        except Exception:
            push_granted(org)
            return
        if not got:
            return  # the holder sees our dirty mark
        try:
            for _ in range(MAX_ROUNDS):
                r.delete(_DIRTY.format(org=org_id))
                push_granted(org)
                if not r.exists(_DIRTY.format(org=org_id)):
                    break
        finally:
            try:
                r.eval(_UNLOCK_LUA, 1, _LOCK.format(org=org_id), token)
            except Exception:
                logger.warning("managed_credits_push_unlock_failed", org_id=org_id)
        if not r.exists(_DIRTY.format(org=org_id)):
            return
    logger.warning("managed_credits_push_rounds_exhausted", org_id=org_id)
