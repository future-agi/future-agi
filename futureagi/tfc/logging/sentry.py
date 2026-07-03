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

import os
from collections.abc import Callable
from typing import Any

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

# Keys whose values must be redacted before leaving the process. Matched
# case-insensitively as substrings against header/cookie/body/extra keys.
SENSITIVE_KEY_SUBSTRINGS = (
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "x-api-key",
    "access_key",
    "private_key",
    "csrf",
)
_REDACTED = "[Filtered]"

# Sample rates - configurable via env, lower in production to reduce costs
# Production: 10% sampling, Staging: 100% sampling
TRACES_SAMPLE_RATE = float(
    os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1" if IS_PRODUCTION else "1.0")
)
PROFILES_SAMPLE_RATE = float(
    os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1" if IS_PRODUCTION else "1.0")
)

_initialized_components: set = set()


def _is_sensitive_key(key: Any) -> bool:
    """True if a dict/header key looks like it carries a secret or PII token."""
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(s in lowered for s in SENSITIVE_KEY_SUBSTRINGS)


def _scrub(value: Any, depth: int = 0) -> Any:
    """Recursively redact values under sensitive keys (defensive, bounded)."""
    if depth > 6:
        return value
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_sensitive_key(k) else _scrub(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(v, depth + 1) for v in value]
    return value


def _scrub_event(event: dict) -> None:
    """
    Redact obvious secrets/PII in-place before the event leaves the process.

    Complements Sentry's server-side scrubbing and the EventScrubber denylist
    so secrets never transit the wire even when send_default_pii is on.
    """
    request = event.get("request")
    if isinstance(request, dict):
        # Cookies are session/CSRF tokens almost by definition - redact wholesale.
        if request.get("cookies"):
            request["cookies"] = _REDACTED
        for section in ("headers", "data", "env"):
            if isinstance(request.get(section), dict):
                request[section] = _scrub(request[section])
        # Strip query-string credentials wholesale rather than parse them.
        if isinstance(request.get("query_string"), str) and any(
            s in request["query_string"].lower() for s in SENSITIVE_KEY_SUBSTRINGS
        ):
            request["query_string"] = _REDACTED

    if isinstance(event.get("extra"), dict):
        event["extra"] = _scrub(event["extra"])
    contexts = event.get("contexts")
    if isinstance(contexts, dict):
        event["contexts"] = _scrub(contexts)


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

    Reuses ``SENSITIVE_BODY_PATH_PREFIXES`` so the OTel middleware and the
    Sentry scrubber agree on which paths are sensitive; previously the prefix
    was hardcoded here and could silently drift from the middleware copy.
    """
    if not url:
        return False
    from tfc.telemetry.middleware import SENSITIVE_BODY_PATH_PREFIXES

    return any(prefix in str(url) for prefix in SENSITIVE_BODY_PATH_PREFIXES)


def _scrub_deployment_telemetry_event(event: dict) -> dict:
    # Sentry's HTTP integrations record outbound POSTs to /telemetry/ as
    # breadcrumbs with the request body in ``data.body``/``data.data``. The
    # inbound ``request.url`` check below would not match those (the exception
    # frame's URL is whatever the caller hit, not the breadcrumb's target), so
    # the registration payload would still ship to Sentry via breadcrumbs even
    # though the inbound branch scrubbed the request body. Walk breadcrumbs
    # unconditionally and strip body fields whose ``url`` points at a telemetry
    # path before running the inbound scrub.
    for breadcrumb in event.get("breadcrumbs", {}).get("values", []) or []:
        data = breadcrumb.get("data") or {}
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
        event = _scrub_deployment_telemetry_event(event)

        # 1. Infrastructure logger noise - never actionable as an issue.
        logger_name = event.get("logger") or ""
        if isinstance(logger_name, str) and any(
            logger_name.startswith(p) for p in NOISY_LOGGER_PREFIXES
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
        try:
            _scrub_event(event)
        except Exception:
            # Never let scrubbing failures drop a real error.
            pass

        return event

    return before_send


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

        # Keep a generous-but-bounded string length so stack frames and request
        # bodies stay readable without bloating every event (the old 10MB cap
        # multiplied payload size across millions of events).
        try:
            sentry_sdk.utils.MAX_STRING_LENGTH = 8192
        except AttributeError:
            pass  # Newer SDK versions may not have this

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
        from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

        scrubber_denylist = list(DEFAULT_DENYLIST) + [
            k for k in SENSITIVE_KEY_SUBSTRINGS if k not in DEFAULT_DENYLIST
        ]

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
            event_scrubber=EventScrubber(denylist=scrubber_denylist, recursive=True),
            # "medium" caps bodies at ~10KB: enough to debug, small enough that
            # large uploads/payloads don't dominate the event volume or leak.
            max_request_body_size="medium",
            max_breadcrumbs=100,
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
            before_send_transaction=lambda event, hint: (
                _scrub_deployment_telemetry_event(event)
            ),
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
