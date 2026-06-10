from __future__ import annotations

import os
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_INTERVAL_HOURS = 6
VALID_INTERVAL_HOURS = frozenset({1, 2, 3, 4, 6, 8, 12, 24})
DEFAULT_JITTER_SECONDS = 30 * 60
BUFFER_FLUSH_BATCH_SIZE = 10
BUFFER_RETENTION_DAYS = 30
MAX_PAYLOAD_BYTES = 512 * 1024
MAX_REGISTRATION_USERS = 500
REGISTRATION_CLAIM_TIMEOUT_SECONDS = 10 * 60


def telemetry_is_disabled() -> bool:
    return os.getenv("FUTURE_AGI_TELEMETRY_DISABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_telemetry_interval_hours() -> int:
    raw_value = os.getenv(
        "FUTURE_AGI_TELEMETRY_INTERVAL_HOURS",
        str(DEFAULT_INTERVAL_HOURS),
    )
    try:
        interval = int(raw_value)
    except (TypeError, ValueError):
        interval = 0

    if interval not in VALID_INTERVAL_HOURS:
        logger.warning(
            "deployment_telemetry_invalid_interval",
            configured_value=raw_value,
            fallback_hours=DEFAULT_INTERVAL_HOURS,
        )
        return DEFAULT_INTERVAL_HOURS
    return interval


def get_telemetry_jitter_seconds() -> int:
    raw_value = os.getenv(
        "FUTURE_AGI_TELEMETRY_JITTER_SECONDS",
        str(DEFAULT_JITTER_SECONDS),
    )
    try:
        jitter_seconds = int(raw_value)
    except (TypeError, ValueError):
        jitter_seconds = -1

    if jitter_seconds < 0:
        logger.warning(
            "deployment_telemetry_invalid_jitter",
            configured_value=raw_value,
            fallback_seconds=DEFAULT_JITTER_SECONDS,
        )
        return DEFAULT_JITTER_SECONDS
    return jitter_seconds


def get_telemetry_interval_seconds_override() -> int | None:
    raw = os.getenv("FUTURE_AGI_TELEMETRY_INTERVAL_SECONDS")
    if raw is None:
        return None
    try:
        val = int(raw)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def get_telemetry_url() -> str:
    return os.getenv(
        "FUTURE_AGI_TELEMETRY_URL",
        "https://api.futureagi.com",
    ).rstrip("/")


def get_telemetry_timeout_seconds() -> float:
    raw_value = os.getenv("FUTURE_AGI_TELEMETRY_TIMEOUT_SECONDS", "5")
    try:
        timeout = float(raw_value)
    except (TypeError, ValueError):
        return 5.0
    return timeout if timeout > 0 else 5.0


def get_telemetry_buffer_dir() -> Path:
    configured = os.getenv("FUTURE_AGI_TELEMETRY_BUFFER_DIR")
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "futureagi-deployment-telemetry"


def is_self_hosted_deployment() -> bool:
    try:
        from tfc.ee_loader import has_ee
    except Exception:
        logger.warning("deployment_telemetry_ee_detection_failed")
        return True

    if not has_ee("ee.usage"):
        return True

    try:
        from ee.usage.deployment import DeploymentMode

        return not DeploymentMode.is_cloud()
    except Exception:
        logger.warning("deployment_telemetry_mode_detection_failed")
        return False


def get_version() -> str:
    return (
        os.getenv("FUTURE_AGI_VERSION")
        or os.getenv("SERVICE_VERSION")
        or os.getenv("GIT_SHA")
        or "unknown"
    )


def detect_deployment_type() -> str:
    explicit = os.getenv("FUTURE_AGI_DEPLOYMENT_TYPE")
    if explicit:
        return explicit
    if os.getenv("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    if os.path.exists("/.dockerenv"):
        return "docker"
    return "bare_metal"
