"""Enterprise heartbeat sender for self-hosted instances.

Sends periodic heartbeats to the FutureAGI license control plane.
Separate from deployment telemetry — uses license credentials, not
telemetry HMAC.

Does NOT block application startup or user requests on failure.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC, datetime, timedelta

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_HEARTBEAT_INTERVAL_HOURS = 24
HEARTBEAT_ENDPOINT_PATH = "/v1/enterprise/heartbeats"
HEARTBEAT_SEQ_CACHE_KEY = "enterprise_heartbeat_sequence"


def get_heartbeat_url() -> str:
    return (
        os.getenv(
            "FUTURE_AGI_LICENSE_URL",
            "https://api.futureagi.com",
        ).rstrip("/")
        + HEARTBEAT_ENDPOINT_PATH
    )


def is_heartbeat_enabled() -> bool:
    disabled = (
        os.getenv("FUTURE_AGI_ENTERPRISE_HEARTBEAT_DISABLED", "false").strip().lower()
    )
    return disabled not in {"1", "true", "yes", "on"}


def build_heartbeat_payload(
    instance_id: str,
    license_id: str | None,
    version: str,
    deployment_type: str,
    usage_data: dict | None = None,
    sequence: int | None = None,
    nonce: str | None = None,
) -> dict:
    return {
        "instance_id": instance_id,
        "license_id": license_id,
        "version": version,
        "deployment_type": deployment_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "nonce": nonce or secrets.token_urlsafe(16),
        "sequence": sequence if sequence is not None else _next_sequence(),
        "usage_data": usage_data or {},
    }


def _next_sequence() -> int:
    """Return a monotonically increasing sequence via Django cache.

    Falls back to a coarse seconds-since-epoch counter if the cache is
    unavailable, which is still monotonic across process restarts.
    """
    try:
        from django.core.cache import cache

        if cache.add(HEARTBEAT_SEQ_CACHE_KEY, 1, timeout=None):
            return 1
        return int(cache.incr(HEARTBEAT_SEQ_CACHE_KEY))
    except Exception:
        logger.debug("enterprise_heartbeat_sequence_cache_unavailable", exc_info=True)
        return int(datetime.now(UTC).timestamp())


def collect_heartbeat_usage() -> dict:
    try:
        from tfc.deployment_telemetry.collectors import collect_counts

        window_end = datetime.now(UTC)
        window_start = window_end - timedelta(hours=DEFAULT_HEARTBEAT_INTERVAL_HOURS)
        return collect_counts(window_start, window_end)
    except Exception:
        logger.debug("enterprise_heartbeat_usage_collection_failed", exc_info=True)
        return {}


def send_heartbeat() -> bool:
    if not is_heartbeat_enabled():
        return False

    try:
        from ee.licensing.activation_client import _get_configured_license_key
        from ee.licensing.state import get_snapshot
        from ee.licensing.validator import hash_key
        from tfc.deployment_telemetry.config import detect_deployment_type, get_version
        from tfc.deployment_telemetry.state import get_or_create_telemetry_state

        state = get_or_create_telemetry_state()
        snapshot = get_snapshot()

        # Enterprise heartbeat is a licence-lifecycle signal — nothing to
        # report without a licence identifier, and unsigned heartbeats
        # cannot authenticate to the receiver, so skip silently.
        if not snapshot.license_id:
            return False

        license_key = _get_configured_license_key()
        if not license_key:
            logger.debug("enterprise_heartbeat_skipped_no_license_key")
            return False
        heartbeat_secret = hash_key(license_key).encode()

        payload = build_heartbeat_payload(
            instance_id=str(state.instance_id),
            license_id=snapshot.license_id,
            version=get_version(),
            deployment_type=detect_deployment_type(),
            usage_data=collect_heartbeat_usage(),
        )

        import httpx

        url = get_heartbeat_url()
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {
            "Content-Type": "application/json",
            "X-FutureAGI-Heartbeat-Signature": hmac.new(
                heartbeat_secret,
                body,
                hashlib.sha256,
            ).hexdigest(),
        }

        response = httpx.post(
            url,
            content=body,
            timeout=10.0,
            headers=headers,
        )

        if response.status_code < 300:
            logger.info("enterprise_heartbeat_sent", license_id=snapshot.license_id)
            return True
        else:
            logger.warning(
                "enterprise_heartbeat_failed",
                status_code=response.status_code,
            )
            return False
    except Exception:
        logger.debug("enterprise_heartbeat_error", exc_info=True)
        return False
