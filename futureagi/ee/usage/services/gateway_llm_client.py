"""Shared AgentCC gateway client for cloud, self-hosted EE, and OSS."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

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
            _get_setting("AGENTCC_INTERNAL_URL", "http://agentcc-gateway:8090"),
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


def get_gateway_client(force_new: bool = False):
    global _client

    mode = _deployment_mode()
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

        _client = OpenAI(
            api_key=api_key,
            base_url=f"{base_url.rstrip('/')}/v1",
            timeout=300.0,
            max_retries=2,
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


def call_llm_raw(client, **create_kwargs) -> GatewayRawResult:
    raw = client.chat.completions.with_raw_response.create(**create_kwargs)
    cost_usd = 0.0
    try:
        cost_header = raw.headers.get("x-agentcc-cost") if raw.headers else None
        if cost_header:
            cost_usd = float(cost_header)
    except (ValueError, TypeError, AttributeError):
        pass
    return GatewayRawResult(response=raw.parse(), cost_usd=cost_usd)


def call_llm(
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 8100,
    **kwargs,
) -> Optional[str]:
    mode = _deployment_mode()
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
) -> Optional[str]:
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

        _async_client = AsyncOpenAI(
            api_key=api_key,
            base_url=f"{base_url.rstrip('/')}/v1",
            timeout=300.0,
            max_retries=2,
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
) -> Optional[str]:
    mode = _deployment_mode()
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
