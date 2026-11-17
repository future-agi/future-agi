"""Deliver outbound webhook events.

Three callers can run delivery at the same time: log ingestion (the events it
just created), the ``agentcc-webhook-delivery`` schedule (every 30 s, the events
whose retry is due) and the console's Retry (one event, started on Temporal).
Before its POST a worker leases the event: it moves next_retry_at forward by
DELIVERY_LEASE, but only while the event is still due, so a second worker skips
it and an event is never sent twice. A worker that dies mid-delivery leaves the
event due again once the lease ends.

A failed attempt waits 30 s, then 60 s, 120 s, ... (doubling) before the next;
after max_attempts the event is dead-lettered. Redirects are not followed: a
3xx answer is recorded once as failed, with WEBHOOK_REDIRECTED, and is not
retried, since sending it again reaches the same redirect.

An event's last_error is read by every org Member, so it holds a fixed message
(delivery_error, WEBHOOK_REDIRECTED, "HTTP <status>"), never the receiver's
answer, its Location or an exception's text, which can quote a header value.
Logs also exclude those values, ciphertexts and receiver bodies.
"""

import hashlib
import hmac
import json
import re
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
from agentcc.services.webhook_credential_access import (
    WEBHOOK_CREDENTIALS_UNAVAILABLE,
    load_delivery_material,
)
from agentcc.services.webhook_credential_storage import WebhookCredentialStorageError

logger = structlog.get_logger(__name__)

# Longer than one POST (10 s timeout) plus the writes around it.
DELIVERY_LEASE = timedelta(minutes=2)
FIRST_RETRY_DELAY_SECONDS = 30

# The fixed messages a failed delivery records (and the Send test answers).
WEBHOOK_INACTIVE = "Webhook is inactive or deleted"
WEBHOOK_TIMEOUT = "The webhook URL did not answer in time."
WEBHOOK_UNREACHABLE = "Could not connect to the webhook URL."
WEBHOOK_FAILED = "The request to the webhook URL failed."
WEBHOOK_HEADERS_INVALID = (
    "The webhook's saved headers are not a JSON object: enter them again."
)
WEBHOOK_REDIRECTED = "The webhook URL answered with a redirect. Save the final URL."
_PUBLIC_ERRORS = frozenset(
    {
        WEBHOOK_INACTIVE,
        WEBHOOK_TIMEOUT,
        WEBHOOK_UNREACHABLE,
        WEBHOOK_FAILED,
        WEBHOOK_HEADERS_INVALID,
        WEBHOOK_REDIRECTED,
        WEBHOOK_CREDENTIALS_UNAVAILABLE,
        WEBHOOK_PRIVATE_URL_ERROR,
    }
)
_HTTP_STATUS = re.compile(r"HTTP (\d{3})\b")


def delivery_error(exc):
    """The fixed message for a delivery that raised ``exc``. Never the
    exception's text: requests' messages carry the URL and, for an invalid
    header (InvalidHeader), the header's value."""
    if isinstance(exc, http_requests.Timeout):
        return WEBHOOK_TIMEOUT
    if isinstance(exc, http_requests.ConnectionError):
        return WEBHOOK_UNREACHABLE
    if isinstance(exc, http_requests.RequestException):
        return WEBHOOK_FAILED
    if isinstance(exc, ConnectionError | ValueError):
        # url_safety's refusal (an internal or unresolvable address).
        return WEBHOOK_PRIVATE_URL_ERROR
    return WEBHOOK_FAILED


def is_redirect(status_code):
    """A 3xx answer: never followed, recorded once with WEBHOOK_REDIRECTED."""
    return 300 <= int(status_code) < 400


def http_status_error(status_code):
    """What a non-2xx answer records: the status code, never the body."""
    return f"HTTP {int(status_code)}"


def shown_delivery_error(text):
    """``last_error`` as a read shows it: a fixed message as saved; a value
    saved before ALERT-ACCESS-1 round 2 as "HTTP <status>" when it began with
    one (the receiver's body dropped), else WEBHOOK_FAILED. Empty stays
    empty."""
    if not text or text in _PUBLIC_ERRORS:
        return text
    status = _HTTP_STATUS.match(text)
    return http_status_error(status.group(1)) if status else WEBHOOK_FAILED


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


def _record_failure(event, now, error, response_code=None, final=False):
    """Record a failed attempt: retried later, dead-lettered once its attempts
    run out, or with ``final`` failed at once and never retried (a redirect)."""
    event.last_attempt_at = now
    event.attempts += 1
    event.last_error = error
    fields = ["status", "last_attempt_at", "attempts", "last_error", "next_retry_at"]
    if response_code is not None:
        event.last_response_code = response_code
        fields.append("last_response_code")
    if final:
        event.status = AgentccWebhookEvent.FAILED
        event.next_retry_at = None
    elif event.attempts >= event.max_attempts:
        event.status = AgentccWebhookEvent.DEAD_LETTER
        event.next_retry_at = None
    else:
        event.status = AgentccWebhookEvent.PENDING
        event.next_retry_at = now + retry_delay(event.attempts)
    event.save(update_fields=[*fields, "updated_at"])


def signed_request(payload, webhook):
    """Test sends and workers share this all-or-nothing credential/signing guard."""
    material = load_delivery_material(webhook)
    body = json.dumps(payload)
    headers = {"Content-Type": "application/json"}
    headers.update(material.headers)
    if material.secret:
        sig = hmac.new(
            material.secret.encode(),
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
        event.last_error = WEBHOOK_INACTIVE
        event.next_retry_at = None
        event.save(
            update_fields=["status", "last_error", "next_retry_at", "updated_at"]
        )
        return False

    try:
        body, headers = signed_request(event.payload, webhook)
    except WebhookCredentialStorageError:
        logger.warning(
            "webhook_delivery_credentials_unavailable",
            event_id=str(event.id),
            webhook_id=str(webhook.id),
        )
        _record_failure(event, timezone.now(), WEBHOOK_CREDENTIALS_UNAVAILABLE)
        return False
    try:
        ensure_public_http_url(webhook.url, WEBHOOK_PRIVATE_URL_ERROR)
        http = build_ssrf_safe_session(WEBHOOK_PRIVATE_URL_ERROR)
        try:
            resp = http.post(
                webhook.url,
                data=body,
                headers=headers,
                timeout=10,
                allow_redirects=False,
            )
        finally:
            http.close()
    except (http_requests.RequestException, ConnectionError, ValueError) as e:
        logger.warning(
            "webhook_delivery_failed",
            event_id=str(event.id),
            webhook_id=str(webhook.id),
            error_type=type(e).__name__,
        )
        _record_failure(event, timezone.now(), delivery_error(e))
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
    logger.warning(
        "webhook_delivery_rejected",
        event_id=str(event.id),
        webhook_id=str(webhook.id),
        status_code=resp.status_code,
    )
    if is_redirect(resp.status_code):
        _record_failure(
            event,
            now,
            WEBHOOK_REDIRECTED,
            response_code=resp.status_code,
            final=True,
        )
        return False
    _record_failure(
        event,
        now,
        http_status_error(resp.status_code),
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
            logger.warning(
                "webhook_event_delivery_error",
                event_id=str(event.id),
                error_type=type(e).__name__,
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
