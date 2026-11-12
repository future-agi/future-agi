"""Keep expected outcomes that third-party loggers report at ERROR out of Sentry.

Some libraries log a handled, expected outcome at ERROR on their own logger.
Sentry's LoggingIntegration turns every such record into an issue, and a
logger's ``level``/``propagate`` in the Django LOGGING dict cannot stop it (see
the NOTE in ``tfc.logging.sentry``). This module removes only the event, chosen
by the exact name of the logger that emitted the record, never by message text.
The record still becomes a breadcrumb, so it stays visible as context on any
genuine event captured later in the same scope.

It is a global event processor rather than part of ``before_send`` so that it
applies to every client ``init_sentry`` creates (Django, Celery and Temporal all
import this package first) regardless of initialisation order.
"""

from typing import Any

# Exact logger names. Child loggers and similarly named loggers stay captured.
EXPECTED_OUTCOME_LOGGERS = frozenset(
    {
        # Django answers a request with an invalid Host header with 400 and logs
        # it here at ERROR. That is scanner/bot traffic, not a fault. The other
        # django.security.* loggers (SuspiciousOperation, SuspiciousFileOperation,
        # RequestDataTooBig, csrf, ...) stay captured.
        "django.security.DisallowedHost",
        # LiteLLM's own verbose_logger logs a provider or credential failure at
        # ERROR (for example an undecodable Vertex AI service-account key) and
        # then raises it to the caller. Our code owns that outcome: model
        # validation answers 400 and logs a WARNING, and genuine faults are
        # logged or captured from our own loggers. We register no LiteLLM
        # callbacks, so none of our code runs under this logger.
        "LiteLLM",
    }
)


def drop_expected_outcome_log_events(event: Any, hint: Any) -> Any:
    """Return None for an event created from a log record of a listed logger.

    Only events that the logging integration built from a record qualify; an
    exception captured directly, or logged from any other logger, is kept.
    """
    record = hint.get("log_record") if isinstance(hint, dict) else None
    if getattr(record, "name", None) in EXPECTED_OUTCOME_LOGGERS:
        return None
    return event


def register_expected_outcome_filter() -> None:
    """Install the processor once for every Sentry client in this process."""
    try:
        from sentry_sdk.scope import add_global_event_processor, global_event_processors
    except ImportError:  # pragma: no cover - sentry_sdk is a hard dependency
        return
    if drop_expected_outcome_log_events not in global_event_processors:
        add_global_event_processor(drop_expected_outcome_log_events)
