"""Shared AgentCC gateway client for cloud, self-hosted EE, and OSS."""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from typing import Any

import structlog

from ee.licensing.activation_client import ManagedServiceError

logger = structlog.get_logger(__name__)

_client = None
_async_client = None


def _get_setting(name: str, default: str = "") -> str:
    try:
        from django.conf import settings

        return getattr(settings, name, os.environ.get(name, default))
    except Exception:
        return os.environ.get(name, default)


def _deployment_mode() -> str:
    if _get_setting("AGENTCC_INTERNAL_API_KEY"):
        return "cloud"
    if _get_setting("EE_LICENSE_KEY"):
        return "ee"
    return "oss"


def _resolve_gateway_config() -> tuple[str, str, str]:
    mode = _deployment_mode()
    if mode == "cloud":
        return (
            _get_setting("AGENTCC_INTERNAL_URL", "http://agentcc-gateway:8080"),
            _get_setting("AGENTCC_INTERNAL_API_KEY"),
            mode,
        )

    if mode == "ee":
        token = _get_ee_service_token()
        if token is None or token.is_expired or not token.access_token:
            return (
                _get_setting(
                    "FUTUREAGI_CLOUD_GATEWAY_URL",
                    "https://gateway.futureagi.com",
                ),
                "",
                mode,
            )
        return token.gateway_url, token.access_token, mode

    return "", "", mode


def _get_ee_service_token():
    try:
        from ee.licensing.activation_client import get_service_token

        return get_service_token()
    except ImportError:
        return None


def _license_bound_api_key(access_token: str):
    """Hand the SDK a service token that is only usable with a license key.

    The OpenAI SDK calls a callable ``api_key`` before every request attempt,
    including its own automatic retries, and outside its retry handler. So a
    self-hosted client stops sending as soon as the license key is unset,
    empty or whitespace-only, even part-way through one SDK call, and the
    caller gets NO_ENTERPRISE_LICENSE unwrapped. While the key is set it
    returns the same token, so the request is unchanged.
    """

    def api_key() -> str:
        from ee.licensing.activation_client import require_configured_license_key

        require_configured_license_key()
        return access_token

    return api_key


def _async_license_bound_api_key(access_token: str):
    """``_license_bound_api_key`` for AsyncOpenAI, which awaits ``api_key``."""
    check = _license_bound_api_key(access_token)

    async def api_key() -> str:
        return check()

    return api_key


@functools.cache
def _sdk_passthrough_denial_type() -> type[ManagedServiceError]:
    from openai import OpenAIError

    class GatewayLicenseDenial(ManagedServiceError, OpenAIError):
        """NO_ENTERPRISE_LICENSE raised inside the SDK's HTTP send.

        The SDK re-raises an ``OpenAIError`` from its send as is, so this
        denial is not retried or wrapped in ``APIConnectionError``.
        """

    return GatewayLicenseDenial


def _require_license_for_request(request) -> None:
    """httpx request hook: re-check the license key before every send.

    The api_key callable runs once per SDK attempt, but httpx then follows
    redirects inside that attempt, re-sending the prompt and bearer. httpx
    runs request hooks before each hop, so a key removed part-way through
    stops the next hop.
    """
    from ee.licensing.activation_client import require_configured_license_key

    try:
        require_configured_license_key()
    except ManagedServiceError as exc:
        raise _sdk_passthrough_denial_type()(exc.code, exc.detail) from None


async def _arequire_license_for_request(request) -> None:
    _require_license_for_request(request)


def _license_bound_http_client(*, is_async: bool):
    """The SDK's default httpx client plus ``_require_license_for_request``.

    ``DefaultHttpxClient`` keeps the SDK's own timeout, connection limits and
    redirect following, so a licensed request is unchanged.
    """
    from openai import DefaultAsyncHttpxClient, DefaultHttpxClient

    if is_async:
        return DefaultAsyncHttpxClient(
            event_hooks={"request": [_arequire_license_for_request]}
        )
    return DefaultHttpxClient(event_hooks={"request": [_require_license_for_request]})


def _is_license_denial(exc: BaseException) -> bool:
    return isinstance(exc, ManagedServiceError) and exc.code == "NO_ENTERPRISE_LICENSE"


def get_gateway_client(force_new: bool = False):
    global _client

    mode = _deployment_mode()
    if mode == "oss":
        _client = None
        return None
    if _client is not None and not force_new:
        if mode != "ee":
            return _client
        token = _get_ee_service_token()
        if token and not token.is_expired:
            return _client
        _client = None

    base_url, api_key, mode = _resolve_gateway_config()
    if not api_key:
        if mode == "ee":
            raise ManagedServiceError(
                "ACTIVATION_FAILED",
                "Could not obtain a managed gateway service token",
            )
        return None

    try:
        from openai import OpenAI

        # Cloud keeps its internal key and default HTTP client as is; a
        # self-hosted client re-checks the license key before every SDK
        # attempt and every HTTP request, including redirect hops.
        license_bound = (
            {
                "api_key": _license_bound_api_key(api_key),
                "http_client": _license_bound_http_client(is_async=False),
            }
            if mode == "ee"
            else {"api_key": api_key}
        )
        _client = OpenAI(
            base_url=f"{base_url.rstrip('/')}/v1",
            timeout=300.0,
            max_retries=2,
            **license_bound,
        )
        logger.info("gateway_client_ready", base_url=base_url, mode=mode)
        return _client
    except ImportError as exc:
        if mode == "ee":
            raise ManagedServiceError(
                "GATEWAY_CLIENT_UNAVAILABLE",
                "Managed gateway client dependency is unavailable",
            ) from exc
        logger.warning("openai_sdk_not_installed")
        return None
    except Exception as exc:
        if mode == "ee":
            raise ManagedServiceError(
                "GATEWAY_CLIENT_UNAVAILABLE",
                "Managed gateway client could not be initialized",
            ) from exc
        logger.exception("gateway_client_init_failed", error=str(exc))
        return None


@dataclass(frozen=True)
class GatewayRawResult:
    response: Any
    cost_usd: float


def require_gateway_access() -> None:
    """Raise NO_ENTERPRISE_LICENSE before a retained gateway client sends.

    Callers keep gateway clients across turns and instances. Cloud
    authenticates with its internal key; every other client was acquired with
    a self-hosted license, so it must stop sending once the license key is
    unset, empty or whitespace-only. Call before each request, outside any
    retry or fallback handler. The SDK's own retries inside one call are
    covered by the self-hosted client's ``_license_bound_api_key``.
    """
    if _deployment_mode() != "cloud":
        from ee.licensing.activation_client import require_configured_license_key

        require_configured_license_key()


def call_llm_raw(client, **create_kwargs) -> GatewayRawResult:
    require_gateway_access()
    raw = client.chat.completions.with_raw_response.create(**create_kwargs)
    cost_usd = 0.0
    try:
        cost_header = raw.headers.get("x-agentcc-cost") if raw.headers else None
        if cost_header:
            cost_usd = float(cost_header)
    except (ValueError, TypeError, AttributeError):
        pass
    return GatewayRawResult(response=raw.parse(), cost_usd=cost_usd)


def _check_managed_model_license(model: str, mode: str) -> None:
    if mode == "oss":
        from ee.licensing.activation_client import require_configured_license_key
        from ee.licensing.managed_ai import is_managed_model

        # Managed model names must not fall through to the BYOK provider path.
        # Cloud uses its internal key and licensed EE resolves a service token.
        if is_managed_model(model):
            require_configured_license_key()


def call_llm(
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 8100,
    **kwargs,
) -> str | None:
    mode = _deployment_mode()
    _check_managed_model_license(model, mode)
    client = get_gateway_client()

    if client is not None:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            return response.choices[0].message.content
        except Exception as exc:
            # A key removed during the call (e.g. before an SDK retry) keeps
            # its own typed outcome and never reaches the provider fallback.
            if _is_license_denial(exc):
                raise
            if mode == "ee":
                raise ManagedServiceError(
                    "GATEWAY_REQUEST_FAILED",
                    "Managed gateway request failed",
                ) from exc
            logger.warning("gateway_call_failed", model=model, error=str(exc))

    return _call_litellm(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )


def _call_litellm(
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    **kwargs,
) -> str | None:
    try:
        import litellm

        response = litellm.completion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            num_retries=3,
            request_timeout=300,
            **kwargs,
        )
        return response.choices[0].message.content
    except Exception as exc:
        logger.exception("litellm_fallback_failed", model=model, error=str(exc))
        return None


def get_async_gateway_client(force_new: bool = False):
    global _async_client

    mode = _deployment_mode()
    if mode == "oss":
        _async_client = None
        return None
    if _async_client is not None and not force_new:
        if mode != "ee":
            return _async_client
        token = _get_ee_service_token()
        if token and not token.is_expired:
            return _async_client
        _async_client = None

    base_url, api_key, mode = _resolve_gateway_config()
    if not api_key:
        if mode == "ee":
            raise ManagedServiceError(
                "ACTIVATION_FAILED",
                "Could not obtain a managed gateway service token",
            )
        return None

    try:
        from openai import AsyncOpenAI

        license_bound = (
            {
                "api_key": _async_license_bound_api_key(api_key),
                "http_client": _license_bound_http_client(is_async=True),
            }
            if mode == "ee"
            else {"api_key": api_key}
        )
        _async_client = AsyncOpenAI(
            base_url=f"{base_url.rstrip('/')}/v1",
            timeout=300.0,
            max_retries=2,
            **license_bound,
        )
        logger.info("async_gateway_client_ready", base_url=base_url, mode=mode)
        return _async_client
    except ImportError as exc:
        if mode == "ee":
            raise ManagedServiceError(
                "GATEWAY_CLIENT_UNAVAILABLE",
                "Managed gateway client dependency is unavailable",
            ) from exc
        logger.warning("openai_sdk_not_installed")
        return None
    except Exception as exc:
        if mode == "ee":
            raise ManagedServiceError(
                "GATEWAY_CLIENT_UNAVAILABLE",
                "Managed gateway client could not be initialized",
            ) from exc
        logger.exception("async_gateway_client_init_failed", error=str(exc))
        return None


async def acall_llm(
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 8100,
    **kwargs,
) -> str | None:
    mode = _deployment_mode()
    _check_managed_model_license(model, mode)
    client = get_async_gateway_client()

    if client is not None:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            return response.choices[0].message.content
        except Exception as exc:
            if _is_license_denial(exc):
                raise
            if mode == "ee":
                raise ManagedServiceError(
                    "GATEWAY_REQUEST_FAILED",
                    "Managed gateway request failed",
                ) from exc
            logger.warning("async_gateway_call_failed", model=model, error=str(exc))

    try:
        import litellm

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            num_retries=3,
            request_timeout=300,
            **kwargs,
        )
        return response.choices[0].message.content
    except Exception as exc:
        logger.exception("litellm_async_fallback_failed", model=model, error=str(exc))
        return None


def invalidate_client() -> None:
    global _client, _async_client
    _client = None
    _async_client = None
