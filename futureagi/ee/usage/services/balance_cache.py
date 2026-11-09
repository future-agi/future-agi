"""The ledger's balance cache and the zero-balance admission check (PLAN 1.4).

Every ledger write that changes an org's balance calls ``write`` on commit:
Redis key ``ledger:balance:<org_id>`` holds ``<entry_seq>:<balance_micros>:<mode>``
and a newer ``entry_seq`` always wins (a compare-and-set script), so two
commits landing out of order never leave the older balance behind. ``mode`` is
``lp`` for an org that is ``prepaid_only`` on ``billing_path=ledger``, else empty.
The same commit sends ``balance_changed`` (BL-STRIPE's auto top-up listens).
The balance cached and sent is the spendable one: 0 while the org is frozen
(``money_freeze``: a receivable or an open dispute), so it is never negative;
a cache miss refills it by the same rule.

``admission_refusal`` is what ``metering.check_usage`` asks before a billable
action. In phase 2a it refuses at or below zero only:

- an event type whose catalog dimension is ``prepaid_only`` (today only
  ``gateway_managed_spend``, UMP guard d), for any org;
- any event type of an org that is ``prepaid_only`` and on
  ``billing_path=ledger`` (no org is, before the phase-2b cutover).

A refusal keeps the error code. Its reason is the generic "add credits" text,
except while the org is frozen, when it names the freeze instead
(``FROZEN_REASONS``, read from Postgres only on the refusing path).

So platform admission in phase 2a is unchanged. A cache miss reads Postgres
once and fills the cache; Redis being down falls back to Postgres. The cache is
never the money: the ledger is (Postgres), and the gateway's own 402
``managed_credits_exhausted`` stays the hot-path authority for model calls.
Entries expire after ``CACHE_TTL_SECONDS`` so a lost write heals by itself.
"""

from __future__ import annotations

import structlog
from django.dispatch import Signal
from django.utils import timezone

from ee.usage.schemas.events import CheckResult

logger = structlog.get_logger(__name__)

CACHE_KEY = "ledger:balance:{org_id}"
CACHE_TTL_SECONDS = 300
ADD_CREDITS_PATH = "/dashboard/settings/billing"
ERROR_CODE = "BALANCE_EXHAUSTED"
MODE_LEDGER_PREPAID = "lp"
EXHAUSTED_REASON = (
    "Your Future AGI credit balance is used up. Add credits in "
    f"Settings > Billing ({ADD_CREDITS_PATH})."
)
# Keyed by money_freeze.RECEIVABLE_OPEN / DISPUTE_OPEN. A top-up pays a
# receivable first, so that text still names Settings > Billing; it does not
# lift a dispute, so that one does not.
FROZEN_REASONS = {
    "receivable_open": (
        "Spending on Future AGI credits is paused: your organization owes for "
        "credit it already used (a refunded or disputed payment). Pay it by "
        f"adding credits in Settings > Billing ({ADD_CREDITS_PATH})."
    ),
    "dispute_open": (
        "Spending on Future AGI credits is paused while a payment dispute on "
        "your organization is open. It resumes when the dispute is resolved."
    ),
}

# Sent on commit after a balance change: sender=None, org_id=str,
# balance_micros=int, entry_seq=int. Receivers must not raise; errors are
# logged and swallowed (robust on_commit).
balance_changed = Signal()

_CAS_LUA = """
local cur = redis.call('GET', KEYS[1])
if cur then
  local seq = tonumber(string.match(cur, '^(%-?%d+):'))
  if seq and seq > tonumber(ARGV[1]) then return 0 end
end
redis.call('SET', KEYS[1], ARGV[1] .. ':' .. ARGV[2] .. ':' .. ARGV[3], 'EX', ARGV[4])
return 1
"""


def _redis():
    from ee.usage.services.emitter import get_redis

    return get_redis()


def write(org_id: str, *, balance_micros: int, entry_seq: int, mode: str = "") -> None:
    """Store the balance if ``entry_seq`` is not older than the cached one. Never raises."""
    try:
        _redis().eval(
            _CAS_LUA,
            1,
            CACHE_KEY.format(org_id=org_id),
            str(int(entry_seq)),
            str(int(balance_micros)),
            mode,
            str(CACHE_TTL_SECONDS),
        )
    except Exception:
        logger.warning("ledger_balance_cache_write_failed", org_id=str(org_id))


def _read_raw(org_id: str) -> tuple[int, str] | None:
    try:
        raw = _redis().get(CACHE_KEY.format(org_id=org_id))
    except Exception:
        logger.warning("ledger_balance_cache_read_failed", org_id=str(org_id))
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    parts = raw.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[1]), parts[2]
    except ValueError:
        return None


def read(org_id: str) -> int | None:
    """The cached balance in micros, or None on a miss or when Redis is down."""
    cached = _read_raw(org_id)
    return cached[0] if cached else None


def _from_db(org_id: str) -> tuple[int, str]:
    from ee.usage.models.managed_credits import ManagedCreditAccount
    from ee.usage.services.managed_credits import billing_profile_for_account
    from ee.usage.services.money_freeze import freeze_reason_for, spendable

    account = ManagedCreditAccount.objects.filter(organization_id=org_id).first()
    if account is None:
        balance, seq, mode = 0, 0, ""
    else:
        balance = spendable(
            account.promo_balance_micros
            + account.commit_balance_micros
            + account.purchased_balance_micros
            - account.debt_micros,
            bool(freeze_reason_for(org_id, debt_micros=account.debt_micros)),
        )
        seq = account.entry_seq
        profile = billing_profile_for_account(account)
        mode = (
            MODE_LEDGER_PREPAID
            if profile["funding"] == "prepaid_only" and profile["billing_path"] == "ledger"
            else ""
        )
    write(org_id, balance_micros=balance, entry_seq=seq, mode=mode)
    return balance, mode


def current_balance(org_id: str) -> int:
    """The cached balance, else Postgres (and the cache is filled)."""
    cached = _read_raw(str(org_id))
    return cached[0] if cached else _from_db(str(org_id))[0]


def _is_prepaid_event_type(event_type: str) -> bool:
    from ee.usage.services import rating

    catalog = rating.catalog_for_validation(timezone.now())
    if catalog is None:
        return event_type == rating.MANAGED_SPEND_EVENT_TYPE
    dimension = catalog.dimensions.get(catalog.event_types.get(event_type, ""))
    if dimension is None:
        return event_type == rating.MANAGED_SPEND_EVENT_TYPE
    return dimension.settlement_class == "prepaid_only"


def admission_refusal(org_id: str, event_type: str) -> CheckResult | None:
    """A refusing ``CheckResult`` (error_code ``BALANCE_EXHAUSTED``), or None.

    The reason names Settings > Billing (``ADD_CREDITS_PATH``) as the place to
    add credits, the same deep link every limit message uses (PLAN 2.1). While
    the org is frozen it names the freeze instead (``FROZEN_REASONS``).
    """
    from ee.usage.services.money_freeze import freeze_reason_of

    org_id = str(org_id)
    cached = _read_raw(org_id)
    balance, mode = cached if cached else _from_db(org_id)
    prepaid = _is_prepaid_event_type(str(event_type))
    if not (prepaid or mode == MODE_LEDGER_PREPAID):
        return None
    if balance > 0:
        return None
    try:
        frozen = freeze_reason_of(org_id)
    except Exception:
        # A failed reason lookup must not discard an already-known refusal.
        logger.warning("ledger_balance_freeze_reason_read_failed")
        frozen = ""
    return CheckResult(
        allowed=False,
        error_code=ERROR_CODE,
        reason=FROZEN_REASONS.get(frozen, EXHAUSTED_REASON),
    )
