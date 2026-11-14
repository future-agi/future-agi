"""Temporal schedules for gateway outbound webhook events."""

from tfc.temporal.schedules.config import ScheduleConfig

AGENTCC_WEBHOOK_SCHEDULES = [
    # Delivery runs here, never inside log ingest. SKIP: a tick that finds
    # the last one still dispatching is dropped, not queued.
    ScheduleConfig(
        schedule_id="agentcc-webhook-delivery",
        activity_name="dispatch_webhook_deliveries_activity",
        interval_seconds=10,
        queue="default",
        description="Start a delivery run for each gateway webhook with due events",
    ),
    ScheduleConfig(
        schedule_id="agentcc-webhook-event-maintenance",
        activity_name="agentcc_webhook_event_maintenance_activity",
        interval_seconds=600,
        queue="default",
        description=(
            "Cancel events of deleted gateway webhooks, expire old pending "
            "events and enforce the per-webhook pending cap"
        ),
    ),
]
