"""Delivery of gateway outbound webhook events.

Log ingest only records events as pending; it never delivers them. A Temporal
schedule (``agentcc-webhook-delivery``, about every 10 s) runs
``dispatch_webhook_deliveries_activity``, which starts a
``deliver_webhook_activity`` for each active webhook with due events. A
webhook's run is single-flight: its workflow id is fixed and an id already
running is reused, so a slow webhook never gets two runs at once and never
holds up another webhook's.

Runs share the worker's ``default`` queue with evals, experiments and alert
emails, so at most AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES are in flight at
once. The dispatcher records each run it starts as a lease on the webhook row
(``delivery_lease_until``), counts the leases, and starts no more than the
free slots allow: webhooks that are not failing first, then the one whose
oldest due event has waited longest.

A run delivers its webhook's due events oldest first, page after page, until
none is due or its time budget is spent. A failed attempt backs that event
off (30 s, doubling, dead-lettered after ``max_attempts``) and the run goes
on with the next event, so a receiver that fails now and then still gets its
events at full speed. The run stops early when the receiver looks down: an
attempt got no HTTP response (refused, timed out, or the URL is not
allowed), or ``RUN_MAX_CONSECUTIVE_FAILURES`` attempts in a row failed. The
webhook's next run then waits (``next_delivery_at``): 10 s after the first
failure of a streak, doubling to at most 5 minutes, so a dead receiver costs
one short run every few minutes, not a worker slot every tick.

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
from django.conf import settings
from django.db.models import Min, Q
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
    within_pending_age,
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
# A dispatched run that has not started within this long (the default queue
# is busy) is dropped by Temporal. It is shorter than the dispatcher's lease,
# so a run can never start after its lease, and its slot, has run out.
DELIVERY_SCHEDULE_TO_START_TIMEOUT = 90
DELIVERY_WORKFLOW_RUN_TIMEOUT = 300
DISPATCH_ACTIVITY_TIME_LIMIT = 60
# A run stops after this many failed attempts in a row.
RUN_MAX_CONSECUTIVE_FAILURES = 3
# The pause before the next run of a webhook whose run stopped on failures:
# PAUSE_BASE_SECONDS for the first failure of a streak, doubling with each
# further failure, at most PAUSE_MAX_SECONDS.
PAUSE_BASE_SECONDS = 10
PAUSE_MAX_SECONDS = 300


def _due(queryset, now):
    events = queryset.filter(
        status=AgentccWebhookEvent.PENDING,
        deleted=False,
    ).filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
    hours = settings.AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS
    if hours > 0:
        # Past its age the maintenance run expires it; never send it first.
        events = events.filter(within_pending_age(now - timedelta(hours=hours)))
    return events


def _backoff(attempts):
    return timedelta(seconds=30 * (2 ** (attempts - 1)))


def _pause(consecutive_failures):
    doublings = min(max(consecutive_failures, 1) - 1, 10)
    return timedelta(seconds=min(PAUSE_BASE_SECONDS * 2**doublings, PAUSE_MAX_SECONDS))


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
    custom = webhook.headers if isinstance(webhook.headers, dict) else {}
    # Header values must be strings; a stored number is sent as its text.
    headers.update({str(name): str(value) for name, value in custom.items()})
    # Stable per event, so a receiver can drop the repeat it gets when a worker
    # died after the POST but before recording it, and the lease ran out.
    headers["X-Agentcc-Event-Id"] = str(event.pk)
    if webhook.secret:
        sig = hmac.new(
            webhook.secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-Agentcc-Signature"] = f"sha256={sig}"
    return body, headers


def _record_attempt(event, response_code, error):
    """Save one attempt's outcome. Returns True when it was delivered.

    The write is conditional on the event still being pending: while the POST
    was in flight, deleting its webhook may have cancelled it, and a late
    result must not undo that.
    """
    now = timezone.now()
    attempts = event.attempts + 1
    values = {
        "last_attempt_at": now,
        "attempts": attempts,
        "last_response_code": response_code,
        "updated_at": now,
    }
    if error is None:
        values.update(
            status=AgentccWebhookEvent.DELIVERED, last_error="", next_retry_at=None
        )
    elif attempts >= event.max_attempts:
        values.update(
            status=AgentccWebhookEvent.DEAD_LETTER,
            last_error=error,
            next_retry_at=None,
        )
    else:
        values.update(
            status=AgentccWebhookEvent.PENDING,
            last_error=error,
            next_retry_at=now + _backoff(attempts),
        )
    AgentccWebhookEvent.all_objects.filter(
        pk=event.pk, status=AgentccWebhookEvent.PENDING
    ).update(**values)
    return error is None


def _post(http, webhook, event):
    """Send one event. Returns (http session, response code, error).

    The response code is None when the receiver gave no HTTP answer: it
    refused or timed out, or its URL is not one deliveries may go to.
    """
    try:
        body, headers = _signed_request(webhook, event)
        ensure_public_http_url(webhook.url, WEBHOOK_PRIVATE_URL_ERROR)
        if http is None:
            http = build_ssrf_safe_session(WEBHOOK_PRIVATE_URL_ERROR)
        resp = http.post(
            webhook.url,
            data=body,
            headers=headers,
            timeout=DELIVERY_TIMEOUT,
        )
    except (
        http_requests.RequestException,
        ConnectionError,
        ValueError,
        TypeError,
    ) as e:
        return http, None, str(e) or type(e).__name__
    if 200 <= resp.status_code < 300:
        return http, resp.status_code, None
    return http, resp.status_code, f"HTTP {resp.status_code}: {resp.text[:200]}"


def _usable_webhook(webhook_id):
    webhook = AgentccWebhook.all_objects.filter(pk=webhook_id).first()
    if webhook is None or webhook.deleted or not webhook.is_active:
        return None
    return webhook


def _end_run(webhook_id, paused, release_lease):
    """End a run: a run that stopped on failures pauses the next. A scheduled
    run also releases its lease; a direct call holds none, so it must not free
    the slot of a scheduled run that may be going at the same time."""
    values = {"next_delivery_at": None}
    if release_lease:
        values["delivery_lease_until"] = None
    if paused:
        streak = (
            AgentccWebhook.all_objects.filter(pk=webhook_id)
            .values_list("consecutive_failures", flat=True)
            .first()
        )
        values["next_delivery_at"] = timezone.now() + _pause(streak or 1)
    AgentccWebhook.all_objects.filter(pk=webhook_id).update(**values)
    return values["next_delivery_at"]


def deliver_webhook(
    webhook_id,
    event_ids=None,
    time_budget_seconds=DELIVERY_TIME_BUDGET_SECONDS,
    page_size=DELIVERY_PAGE_SIZE,
    release_lease=False,
):
    """Deliver one webhook's due events, oldest first.

    Stops when none is due, the budget is spent, the receiver looks down (see
    the module docstring), or the webhook was disabled. ``event_ids`` limits
    the run to those events. ``release_lease`` is for the scheduled run,
    which holds the dispatcher's lease. Returns counts.
    """
    result = {"delivered": 0, "failed": 0, "retired": 0}
    webhook = AgentccWebhook.all_objects.filter(pk=webhook_id).first()
    if webhook is None or webhook.deleted:
        result["retired"] = cancel_events_for_webhook(webhook_id)
        _end_run(webhook_id, paused=False, release_lease=release_lease)
        return result
    if not webhook.is_active:
        # Kept pending: re-enabling it resumes delivery, and the age limit
        # expires what waits too long.
        _end_run(webhook_id, paused=False, release_lease=release_lease)
        return result

    events = AgentccWebhookEvent.all_objects.filter(webhook_id=webhook_id)
    if event_ids is not None:
        events = events.filter(pk__in=list(event_ids))

    budget_ends = time.monotonic() + time_budget_seconds
    http = None
    failures_in_a_row = 0
    stopped_on_failure = False
    paused_until = None
    try:
        while time.monotonic() < budget_ends and not stopped_on_failure:
            if result["delivered"] or result["failed"]:
                # Each page sees edits made since the run began: a changed
                # URL, secret or headers, or the webhook turned off or deleted.
                webhook = _usable_webhook(webhook_id)
                if webhook is None:
                    break
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
                http, response_code, error = _post(http, webhook, event)

                delivered = _record_attempt(event, response_code, error)
                disabled = record_delivery_result(webhook.pk, delivered, error or "")
                if disabled:
                    # This failure disabled it: no further POST in this run.
                    result["failed"] += 1
                    stopped_on_failure = True
                    break
                if delivered:
                    result["delivered"] += 1
                    failures_in_a_row = 0
                    continue
                result["failed"] += 1
                failures_in_a_row += 1
                if (
                    response_code is None
                    or failures_in_a_row >= RUN_MAX_CONSECUTIVE_FAILURES
                ):
                    stopped_on_failure = True
                    break
    finally:
        if http is not None:
            http.close()
        paused_until = _end_run(
            webhook_id, paused=stopped_on_failure, release_lease=release_lease
        )
        logger.info(
            "webhook_events_processed",
            webhook_id=str(webhook_id),
            stopped_on_failure=stopped_on_failure,
            paused_until=paused_until.isoformat() if paused_until else None,
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


def webhooks_ready_for_delivery(now=None):
    """Active webhooks with due events, no run in flight and no pause, in the
    order the dispatcher serves them: webhooks that are not failing first,
    then the one whose oldest due event has waited longest."""
    now = now or timezone.now()
    rows = (
        _due(AgentccWebhookEvent.all_objects.all(), now)
        .filter(webhook__deleted=False, webhook__is_active=True)
        .filter(
            Q(webhook__delivery_lease_until__isnull=True)
            | Q(webhook__delivery_lease_until__lte=now)
        )
        .filter(
            Q(webhook__next_delivery_at__isnull=True)
            | Q(webhook__next_delivery_at__lte=now)
        )
        .order_by()
        .values("webhook_id", "webhook__consecutive_failures")
        .annotate(oldest=Min("created_at"))
    )
    rows = sorted(
        rows,
        key=lambda r: (r["webhook__consecutive_failures"] > 0, r["oldest"]),
    )
    return [row["webhook_id"] for row in rows]


def deliveries_in_flight(now=None):
    """Webhooks holding a delivery run's lease."""
    now = now or timezone.now()
    return AgentccWebhook.all_objects.filter(delivery_lease_until__gt=now).count()


def _take_delivery_lease(webhook_id, now):
    """Lease the webhook for a run, unless something changed since it was
    listed: a run took it, a run that just ended paused it, or it was turned
    off or deleted."""
    free = Q(delivery_lease_until__isnull=True) | Q(delivery_lease_until__lte=now)
    not_paused = Q(next_delivery_at__isnull=True) | Q(next_delivery_at__lte=now)
    return bool(
        AgentccWebhook.all_objects.filter(
            free, not_paused, pk=webhook_id, is_active=True, deleted=False
        ).update(
            delivery_lease_until=now + timedelta(seconds=DELIVERY_ACTIVITY_TIME_LIMIT)
        )
    )


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
    max_retries=0,
    time_limit=DELIVERY_ACTIVITY_TIME_LIMIT,
    queue="default",
    schedule_to_start_timeout=DELIVERY_SCHEDULE_TO_START_TIMEOUT,
    workflow_run_timeout=DELIVERY_WORKFLOW_RUN_TIMEOUT,
)
def deliver_webhook_activity(webhook_id):
    """One webhook's delivery run. A failed run leaves its events due."""
    # The run may have waited in the queue; its lease counts from now.
    AgentccWebhook.all_objects.filter(pk=webhook_id).update(
        delivery_lease_until=timezone.now()
        + timedelta(seconds=DELIVERY_ACTIVITY_TIME_LIMIT)
    )
    return deliver_webhook(webhook_id, release_lease=True)


@temporal_activity(
    max_retries=0, time_limit=DISPATCH_ACTIVITY_TIME_LIMIT, queue="default"
)
def dispatch_webhook_deliveries_activity():
    """Start delivery runs for webhooks with due events, within the cap.

    A webhook whose run is still going keeps it: its lease keeps it out of
    this list, and the fixed workflow id means a run that outlived its lease
    is reused, so the webhook is never sent to twice at once.
    """
    from temporalio.common import WorkflowIDConflictPolicy

    now = timezone.now()
    cap = settings.AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES
    running = deliveries_in_flight(now)
    webhook_ids = webhooks_ready_for_delivery(now)
    free = max(cap - running, 0) if cap > 0 else len(webhook_ids)
    started = 0
    for webhook_id in webhook_ids:
        if started >= free:
            break
        if not _take_delivery_lease(webhook_id, now):
            continue
        try:
            deliver_webhook_activity.apply_async(
                args=(str(webhook_id),),
                task_id=delivery_task_id(webhook_id),
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
            started += 1
        except Exception:
            # The next tick tries again; one webhook must not stop the others.
            AgentccWebhook.all_objects.filter(pk=webhook_id).update(
                delivery_lease_until=None
            )
            logger.exception(
                "webhook_delivery_dispatch_failed", webhook_id=str(webhook_id)
            )
    result = {
        "webhooks": len(webhook_ids),
        "started": started,
        "running": running,
        "waiting": len(webhook_ids) - started,
    }
    if result["waiting"]:
        logger.info("webhook_delivery_dispatch_capped", cap=cap, **result)
    return result
