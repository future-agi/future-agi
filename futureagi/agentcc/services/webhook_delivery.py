import hashlib
import hmac
import json
import time
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

DELIVERY_TIMEOUT_S = 10
MIN_DELIVERY_WINDOW_S = 1.0  # don't start a delivery with less time than this left


def deliver_webhook_events(event_ids=None, org_id=None, limit=100, deadline=None):
    """Deliver pending webhook events.

    ``deadline`` is a ``time.monotonic()`` value. Each request's timeout is
    capped at the time left, and once less than MIN_DELIVERY_WINDOW_S is left
    the remaining events are left pending and untouched (no attempt counted),
    so the next delivery picks them up.
    """
    now = timezone.now()
    queryset = AgentccWebhookEvent.no_workspace_objects.filter(
        status=AgentccWebhookEvent.PENDING,
        deleted=False,
    ).filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))

    if event_ids:
        queryset = queryset.filter(id__in=event_ids)
    if org_id:
        queryset = queryset.filter(organization_id=org_id)

    all_events = list(queryset.select_related("webhook")[:limit])
    delivered = 0
    failed = 0
    deferred = 0

    for index, event in enumerate(all_events):
        timeout = DELIVERY_TIMEOUT_S
        if deadline is not None:
            left = deadline - time.monotonic()
            if left < MIN_DELIVERY_WINDOW_S:
                deferred = len(all_events) - index
                break
            timeout = min(timeout, left)

        webhook = event.webhook
        if not webhook or not webhook.is_active or webhook.deleted:
            event.status = AgentccWebhookEvent.DEAD_LETTER
            event.last_error = "Webhook is inactive or deleted"
            event.save(update_fields=["status", "last_error", "updated_at"])
            continue

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

        try:
            ensure_public_http_url(webhook.url, WEBHOOK_PRIVATE_URL_ERROR)
            http = build_ssrf_safe_session(WEBHOOK_PRIVATE_URL_ERROR)
            try:
                resp = http.post(
                    webhook.url,
                    data=body,
                    headers=headers,
                    timeout=timeout,
                )
            finally:
                http.close()

            event.last_response_code = resp.status_code
            event.last_attempt_at = timezone.now()
            event.attempts += 1

            if 200 <= resp.status_code < 300:
                event.status = AgentccWebhookEvent.DELIVERED
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
                delivered += 1
            else:
                event.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if event.attempts >= event.max_attempts:
                    event.status = AgentccWebhookEvent.DEAD_LETTER
                    event.next_retry_at = None
                else:
                    event.status = AgentccWebhookEvent.PENDING
                    backoff = timedelta(seconds=30 * (2 ** (event.attempts - 1)))
                    event.next_retry_at = timezone.now() + backoff
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
                failed += 1

        except (http_requests.RequestException, ConnectionError, ValueError) as e:
            event.last_attempt_at = timezone.now()
            event.attempts += 1
            event.last_error = str(e)
            if event.attempts >= event.max_attempts:
                event.status = AgentccWebhookEvent.DEAD_LETTER
                event.next_retry_at = None
            else:
                event.status = AgentccWebhookEvent.PENDING
                backoff = timedelta(seconds=30 * (2 ** (event.attempts - 1)))
                event.next_retry_at = timezone.now() + backoff
            event.save(
                update_fields=[
                    "status",
                    "last_attempt_at",
                    "attempts",
                    "last_error",
                    "next_retry_at",
                    "updated_at",
                ]
            )
            failed += 1
        except Exception as e:
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
        deferred=deferred,
        total=len(all_events),
        immediate=bool(event_ids),
    )
    return {"delivered": delivered, "failed": failed, "deferred": deferred}
