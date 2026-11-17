"""Durable Hub email delivery with retries and a dead-letter state."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from omega_hub.models import EmailOutbox, EmailOutboxStatus

logger = structlog.get_logger(__name__)

MAX_ATTEMPTS = 8
LEASE = timedelta(minutes=5)
BACKOFF_SECONDS = (0, 1, 5, 30, 120, 600, 1800, 3600)
# The eager wake-up runs in the decision request's thread (on commit). It is bounded so a slow or
# blackholed Temporal frontend costs a request at most this long, and after one failure this
# process skips eager wake-ups for the pause: the 30-second omega-hub-outbox-drain schedule
# delivers the rows anyway, so nothing waits on the request path while Temporal is down.
DISPATCH_TIMEOUT_SECONDS = 2.0
DISPATCH_PAUSE_SECONDS = 30.0
_dispatch_paused_until = 0.0


def enqueue(*, template: str, recipients: list[str], subject: str, body: str) -> EmailOutbox | None:
    """Persist one email intent and wake the worker after the surrounding commit."""
    recipients = sorted({address for address in recipients if address})
    if not recipients:
        return None
    row = EmailOutbox.objects.create(
        template=template,
        recipients=recipients,
        subject=subject,
        body=body,
    )
    # Dispatching before commit could make a worker observe an email whose decision rolled back.
    transaction.on_commit(lambda row_id=str(row.pk): dispatch(row_id))
    return row


def dispatch(row_id: str) -> bool:
    """Wake the worker for one row, never letting the ESP or Temporal set the request's latency.

    Returns whether the wake-up was sent. A row that was not woken stays pending and the drain
    schedule delivers it.
    """
    global _dispatch_paused_until
    if time.monotonic() < _dispatch_paused_until:
        return False
    try:
        from omega_hub.temporal import deliver_outbox

        deliver_outbox.apply_async(args=(row_id,), dispatch_timeout_seconds=DISPATCH_TIMEOUT_SECONDS)
        return True
    except Exception as exc:  # noqa: BLE001 - the drain schedule is the durable fallback
        _dispatch_paused_until = time.monotonic() + DISPATCH_PAUSE_SECONDS
        logger.warning("omega_hub_outbox_dispatch_failed", outbox_id=row_id, error=f"{exc.__class__.__name__}: {exc}"[:300],
                       paused_seconds=DISPATCH_PAUSE_SECONDS)
        return False


def forget_address(email: str) -> dict[str, int]:
    """Erase one person's address from the outbox (account erasure).

    Sent and dead rows that name the address are deleted: their frozen body was written to them.
    A row still to be delivered keeps its other recipients, or is deleted when none is left.
    """
    email = (email or "").strip()
    if not email:
        return {"deleted": 0, "redacted": 0}
    rows = EmailOutbox.objects.filter(recipients__contains=[email])
    deleted = redacted = 0
    with transaction.atomic():
        for row in rows.select_for_update():
            remaining = [address for address in row.recipients if address != email]
            if row.status in (EmailOutboxStatus.SENT, EmailOutboxStatus.DEAD) or not remaining:
                row.delete()
                deleted += 1
            else:
                row.recipients = remaining
                row.save(update_fields=["recipients", "updated_at"])
                redacted += 1
    return {"deleted": deleted, "redacted": redacted}


def _claim(row_id: str, now):
    with transaction.atomic():
        row = (
            EmailOutbox.objects.select_for_update()
            .filter(pk=row_id)
            .filter(
                Q(status=EmailOutboxStatus.PENDING, next_attempt_at__lte=now)
                | Q(status=EmailOutboxStatus.SENDING, last_attempt_at__lt=now - LEASE)
            )
            .first()
        )
        if row is None:
            return None
        row.status = EmailOutboxStatus.SENDING
        row.attempts += 1
        row.last_attempt_at = now
        row.save(update_fields=["status", "attempts", "last_attempt_at", "updated_at"])
        return row


def deliver_one(
    row_id: str,
    *,
    send: Callable[..., int] | None = None,
    now=None,
) -> str | None:
    """Deliver one row once; failures stay durable and are retried by the next drain."""
    now = now or timezone.now()
    row = _claim(row_id, now)
    if row is None:
        return None
    if send is None:
        from omega_hub.services.notify import _deliver_message

        send = _deliver_message
    try:
        send(
            row.subject,
            row.body,
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
            row.recipients,
            fail_silently=False,
        )
    except Exception as exc:
        error = str(exc)[:500] or exc.__class__.__name__
        if row.attempts >= MAX_ATTEMPTS:
            row.status = EmailOutboxStatus.DEAD
            row.dead_at = now
            row.next_attempt_at = now
        else:
            row.status = EmailOutboxStatus.PENDING
            row.next_attempt_at = now + timedelta(seconds=BACKOFF_SECONDS[min(row.attempts, len(BACKOFF_SECONDS) - 1)])
        row.last_error = error
        row.save(update_fields=["status", "dead_at", "next_attempt_at", "last_error", "updated_at"])
        logger.warning(
            "omega_hub_email_retry",
            outbox_id=str(row.pk),
            attempt=row.attempts,
            status=row.status,
            error=error,
        )
        return row.status
    row.status = EmailOutboxStatus.SENT
    row.sent_at = now
    row.last_error = ""
    row.save(update_fields=["status", "sent_at", "last_error", "updated_at"])
    logger.info("omega_hub_email_sent", outbox_id=str(row.pk), attempt=row.attempts)
    return row.status


def drain_due(*, limit: int = 100, now=None) -> dict[str, int]:
    """Drain due rows; the DB lock makes concurrent workers safe."""
    now = now or timezone.now()
    ids = list(
        EmailOutbox.objects.filter(
            Q(status=EmailOutboxStatus.PENDING, next_attempt_at__lte=now)
            | Q(status=EmailOutboxStatus.SENDING, last_attempt_at__lt=now - LEASE)
        )
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit]
    )
    stats = {"sent": 0, "pending": 0, "dead": 0, "skipped": 0}
    for row_id in ids:
        status = deliver_one(str(row_id), now=now)
        if status is None:
            stats["skipped"] += 1
        elif status == EmailOutboxStatus.SENT:
            stats["sent"] += 1
        elif status == EmailOutboxStatus.DEAD:
            stats["dead"] += 1
        else:
            stats["pending"] += 1
    return stats
