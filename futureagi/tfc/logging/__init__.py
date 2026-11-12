"""
FutureAGI Structured Logging System

A production-grade logging system with:
- Request tracing via request_id (correlation ID)
- User/org context: user_id, user_email, organization_id, workspace_id
- OpenTelemetry integration: trace_id, span_id
- JSON output in production, colored console in development

Usage:
    import structlog
    logger = structlog.get_logger(__name__)

    # Simple event logging
    logger.info("user_login", method="oauth", provider="google")

    # With additional context
    logger.info("evaluation_started",
        eval_id=eval_id,
        model="gpt-4",
        dataset_size=1000,
    )

    # Error with exception
    try:
        risky_operation()
    except Exception:
        logger.exception("operation_failed", operation="risky")

Context fields are automatically added:
- request_id: Unique ID per HTTP request
- user_id: Authenticated user ID
- user_email: User's email
- organization_id: User's organization
- workspace_id: From X-Workspace-Id header
- trace_id, span_id: OpenTelemetry trace context
"""

# Import signals module to register signal handlers
# This ensures the bind_user_and_org_context receiver is registered
from . import signals  # noqa: F401
from .config import configure_structlog, get_logging_config
from .processors import add_otel_context, add_pid_and_tid, add_region_context
from .sentry import (
    SENTRY_ENABLED,
    add_breadcrumb,
    capture_exception_with_context,
    capture_message,
    init_sentry,
    set_user_context,
    start_transaction,
)

__all__ = [
    "configure_structlog",
    "get_logging_config",
    "add_otel_context",
    "add_pid_and_tid",
    "add_region_context",
    "init_sentry",
    "set_user_context",
    "capture_exception_with_context",
    "capture_message",
    "add_breadcrumb",
    "start_transaction",
    "SENTRY_ENABLED",
]
