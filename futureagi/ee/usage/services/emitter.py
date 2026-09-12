"""Usage event emitter — the ONLY way to record usage.

Usage:
    from ee.usage.schemas.event_types import BillingEventType
    from ee.usage.schemas.events import UsageEvent
    from ee.usage.services.emitter import emit

    emit(UsageEvent(org_id="org-123", event_type=BillingEventType.TURING_LARGE_EVALUATOR))

Fire-and-forget. Non-blocking (~0.5ms). On Redis failure, logs but does NOT raise.
"""

from __future__ import annotations

from typing import Optional

import redis
import structlog
from django.conf import settings
from ee.usage.schemas.events import UsageEvent
from tfc.logging.sentry import capture_message

logger = structlog.get_logger(__name__)

STREAM_KEY = "usage:events"
STREAM_MAXLEN = 1_000_000

_redis_client: Optional[redis.Redis] = None
_consumer_started: bool = False


def get_redis() -> redis.Redis:
    """Get or create the Redis client singleton."""
    global _redis_client
    if _redis_client is None:
        redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        _redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
    return _redis_client


def _start_consumer_best_effort() -> None:
    # Lazy-start the consumer workflow on first emit (singleton, non-blocking)
    global _consumer_started
    if not _consumer_started:
        try:
            from ee.cloud.temporal.workflows import UsageConsumerWorkflow
            from temporalio.common import WorkflowIDConflictPolicy
            from tfc.temporal.common.client import (
                _run_async_in_sync_context,
                get_client_sync,
            )

            client = get_client_sync()
            _run_async_in_sync_context(
                lambda: client.start_workflow(
                    UsageConsumerWorkflow.run,
                    None,  # No initial state — workflow uses defaults
                    id="usage-consumer-singleton",
                    task_queue="default",
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                )
            )
            _consumer_started = True
        except Exception:
            logger.debug("usage_consumer_start_deferred")


def _stream_data(event: UsageEvent) -> dict[str, str]:
    data = {}
    dumped = event.model_dump(mode="json")
    for key, value in dumped.items():
        if isinstance(value, dict):
            import json

            data[key] = json.dumps(value)
        elif value is not None:
            data[key] = str(value)
    return data


def emit_confirmed(event: UsageEvent) -> str | bytes:
    """Enqueue an event and propagate Redis failures to durable callers.

    Callers must persist a deterministic ``event_id`` before invoking this.
    A crash after ``XADD`` may enqueue the same ID again; the usage consumer is
    responsible for idempotency. Ordinary request paths should keep using
    :func:`emit`, whose fire-and-forget behavior is unchanged.
    """
    _start_consumer_best_effort()
    return get_redis().xadd(
        STREAM_KEY,
        _stream_data(event),
        maxlen=STREAM_MAXLEN,
    )


def emit(event: UsageEvent) -> None:
    """Emit a usage event to the Redis Stream.

    This is the default fire-and-forget path. On Redis failure it logs but does
    not raise, so user actions are never failed by metering.
    """
    try:
        emit_confirmed(event)
    except Exception:
        # Fire-and-forget billing: a failure here is permanently lost usage,
        # otherwise invisible. The structured log already reaches Sentry via the
        # logging integration, but stack-grouped — add an explicit, stable
        # capture tagged ``emit_failed_total`` so lost billing is a first-class
        # alert target. Sentry is HTTP, so it fires even when a Redis outage is
        # the cause; capture_message no-ops when Sentry is off and never raises.
        logger.exception(
            "usage_event_emit_failed",
            event_type=event.event_type,
            org_id=event.org_id,
            amount=event.amount,
        )
        capture_message(
            "usage_event_emit_failed",
            level="error",
            tags={"alarm": "emit_failed_total", "event_type": str(event.event_type)},
            context={"billing": {"org_id": event.org_id, "amount": event.amount}},
        )
