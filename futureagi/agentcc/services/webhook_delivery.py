"""Delivery of gateway outbound webhook events.

Log ingest only records events as pending; it never delivers them. A Temporal
schedule (``agentcc-webhook-delivery``, about every 10 s) runs
``dispatch_webhook_deliveries_activity``, which starts one
``deliver_webhook_activity`` per webhook that has due events. A webhook's run
is single-flight: its workflow id is fixed and an id already running is
reused, so a slow webhook never gets two runs at once and never holds up
another webhook's.

A run delivers its webhook's due events oldest first, page after page, until
none is due or its time budget is spent. It stops at the first failed
attempt: that event backs off as before (30 s, doubling, dead-lettered after
``max_attempts``), and the webhook's other events wait, untouched, for a later
run. A failing webhook therefore costs at most one failed POST per run.

Each event is claimed before its POST by a conditional UPDATE that moves
``next_retry_at`` a lease ahead. A second run cannot claim it while the lease
holds, so no event is sent twice; if a worker dies mid-send, the event is due
again once the lease ends.
"""

import hashlib
import hmac
import json
import time
from datetime import timedelta

import requests as http_requests
import structlog
from django.db.models import Q
from django.utils import timezone

from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.url_safety import (
    WEBHOOK_PRIVATE_URL_ERROR,
    build_ssrf_safe_session,
    ensure_public_http_url,
)
from agentcc.services.webhook_hygiene import (
    cancel_events_for_webhook,
    record_delivery_result,
)
from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)

# (connect, read) seconds for one POST.
DELIVERY_TIMEOUT = (3, 10)
# Events read per page of a run.
DELIVERY_PAGE_SIZE = 100
# A run starts no new POST after this long.
DELIVERY_TIME_BUDGET_SECONDS = 60
# How long a claimed event stays out of other runs. Longer than one POST.
DELIVERY_LEASE_SECONDS = 120
# Bounds the per-webhook activity; above the budget plus one POST.
DELIVERY_ACTIVITY_TIME_LIMIT = 120
DISPATCH_ACTIVITY_TIME_LIMIT = 60


def _due(queryset, now):
    return queryset.filter(
        status=AgentccWebhookEvent.PENDING,
        deleted=False,
    ).filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))


def _backoff(attempts):
    return timedelta(seconds=30 * (2 ** (attempts - 1)))


def _claim(event_id, now):
    """Lease one due event for this run. False when another run has it."""
    return bool(
        _due(AgentccWebhookEvent.all_objects.filter(pk=event_id), now).update(
            next_retry_at=now + timedelta(seconds=DELIVERY_LEASE_SECONDS),
            updated_at=now,
        )
    )


def _signed_request(webhook, event):
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


def _record_attempt(event, response_code, error):
    """Save one attempt's outcome. Returns True when it was delivered."""
    event.last_attempt_at = timezone.now()
    event.attempts += 1
    event.last_response_code = response_code
    fields = [
        "status",
        "last_response_code",
        "last_attempt_at",
        "attempts",
        "last_error",
        "next_retry_at",
        "updated_at",
    ]
    if error is None:
        event.status = AgentccWebhookEvent.DELIVERED
        event.last_error = ""
        event.next_retry_at = None
    else:
        event.last_error = error
        if event.attempts >= event.max_attempts:
            event.status = AgentccWebhookEvent.DEAD_LETTER
            event.next_retry_at = None
        else:
            event.status = AgentccWebhookEvent.PENDING
            event.next_retry_at = timezone.now() + _backoff(event.attempts)
    event.save(update_fields=fields)
    return error is None


def _retire_events_of_unusable_webhook(webhook, webhook_id):
    """A deleted webhook's events are cancelled; an inactive one's dead-lettered."""
    if webhook is None or webhook.deleted:
        return cancel_events_for_webhook(webhook_id)
    return AgentccWebhookEvent.all_objects.filter(
        webhook_id=webhook_id,
        status=AgentccWebhookEvent.PENDING,
        deleted=False,
    ).update(
        status=AgentccWebhookEvent.DEAD_LETTER,
        next_retry_at=None,
        last_error="Webhook is inactive",
        updated_at=timezone.now(),
    )


def deliver_webhook(
    webhook_id,
    event_ids=None,
    time_budget_seconds=DELIVERY_TIME_BUDGET_SECONDS,
    page_size=DELIVERY_PAGE_SIZE,
):
    """Deliver one webhook's due events, oldest first, until one fails.

    ``event_ids`` limits the run to those events. Returns counts.
    """
    result = {"delivered": 0, "failed": 0, "retired": 0}
    webhook = AgentccWebhook.all_objects.filter(pk=webhook_id).first()
    if webhook is None or webhook.deleted or not webhook.is_active:
        result["retired"] = _retire_events_of_unusable_webhook(webhook, webhook_id)
        return result

    events = AgentccWebhookEvent.all_objects.filter(webhook_id=webhook_id)
    if event_ids is not None:
        events = events.filter(pk__in=list(event_ids))

    budget_ends = time.monotonic() + time_budget_seconds
    http = None
    try:
        while time.monotonic() < budget_ends:
            now = timezone.now()
            page = list(
                _due(events, now)
                .order_by("created_at", "pk")
                .values_list("pk", flat=True)[:page_size]
            )
            if not page:
                break
            for event_id in page:
                if time.monotonic() >= budget_ends:
                    break
                if not _claim(event_id, timezone.now()):
                    continue  # another run has it, or it is no longer due
                event = AgentccWebhookEvent.all_objects.get(pk=event_id)
                body, headers = _signed_request(webhook, event)
                response_code, error = None, None
                try:
                    ensure_public_http_url(webhook.url, WEBHOOK_PRIVATE_URL_ERROR)
                    if http is None:
                        http = build_ssrf_safe_session(WEBHOOK_PRIVATE_URL_ERROR)
                    resp = http.post(
                        webhook.url,
                        data=body,
                        headers=headers,
                        timeout=DELIVERY_TIMEOUT,
                    )
                    response_code = resp.status_code
                    if not 200 <= resp.status_code < 300:
                        error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                except (
                    http_requests.RequestException,
                    ConnectionError,
                    ValueError,
                ) as e:
                    error = str(e) or type(e).__name__

                delivered = _record_attempt(event, response_code, error)
                record_delivery_result(webhook.pk, delivered, error or "")
                if not delivered:
                    result["failed"] += 1
                    return result
                result["delivered"] += 1
    finally:
        if http is not None:
            http.close()
        logger.info(
            "webhook_events_processed",
            webhook_id=str(webhook_id),
            **result,
        )
    return result


def webhooks_with_due_events(org_id=None, event_ids=None):
    """The ids of the webhooks that have events due now."""
    events = _due(AgentccWebhookEvent.all_objects.all(), timezone.now())
    if org_id:
        events = events.filter(organization_id=org_id)
    if event_ids:
        events = events.filter(pk__in=list(event_ids))
    return list(events.order_by().values_list("webhook_id", flat=True).distinct())


def deliver_webhook_events(event_ids=None, org_id=None):
    """Deliver every due event now, one webhook after another, in this process.

    The schedule does not use this; it starts one activity per webhook. It is
    for tests and operators.
    """
    totals = {"delivered": 0, "failed": 0, "retired": 0}
    for webhook_id in webhooks_with_due_events(org_id=org_id, event_ids=event_ids):
        for key, value in deliver_webhook(webhook_id, event_ids=event_ids).items():
            totals[key] += value
    return totals


def delivery_task_id(webhook_id):
    """The fixed workflow id that makes a webhook's runs single-flight."""
    return f"agentcc-webhook-delivery-{webhook_id}"


@temporal_activity(
    max_retries=0, time_limit=DELIVERY_ACTIVITY_TIME_LIMIT, queue="default"
)
def deliver_webhook_activity(webhook_id):
    """One webhook's delivery run. A failed run leaves its events due."""
    return deliver_webhook(webhook_id)


@temporal_activity(
    max_retries=0, time_limit=DISPATCH_ACTIVITY_TIME_LIMIT, queue="default"
)
def dispatch_webhook_deliveries_activity():
    """Start a delivery run for each webhook with due events.

    A webhook whose run is still going keeps it: the id is fixed and the
    running workflow is reused, so the webhook is never sent to twice at once.
    """
    from temporalio.common import WorkflowIDConflictPolicy

    started = 0
    webhook_ids = webhooks_with_due_events()
    for webhook_id in webhook_ids:
        try:
            deliver_webhook_activity.apply_async(
                args=(str(webhook_id),),
                task_id=delivery_task_id(webhook_id),
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
            started += 1
        except Exception:
            # The next tick tries again; one webhook must not stop the others.
            logger.exception(
                "webhook_delivery_dispatch_failed", webhook_id=str(webhook_id)
            )
    return {"webhooks": len(webhook_ids), "started": started}
