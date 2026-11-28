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
import math
import os
import threading
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import structlog

from ee.licensing.validator import hash_key

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger(__name__)

REFRESH_MARGIN_SECONDS = 300  # Refresh 5 min before expiry
MANAGED_STREAM_TIMEOUT_SECONDS = 300.0
# The longest Retry-After an evaluation waits out before its one retry: a
# gateway request window (60 s) plus the second its sliding window takes to
# release the previous window's count. A longer hint (a quota, a provider
# back-off) fails the evaluation at once: a retry sooner than the hint lands in
# the window that refused it and spends the recovery on a second refusal.
MAX_RETRY_AFTER_SECONDS = 61.0

_LICENSE_REFUSALS = {
    "invalid_license": "License key not recognized. Check the key and activation URL.",
    "license_inactive": "License is inactive. Check the license status.",
    "license_expired": "License has expired. Renew the license.",
    "instance_limit": "License instance limit reached. Check the registered instances.",
}


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


@dataclass(frozen=True)
class _ActivationFailure:
    config: tuple[str, str]  # endpoint and key hash, never the key
    detail: str
    retry_at: float
    backoff: int


_lock = threading.RLock()
_cached_token: ServiceToken | None = None
_last_failure: _ActivationFailure | None = None


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
        # Refresh starts early. A failed refresh can use the old token until
        # its actual expiry, but never send an expired token during backoff.
        if token is None and _cached_token and time.time() < _cached_token.expires_at:
            return _cached_token
        _cached_token = token
        return token


def invalidate_token() -> None:
    global _cached_token
    with _lock:
        _cached_token = None


def _refusal_detail(response: httpx.Response) -> str:
    status = response.status_code
    detail = f"License activation failed (HTTP {status})"
    if status == 429:
        return "License activation is rate limited"
    if status in (401, 403):
        try:
            body = response.json()
        except ValueError:
            body = None
        reason = body.get("error") if isinstance(body, dict) else None
        if isinstance(reason, str):
            detail = _LICENSE_REFUSALS.get(reason, detail)
    return detail


def _activate() -> ServiceToken | None:
    """Called under the token lock so only one caller retries an expired window."""
    global _last_failure

    license_key = _get_configured_license_key()
    key_hash = hash_key(license_key) if license_key else ""
    url = get_activation_url()
    config = (url, key_hash)
    previous = _last_failure
    if previous and previous.config != config:
        previous = None
    if previous and time.monotonic() < previous.retry_at:
        return None

    try:
        import httpx

        from tfc.deployment_telemetry.config import get_version
        from tfc.deployment_telemetry.state import get_or_create_telemetry_state

        state = get_or_create_telemetry_state()
        payload = {"instance_id": str(state.instance_id), "version": get_version()}
        if key_hash:
            payload["license_key_hash"] = key_hash
    except Exception as exc:
        # A worker can read telemetry even if an event-loop caller could not.
        _last_failure = None
        logger.error("activation_preparation_failed", error_type=type(exc).__name__)
        return None

    retry_after = None
    status = None
    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        status = response.status_code
        if status == 200:
            token = _parse_service_token(response.json())
            if token is not None or not key_hash:  # Includes keyless OSS check-in.
                _last_failure = None
                return token
            detail = "Activation returned no service token"
        else:
            detail = _refusal_detail(response)
            if status == 429:
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
    except Exception:
        detail = "Could not complete license activation"

    backoff = 60
    if previous and status in (401, 403, 429):
        backoff = min(3600, 2 * previous.backoff)
    delay = backoff if retry_after is None else retry_after
    retry_at = time.monotonic() + delay
    _last_failure = _ActivationFailure(config, detail, retry_at, backoff)
    logger.error(
        "activation_failed", status_code=status, detail=detail, retry_after=delay
    )
    return None


def _parse_service_token(data: dict) -> ServiceToken | None:
    access_token = data.get("access_token")
    if not access_token:
        return None

    return ServiceToken(
        access_token=access_token,
        gateway_url=data.get("gateway_url", "https://gateway.futureagi.com"),
        expires_at=time.time() + data.get("expires_in", 3600),
        allowed_services=data.get("allowed_services", []),
        allowed_models=data.get("allowed_models", []),
        scope=data.get("scope", "oss"),
    )


def parse_retry_after(value: str | None) -> float | None:
    """Seconds named by a Retry-After header (delta-seconds or an HTTP date).

    The value is kept as sent, floored at zero. The retry policy bounds the
    wait, and must tell a window it can wait out from one it cannot: clamping
    here made an hour-long hint look like a 30 s one.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None
    if not math.isfinite(delay):
        return None
    return max(0.0, delay)


def _raise_for_managed_status(
    status_code: int,
    on_unauthorized: Callable[[], None],
    *,
    retry_after: str | None = None,
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
        raise ManagedServiceError(
            "RATE_LIMITED",
            "Managed AI rate limit exceeded",
            retry_after=parse_retry_after(retry_after),
        )
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
    except httpx.TimeoutException as exc:
        raise ManagedServiceError(
            "GATEWAY_TIMEOUT", "Managed AI gateway timed out"
        ) from exc
    except httpx.ConnectError as exc:
        raise ManagedServiceError(
            "GATEWAY_UNREACHABLE", "Cannot reach managed AI gateway"
        ) from exc

    _raise_for_managed_status(
        response.status_code,
        on_unauthorized,
        retry_after=response.headers.get("Retry-After"),
    )
    return response.json()


async def dispatch_managed_stream(
    *,
    url: str,
    api_key: str,
    json_body: dict,
    timeout: float,
    on_unauthorized: Callable[[], None],
) -> AsyncGenerator[dict, None]:
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
                _raise_for_managed_status(
                    response.status_code,
                    on_unauthorized,
                    retry_after=response.headers.get("Retry-After"),
                )
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


def _require_service_token() -> ServiceToken:
    # Read the failure from this lookup, not from a retry on another thread.
    with _lock:
        token = get_service_token()
        if token is None:
            failure = _last_failure
            detail = failure.detail if failure else "Could not obtain service token"
            raise ManagedServiceError("ACTIVATION_FAILED", detail)
    if token.scope == "oss" and not token.access_token:
        raise ManagedServiceError(
            "NO_ENTERPRISE_LICENSE", "Managed AI requires an Enterprise license"
        )
    return token


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
    token = _require_service_token()

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
    timeout: float = MANAGED_STREAM_TIMEOUT_SECONDS,
) -> AsyncGenerator[dict, None]:
    token = await asyncio.to_thread(_require_service_token)

    def _on_unauthorized() -> None:
        invalidate_token()
        raise ManagedServiceError(
            "TOKEN_EXPIRED", "Service token rejected — will refresh on next call"
        )

    async with aclosing(
        dispatch_managed_stream(
            url=token.gateway_url.rstrip("/") + path,
            api_key=token.access_token,
            json_body=json_body,
            timeout=timeout,
            on_unauthorized=_on_unauthorized,
        )
    ) as stream:
        async for chunk in stream:
            yield chunk


class ManagedServiceError(Exception):
    def __init__(self, code: str, detail: str, *, retry_after: float | None = None):
        self.code = code
        self.detail = detail
        self.retry_after = retry_after
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
