"""Deliver outbound webhook events.

Three callers can run delivery at the same time: log ingestion (the events it
just created), the ``agentcc-webhook-delivery`` schedule (every 30 s, the events
whose retry is due) and the console's Retry (one event, started on Temporal).
Before its POST a worker leases the event: it moves next_retry_at forward by
DELIVERY_LEASE, but only while the event is still due, so a second worker skips
it and an event is never sent twice. A worker that dies mid-delivery leaves the
event due again once the lease ends.

A failed attempt waits 30 s, then 60 s, 120 s, ... (doubling) before the next;
after max_attempts the event is dead-lettered.
"""

import hashlib
import hmac
import json
from datetime import timedelta

import requests as http_requests
import structlog
from django.db.models import Q
from django.utils import timezone

from agentcc.models.webhook import AgentccWebhookEvent
from agentcc.services.url_safety import (
    WEBHOOK_PRIVATE_URL_ERROR,
    build_ssrf_safe_session,
    ensure_public_http_url,
)

logger = structlog.get_logger(__name__)

# Longer than one POST (10 s timeout) plus the writes around it.
DELIVERY_LEASE = timedelta(minutes=2)
FIRST_RETRY_DELAY_SECONDS = 30


def retry_delay(attempts):
    """The wait after the ``attempts``-th failed attempt: 30 s, doubling."""
    return timedelta(seconds=FIRST_RETRY_DELAY_SECONDS * (2 ** max(attempts - 1, 0)))


def _due(now):
    return Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)


def _lease(event, now):
    """Take the event for this worker; False when another worker has it."""
    taken = (
        AgentccWebhookEvent.no_workspace_objects.filter(
            id=event.id, status=AgentccWebhookEvent.PENDING
        )
        .filter(_due(now))
        .update(next_retry_at=now + DELIVERY_LEASE)
    )
    return taken == 1


def _record_failure(event, now, error, response_code=None):
    event.last_attempt_at = now
    event.attempts += 1
    event.last_error = error
    fields = ["status", "last_attempt_at", "attempts", "last_error", "next_retry_at"]
    if response_code is not None:
        event.last_response_code = response_code
        fields.append("last_response_code")
    if event.attempts >= event.max_attempts:
        event.status = AgentccWebhookEvent.DEAD_LETTER
        event.next_retry_at = None
    else:
        event.status = AgentccWebhookEvent.PENDING
        event.next_retry_at = now + retry_delay(event.attempts)
    event.save(update_fields=[*fields, "updated_at"])


def _signed_request(event, webhook):
    body = json.dumps(event.payload)
    headers = {"Content-Type": "application/json"}
    headers.update(webhook.headers or {})
    if webhook.secret:
        sig = hmac.new(
            webhook.secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-Agentcc-Signature"] = f"sha256={sig}"
    return body, headers


def _deliver_one(event):
    """POST one leased event; True when delivered."""
    webhook = event.webhook
    if not webhook or not webhook.is_active or webhook.deleted:
        event.status = AgentccWebhookEvent.DEAD_LETTER
        event.last_error = "Webhook is inactive or deleted"
        event.next_retry_at = None
        event.save(
            update_fields=["status", "last_error", "next_retry_at", "updated_at"]
        )
        return False

    body, headers = _signed_request(event, webhook)
    try:
        ensure_public_http_url(webhook.url, WEBHOOK_PRIVATE_URL_ERROR)
        http = build_ssrf_safe_session(WEBHOOK_PRIVATE_URL_ERROR)
        try:
            resp = http.post(webhook.url, data=body, headers=headers, timeout=10)
        finally:
            http.close()
    except (http_requests.RequestException, ConnectionError, ValueError) as e:
        _record_failure(event, timezone.now(), str(e))
        return False

    now = timezone.now()
    if 200 <= resp.status_code < 300:
        event.status = AgentccWebhookEvent.DELIVERED
        event.last_response_code = resp.status_code
        event.last_attempt_at = now
        event.attempts += 1
        event.last_error = ""
        event.next_retry_at = None
        event.save(
            update_fields=[
                "status",
                "last_response_code",
                "last_attempt_at",
                "attempts",
                "last_error",
                "next_retry_at",
                "updated_at",
            ]
        )
        return True
    _record_failure(
        event,
        now,
        f"HTTP {resp.status_code}: {resp.text[:200]}",
        response_code=resp.status_code,
    )
    return False


def deliver_webhook_events(event_ids=None, org_id=None, limit=100):
    """Deliver the pending events that are due; returns {delivered, failed}.

    An event another worker is delivering (leased) is skipped and not counted.
    """
    now = timezone.now()
    queryset = AgentccWebhookEvent.no_workspace_objects.filter(
        status=AgentccWebhookEvent.PENDING,
        deleted=False,
    ).filter(_due(now))

    if event_ids:
        queryset = queryset.filter(id__in=event_ids)
    if org_id:
        queryset = queryset.filter(organization_id=org_id)

    candidates = list(queryset.select_related("webhook").order_by("created_at")[:limit])
    delivered = 0
    failed = 0
    attempted = 0

    for event in candidates:
        if not _lease(event, timezone.now()):
            continue
        attempted += 1
        try:
            if _deliver_one(event):
                delivered += 1
            else:
                failed += 1
        except Exception as e:
            # The lease ends on its own, so the event is retried after it.
            logger.exception(
                "webhook_event_delivery_error",
                event_id=str(event.id),
                error=str(e),
            )
            failed += 1

    logger.info(
        "webhook_events_processed",
        delivered=delivered,
        failed=failed,
        total=attempted,
        immediate=bool(event_ids),
    )
    return {"delivered": delivered, "failed": failed}
