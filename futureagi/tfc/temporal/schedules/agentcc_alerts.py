"""Schedules and activities for gateway email alerts and webhook deliveries.

- ``agentcc-alerts-evaluator`` (every minute): evaluates error.rate_spike,
  latency.spike and cost.threshold from each org's request logs and sends every
  alert's pending events, including the ones a cooldown, a provider failure or a
  failed Temporal start left behind (agentcc.services.alert_dispatcher).
- ``agentcc-webhook-delivery`` (every 30 s): delivers pending webhook events
  whose next_retry_at is due, with the 30 s doubling backoff
  (agentcc.services.webhook_delivery).

Two activities are started on demand, off the request thread:
``dispatch_agentcc_email_alerts_activity`` by log ingestion after it records a
request-level event, and ``deliver_agentcc_webhook_event_activity`` by the
console's Retry.
"""

import structlog

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

logger = structlog.get_logger(__name__)

# A run that overlaps the next tick is skipped (ScheduleOverlapPolicy.SKIP), so
# each limit stays under its schedule's interval plus one tick.
EVALUATOR_TIME_LIMIT = 120
DELIVERY_TIME_LIMIT = 120
# Due webhook events one schedule run delivers; the rest wait for the next run.
WEBHOOK_EVENTS_PER_RUN = 500


@temporal_activity(time_limit=EVALUATOR_TIME_LIMIT, queue="default", max_retries=0)
def evaluate_agentcc_email_alerts_activity():
    """One evaluator tick: threshold events, then every pending send."""
    from agentcc.services.alert_dispatcher import evaluate_email_alerts

    result = evaluate_email_alerts()
    logger.info("agentcc_email_alerts_evaluated", **result)
    return result


@temporal_activity(time_limit=EVALUATOR_TIME_LIMIT, queue="default", max_retries=0)
def dispatch_agentcc_email_alerts_activity(org_id):
    """Send one org's pending alert events now (started by log ingestion).

    Not retried by Temporal: a failed send stays pending and the evaluator
    schedule retries it on its next tick, after the alert's cooldown.
    """
    from agentcc.services.alert_dispatcher import dispatch_pending

    result = dispatch_pending(org_id=org_id)
    logger.info("agentcc_email_alerts_dispatched", org_id=str(org_id), **result)
    return result


@temporal_activity(time_limit=DELIVERY_TIME_LIMIT, queue="default", max_retries=0)
def deliver_agentcc_webhook_events_activity():
    """Deliver the webhook events that are due (new, or past next_retry_at)."""
    from agentcc.services.webhook_delivery import deliver_webhook_events

    return deliver_webhook_events(limit=WEBHOOK_EVENTS_PER_RUN)


@temporal_activity(time_limit=DELIVERY_TIME_LIMIT, queue="default", max_retries=0)
def deliver_agentcc_webhook_event_activity(event_id):
    """Deliver one webhook event now (the console's Retry)."""
    from agentcc.services.webhook_delivery import deliver_webhook_events

    return deliver_webhook_events(event_ids=[event_id], limit=1)


AGENTCC_ALERT_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="agentcc-alerts-evaluator",
        activity_name="evaluate_agentcc_email_alerts_activity",
        interval_seconds=60,
        queue="default",
        description=(
            "Evaluate gateway email alert thresholds (error rate, p95 latency, "
            "spend) and send pending alert emails (every minute)"
        ),
    ),
    ScheduleConfig(
        schedule_id="agentcc-webhook-delivery",
        activity_name="deliver_agentcc_webhook_events_activity",
        interval_seconds=30,
        queue="default",
        description=(
            "Deliver pending gateway webhook events whose retry is due, with "
            "backoff (every 30 s)"
        ),
    ),
]
