"""Pub/Sub drain for Cloud Marketplace lifecycle events.

Synchronous pull, not the streaming subscribe() callback: an activity has to
return, and subscribe() blocks forever.

Acks go out only after the handler commits. A failed message keeps its ack id
out of the batch, so Google redelivers it while the rest of the batch completes.
"""

import asyncio
import json

import structlog
from django.conf import settings
from django.db import close_old_connections
from temporalio import activity

from tfc.temporal.marketplace.types import DrainResult

logger = structlog.get_logger(__name__)

MAX_MESSAGES = 100
PULL_TIMEOUT_SECONDS = 30


def _subscription_path(subscriber):
    project = settings.GCP_MARKETPLACE_PROJECT_ID
    subscription = settings.GCP_MARKETPLACE_PUBSUB_SUBSCRIPTION
    if not project or not subscription:
        raise RuntimeError("GCP Marketplace Pub/Sub subscription is not configured")
    return subscriber.subscription_path(project, subscription)


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
                continue

            handled_ack_ids.append(received.ack_id)

        if handled_ack_ids:
            subscriber.acknowledge(
                request={"subscription": path, "ack_ids": handled_ack_ids}
            )

        return {
            "events_processed": len(handled_ack_ids),
            "had_events": bool(response.received_messages),
        }
    finally:
        close_old_connections()


@activity.defn(name="drain_gcp_marketplace_events_activity")
async def drain_gcp_marketplace_events_activity(input=None) -> DrainResult:
    result = await asyncio.to_thread(_drain_sync, activity.heartbeat)
    return DrainResult(
        events_processed=result["events_processed"],
        had_events=result["had_events"],
    )
