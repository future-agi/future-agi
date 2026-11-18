"""
Centralized Sentry configuration for all backend components.

Keep Sentry and structlog separate.
Sentry SDK integrations handle error capture automatically.

Usage:
    from tfc.logging.sentry import init_sentry

    # In Django settings.py
    init_sentry(component="django")

    # In celery.py
    init_sentry(component="celery")

    # In Temporal worker startup
    init_sentry(component="temporal", tags={"queue": "tasks_l"})
"""

import contextlib
import os
import sys
from collections.abc import Callable
from typing import Any

from tfc.logging.sentry_scrub import (
    _SDK_MAX_VALUE_LENGTH,
    SENSITIVE_KEY_SUBSTRINGS,
    _budgeted,
    _cap_strings,
    _scrub_event,
    _scrub_span,
)

# Environment detection
ENV_TYPE = os.getenv("ENV_TYPE", "local")
IS_PRODUCTION = ENV_TYPE in ("prod", "production")
IS_STAGING = ENV_TYPE == "staging"

# Sentry is enabled in staging/prod by default. An explicit SENTRY_ENABLED env
# var (also read by settings.py) always wins so the two never diverge.
_sentry_enabled_override = os.getenv("SENTRY_ENABLED")
if _sentry_enabled_override not in (None, ""):
    SENTRY_ENABLED = _sentry_enabled_override.lower() == "true"
else:
    SENTRY_ENABLED = ENV_TYPE in ("staging", "prod", "production")
SENTRY_DSN = os.getenv("SENTRY_DSN")

# Noise control
# Loggers whose records must NEVER become Sentry issues. These are pure
# infrastructure/SDK loggers that emit ERROR records for transient, expected
# conditions (e.g. the OTel exporter failing to reach its collector). Left
# unchecked, a single unreachable collector produced tens of millions of
# identical events. Real application exceptions never originate here.
#
# NOTE: Sentry's LoggingIntegration patches logging.Logger.callHandlers, so a
# logger's `propagate=False`/`level` in Django's LOGGING dict does NOT stop it
# from becoming an event. ignore_logger() (below) and the before_send prefix
# check are the only reliable levers.
NOISY_LOGGER_PREFIXES = (
    "opentelemetry",  # OTLP exporter/instrumentation export failures
    "saml2",
    "xmlschema",
)

# Exact logger names handed to sentry_sdk.integrations.logging.ignore_logger.
# Children are covered by the before_send prefix check as a backstop.
IGNORED_LOGGERS = (
    "opentelemetry",
    "opentelemetry.sdk.metrics._internal.export",
    "opentelemetry.sdk.trace.export",
    "opentelemetry.exporter.otlp.proto.grpc.exporter",
    "opentelemetry.exporter.otlp.proto.http.metric_exporter",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.exporter.otlp.proto.http._log_exporter",
    "saml2",
    "saml2.*",
    "xmlschema",
    "xmlschema.*",
)

# Exception class names that are expected/handled and not actionable as issues.
IGNORED_EXCEPTIONS = frozenset(
    {
        "DisallowedHost",  # Django - invalid host header (scanner/bot traffic)
        "SuspiciousOperation",  # Django - security-related, handled
        "ConnectionResetError",  # Client/peer disconnected mid-request
        "BrokenPipeError",  # Client disconnected mid-response
    }
)

# Substrings of structlog/event messages that represent expected, non-actionable
# conditions. Kept deliberately specific so real bugs are never hidden.
IGNORED_MESSAGE_SUBSTRINGS = (
    "trace_payload_not_found_in_redis",  # expected ingestion race (payload TTL)
    "Failed to export traces",  # OTel exporter (backstop for logger check)
    "Exception while exporting metrics",  # OTel exporter (backstop)
    # litellm's internal 'LiteLLM' logger emits this at ERROR when a user
    # supplies an invalid/partial Vertex AI service-account key during model
    # validation. Expected user-input error, not actionable. CORE-BACKEND-119Y.
    "Failed to load vertex credentials",
)

# Sample rates - configurable via env, lower in production to reduce costs
# Production: 10% sampling, Staging: 100% sampling
TRACES_SAMPLE_RATE = float(
    os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1" if IS_PRODUCTION else "1.0")
)
PROFILES_SAMPLE_RATE = float(
    os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1" if IS_PRODUCTION else "1.0")
)

_initialized_components: set = set()


def _event_scrubber() -> Any:
    """The denylist scrubber installed by init_sentry.

    It redacts values under denylisted names in the request, extras,
    breadcrumbs, spans and stack-frame local variables, but it matches whole
    names only (case-insensitively), not the substrings `_scrub` matches.
    """
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

    denylist = list(DEFAULT_DENYLIST) + [
        k for k in SENSITIVE_KEY_SUBSTRINGS if k not in DEFAULT_DENYLIST
    ]
    return EventScrubber(denylist=denylist, recursive=True)


def _event_message(event: dict) -> str:
    """Best-effort extraction of an event's human-readable message."""
    msg = event.get("message")
    if isinstance(msg, dict):
        msg = msg.get("formatted") or msg.get("message") or ""
    logentry = event.get("logentry")
    logentry_msg = logentry.get("message", "") if isinstance(logentry, dict) else ""
    parts = [str(msg or ""), str(logentry_msg or "")]
    return " ".join(p for p in parts if p)


def _is_telemetry_url(url: object) -> bool:
    """True when ``url`` matches a path prefix whose body must never reach Sentry.

    Reuses ``SENSITIVE_BODY_PATH_PREFIXES`` (as its patterns) so the OTel
    middleware and the Sentry scrubber agree on which paths are sensitive;
    previously the prefix was hardcoded here and could silently drift from the
    middleware copy.
    """
    if not url:
        return False
    from tfc.telemetry.middleware import SENSITIVE_BODY_PATHS

    return any(pattern.search(str(url)) for pattern in SENSITIVE_BODY_PATHS)


def _scrub_deployment_telemetry_event(event: dict) -> dict:
    # Sentry's HTTP integrations record outbound POSTs to /telemetry/ as
    # breadcrumbs with the request body in ``data.body``/``data.data``. The
    # inbound ``request.url`` check below would not match those (the exception
    # frame's URL is whatever the caller hit, not the breadcrumb's target), so
    # the registration payload would still ship to Sentry via breadcrumbs even
    # though the inbound branch scrubbed the request body. Walk breadcrumbs
    # unconditionally and strip body fields whose ``url`` points at a telemetry
    # path before running the inbound scrub.
    breadcrumbs = event.get("breadcrumbs") or []
    # Sentry SDK versions do not agree on the normalized breadcrumb shape:
    # error events commonly expose {"values": [...]}, while transaction
    # events can already expose the values list.  The scrubber runs inside
    # ``before_send_transaction`` and must never turn a successful request
    # into a 500 merely because the SDK chose the latter representation.
    if isinstance(breadcrumbs, dict):
        breadcrumbs = breadcrumbs.get("values") or []
    if not isinstance(breadcrumbs, (list, tuple)):
        breadcrumbs = []

    for breadcrumb in breadcrumbs:
        if not isinstance(breadcrumb, dict):
            continue
        data = breadcrumb.get("data") or {}
        if not isinstance(data, dict):
            continue
        if _is_telemetry_url(data.get("url")):
            for key in ("body", "data", "http.request.body", "request_body"):
                data.pop(key, None)
            breadcrumb["data"] = data

    request = event.get("request") or {}
    if not _is_telemetry_url(request.get("url")):
        return event

    request.pop("data", None)
    for exception in (event.get("exception") or {}).get("values", []):
        for frame in (exception.get("stacktrace") or {}).get("frames", []):
            frame.pop("vars", None)
    return event


def _report_scrub_failure(exc: Exception) -> None:
    """Say that a scrub failed, by exception class only. It does not go through
    the logging module: Python 3.13.4+ drops a record logged while another record
    on the same thread is being handled, and a hook runs inside the handler of
    the record that made the event. It goes to the process's own stderr: in a
    Celery worker `sys.stderr` is a proxy that logs what it is given.

    Only the ways the write can fail are quiet: a closed pipe (OSError), a closed
    file (ValueError), or no stream at all (AttributeError on None). Anything else
    is a bug here and surfaces."""
    with contextlib.suppress(OSError, ValueError, AttributeError):
        stream = sys.__stderr__ if sys.__stderr__ is not None else sys.stderr
        stream.write(f"sentry_scrub_failed: {type(exc).__name__}\n")
        stream.flush()


def _get_before_send() -> Callable:
    """
    Create the before_send hook that runs on every error event.

    Responsibilities, in order:
    1. Drop pure-infrastructure logger noise (OTel exporter, etc.).
    2. Drop expected/handled exceptions and expected 4xx responses.
    3. Drop known expected log conditions matched by message substring.
    4. Scrub secrets/PII from whatever remains.
    """

    def before_send(event: dict, hint: dict) -> dict | None:
        # Fail closed: if anything raises, the event is dropped, never sent raw.
        try:
            return _before_send(event, hint)
        except Exception as exc:  # noqa: BLE001 - fail closed: drop the event, never send it raw
            _report_scrub_failure(exc)
            return None

    def _before_send(event: dict, hint: dict) -> dict | None:
        event = _scrub_deployment_telemetry_event(event)

        # 1. Infrastructure logger noise - never actionable as an issue.
        logger_name = event.get("logger") or ""
        # Match on a dot boundary: "saml2" must not swallow "saml2_auth".
        if isinstance(logger_name, str) and any(
            logger_name == p or logger_name.startswith(f"{p}.")
            for p in NOISY_LOGGER_PREFIXES
        ):
            return None

        # 2. Expected/handled exceptions and expected client (4xx) errors.
        if "exc_info" in hint:
            exc_type, exc_value, _tb = hint["exc_info"]
            exc_name = exc_type.__name__ if exc_type else ""
            if exc_name in IGNORED_EXCEPTIONS:
                return None

            status_code = getattr(exc_value, "status_code", None)
            if (
                isinstance(status_code, int)
                and 400 <= status_code < 500
                and status_code not in (401, 403)
            ):
                # Drop 400/404/405/429/etc. (client behaviour); keep 401/403
                # for security monitoring.
                return None

        # 3. Known expected conditions identified by message text.
        message = _event_message(event)
        if message and any(s in message for s in IGNORED_MESSAGE_SUBSTRINGS):
            return None

        # 4. Scrub secrets/PII from the surviving event.
        with _budgeted():
            _scrub_event(event)
        return _cap_strings(event)

    return before_send


def _get_before_send_transaction() -> Callable:
    """The before_send_transaction hook: the same scrub as error events, plus
    every span's data (an outbound call's `http.query` lands there)."""

    def before_send_transaction(event: dict, hint: dict) -> dict | None:
        try:
            event = _scrub_deployment_telemetry_event(event)
            with _budgeted():
                _scrub_event(event)
                for span in event.get("spans") or []:
                    if isinstance(span, dict):
                        _scrub_span(span)
            return _cap_strings(event)
        except Exception as exc:  # noqa: BLE001 - fail closed: drop the event, never send it raw
            _report_scrub_failure(exc)
            return None

    return before_send_transaction


def _get_traces_sampler() -> Callable:
    """
    Create a traces_sampler function for intelligent sampling.

    This allows different sample rates for different transaction types.
    """

    def traces_sampler(sampling_context: dict) -> float:
        # Get the transaction context
        transaction_context = sampling_context.get("transaction_context", {})
        transaction_name = transaction_context.get("name", "")
        op = transaction_context.get("op", "")

        # Always sample error-related transactions
        if "error" in transaction_name.lower():
            return 1.0

        # Lower sample rate for health checks and static files
        if any(
            path in transaction_name
            for path in ["/health", "/ready", "/metrics", "/static", "/favicon"]
        ):
            return 0.0  # Don't trace health checks

        # Temporal workflow and activity transactions - use configured rate
        if op.startswith("temporal."):
            return TRACES_SAMPLE_RATE

        # Higher sample rate for API endpoints
        if "/api/" in transaction_name:
            return TRACES_SAMPLE_RATE

        # Default sample rate
        return TRACES_SAMPLE_RATE

    return traces_sampler


def init_sentry(
    component: str = "django",
    tags: dict | None = None,
) -> bool:
    """
    Initialize Sentry for a specific component.

    Args:
        component: Component name ("django", "celery", "temporal")
        tags: Additional tags to add to all events from this component

    Returns:
        True if Sentry was initialized, False otherwise
    """
    global _initialized_components

    # Prevent double initialization
    if component in _initialized_components:
        return True

    if not SENTRY_ENABLED or not SENTRY_DSN:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.httpx import HttpxIntegration
        from sentry_sdk.integrations.logging import (
            LoggingIntegration,
            ignore_logger,
        )

        # Suppress pure-infrastructure loggers at the source. This is the
        # single most important noise lever: it stops the OTel exporter's
        # transient "failed to reach collector" ERRORs from becoming issues.
        for logger_name in IGNORED_LOGGERS:
            ignore_logger(logger_name)

        import logging as _logging

        # Level at/above which a log record becomes a Sentry event. ERROR by
        # default; overridable via SENTRY_LOG_LEVEL (e.g. WARNING, CRITICAL).
        _event_level = getattr(
            _logging, os.getenv("SENTRY_LOG_LEVEL", "ERROR").upper(), _logging.ERROR
        )

        integrations = [
            # The ONLY path that turns ERROR logs into Sentry events. The
            # redundant Django EventHandler was removed from logging/config.py
            # so events are captured exactly once and Temporal/Celery (which do
            # not use the Django LOGGING dict) are covered too.
            LoggingIntegration(
                level=_logging.INFO,  # capture INFO+ as breadcrumbs
                event_level=_event_level,  # send ERROR+ as events
            ),
            HttpxIntegration(),  # Track outgoing HTTP requests via httpx
        ]

        # Add component-specific integrations
        if component == "django":
            import django.db.models.signals
            from sentry_sdk.integrations.django import DjangoIntegration

            integrations.append(
                DjangoIntegration(
                    transaction_style="url",
                    middleware_spans=True,
                    signals_spans=True,
                    signals_denylist=[
                        django.db.models.signals.pre_init,
                        django.db.models.signals.post_init,
                    ],
                    cache_spans=True,
                    http_methods_to_capture=("GET", "POST", "PUT", "DELETE", "PATCH"),
                )
            )

        if component in ("django", "celery"):
            from sentry_sdk.integrations.celery import CeleryIntegration

            integrations.append(
                CeleryIntegration(
                    monitor_beat_tasks=True,
                    propagate_traces=True,
                )
            )

        # Build default tags
        default_tags = {"component": component}
        if tags:
            default_tags.update(tags)

        # Defence-in-depth scrubbing: even with send_default_pii on, redact a
        # broad denylist of secret-bearing keys recursively before send.
        event_scrubber = _event_scrubber()

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=integrations,
            # Environment and release
            environment=ENV_TYPE,
            release=os.getenv("APP_VERSION", os.getenv("GIT_SHA", "unknown")),
            # Tracing configuration
            enable_tracing=True,
            traces_sampler=_get_traces_sampler(),
            # Profiling configuration
            profiles_sample_rate=PROFILES_SAMPLE_RATE,
            # Error capture configuration. PII is captured for debugging but
            # passed through the scrubber + before_send redaction below.
            send_default_pii=True,
            event_scrubber=event_scrubber,
            # "medium" caps bodies at ~10KB: enough to debug, small enough that
            # large uploads/payloads don't dominate the event volume or leak.
            max_request_body_size="medium",
            max_breadcrumbs=100,
            # Bounds each captured string, and so the cost of scrubbing it. Kept
            # far above MAX_EVENT_STRING_LENGTH (8192): the hooks scrub strings
            # whole, then cut each to that limit.
            max_value_length=_SDK_MAX_VALUE_LENGTH,
            attach_stacktrace=True,  # Attach stack traces to all messages
            include_source_context=True,  # Include source code context
            include_local_variables=True,  # Capture local variables for debugging
            # In-app marking for better stack traces
            in_app_include=[
                "tfc",
                "accounts",
                "agentic_eval",
                "simulate",
                "tracer",
                "prompts",
            ],
            in_app_exclude=["celery", "kombu", "temporalio", "django"],
            # Hooks
            before_send=_get_before_send(),
            before_send_transaction=_get_before_send_transaction(),
            # Debug mode in staging for troubleshooting
            debug=IS_STAGING,
            # Session tracking
            auto_session_tracking=True,
            # DB query source tracking
            enable_db_query_source=True,
            db_query_source_threshold_ms=100,  # Add source for queries > 100ms
        )

        # Set default tags for this component
        sentry_sdk.set_tags(default_tags)
        # Tag events with region for multi-region observability
        sentry_sdk.set_tag("region", os.getenv("REGION", "us"))
        sentry_sdk.set_tag(
            "cloud_deployment", os.getenv("CLOUD_DEPLOYMENT", "") or "self-hosted"
        )

        _initialized_components.add(component)
        return True

    except Exception as e:
        print(f"Failed to initialize Sentry for {component}: {e}")
        return False


def set_user_context(user_id: str, email: str = "", username: str = "") -> None:
    """Set user context for Sentry events."""
    if not SENTRY_ENABLED:
        return

    try:
        import sentry_sdk

        sentry_sdk.set_user(
            {
                "id": str(user_id),
                "email": email,
                "username": username,
            }
        )
    except Exception:
        pass


def capture_exception_with_context(
    exception: Exception,
    context: dict | None = None,
    tags: dict | None = None,
) -> str | None:
    """
    Capture an exception with additional context.

    Args:
        exception: The exception to capture
        context: Additional context data (e.g., {"activity": {...}})
        tags: Additional tags for filtering (e.g., {"workflow": "experiment"})

    Returns:
        The Sentry event ID if captured, None otherwise
    """
    if not SENTRY_ENABLED:
        return None

    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            if context:
                for key, value in context.items():
                    scope.set_context(key, value)
            if tags:
                for key, value in tags.items():
                    scope.set_tag(key, value)
            return sentry_sdk.capture_exception(exception)
    except Exception:
        return None


def capture_message(
    message: str,
    level: str = "info",
    context: dict | None = None,
    tags: dict | None = None,
) -> str | None:
    """
    Capture a message to Sentry.

    Args:
        message: The message to capture
        level: Log level ("debug", "info", "warning", "error", "fatal")
        context: Additional context data
        tags: Additional tags for filtering

    Returns:
        The Sentry event ID if captured, None otherwise
    """
    if not SENTRY_ENABLED:
        return None

    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            if context:
                for key, value in context.items():
                    scope.set_context(key, value)
            if tags:
                for key, value in tags.items():
                    scope.set_tag(key, value)
            return sentry_sdk.capture_message(message, level=level)
    except Exception:
        return None


def add_breadcrumb(
    message: str,
    category: str = "custom",
    level: str = "info",
    data: dict | None = None,
) -> None:
    """
    Add a breadcrumb for debugging.

    Breadcrumbs are a trail of events that happened before an error.

    Args:
        message: Description of what happened
        category: Category for grouping (e.g., "http", "query", "user")
        level: Severity ("debug", "info", "warning", "error", "critical")
        data: Additional data to attach
    """
    if not SENTRY_ENABLED:
        return

    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(
            message=message,
            category=category,
            level=level,
            data=data or {},
        )
    except Exception:
        pass


def start_transaction(
    name: str,
    op: str = "task",
    description: str = "",
) -> Any:
    """
    Start a new Sentry transaction for performance monitoring.

    Usage:
        with start_transaction(name="process_experiment", op="workflow") as txn:
            # ... do work ...
            txn.set_tag("experiment_id", exp_id)

    Args:
        name: Transaction name (e.g., "process_experiment")
        op: Operation type (e.g., "http.server", "task", "workflow")
        description: Optional description

    Returns:
        Transaction context manager (or no-op if Sentry disabled)
    """
    if not SENTRY_ENABLED:
        from contextlib import nullcontext

        return nullcontext()

    try:
        import sentry_sdk

        return sentry_sdk.start_transaction(
            name=name,
            op=op,
            description=description,
        )
    except Exception:
        from contextlib import nullcontext

        return nullcontext()
