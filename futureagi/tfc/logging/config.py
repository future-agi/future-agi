"""
Structlog configuration for Django/Celery logging.
Uses a centralized django-structlog configuration.
"""

import os

import structlog

from .processors import (
    add_otel_context,
    add_otel_context_from_record,
    add_pid_and_tid,
    add_region_context,
)


def get_env() -> str:
    """Get current environment type."""
    return os.getenv("ENV_TYPE", "local")


def is_production() -> bool:
    """Check if running in production environment."""
    return get_env() in ("staging", "prod", "production")


def get_processors():
    """
    Get structlog processors.

    These processors run in order for every log entry.
    """
    # Shared processor chain for structured application logs
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        add_pid_and_tid,  # Add process/thread IDs
        add_region_context,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        add_otel_context,  # FutureAGI addition: OpenTelemetry context
    ]
    return shared_processors


def get_renderer():
    """Get final renderer based on environment."""
    if is_production():
        return structlog.processors.JSONRenderer()
    else:
        return structlog.dev.ConsoleRenderer(colors=True)


def configure_structlog():
    """Configure structlog with proper processors."""
    structlog.configure(
        processors=get_processors()
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logging_config(base_dir: str) -> dict:
    """
    Get Django LOGGING configuration integrated with structlog.

    Args:
        base_dir: Project base directory (for log file paths)

    Returns:
        Django LOGGING dict
    """
    log_level = os.getenv("LOG_LEVEL", "INFO")
    logs_dir = os.path.join(base_dir, "logs")

    # Ensure logs directory exists
    os.makedirs(logs_dir, exist_ok=True)

    # Build foreign_pre_chain for stdlib logging integration
    # This allows standard library loggers to also get structured output
    # Build stdlib pre-chain for consistent structured output
    #
    # Note: We use add_otel_context_from_record here because stdlib loggers
    # may have trace context injected by LoggingInstrumentor into LogRecord
    # attributes (otelTraceID, otelSpanID). This processor reads those AND
    # falls back to getting context from the current span.
    foreign_pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        add_pid_and_tid,
        add_region_context,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        add_otel_context_from_record,  # Reads from LogRecord AND current span
    ]

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": get_renderer(),
                "foreign_pre_chain": foreign_pre_chain,
            },
            "plain": {
                "format": "{levelname} {asctime} {name} {message}",
                "style": "{",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "structured",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": os.path.join(logs_dir, "wsgi.log"),
                "maxBytes": 1024 * 1024 * 10,  # 10MB
                "backupCount": 10,
                "formatter": "structured",
            },
        },
        "loggers": {
            "": {  # Root logger
                "handlers": ["console", "file"],
                "level": log_level,
                "propagate": True,
            },
            "django": {
                "handlers": ["console", "file"],
                "level": log_level,
                "propagate": False,
            },
            "django.db.backends": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
            "celery": {
                "handlers": ["console", "file"],
                "level": log_level,
                "propagate": False,
            },
            "agentic_eval": {
                "handlers": ["console", "file"],
                "level": "DEBUG",
                "propagate": False,
            },
            "django_structlog": {
                "handlers": ["console", "file"],
                "level": "INFO",
                "propagate": False,
            },
            # Temporal workflow loggers: console-only to avoid RotatingFileHandler
            # triggering deadlock detection (os.path.exists is blocking I/O in sandbox)
            "temporalio": {
                "handlers": ["console"],
                "level": log_level,
                "propagate": False,
            },
            # Silence noisy third-party loggers
            "httpx": {"level": "WARNING", "propagate": False},
            "httpcore": {"level": "WARNING", "propagate": False},
            "urllib3": {"level": "WARNING", "propagate": False},
            "opentelemetry": {"level": "WARNING", "propagate": False},
            "openai": {"level": "WARNING", "propagate": False},
            "anthropic": {"level": "WARNING", "propagate": False},
            "LiteLLM": {"level": "WARNING", "propagate": False},
            # SAML/XML libraries (pysaml2 dependencies)
            "xmlschema": {"level": "WARNING", "propagate": False},
            "saml2": {"level": "WARNING", "propagate": False},
        },
    }

    # NOTE: We deliberately do NOT attach a sentry EventHandler here.
    #
    # Sentry's LoggingIntegration (configured in tfc.logging.sentry.init_sentry)
    # already captures every ERROR-level log record as an event by patching
    # logging.Logger.callHandlers. Adding an explicit EventHandler on the root
    # logger double-captured each error and, because the integration runs in
    # Temporal/Celery too (which never load this Django LOGGING dict), split
    # capture across two code paths. Routing all log->event capture through the
    # single LoggingIntegration keeps behaviour consistent everywhere and lets
    # noise control (ignore_logger + before_send) live in one place.
    #
    # SENTRY_LOG_LEVEL is still honoured by the LoggingIntegration's event_level.

    return config
