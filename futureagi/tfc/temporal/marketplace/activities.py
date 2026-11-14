"""Pub/Sub drain for Cloud Marketplace lifecycle events.

Synchronous pull, not the streaming subscribe() callback: an activity has to
return, and subscribe() blocks forever.

Acks go out only after the handler commits. A failed message keeps its ack id
out of the batch, so Google redelivers it while the rest of the batch completes.
"""

import asyncio
import contextvars
import json
from datetime import datetime, timedelta

import structlog
from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections
from django.utils import timezone
from temporalio import activity

from tfc.temporal.marketplace.types import DrainResult

logger = structlog.get_logger(__name__)

# Sized against the activity's 5 minute start-to-close: each message can cost
# two Procurement API calls of up to 30 seconds. 100 would not fit; 20 does.
MAX_MESSAGES = 20
PULL_TIMEOUT_SECONDS = 30

# Set from here on every pull so the batch cannot outlive the deadline whatever
# the subscription was created with. Pub/Sub's default is 10 seconds, which
# expires mid-batch and redelivers messages that are still being handled.
ACK_DEADLINE_SECONDS = 600

# A message that keeps failing is dropped after this long, with its payload in
# the log, so it cannot redeliver for ever. Long enough that an outage of the
# Procurement API is a retry, not a loss; a dead-letter topic on the
# subscription, if configured, gets there first.
POISON_AFTER = timedelta(hours=24)
POISON_CACHE_PREFIX = "gcp_marketplace_event_failure"


def _subscription_path(subscriber):
    project = settings.GCP_MARKETPLACE_PROJECT_ID
    subscription = settings.GCP_MARKETPLACE_PUBSUB_SUBSCRIPTION
    if not project or not subscription:
        raise RuntimeError("GCP Marketplace Pub/Sub subscription is not configured")
    return subscriber.subscription_path(project, subscription)


def _is_configured() -> bool:
    return bool(
        settings.GCP_MARKETPLACE_PROJECT_ID
        and settings.GCP_MARKETPLACE_PUBSUB_SUBSCRIPTION
    )


def _drain_sync(heartbeat) -> dict:
    from google.cloud import pubsub_v1

    from accounts.gcp_marketplace_events import process_event

    close_old_connections()
    try:
        subscriber = pubsub_v1.SubscriberClient()
        path = _subscription_path(subscriber)

        response = subscriber.pull(
            request={"subscription": path, "max_messages": MAX_MESSAGES},
            timeout=PULL_TIMEOUT_SECONDS,
        )
        if not response.received_messages:
            return {"events_processed": 0, "had_events": False}

        subscriber.modify_ack_deadline(
            request={
                "subscription": path,
                "ack_ids": [m.ack_id for m in response.received_messages],
                "ack_deadline_seconds": ACK_DEADLINE_SECONDS,
            },
            timeout=PULL_TIMEOUT_SECONDS,
        )

        handled_ack_ids = []
        for received in response.received_messages:
            heartbeat()
            try:
                payload = json.loads(received.message.data)
            except (ValueError, TypeError):
                # Unparseable messages will never succeed. Ack so they go away
                # instead of redelivering until the dead-letter queue catches them.
                logger.exception("gcp_marketplace_message_unparseable")
                handled_ack_ids.append(received.ack_id)
                continue

            try:
                process_event(payload)
            except Exception:
                logger.exception(
                    "gcp_marketplace_event_failed",
                    event_id=payload.get("eventId"),
                    event_type=payload.get("eventType"),
                )
                if _is_poison(payload):
                    handled_ack_ids.append(received.ack_id)
                continue

            _clear_failures(payload)
            handled_ack_ids.append(received.ack_id)

        if handled_ack_ids:
            # Bounded like pull. An ack that hangs would hold the activity past
            # its heartbeat with the handlers' work already committed; a lost
            # ack only means a redelivery that the event ledger deduplicates.
            subscriber.acknowledge(
                request={"subscription": path, "ack_ids": handled_ack_ids},
                timeout=PULL_TIMEOUT_SECONDS,
            )

        return {
            "events_processed": len(handled_ack_ids),
            "had_events": bool(response.received_messages),
        }
    finally:
        close_old_connections()


def _failure_key(payload: dict) -> str | None:
    from accounts.gcp_marketplace_events import ledger_key

    event_id = payload.get("eventId")
    if not event_id:
        return None
    return f"{POISON_CACHE_PREFIX}:{ledger_key(payload.get('eventType', ''), event_id)}"


def _is_poison(payload: dict) -> bool:
    """Record this failure and say whether the message should be dropped.

    Dropped means acked and logged at error level with the payload, so a human
    can replay it with process_event once the cause is fixed. Redelivery is
    at-least-once with no ordering, so nothing behind it is held up either way;
    what a poison message costs is a failing handler and a log line every
    delivery, for ever.
    """
    key = _failure_key(payload)
    if key is None:
        return True

    now = timezone.now()
    try:
        record = cache.get(key) or {"first": now.isoformat(), "count": 0}
        record["count"] += 1
        cache.set(key, record, timeout=int(POISON_AFTER.total_seconds() * 2))
    except Exception:
        # The cache is bookkeeping, not the decision. Without it the message
        # is simply retried, which is the safe answer.
        logger.exception("gcp_marketplace_event_failure_count_unavailable")
        return False

    first = datetime.fromisoformat(record["first"])
    if now - first < POISON_AFTER:
        return False

    logger.error(
        "gcp_marketplace_event_dropped",
        event_id=payload.get("eventId"),
        event_type=payload.get("eventType"),
        failures=record["count"],
        first_failure=record["first"],
        payload=payload,
    )
    cache.delete(key)
    return True


def _clear_failures(payload: dict) -> None:
    key = _failure_key(payload)
    if not key:
        return
    try:
        cache.delete(key)
    except Exception:
        logger.exception("gcp_marketplace_event_failure_count_unavailable")


@activity.defn(name="drain_gcp_marketplace_events_activity")
async def drain_gcp_marketplace_events_activity(input=None) -> DrainResult:
    if not _is_configured():
        # Cloud-only. Elsewhere this would fail every five minutes for ever.
        return DrainResult(events_processed=0, had_events=False)

    # _drain_sync runs on a worker thread, but activity.heartbeat must run on
    # the activity's event loop: it schedules a task with asyncio.create_task,
    # which raises "no running event loop" from any other thread. Hop back to
    # the loop, carrying the activity context so heartbeat finds its activity.
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()

    def heartbeat_from_thread(*details) -> None:
        loop.call_soon_threadsafe(activity.heartbeat, *details, context=ctx)

    result = await asyncio.to_thread(_drain_sync, heartbeat_from_thread)
    return DrainResult(
        events_processed=result["events_processed"],
        had_events=result["had_events"],
    )
