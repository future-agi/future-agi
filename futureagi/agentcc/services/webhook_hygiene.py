"""Housekeeping for gateway outbound webhook events.

Deleting a webhook only soft-deletes it, so its events never cascade away;
nothing else bounds a failing webhook's backlog either. This module keeps the
event table bounded:

- a deleted webhook's undelivered events are cancelled;
- pending events expire after AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS;
- a webhook holds at most AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK pending
  events, the oldest dropped first and counted on the webhook;
- a webhook that fails AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES deliveries in
  a row is disabled, with the reason shown on the dashboard;
- ``purge_webhook_events`` deletes history an operator no longer needs.

Every bulk change runs in batches of at most ``BATCH_SIZE`` primary keys, each
in its own short transaction, and takes no table lock. Every change to a
webhook's counters is a single atomic UPDATE, so concurrent workers are safe.
"""

import time
from collections import Counter
from datetime import timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)

BATCH_SIZE = 5000
# The maintenance activity stops starting batches after this long.
MAINTENANCE_TIME_BUDGET_SECONDS = 120

# Not delivered yet, and not given up on for good.
UNDELIVERED_STATUSES = (
    AgentccWebhookEvent.PENDING,
    AgentccWebhookEvent.FAILED,
    AgentccWebhookEvent.DEAD_LETTER,
)
# Finished: safe to purge.
TERMINAL_STATUSES = (
    AgentccWebhookEvent.DEAD_LETTER,
    AgentccWebhookEvent.FAILED,
    AgentccWebhookEvent.CANCELLED,
    AgentccWebhookEvent.EXPIRED,
    AgentccWebhookEvent.DROPPED,
)


def _events():
    """Every event row, soft-deleted or not, of any webhook."""
    return AgentccWebhookEvent.all_objects


def _batched_update(queryset, budget_ends=None, **values):
    """Apply ``values`` to every row of ``queryset`` in batches by primary key.

    Each batch locks its rows (skipping rows another worker holds), so the
    rows it changes are exactly the rows it selected. Returns the rows
    changed and, per webhook, how many of them were its own.
    """
    changed = 0
    per_webhook = Counter()
    while budget_ends is None or time.monotonic() < budget_ends:
        with transaction.atomic():
            batch = list(
                queryset.select_for_update(skip_locked=True, of=("self",))
                .order_by("created_at", "pk")
                .values_list("pk", "webhook_id")[:BATCH_SIZE]
            )
            if not batch:
                break
            changed += AgentccWebhookEvent.all_objects.filter(
                pk__in=[pk for pk, _ in batch]
            ).update(updated_at=timezone.now(), **values)
        per_webhook.update(webhook_id for _, webhook_id in batch)
        if len(batch) < BATCH_SIZE:
            break
    return changed, per_webhook


def _count_drops(per_webhook, now):
    for webhook_id, count in per_webhook.items():
        AgentccWebhook.all_objects.filter(pk=webhook_id).update(
            dropped_events_total=F("dropped_events_total") + count,
            last_dropped_at=now,
        )


def cancel_events_for_webhook(webhook_id):
    """Cancel a deleted webhook's undelivered events. Returns the count."""
    count, _ = _batched_update(
        _events().filter(webhook_id=webhook_id, status__in=UNDELIVERED_STATUSES),
        status=AgentccWebhookEvent.CANCELLED,
        next_retry_at=None,
        last_error="Webhook deleted",
    )
    if count:
        logger.info(
            "agentcc_webhook_events_cancelled",
            webhook_id=str(webhook_id),
            count=count,
        )
    return count


def cancel_orphaned_events(budget_ends=None):
    """Cancel the undelivered events of every soft-deleted webhook.

    This is how an install that deleted webhooks before this release cleans
    up, with no data migration.
    """
    count, per_webhook = _batched_update(
        _events().filter(
            webhook__deleted=True,
            status__in=UNDELIVERED_STATUSES,
        ),
        budget_ends=budget_ends,
        status=AgentccWebhookEvent.CANCELLED,
        next_retry_at=None,
        last_error="Webhook deleted",
    )
    if count:
        logger.info(
            "agentcc_webhook_events_cancelled",
            reason="orphaned",
            count=count,
            webhooks=len(per_webhook),
        )
    return count


def record_delivery_result(webhook_id, ok, error=""):
    """Track a webhook's failure streak after one delivery attempt.

    A success resets the streak. A failure extends it, and the failure that
    reaches AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES disables the webhook. Each
    step is one conditional UPDATE, so of two workers that fail at once only
    one disables it. Returns True when this call disabled the webhook.
    """
    webhooks = AgentccWebhook.all_objects.filter(pk=webhook_id)
    if ok:
        webhooks.filter(consecutive_failures__gt=0).update(consecutive_failures=0)
        return False

    webhooks.update(consecutive_failures=F("consecutive_failures") + 1)
    limit = settings.AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES
    if limit <= 0:
        return False
    now = timezone.now()
    reason = (
        f"Disabled after {limit} consecutive failed deliveries; "
        f"last error: {(error or 'unknown')[:200]}"
    )
    disabled = webhooks.filter(is_active=True, consecutive_failures__gte=limit).update(
        is_active=False,
        disabled_at=now,
        disabled_reason=reason,
        updated_at=now,
    )
    if not disabled:
        return False

    # The user can Retry these once they have fixed and re-enabled it.
    moved, _ = _batched_update(
        _events().filter(
            webhook_id=webhook_id,
            status=AgentccWebhookEvent.PENDING,
            deleted=False,
        ),
        status=AgentccWebhookEvent.DEAD_LETTER,
        next_retry_at=None,
        last_error=reason,
    )
    logger.warning(
        "agentcc_webhook_auto_disabled",
        webhook_id=str(webhook_id),
        consecutive_failures=limit,
        pending_moved_to_dead_letter=moved,
        last_error=(error or "")[:200],
    )
    return True


def enforce_pending_cap(webhook_ids, budget_ends=None):
    """Drop each webhook's oldest pending events above the per-webhook cap.

    Returns the number dropped, per webhook id.
    """
    cap = settings.AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK
    dropped = {}
    if cap <= 0:
        return dropped
    now = timezone.now()
    for webhook_id in webhook_ids:
        pending = _events().filter(
            webhook_id=webhook_id,
            status=AgentccWebhookEvent.PENDING,
            deleted=False,
        )
        excess = pending.count() - cap
        total = 0
        while excess > 0 and (budget_ends is None or time.monotonic() < budget_ends):
            with transaction.atomic():
                ids = list(
                    pending.select_for_update(skip_locked=True)
                    .order_by("created_at", "pk")
                    .values_list("pk", flat=True)[: min(excess, BATCH_SIZE)]
                )
                if not ids:
                    break
                count = AgentccWebhookEvent.all_objects.filter(pk__in=ids).update(
                    status=AgentccWebhookEvent.DROPPED,
                    next_retry_at=None,
                    last_error=f"Pending cap of {cap} reached",
                    updated_at=now,
                )
            total += count
            excess -= count
        if total:
            _count_drops({webhook_id: total}, now)
            dropped[webhook_id] = total
            logger.warning(
                "agentcc_webhook_events_dropped",
                webhook_id=str(webhook_id),
                reason="cap",
                cap=cap,
                count=total,
            )
    return dropped


def webhooks_over_pending_cap():
    """The webhooks holding more pending events than the cap allows."""
    from django.db.models import Count

    cap = settings.AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK
    if cap <= 0:
        return []
    return list(
        _events()
        .filter(status=AgentccWebhookEvent.PENDING, deleted=False)
        .order_by()
        .values("webhook_id")
        .annotate(n=Count("pk"))
        .filter(n__gt=cap)
        .values_list("webhook_id", flat=True)
    )


def expire_pending_events(budget_ends=None):
    """Expire pending events older than AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS."""
    hours = settings.AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS
    if hours <= 0:
        return 0
    now = timezone.now()
    count, per_webhook = _batched_update(
        _events().filter(
            status=AgentccWebhookEvent.PENDING,
            deleted=False,
            created_at__lt=now - timedelta(hours=hours),
        ),
        budget_ends=budget_ends,
        status=AgentccWebhookEvent.EXPIRED,
        next_retry_at=None,
        last_error=f"Pending for more than {hours} hours",
    )
    if count:
        _count_drops(per_webhook, now)
        logger.warning(
            "agentcc_webhook_events_dropped",
            reason="expired",
            max_age_hours=hours,
            count=count,
            webhooks=len(per_webhook),
        )
    return count


def purge_webhook_events(
    delivered_older_than_days=None,
    terminal_older_than_days=None,
    deleted_webhooks=False,
    organization_id=None,
    batch_size=BATCH_SIZE,
    sleep_seconds=0.1,
    dry_run=False,
):
    """Hard-delete webhook events an operator no longer needs.

    - ``delivered_older_than_days``: delivered events older than that;
    - ``terminal_older_than_days``: dead_letter, failed, cancelled, expired
      and dropped events older than that;
    - ``deleted_webhooks``: every event, of any status, of a deleted webhook.

    It never deletes a pending event of a live webhook. Soft-deleted event
    rows are included. Returns the count per category; a dry run counts and
    deletes nothing.
    """
    now = timezone.now()
    events = _events()
    if organization_id:
        events = events.filter(organization_id=organization_id)

    selections = {}
    if deleted_webhooks:
        selections["deleted_webhooks"] = events.filter(webhook__deleted=True)
    if delivered_older_than_days is not None:
        selections["delivered"] = events.filter(
            webhook__deleted=False,
            status=AgentccWebhookEvent.DELIVERED,
            created_at__lt=now - timedelta(days=delivered_older_than_days),
        )
    if terminal_older_than_days is not None:
        selections["terminal"] = events.filter(
            webhook__deleted=False,
            status__in=TERMINAL_STATUSES,
            created_at__lt=now - timedelta(days=terminal_older_than_days),
        )

    counts = {}
    for category, queryset in selections.items():
        if dry_run:
            counts[category] = queryset.count()
            continue
        deleted = 0
        while True:
            ids = list(queryset.order_by().values_list("pk", flat=True)[:batch_size])
            if not ids:
                break
            with transaction.atomic():
                count, _ = AgentccWebhookEvent.all_objects.filter(pk__in=ids).delete()
            deleted += count
            if len(ids) < batch_size:
                break
            if sleep_seconds:
                time.sleep(sleep_seconds)
        counts[category] = deleted
    return counts


@temporal_activity(max_retries=0, time_limit=600, queue="default")
def agentcc_webhook_event_maintenance_activity():
    """Cancel orphaned events, expire old pending ones and enforce the cap.

    Each step stops starting batches once the time budget is spent; whatever
    is left is picked up by the next run.
    """
    budget_ends = time.monotonic() + MAINTENANCE_TIME_BUDGET_SECONDS
    cancelled = cancel_orphaned_events(budget_ends=budget_ends)
    expired = expire_pending_events(budget_ends=budget_ends)
    dropped = enforce_pending_cap(webhooks_over_pending_cap(), budget_ends=budget_ends)
    result = {
        "cancelled": cancelled,
        "expired": expired,
        "dropped": sum(dropped.values()),
    }
    logger.info("agentcc_webhook_event_maintenance", **result)
    return result
