"""HTTP transport for deployment telemetry.

Owns the request, timeout, retry, payload-size guard, and HMAC signing —
the concerns that were previously inlined in ``sender.py`` alongside the
registration state machine. Keeping them here gives heartbeat signing a
single home and lets ``sender.py`` focus on orchestration.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import requests
import structlog

from tfc.deployment_telemetry.config import (
    get_telemetry_timeout_seconds,
    get_telemetry_url,
)
from tfc.deployment_telemetry.schema import MAX_PAYLOAD_BYTES

logger = structlog.get_logger(__name__)

# Must match the receiver's ee.usage.services.deployment_telemetry.SIGNATURE_HEADER
# (request.META "HTTP_X_FAGI_TELEMETRY_SIGNATURE"). A contract test asserts
# the two sides compute the same signature for the same body+secret.
SIGNATURE_HEADER = "X-FAGI-Telemetry-Signature"

_MAX_ATTEMPTS = 3
_RETRY_DELAYS_SECONDS = (0.2, 0.5)


def compute_signature(secret: str, body: bytes) -> str:
    """HMAC-SHA256 of ``body`` keyed by ``secret``, hex-encoded."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class TelemetryResponse:
    ok: bool
    status_code: int | None = None
    data: dict | None = None


def _encode(payload: dict) -> bytes:
    return json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


class TelemetryClient:
    """Posts telemetry payloads, optionally HMAC-signing the body."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        secret: str | None = None,
    ) -> None:
        self._base_url = (base_url or get_telemetry_url()).rstrip("/")
        self._timeout = timeout or get_telemetry_timeout_seconds()
        self._secret = secret or ""

    def post(self, path: str, payload: dict) -> TelemetryResponse:
        """POST a payload. Signs the body when a secret is configured.

        Returns ``TelemetryResponse(ok=...)``; ``data`` is the parsed JSON
        response body on success (used to read the issued instance secret
        off a registration response).
        """
        body = _encode(payload)
        if len(body) > MAX_PAYLOAD_BYTES:
            logger.warning("deployment_telemetry_payload_too_large", endpoint=path)
            return TelemetryResponse(ok=False)

        headers = {"Content-Type": "application/json"}
        if self._secret:
            headers[SIGNATURE_HEADER] = compute_signature(self._secret, body)

        url = f"{self._base_url}{path}"
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = requests.post(
                    url, data=body, headers=headers, timeout=self._timeout
                )
                if 200 <= response.status_code < 300:
                    data = None
                    try:
                        data = response.json()
                    except ValueError:
                        pass
                    return TelemetryResponse(
                        ok=True,
                        status_code=response.status_code,
                        data=data if isinstance(data, dict) else None,
                    )
                logger.warning(
                    "deployment_telemetry_request_failed",
                    endpoint=path,
                    status_code=response.status_code,
                    attempt=attempt + 1,
                )
            except requests.RequestException:
                logger.warning(
                    "deployment_telemetry_request_unreachable",
                    endpoint=path,
                    attempt=attempt + 1,
                    exc_info=True,
                )

            if attempt < len(_RETRY_DELAYS_SECONDS):
                time.sleep(_RETRY_DELAYS_SECONDS[attempt])
        return TelemetryResponse(ok=False)
