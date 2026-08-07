"""Managed AI activation client for self-hosted EE instances.

Handles the activation exchange: sends instance_id + optional license
proof to the FutureAGI activation service, receives a short-lived
service token for managed AI calls (Turing, Falcon, Protect).

Token lifecycle:
- On first managed-service request: activate and cache token
- Before expiry: refresh token automatically
- On failure: return typed error, never block startup
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import structlog
from ee.licensing.validator import hash_key

logger = structlog.get_logger(__name__)

REFRESH_MARGIN_SECONDS = 300  # Refresh 5 min before expiry


@dataclass
class ServiceToken:
    access_token: str
    gateway_url: str
    expires_at: float
    allowed_services: list[str]
    allowed_models: list[str]
    scope: str

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - REFRESH_MARGIN_SECONDS


_lock = threading.Lock()
_cached_token: ServiceToken | None = None


def get_activation_url() -> str:
    return (
        os.getenv(
            "FUTURE_AGI_LICENSE_URL",
            "https://api.futureagi.com",
        ).rstrip("/")
        + "/v1/self-hosted/activations"
    )


def get_service_token() -> ServiceToken | None:
    global _cached_token

    if _cached_token and not _cached_token.is_expired:
        return _cached_token

    with _lock:
        if _cached_token and not _cached_token.is_expired:
            return _cached_token

        token = _activate()
        if token:
            _cached_token = token
        return _cached_token


def invalidate_token() -> None:
    global _cached_token
    with _lock:
        _cached_token = None


def _activate() -> ServiceToken | None:
    try:
        from tfc.deployment_telemetry.state import get_or_create_telemetry_state

        state = get_or_create_telemetry_state()

        payload = {
            "instance_id": str(state.instance_id),
            "version": os.getenv("FUTURE_AGI_VERSION", "unknown"),
        }

        license_key = _get_configured_license_key()
        if license_key:
            payload["license_key_hash"] = hash_key(license_key)

        import httpx

        url = get_activation_url()
        response = httpx.post(url, json=payload, timeout=10.0)

        if response.status_code != 200:
            logger.warning(
                "activation_failed",
                status_code=response.status_code,
                body=response.text[:200],
            )
            return None

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            return None

        expires_in = data.get("expires_in", 3600)
        return ServiceToken(
            access_token=access_token,
            gateway_url=data.get("gateway_url", "https://gateway.futureagi.com"),
            expires_at=time.time() + expires_in,
            allowed_services=data.get("allowed_services", []),
            allowed_models=data.get("allowed_models", []),
            scope=data.get("scope", "oss"),
        )
    except Exception:
        logger.debug("activation_error", exc_info=True)
        return None


def call_managed_service(
    path: str = "/v1/chat/completions",
    *,
    json_body: dict,
    timeout: float = 30.0,
) -> dict:
    """Make an authenticated request to the FutureAGI managed gateway.

    Raises ManagedServiceError on auth/service failures with typed codes.
    """
    token = get_service_token()
    if token is None:
        raise ManagedServiceError("ACTIVATION_FAILED", "Could not obtain service token")

    if token.scope == "oss" and not token.access_token:
        raise ManagedServiceError(
            "NO_ENTERPRISE_LICENSE", "Managed AI requires an Enterprise license"
        )

    import httpx

    url = token.gateway_url.rstrip("/") + path
    try:
        response = httpx.post(
            url,
            json=json_body,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token.access_token}",
                "Content-Type": "application/json",
            },
        )
    except httpx.TimeoutException:
        raise ManagedServiceError("GATEWAY_TIMEOUT", "Managed AI gateway timed out")
    except httpx.ConnectError:
        raise ManagedServiceError(
            "GATEWAY_UNREACHABLE", "Cannot reach managed AI gateway"
        )

    if response.status_code == 401:
        invalidate_token()
        raise ManagedServiceError(
            "TOKEN_EXPIRED", "Service token rejected — will refresh on next call"
        )
    if response.status_code == 403:
        raise ManagedServiceError(
            "FEATURE_DENIED", "Feature not included in license scope"
        )
    if response.status_code == 429:
        raise ManagedServiceError("RATE_LIMITED", "Managed AI rate limit exceeded")
    if response.status_code >= 500:
        raise ManagedServiceError(
            "SERVICE_ERROR", f"Managed AI service error ({response.status_code})"
        )

    return response.json()


class ManagedServiceError(Exception):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _get_configured_license_key() -> str:
    try:
        from django.conf import settings

        configured = getattr(settings, "EE_LICENSE_KEY", "")
        if configured:
            return configured
    except Exception:
        logger.debug("activation_client_django_settings_unavailable", exc_info=True)
    return os.getenv("EE_LICENSE_KEY", "")
