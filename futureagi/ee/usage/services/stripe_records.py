"""Stripe-side records the private billing code keeps in the public ledger tables.

PLAN 1.2 puts ``StripeEventLog`` and the invoice mirror (billing-ledger-read-v1
gap G4) in public ``ee/usage``; Stripe calls stay in private ee/cloud
(BL-STRIPE). These helpers take plain values and never import Stripe.

- ``record_stripe_event`` inserts one row per ``event.id`` (unique); a repeat
  returns ``(row, False)``. BL-STRIPE inserts first, answers 2xx, then
  processes (PLAN 4.3).
- ``mark_stripe_event`` moves a row to processed, failed, ignored or
  quarantined, counting attempts.
- ``upsert_invoice_mirror`` keeps one row per (mode, ``stripe_invoice_id``);
  status only moves forward (draft, open, then paid, uncollectible or void), so
  a late ``invoice.payment_failed`` never undoes a paid invoice.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

EVENT_STATUSES = ("received", "processed", "failed", "ignored", "quarantined")
INVOICE_KINDS = ("usage", "plan", "topup", "one_off", "legacy")
INVOICE_STATUSES = ("draft", "open", "paid", "uncollectible", "void")
MODES = ("test", "live")
_INVOICE_ID = re.compile(r"^in_[A-Za-z0-9]{1,250}$")
# Status never moves to a lower rank, and paid and void are final. An
# uncollectible invoice can still be paid or voided at Stripe.
_STATUS_RANK = {"draft": 0, "open": 1, "uncollectible": 2, "paid": 3, "void": 3}


class StripeRecordError(ValueError):
    """A record that cannot be stored; nothing was written."""


def _text(value: Any, name: str, *, required: bool = True, limit: int = 255) -> str:
    text = "" if value is None else str(value)
    if required and not text:
        raise StripeRecordError(f"{name} is required")
    if len(text) > limit:
        raise StripeRecordError(f"{name} is longer than {limit}")
    return text


def _mode(mode: Any) -> str:
    mode = str(getattr(mode, "value", mode) or "")
    if mode not in MODES:
        raise StripeRecordError(f"mode must be test or live, got {mode!r}")
    return mode


def record_stripe_event(
    *,
    event_id: str,
    event_type: str,
    mode: str,
    object_id: str = "",
    org: Any = None,
) -> tuple[Any, bool]:
    from ee.usage.models.billing_ledger import StripeEventLog

    event_id = _text(event_id, "event_id")
    defaults = {
        "event_type": _text(event_type, "event_type", limit=128),
        "mode": _mode(mode),
        "object_id": _text(object_id, "object_id", required=False),
        "organization": org,
    }
    try:
        with transaction.atomic():
            return StripeEventLog.objects.get_or_create(event_id=event_id, defaults=defaults)
    except IntegrityError:
        return StripeEventLog.objects.get(event_id=event_id), False


def mark_stripe_event(event_id: str, *, status: str, error: str = "") -> None:
    from django.db.models import F

    from ee.usage.models.billing_ledger import StripeEventLog

    if status not in EVENT_STATUSES or status == "received":
        raise StripeRecordError(f"status must be one of {EVENT_STATUSES[1:]}")
    updated = StripeEventLog.objects.filter(event_id=event_id).update(
        status=status,
        attempts=F("attempts") + 1,
        last_error=str(error or "")[:10_000],
        processed_at=timezone.now() if status != "failed" else None,
    )
    if not updated:
        raise StripeRecordError(f"no Stripe event {event_id!r} was recorded")


def upsert_invoice_mirror(
    org: Any,
    *,
    mode: str,
    stripe_invoice_id: str,
    kind: str,
    status: str,
    period_start: date | None,
    period_end: date | None,
    subtotal_cents: int,
    tax_cents: int,
    total_cents: int,
    amount_due_cents: int,
    hosted_invoice_url: str = "",
    invoice_pdf_url: str = "",
    stripe_created_at: datetime,
) -> tuple[Any, bool]:
    from ee.usage.models.billing_ledger import BillingInvoiceMirror

    mode = _mode(mode)
    invoice_id = _text(stripe_invoice_id, "stripe_invoice_id")
    if not _INVOICE_ID.match(invoice_id):
        raise StripeRecordError("stripe_invoice_id must be an in_ id (billing-ledger-read-v1)")
    if kind not in INVOICE_KINDS:
        raise StripeRecordError(f"kind must be one of {INVOICE_KINDS}")
    if status not in INVOICE_STATUSES:
        raise StripeRecordError(f"status must be one of {INVOICE_STATUSES}")
    for name, value in (
        ("subtotal_cents", subtotal_cents),
        ("tax_cents", tax_cents),
        ("total_cents", total_cents),
        ("amount_due_cents", amount_due_cents),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise StripeRecordError(f"{name} is an integer of cents")
    if tax_cents < 0 or amount_due_cents < 0:
        raise StripeRecordError("tax and amount due are never negative")
    for name, url in (("hosted_invoice_url", hosted_invoice_url), ("invoice_pdf_url", invoice_pdf_url)):
        if url and not str(url).startswith("https://"):
            raise StripeRecordError(f"{name} must be an https URL")
    values = {
        "organization": org,
        "kind": kind,
        "status": status,
        "period_start": period_start,
        "period_end": period_end,
        "subtotal_cents": subtotal_cents,
        "tax_cents": tax_cents,
        "total_cents": total_cents,
        "amount_due_cents": amount_due_cents,
        "hosted_invoice_url": str(hosted_invoice_url or "")[:2048],
        "invoice_pdf_url": str(invoice_pdf_url or "")[:2048],
        "stripe_created_at": stripe_created_at,
    }
    with transaction.atomic():
        row = (
            BillingInvoiceMirror.objects.select_for_update()
            .filter(mode=mode, stripe_invoice_id=invoice_id)
            .first()
        )
        if row is None:
            try:
                with transaction.atomic():
                    return (
                        BillingInvoiceMirror.objects.create(
                            mode=mode, stripe_invoice_id=invoice_id, **values
                        ),
                        True,
                    )
            except IntegrityError:
                row = BillingInvoiceMirror.objects.select_for_update().get(
                    mode=mode, stripe_invoice_id=invoice_id
                )
        if row.organization_id != getattr(org, "pk", org):
            raise StripeRecordError(f"invoice {invoice_id} belongs to another org")
        if _STATUS_RANK[status] < _STATUS_RANK[row.status] or (
            _STATUS_RANK[row.status] == 3 and status != row.status
        ):
            values["status"] = row.status  # never move back, never between finals
        for name, value in values.items():
            setattr(row, name, value)
        row.save()
        return row, False
