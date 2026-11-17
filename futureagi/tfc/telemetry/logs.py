"""
OpenTelemetry Logs integration for log-trace correlation.

This module provides OTLP log export for true log-trace correlation in
any OpenTelemetry-compatible observability backend.

Usage:
    # In application startup (after init_telemetry):
    from tfc.telemetry import init_telemetry
    from tfc.telemetry.logs import init_otel_logging

    init_telemetry(component="django")
    init_otel_logging()  # Optional: for OTLP log export

Environment Variables:
    OTEL_LOGS_ENABLED: Enable OTLP log export (default: false)
    OTEL_EXPORTER_OTLP_LOGS_ENDPOINT: Override logs endpoint
    OTEL_PYTHON_LOG_LEVEL: Minimum log level to export (default: INFO)

References:
    - OTel Python Logs: https://opentelemetry.io/docs/languages/python/logging/
    - OTel Logs SDK: https://opentelemetry-python.readthedocs.io/en/latest/sdk/logs.html
"""

import logging
import os
from typing import TYPE_CHECKING, Optional

import structlog

if TYPE_CHECKING:
    from opentelemetry.sdk._logs import LoggerProvider

logger = structlog.get_logger(__name__)

# Singleton state
_logs_initialized = False
_logger_provider: Optional["LoggerProvider"] = None


def _str_to_bool(val: str | None) -> bool:
    """Convert string to boolean."""
    if val is None:
        return False  # Default to disabled for logs
    return val.lower() in ("true", "1", "yes", "on", "enabled")


# Check if OTLP log export is enabled (default: disabled)
# This is separate from trace export because log volume is typically much higher
OTEL_LOGS_ENABLED = _str_to_bool(os.getenv("OTEL_LOGS_ENABLED", "false"))


def get_logs_endpoint() -> str:
    """
    Get the OTLP logs endpoint.

    Falls back to the general OTLP endpoint with /v1/logs path for HTTP.
    """
    # Check for logs-specific endpoint first
    logs_endpoint = os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
    if logs_endpoint:
        return logs_endpoint

    # Fall back to general endpoint
    base_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")

    if protocol == "http/protobuf":
        # HTTP needs /v1/logs path
        if not base_endpoint.endswith("/v1/logs"):
            return f"{base_endpoint.rstrip('/')}/v1/logs"
    return base_endpoint


def _create_log_exporter():
    """Create the OTLP log exporter based on protocol."""
    from .checkout_output import log_exporter

    protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    endpoint = get_logs_endpoint()
    if protocol == "http/protobuf":
        return log_exporter(protocol, endpoint=endpoint)
    return log_exporter(protocol, endpoint=endpoint, insecure=True)


def init_otel_logging(
    service_name: str | None = None,
    log_level: int = logging.INFO,
) -> Optional["LoggerProvider"]:
    """
    Initialize OpenTelemetry log export.

    This sets up OTLP log export, which sends logs to the same collector
    as traces for automatic correlation. The collector then routes logs
    to any OpenTelemetry-compatible log backend.

    Args:
        service_name: Override service name (uses OTEL_SERVICE_NAME if not set)
        log_level: Minimum log level to export (default: INFO)

    Returns:
        LoggerProvider instance or None if disabled

    Note:
        This is OPTIONAL and disabled by default. The structlog processors
        already add trace context to logs. OTLP export is useful when:
        1. Your backend supports OTLP logs
        2. You want automatic log-trace correlation in the backend
        3. You want logs and traces in the same collector pipeline
    """
    global _logs_initialized, _logger_provider

    if not OTEL_LOGS_ENABLED:
        logger.debug("otel_logs_disabled")
        return None

    if _logs_initialized:
        logger.debug("otel_logs_already_initialized")
        return _logger_provider

    try:
        from opentelemetry import _logs as logs_api
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource

        # Get service name
        if service_name is None:
            service_name = os.getenv("OTEL_SERVICE_NAME", "futureagi-backend")

        # Create resource (should match trace resource for correlation)
        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                SERVICE_VERSION: os.getenv(
                    "SERVICE_VERSION", os.getenv("GIT_SHA", "dev")
                ),
                "deployment.environment": os.getenv("ENV_TYPE", "local"),
                "service.namespace": "futureagi",
            }
        )

        # Create logger provider
        _logger_provider = LoggerProvider(resource=resource)

        # Create and add log exporter
        exporter = _create_log_exporter()
        processor = BatchLogRecordProcessor(
            exporter,
            max_queue_size=2048,
            max_export_batch_size=512,
            schedule_delay_millis=5000,
        )
        _logger_provider.add_log_record_processor(processor)

        # Set as global logger provider
        logs_api.set_logger_provider(_logger_provider)

        # Create handler that sends logs via OTLP
        otel_handler = LoggingHandler(
            level=log_level,
            logger_provider=_logger_provider,
        )

        # Add handler to root logger
        # This captures all Python logging and sends to OTLP
        root_logger = logging.getLogger()
        root_logger.addHandler(otel_handler)

        _logs_initialized = True

        logger.info(
            "otel_logs_initialized",
            service_name=service_name,
            endpoint=get_logs_endpoint(),
            log_level=logging.getLevelName(log_level),
        )

        return _logger_provider

    except ImportError as e:
        logger.warning(
            "otel_logs_unavailable",
            error=str(e),
            hint="Install opentelemetry-exporter-otlp-proto-grpc or http",
        )
        return None
    except Exception as e:
        logger.error("otel_logs_init_failed", error=str(e), exc_info=True)
        return None


def get_logger_provider() -> Optional["LoggerProvider"]:
    """Get the initialized logger provider."""
    return _logger_provider


__all__ = [
    "OTEL_LOGS_ENABLED",
    "init_otel_logging",
    "get_logger_provider",
    "get_logs_endpoint",
]
