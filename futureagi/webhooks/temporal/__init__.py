"""Temporal wiring for the ``webhooks`` task queue (DESIGN.md section 13.1)."""

QUEUE = "webhooks"


def get_workflows():
    """Workflows only; no Django import (the workflow sandbox loads these)."""
    from webhooks.temporal.workflows import (
        WebhooksDispatcherWorkflow,
        WebhooksHealthWorkflow,
        WebhooksRetentionWorkflow,
    )

    return [
        WebhooksDispatcherWorkflow,
        WebhooksHealthWorkflow,
        WebhooksRetentionWorkflow,
    ]


def get_activities():
    """Activities import Django."""
    from webhooks.temporal.activities import dispatch_loop, ensure_dispatchers
    from webhooks.temporal.ops_activities import health_pass, retention_pass

    return [dispatch_loop, ensure_dispatchers, health_pass, retention_pass]
