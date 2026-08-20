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

import asyncio
import json
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
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


def _raise_for_managed_status(
    status_code: int,
    on_unauthorized: Callable[[], None],
) -> None:
    if status_code == 401:
        on_unauthorized()
        raise ManagedServiceError(
            "GATEWAY_UNAUTHORIZED", "Managed AI gateway rejected credentials"
        )
    if status_code == 403:
        raise ManagedServiceError(
            "FEATURE_DENIED", "Feature not included in license scope"
        )
    if status_code == 429:
        raise ManagedServiceError("RATE_LIMITED", "Managed AI rate limit exceeded")
    if status_code >= 500:
        raise ManagedServiceError(
            "SERVICE_ERROR", f"Managed AI service error ({status_code})"
        )


def dispatch_managed_request(
    *,
    url: str,
    api_key: str,
    json_body: dict,
    timeout: float,
    on_unauthorized: Callable[[], None],
) -> dict:
    """POST to the managed gateway and map failures to typed errors.

    Shared transport for both managed-AI auth models: the self-hosted
    activation-token flow (``call_managed_service``) and the cloud
    internal-key flow (``ee.licensing.managed_ai``). Only the 401 response
    is handled differently between them, so callers pass ``on_unauthorized``
    (which must raise); every other status maps identically here so the two
    paths cannot drift.
    """
    import httpx

    try:
        response = httpx.post(
            url,
            json=json_body,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
    except httpx.TimeoutException:
        raise ManagedServiceError("GATEWAY_TIMEOUT", "Managed AI gateway timed out")
    except httpx.ConnectError:
        raise ManagedServiceError(
            "GATEWAY_UNREACHABLE", "Cannot reach managed AI gateway"
        )

    _raise_for_managed_status(response.status_code, on_unauthorized)
    return response.json()


async def dispatch_managed_stream(
    *,
    url: str,
    api_key: str,
    json_body: dict,
    timeout: float,
    on_unauthorized: Callable[[], None],
) -> AsyncIterator[dict]:
    import httpx

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "x-agentcc-include-metadata": "true",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                json=json_body,
                headers=headers,
            ) as response:
                _raise_for_managed_status(response.status_code, on_unauthorized)
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        return
                    yield json.loads(data)
    except httpx.TimeoutException as exc:
        raise ManagedServiceError(
            "GATEWAY_TIMEOUT", "Managed AI gateway timed out"
        ) from exc
    except httpx.ConnectError as exc:
        raise ManagedServiceError(
            "GATEWAY_UNREACHABLE", "Cannot reach managed AI gateway"
        ) from exc


def call_managed_service(
    path: str = "/v1/chat/completions",
    *,
    json_body: dict,
    timeout: float = 30.0,
) -> dict:
    """Make an authenticated request to the FutureAGI managed gateway.

    Self-hosted EE auth: exchange the license for a short-lived service
    token, then call the gateway with it. Raises ManagedServiceError on
    auth/service failures with typed codes.
    """
    token = get_service_token()
    if token is None:
        raise ManagedServiceError("ACTIVATION_FAILED", "Could not obtain service token")

    if token.scope == "oss" and not token.access_token:
        raise ManagedServiceError(
            "NO_ENTERPRISE_LICENSE", "Managed AI requires an Enterprise license"
        )

    def _on_unauthorized() -> None:
        invalidate_token()
        raise ManagedServiceError(
            "TOKEN_EXPIRED", "Service token rejected — will refresh on next call"
        )

    return dispatch_managed_request(
        url=token.gateway_url.rstrip("/") + path,
        api_key=token.access_token,
        json_body=json_body,
        timeout=timeout,
        on_unauthorized=_on_unauthorized,
    )


async def stream_managed_service(
    path: str = "/v1/chat/completions",
    *,
    json_body: dict,
    timeout: float = 300.0,
) -> AsyncIterator[dict]:
    token = await asyncio.to_thread(get_service_token)
    if token is None:
        raise ManagedServiceError("ACTIVATION_FAILED", "Could not obtain service token")

    if token.scope == "oss" and not token.access_token:
        raise ManagedServiceError(
            "NO_ENTERPRISE_LICENSE", "Managed AI requires an Enterprise license"
        )

    def _on_unauthorized() -> None:
        invalidate_token()
        raise ManagedServiceError(
            "TOKEN_EXPIRED", "Service token rejected — will refresh on next call"
        )

    async for chunk in dispatch_managed_stream(
        url=token.gateway_url.rstrip("/") + path,
        api_key=token.access_token,
        json_body=json_body,
        timeout=timeout,
        on_unauthorized=_on_unauthorized,
    ):
        yield chunk


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
