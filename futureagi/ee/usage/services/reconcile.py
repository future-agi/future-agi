"""Ledger invariants as checks (PLAN 4.4, billing-ledger-read-v1 section 5).

Phase 2a runs I1, I2, I5, I6 and I11; I3, I4, I7-I10 need the Stripe reporter
or the catalog markup check and come with BL-STRIPE and GW-METERS. Each check
returns an ``InvariantResult`` and never raises for a failed invariant; a run
records a ``ReconciliationRun`` row. Tolerance is 0.

- **I1** every ``UsageEventLog`` row received at or before ``rated_through`` has
  exactly one of a rated entry (of its org, kind ``spend`` or ``usage_debit``,
  its dimension) and a not-billable reason; a managed-spend event's quantity is
  its SPEND's amount; no entry is the rating of two events; and no event waits
  unrated longer than ``STUCK_AFTER`` (an event that never rates fails loudly).
- **I2** every entry with a funding split has ``sum(funded_*) == amount_micros``
  and every bucket within its sign (the database also enforces the sum).
- **I5** the account's totals equal the entry sums: ``granted_total_micros`` = F,
  ``settled_spend_micros`` = S (spend only), ``platform_spend_micros`` = P (wallet
  ``usage_debit`` only), ``promo_total_micros``, ``commit_total_micros`` and
  ``purchased_total_micros`` = their grant sums, each bucket balance = the
  legacy replay plus the sum of its funded column, ``entry_seq`` = the entry
  count, and promo + commit + purchased - debt = F - S - P.
- **I6** with managed models on (not ``na_managed_off``) and the ack current:
  the gateway's ``granted_total_micros`` = ``max(F - P, 0)``, at most S while
  the org is frozen (``money_freeze``, ``gateway_push.granted_value``), its
  ``granted_version`` = ``version`` and its ``spent_micros`` >= S.
- **I11** mixed traffic with both pushes acknowledged and the gateway's outbox
  drained: the gateway's ``available_micros`` = granted - S - H, which is
  F - P - S - H when F >= P, else -S - H, and at most -H while frozen; the
  ledger's ``balance_micros`` = F - P - S either way.

``check_gateway`` reads the gateway's credits-v1 admin GET once and runs I5, I6
and I11 for one org (R4). The 404 ``managed_models_disabled`` answer exempts
the org from I6 and I11 (``na_managed_off``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
import structlog
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

logger = structlog.get_logger(__name__)

STUCK_AFTER = timedelta(hours=1)
MAX_FAILURES = 50


@dataclass(frozen=True)
class InvariantResult:
    name: str  # "I1", "I2", "I5", "I6" or "I11"
    ok: bool
    checked: int
    failures: tuple[str, ...] = field(default_factory=tuple)
    exempt: bool = False  # I6/I11 for an na_managed_off org


def _result(name: str, checked: int, failures: list[str], exempt: bool = False):
    return InvariantResult(name, not failures, checked, tuple(failures[:MAX_FAILURES]), exempt)


def _models():
    from ee.usage.models.billing_ledger import ReconciliationRun, UsageEventRecord
    from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
    from ee.usage.models.usage import UsageEventLog

    return ManagedCreditEntry, ManagedCreditAccount, UsageEventLog, UsageEventRecord, ReconciliationRun


def check_i1(*, org: Any = None, now: datetime | None = None) -> InvariantResult:
    from ee.usage.services import rating

    Entry, _, Log, Record, _ = _models()
    now = now or timezone.now()
    watermark = rating.rated_through(now)
    records = Record.objects.select_related("event", "rated_entry")
    logs = Log.objects.all()
    if org is not None:
        records = records.filter(organization=org)
        logs = logs.filter(organization=org)
    failures: list[str] = []
    checked = 0
    for record in records.filter(received_at__lte=watermark).iterator():
        checked += 1
        has_entry = record.rated_entry_id is not None
        has_reason = bool(record.not_billable_reason)
        if has_entry == has_reason:
            failures.append(f"event {record.event_uuid}: rated={has_entry} reason={has_reason}")
            continue
        if not has_entry:
            continue
        entry = record.rated_entry
        dimension = entry.dimension or ("managed_spend" if entry.kind == Entry.Kind.SPEND else "")
        if entry.organization_id != record.organization_id:
            failures.append(f"event {record.event_uuid}: rated to another org's entry")
        elif entry.kind not in (Entry.Kind.SPEND, Entry.Kind.USAGE_DEBIT):
            failures.append(f"event {record.event_uuid}: rated to a {entry.kind} entry")
        elif dimension != record.event.dimension:
            failures.append(f"event {record.event_uuid}: entry dimension {dimension!r}")
        elif entry.kind == Entry.Kind.SPEND and record.event.amount_raw != -entry.amount_micros:
            failures.append(
                f"event {record.event_uuid}: quantity {record.event.amount_raw} "
                f"!= spend {-entry.amount_micros}"
            )
    doubles = (
        records.exclude(rated_entry__isnull=True)
        .values("rated_entry")
        .annotate(n=Count("pk"))
        .filter(n__gt=1)
    )
    for row in doubles:
        failures.append(f"entry {row['rated_entry']} rates {row['n']} events")
    stuck_before = now - STUCK_AFTER
    stuck = records.filter(
        rated_entry__isnull=True, not_billable_reason="", received_at__lt=stuck_before
    ).count() + logs.filter(ledger_record__isnull=True, created_at__lt=stuck_before).count()
    if stuck:
        failures.append(f"{stuck} events unrated for more than {STUCK_AFTER}")
    return _result("I1", checked, failures)


def check_i2(*, org: Any = None) -> InvariantResult:
    Entry, *_ = _models()
    entries = Entry.objects.filter(funded_promo_micros__isnull=False)
    if org is not None:
        entries = entries.filter(organization=org)
    total = F("funded_promo_micros") + F("funded_commit_micros") + F(
        "funded_purchased_micros"
    ) + F("funded_debt_micros")
    buckets = ("funded_promo_micros", "funded_commit_micros", "funded_purchased_micros", "funded_debt_micros")
    wrong_sign = Q()
    for name in buckets:
        wrong_sign |= Q(amount_micros__lt=0, **{f"{name}__gt": 0})
        wrong_sign |= Q(amount_micros__gt=0, **{f"{name}__lt": 0})
        wrong_sign |= Q(amount_micros=0, **{f"{name}__lt": 0})
        wrong_sign |= Q(amount_micros=0, **{f"{name}__gt": 0})
    bad = entries.annotate(funded_total=total).filter(
        ~Q(funded_total=F("amount_micros")) | wrong_sign
    )
    failures = [f"entry {e.idempotency_key}: split does not fund {e.amount_micros}" for e in bad[:MAX_FAILURES]]
    return _result("I2", entries.count(), failures)


def _replayed_buckets(org):
    from ee.usage.services import funding

    Entry, *_ = _models()
    buckets = funding.Buckets()
    legacy = Entry.objects.filter(organization=org, funded_promo_micros__isnull=True).exclude(
        kind=Entry.Kind.USAGE_DEBIT
    )
    for kind, amount in legacy.order_by("occurred_at", "created_at", "id").values_list(
        "kind", "amount_micros"
    ):
        buckets = funding.apply(buckets, funding.legacy_allocation(buckets, kind, amount))
    sums = Entry.objects.filter(organization=org, funded_promo_micros__isnull=False).aggregate(
        promo=Sum("funded_promo_micros"),
        commit=Sum("funded_commit_micros"),
        purchased=Sum("funded_purchased_micros"),
        debt=Sum("funded_debt_micros"),
    )
    return (
        buckets.promo + (sums["promo"] or 0),
        buckets.commit + (sums["commit"] or 0),
        buckets.purchased + (sums["purchased"] or 0),
        buckets.debt - (sums["debt"] or 0),
    )


def _sums(org) -> dict[str, int]:
    from ee.usage.services.managed_credits import CREDIT_KINDS

    Entry, *_ = _models()
    mine = Entry.objects.filter(organization=org)
    K = Entry.Kind

    def total(qs):
        return qs.aggregate(t=Sum("amount_micros"))["t"] or 0

    return {
        "F": total(mine.filter(kind__in=CREDIT_KINDS)),
        "S": -total(mine.filter(kind=K.SPEND)),
        "P": -total(mine.filter(kind=K.USAGE_DEBIT, funded_promo_micros__isnull=False)),
        "promo_total": total(
            mine.filter(kind__in=[K.GRANT, K.ADJUSTMENT], amount_micros__gt=0).filter(
                Q(funding="promo") | Q(funding="")
            )
        ),
        "commit_total": total(
            mine.filter(kind__in=[K.GRANT, K.ADJUSTMENT], amount_micros__gt=0, funding="commit")
        ),
        "purchased_total": total(mine.filter(kind=K.TOPUP)),
        "count": mine.count(),
    }


def check_i5(org: Any) -> InvariantResult:
    _, Account, *_ = _models()
    account = Account.objects.filter(organization=org).first()
    s = _sums(org)
    if account is None:
        failures = [] if s["count"] == 0 else ["entries exist without an account"]
        return _result("I5", 0, failures)
    pairs = [
        ("granted_total_micros (F)", account.granted_total_micros, s["F"]),
        ("settled_spend_micros (S)", account.settled_spend_micros, s["S"]),
        ("platform_spend_micros (P)", account.platform_spend_micros, s["P"]),
        ("promo_total_micros", account.promo_total_micros, s["promo_total"]),
        ("commit_total_micros", account.commit_total_micros, s["commit_total"]),
        ("purchased_total_micros", account.purchased_total_micros, s["purchased_total"]),
        ("entry_seq", account.entry_seq, s["count"]),
    ]
    promo, commit, purchased, debt = _replayed_buckets(org)
    pairs += [
        ("promo_balance_micros", account.promo_balance_micros, promo),
        ("commit_balance_micros", account.commit_balance_micros, commit),
        ("purchased_balance_micros", account.purchased_balance_micros, purchased),
        ("debt_micros", account.debt_micros, debt),
        (
            "balance = F - S - P",
            account.promo_balance_micros
            + account.commit_balance_micros
            + account.purchased_balance_micros
            - account.debt_micros,
            s["F"] - s["S"] - s["P"],
        ),
    ]
    failures = [f"{name}: account {have} != entries {want}" for name, have, want in pairs if have != want]
    return _result("I5", len(pairs), failures)


def _account(org):
    _, Account, *_ = _models()
    return Account.objects.filter(organization=org).first()


def _expected_granted(org, s: dict[str, int]) -> int:
    """The granted value the gateway should hold, from the entries alone: the
    debt replayed from the entries and the open-dispute rule decide the freeze
    (``money_freeze``), never the account row."""
    from ee.usage.services.gateway_push import granted_value
    from ee.usage.services.money_freeze import freeze_reason_for

    debt = _replayed_buckets(org)[3]
    frozen = bool(freeze_reason_for(org.pk, debt_micros=debt))
    return granted_value(s["F"], s["P"], s["S"], frozen)


def _gateway_int(state: dict, name: str) -> int | None:
    value = state.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def check_i6(org: Any, gateway_state: dict[str, Any] | None) -> InvariantResult:
    """``gateway_state`` is the credits-v1 admin GET, or None for 404 managed off."""
    account = _account(org)
    if gateway_state is None or account is None or account.gateway_push_state == "na_managed_off":
        return _result("I6", 0, [], exempt=True)
    if account.gateway_acked_version != account.version:
        # Not checkable until the gateway acknowledged the current version.
        return _result("I6", 0, [])
    s = _sums(org)
    failures = []
    granted = _gateway_int(gateway_state, "granted_total_micros")
    version = _gateway_int(gateway_state, "granted_version")
    spent = _gateway_int(gateway_state, "spent_micros")
    want = _expected_granted(org, s)
    if granted != want:
        failures.append(
            f"gateway granted {granted} != max(F - P, 0) (at most S while frozen) = {want}"
        )
    if version != account.version:
        failures.append(f"gateway version {version} != ledger version {account.version}")
    if spent is None or spent < s["S"]:
        failures.append(f"gateway spent {spent} is below the settled floor {s['S']}")
    return _result("I6", 3, failures)


def check_i11(org: Any, gateway_state: dict[str, Any] | None) -> InvariantResult:
    account = _account(org)
    if gateway_state is None or account is None or account.gateway_push_state == "na_managed_off":
        return _result("I11", 0, [], exempt=True)
    pending = gateway_state.get("outbox_pending")
    if account.gateway_acked_version != account.version or (
        isinstance(pending, int) and pending > 0
    ):
        return _result("I11", 0, [])  # not quiescent: pushes or settlements in flight
    s = _sums(org)
    held = _gateway_int(gateway_state, "held_micros") or 0
    available = _gateway_int(gateway_state, "available_micros")
    F_, P_, S_ = s["F"], s["P"], s["S"]
    # F - P - S - H when F >= P, else -S - H; while frozen at most -H.
    expected = _expected_granted(org, s) - S_ - held
    failures = []
    if available != expected:
        failures.append(f"gateway available {available} != {expected} (F={F_} P={P_} S={S_} H={held})")
    ledger_balance = (
        account.promo_balance_micros
        + account.commit_balance_micros
        + account.purchased_balance_micros
        - account.debt_micros
    )
    if ledger_balance != F_ - P_ - S_:
        failures.append(f"ledger balance {ledger_balance} != F - P - S = {F_ - P_ - S_}")
    return _result("I11", 2, failures)


def record_run(result: InvariantResult, org: Any = None) -> None:
    *_, Run = _models()
    Run.objects.create(
        check_name=result.name,
        organization=org,
        finished_at=timezone.now(),
        ok=result.ok,
        exempt=result.exempt,
        checked=result.checked,
        failures=list(result.failures),
    )
    if not result.ok:
        logger.error("ledger_invariant_failed", check=result.name, failures=list(result.failures[:5]))


def check_gateway(org: Any) -> tuple[InvariantResult, ...]:
    """I5, I6 and I11 for one org from one gateway read; records the run (R4)."""
    from agentcc.services.gateway_admin import credits as gateway_credits
    from agentcc.services.gateway_client import GatewayClientError
    from ee.usage.services.gateway_push import is_managed_off

    state: dict[str, Any] | None
    unreachable = None
    try:
        state = gateway_credits.get_credits(str(org.pk))
    except (GatewayClientError, httpx.HTTPError, ValueError) as exc:
        state = None
        if not is_managed_off(exc):
            unreachable = str(exc)[:200]
    i5 = check_i5(org)
    if unreachable is not None:
        i6 = _result("I6", 0, [f"gateway unreachable: {unreachable}"])
        i11 = _result("I11", 0, [f"gateway unreachable: {unreachable}"])
    else:
        i6 = check_i6(org, state)
        i11 = check_i11(org, state)
    results = (i5, i6, i11)
    for result in results:
        record_run(result, org)
    return results
