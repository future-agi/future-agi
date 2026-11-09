"""Webhooks Temporal schedules (webhooks DESIGN.md section 13.1), on the ``webhooks`` queue the dispatcher's worker
polls (the dedicated deployment on hosted, the embedded worker on the standalone install)."""

from tfc.temporal.schedules.config import ScheduleConfig


def _workflows():
    from webhooks.temporal.workflows import (
        WebhooksHealthWorkflow,
        WebhooksRetentionWorkflow,
    )

    return WebhooksHealthWorkflow, WebhooksRetentionWorkflow


_HEALTH, _RETENTION = _workflows()

WEBHOOKS_SCHEDULES = [
    ScheduleConfig(
        schedule_id="webhooks-health",
        activity_name="webhooks_health_pass",
        interval_seconds=60,
        queue="webhooks",
        description=(
            "Webhooks health: gauges, slow-lane re-check, failing and turned-off notices, expired secrets, "
            "ops alerts, and every dispatcher loop running"
        ),
        workflow_class=_HEALTH,
    ),
    ScheduleConfig(
        schedule_id="webhooks-retention",
        activity_name="webhooks_retention_pass",
        cron_expression="30 3 * * *",
        catchup_window_seconds=6 * 3600,
        queue="webhooks",
        description="Webhooks retention: bodies 30 d, empty events 7 d, logs per org, secrets (chunked)",
        workflow_class=_RETENTION,
    ),
]
