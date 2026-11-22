"""
Temporal-specific structured logging.

Usage in workflows:
    from tfc.logging.temporal import get_logger, bind_context

    logger = get_logger(__name__)

    @workflow.defn
    class MyWorkflow:
        @workflow.run
        async def run(self, input: Input):
            bind_context(
                user_id=input.user_id,
                organization_id=input.organization_id,
            )
            logger.info("workflow_started", param=input.param)

Usage in activities:
    @activity.defn
    async def my_activity():
        # Use async logging to avoid blocking event loop
        await logger.ainfo("activity_running", step=1)
"""

import os

import structlog
import temporalio.activity
import temporalio.workflow

from ..processors import add_otel_context, add_pid_and_tid
from ..sentry import with_sentry_log_fields
from .context import get_temporal_activity_context, get_temporal_workflow_context


def merge_temporal_context(logger, method_name: str, event_dict: dict) -> dict:
    """
    Merge Temporal workflow/activity context into log entry.

    This processor automatically adds workflow_id, activity_id,
    attempt number, task_queue, etc. to all logs.
    """
    if temporalio.activity.in_activity():
        ctx = get_temporal_activity_context()
        for k, v in ctx.items():
            event_dict.setdefault(k, v)
    elif temporalio.workflow.in_workflow():
        ctx = get_temporal_workflow_context()
        for k, v in ctx.items():
            event_dict.setdefault(k, v)
    return event_dict


def is_production() -> bool:
    """Check if running in production environment."""
    return os.getenv("ENV_TYPE", "local") in ("staging", "prod", "production")


def configure_temporal_logging():
    """
    Configure structlog for Temporal workers.

    Call this at Temporal worker startup, before processing any workflows.

    Example:
        from tfc.logging.temporal import configure_temporal_logging

        configure_temporal_logging()

        worker = Worker(
            client,
            task_queue="my-queue",
            workflows=[MyWorkflow],
            activities=[my_activity],
        )
        await worker.run()
    """
    processors = [
        structlog.stdlib.add_log_level,
        structlog.contextvars.merge_contextvars,
        merge_temporal_context,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
        add_pid_and_tid,
        structlog.processors.CallsiteParameterAdder(
            {
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            },
            additional_ignores=["tfc.logging.temporal"],
        ),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        add_otel_context,
    ]

    renderer = (
        structlog.processors.JSONRenderer()
        if is_production()
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    processors.append(with_sentry_log_fields(renderer))

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = None):
    """
    Get a structured logger for Temporal workflows/activities.

    Args:
        name: Logger name (typically __name__)

    Returns:
        structlog BoundLogger
    """
    return structlog.get_logger(name)


def bind_context(**kwargs):
    """
    Bind context variables for the current workflow/activity.

    Typically called at workflow start to bind user_id, organization_id, etc.
    These values will be included in all subsequent logs.

    Example:
        bind_context(
            user_id="123",
            user_email="user@example.com",
            organization_id="org_456",
            workspace_id="ws_789",
        )
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context():
    """Clear all bound context variables."""
    structlog.contextvars.clear_contextvars()
