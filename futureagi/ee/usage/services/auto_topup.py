"""Auto top-up state on the org's ledger account (PLAN 2.3, decision 12).

Opt-in and off by default, with a threshold, an amount, a daily count cap and
a daily amount cap (UTC day), card-funded payment methods only (BL-STRIPE
checks the method), one charge in flight per org, and the consent text version
and time recorded. Every enabled save is an authenticated actor acknowledging
the CURRENT published terms, kept as a gateway consent receipt
(``agentcc.services.auto_reload_consent``) in the same transaction, and a claim
without a matching current receipt never charges: an enabled flag, a bare
version string or a legacy row is not consent, and a database error aborts the
claim. A NULL daily count or amount cap never charges either (it is not
"unlimited"), and neither does a frozen org (``money_freeze``: a receivable
or an open dispute), both checked under the same row lock: the adapter's own
freeze check runs before the claim, outside it. The charge itself is
BL-STRIPE's: it claims here, charges
with the claim's Idempotency-Key, and the PaymentIntent's fulfilment
(``managed_credits.fulfil_topup`` with ``auto_topup_seq``) clears the claim. A
failed or authentication-required charge turns auto top-up off with a reason.

``managed_credits`` re-exports these three functions; they write no entry.
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.user import User
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.services.money_freeze import freeze_reason_for

OUTCOMES = ("succeeded", "failed", "requires_action")
AUTO_TOPUP_CATEGORY = "auto_topup"


class AutoTopupError(ValueError):
    """Settings that cannot enable auto top-up; nothing was changed."""


def _locked(org: Organization) -> ManagedCreditAccount:
    ManagedCreditAccount.objects.get_or_create(organization=org)
    return ManagedCreditAccount.objects.select_for_update().get(organization=org)


def _int_or_none(value, name, minimum):
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise AutoTopupError(f"{name} must be an integer >= {minimum}, got {value!r}")
    return value


def configure_auto_topup(
    org: Organization,
    *,
    enabled: bool,
    threshold_micros: int | None,
    amount_micros: int | None,
    daily_count_cap: int | None,
    daily_amount_cap_micros: int | None,
    payment_method_id: str = "",
    consent_text_version: str = "",
    actor: User | None = None,
) -> ManagedCreditAccount:
    threshold = _int_or_none(threshold_micros, "threshold_micros", 0)
    amount = _int_or_none(amount_micros, "amount_micros", 1)
    count_cap = _int_or_none(daily_count_cap, "daily_count_cap", 1)
    amount_cap = _int_or_none(daily_amount_cap_micros, "daily_amount_cap_micros", 1)
    if enabled:
        if None in (threshold, amount, count_cap, amount_cap):
            raise AutoTopupError("enabling auto top-up needs every limit")
        if amount_cap < amount:
            raise AutoTopupError("the daily amount cap is below one top-up")
        if not payment_method_id or not consent_text_version:
            raise AutoTopupError(
                "enabling auto top-up needs a payment method and consent"
            )
        _require_current_consent(consent_text_version, actor)
    with transaction.atomic():
        account = _locked(org)
        account.autoreload_enabled = bool(enabled)
        account.autoreload_threshold_micros = threshold
        account.autoreload_amount_micros = amount
        account.autoreload_daily_count_cap = count_cap
        account.autoreload_daily_amount_cap_micros = amount_cap
        changed = [
            "autoreload_enabled",
            "autoreload_threshold_micros",
            "autoreload_amount_micros",
            "autoreload_daily_count_cap",
            "autoreload_daily_amount_cap_micros",
        ]
        if enabled:
            account.autoreload_payment_method_id = payment_method_id[:255]
            account.autoreload_consent_version = consent_text_version[:64]
            account.autoreload_consent_at = timezone.now()
            account.autoreload_disabled_reason = ""
            changed += [
                "autoreload_payment_method_id",
                "autoreload_consent_version",
                "autoreload_consent_at",
                "autoreload_disabled_reason",
            ]
        account.save(update_fields=[*changed, "updated_at"])
        if enabled:
            # The receipt pins the exact time, threshold and amount saved above;
            # a failed write rolls the settings back with it.
            from agentcc.services.auto_reload_consent import record_auto_reload_consent

            record_auto_reload_consent(
                account,
                version=consent_text_version,
                actor=actor,
                accepted_at=account.autoreload_consent_at,
            )
    return account


def _require_current_consent(version: str, actor: User | None) -> None:
    """Refuse, before any write, an outdated version or a missing actor."""
    from agentcc.auto_reload_terms import (
        AutoReloadConsentError,
        require_current_consent_version,
    )
    from agentcc.services.auto_reload_consent import require_consent_actor

    try:
        require_current_consent_version(version)
        require_consent_actor(actor)
    except AutoReloadConsentError as exc:
        raise AutoTopupError(str(exc)) from exc


def _today_start(now: datetime) -> datetime:
    day = now.astimezone(UTC)
    return day.replace(hour=0, minute=0, second=0, microsecond=0)


def claim_auto_topup(org: Organization):
    from agentcc.services.auto_reload_consent import has_current_auto_reload_consent
    from ee.usage.services.managed_credits import AutoTopupClaim

    now = timezone.now()
    with transaction.atomic():
        account = _locked(org)
        # Under the same row lock as a settings save: no current receipt, no
        # charge. A database error propagates and aborts; it never charges.
        if not has_current_auto_reload_consent(account):
            return None
        if (
            not account.autoreload_enabled
            or account.autoreload_in_flight_seq is not None
        ):
            return None
        threshold = account.autoreload_threshold_micros
        amount = account.autoreload_amount_micros
        if threshold is None or not amount or not account.autoreload_payment_method_id:
            return None
        count_cap = account.autoreload_daily_count_cap
        amount_cap = account.autoreload_daily_amount_cap_micros
        if count_cap is None or amount_cap is None:
            # A NULL cap is not "unlimited" (FA ledger G-06): no charge. Read
            # here, under the row lock, so a save that clears a cap while this
            # claim waits for the row is seen.
            return None
        if freeze_reason_for(org.pk, debt_micros=account.debt_micros):
            # Frozen (a receivable or an open dispute): no charge. Read under
            # the row lock, so a reversal or dispute hold committed while this
            # claim waited for the row is seen.
            return None
        balance = (
            account.promo_balance_micros
            + account.commit_balance_micros
            + account.purchased_balance_micros
            - account.debt_micros
        )
        if balance > threshold:
            return None
        today = ManagedCreditEntry.objects.filter(
            organization=org,
            kind=ManagedCreditEntry.Kind.TOPUP,
            category=AUTO_TOPUP_CATEGORY,
            occurred_at__gte=_today_start(now),
        ).aggregate(total=Sum("amount_micros"))
        count = ManagedCreditEntry.objects.filter(
            organization=org,
            kind=ManagedCreditEntry.Kind.TOPUP,
            category=AUTO_TOPUP_CATEGORY,
            occurred_at__gte=_today_start(now),
        ).count()
        if count + 1 > count_cap:
            return None
        if (today["total"] or 0) + amount > amount_cap:
            return None
        account.autoreload_seq += 1
        account.autoreload_in_flight_seq = account.autoreload_seq
        account.autoreload_in_flight_at = now
        account.save(
            update_fields=[
                "autoreload_seq",
                "autoreload_in_flight_seq",
                "autoreload_in_flight_at",
                "updated_at",
            ]
        )
        seq = account.autoreload_seq
    return AutoTopupClaim(
        org_id=str(org.pk),
        seq=seq,
        amount_micros=amount,
        payment_method_id=account.autoreload_payment_method_id,
        idempotency_key=f"pi:autotopup:{org.pk}:{seq}",
    )


def finish_auto_topup(
    org: Organization, *, seq: int, outcome: str, reason: str = ""
) -> None:
    from ee.usage.services.managed_credits import AutoTopupNotClaimed

    if outcome not in OUTCOMES:
        raise AutoTopupError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    with transaction.atomic():
        account = _locked(org)
        if account.autoreload_in_flight_seq != seq:
            if (
                outcome == "succeeded"
                and account.autoreload_in_flight_seq is None
                and (0 < seq <= account.autoreload_seq)
            ):
                return  # its fulfilment already cleared the claim
            raise AutoTopupNotClaimed(
                f"auto top-up {seq} is not in flight for org {org.pk} "
                f"(in flight: {account.autoreload_in_flight_seq})"
            )
        account.autoreload_in_flight_seq = None
        account.autoreload_in_flight_at = None
        changed = ["autoreload_in_flight_seq", "autoreload_in_flight_at"]
        if outcome != "succeeded":
            account.autoreload_enabled = False
            account.autoreload_disabled_reason = (reason or outcome)[:255]
            changed += ["autoreload_enabled", "autoreload_disabled_reason"]
        account.save(update_fields=[*changed, "updated_at"])


def clear_claim_on_fulfil(org: Organization, seq: int | None) -> None:
    """Called inside ``fulfil_topup``'s transaction for an auto top-up credit."""
    if seq is None:
        return
    ManagedCreditAccount.objects.filter(
        organization=org, autoreload_in_flight_seq=seq
    ).update(autoreload_in_flight_seq=None, autoreload_in_flight_at=None)
