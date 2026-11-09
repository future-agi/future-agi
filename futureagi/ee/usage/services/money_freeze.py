"""The ledger's money freeze: an org with a receivable or an open dispute.

The same rule as the ee Stripe adapter's ``money_freeze_reason``
(``billing/managed_credits_checkout.py``), kept here so the ledger never
imports the cloud module. In this order:

- ``receivable_open``: the account owes, its debt bucket is above zero
  (``ledger_balance(org).debt_micros``);
- ``dispute_open``: a ``dispute_hold`` entry with no ``dispute_release`` of the
  same dispute (``stripe_ref``). A lost dispute leaves its hold standing, so
  the org stays frozen until a person decides otherwise;
- otherwise ``""``.

While frozen, the spendable balance the ledger caches and signals is 0
(``spendable``) and the gateway's granted push is capped at the settled spend
(``gateway_push.granted_value``), so the gateway admits no new spend;
``managed_credits.grant`` refuses (ruling 971bd25c) and
``auto_topup.claim_auto_topup`` never charges. The ledger's admission refusal
and the console's balance summary name the reason instead of the generic
"add credits" text. The freeze never changes a stored bucket.
"""

from __future__ import annotations

from typing import Any

from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry

RECEIVABLE_OPEN = "receivable_open"
DISPUTE_OPEN = "dispute_open"

_Kind = ManagedCreditEntry.Kind


def dispute_open(org_id: Any) -> bool:
    """True when a ``dispute_hold`` of the org has no ``dispute_release``."""
    released = ManagedCreditEntry.objects.filter(
        organization_id=org_id, kind=_Kind.DISPUTE_RELEASE
    ).values("stripe_ref")
    return (
        ManagedCreditEntry.objects.filter(
            organization_id=org_id, kind=_Kind.DISPUTE_HOLD
        )
        .exclude(stripe_ref__in=released)
        .exists()
    )


def freeze_reason_for(org_id: Any, *, debt_micros: int) -> str:
    """The freeze reason given the account's debt (read under the caller's lock)."""
    if debt_micros > 0:
        return RECEIVABLE_OPEN
    if dispute_open(org_id):
        return DISPUTE_OPEN
    return ""


def freeze_reason_of(org_id: Any) -> str:
    """``freeze_reason`` by org id (for callers holding no ``Organization``)."""
    debt = (
        ManagedCreditAccount.objects.filter(organization_id=org_id)
        .values_list("debt_micros", flat=True)
        .first()
    )
    return freeze_reason_for(org_id, debt_micros=debt or 0)


def freeze_reason(org) -> str:
    """Why new grants and spendable money are frozen for ``org``, or ``""``."""
    return freeze_reason_of(org.pk)


def spendable(balance_micros: int, frozen: bool) -> int:
    """The balance cached and signalled as spendable: 0 while frozen, never negative."""
    return 0 if frozen else max(balance_micros, 0)
