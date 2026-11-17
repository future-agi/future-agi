"""Temporal activities for durable Omega Hub operations.

Hub operations run on the platform's Temporal workers rather than a dormant
Celery worker. Each activity is deliberately idempotent: the database outbox,
lease checks, and retention commands own retry and deduplication state.
"""

from __future__ import annotations

from tfc.temporal.drop_in import temporal_activity


@temporal_activity(
    name="omega_hub.deliver_outbox",
    time_limit=120,
    queue="tasks_s",
    max_retries=0,
)
def deliver_outbox(row_id: str | None = None):
    """Deliver one committed email or drain due rows."""
    from omega_hub.services import outbox

    if row_id:
        return {"status": outbox.deliver_one(row_id)}
    return outbox.drain_due()


@temporal_activity(
    name="omega_hub.check_operations",
    time_limit=120,
    queue="tasks_s",
    max_retries=0,
)
def check_operations():
    """Reap expired checker leases and page durable operational alerts."""
    from omega_hub.services import ops

    return ops.check_and_page()


@temporal_activity(
    name="omega_hub.retention",
    time_limit=7200,
    queue="tasks_s",
    max_retries=0,
)
def run_retention():
    from django.core.management import call_command

    call_command("omega_hub_retention")
    return {"status": "ok"}


@temporal_activity(
    name="omega_hub.rules_notify",
    time_limit=120,
    queue="tasks_s",
    max_retries=0,
)
def run_rules_notify(summary: str | None = None, force: bool = False):
    from django.conf import settings
    from django.core.management import call_command

    summary = summary or getattr(settings, "OMEGA_HUB_RULES_NOTICE_SUMMARY", "")
    if not summary:
        return {"status": "skipped", "reason": "no rules notice summary configured"}
    # The schedule runs daily: once this rules version is announced, the run is a no-op.
    call_command("omega_hub_rules_notify", summary=summary, force=force, if_needed=not force)
    return {"status": "ok"}


__all__ = ["deliver_outbox", "check_operations", "run_retention", "run_rules_notify"]
