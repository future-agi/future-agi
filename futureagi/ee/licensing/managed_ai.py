from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ee.licensing.activation_client import (
    ManagedServiceError,
    call_managed_service,
    dispatch_managed_request,
    dispatch_managed_stream,
    stream_managed_service,
)


def is_managed_model(model: object) -> bool:
    value = str(model or "")
    return (
        value == "falcon_ai"
        or value.startswith("turing_")
        or value.startswith("protect")
    )


def service_for_model(model: object) -> str | None:
    value = str(model or "")
    if value == "falcon_ai":
        return "falcon"
    if value.startswith("turing_"):
        return "turing"
    if value.startswith("protect"):
        return "protect"
    return None


def _cloud_gateway_credentials() -> tuple[str, str]:
    from ee.usage.services.gateway_llm_client import _get_setting

    base_url = _get_setting("AGENTCC_INTERNAL_URL", "http://agentcc-gateway:8090")
    api_key = _get_setting("AGENTCC_INTERNAL_API_KEY")
    if not api_key:
        raise ManagedServiceError(
            "GATEWAY_UNCONFIGURED",
            "AGENTCC_INTERNAL_API_KEY is not configured for managed AI",
        )
    return base_url, api_key


def _cloud_chat_completion(
    payload: dict[str, Any],
    *,
    path: str,
    timeout: float,
) -> dict[str, Any]:
    base_url, api_key = _cloud_gateway_credentials()

    def _on_unauthorized() -> None:
        raise ManagedServiceError(
            "GATEWAY_UNAUTHORIZED",
            "Managed AI gateway rejected the internal API key",
        )

    return dispatch_managed_request(
        url=base_url.rstrip("/") + path,
        api_key=api_key,
        json_body=payload,
        timeout=timeout,
        on_unauthorized=_on_unauthorized,
    )


def chat_completion(payload: dict[str, Any]) -> dict[str, Any]:
    model = payload.get("model")
    if not is_managed_model(model):
        raise ValueError(f"Model {model!r} is not a FutureAGI-managed model")

    from ee.usage.deployment import DeploymentMode

    if DeploymentMode.is_cloud():
        return _cloud_chat_completion(
            payload,
            path="/v1/chat/completions",
            timeout=300.0,
        )
    return call_managed_service(
        path="/v1/chat/completions",
        json_body=payload,
    )


async def stream_chat_completion(
    payload: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    model = payload.get("model")
    if not is_managed_model(model):
        raise ValueError(f"Model {model!r} is not a FutureAGI-managed model")

    from ee.usage.deployment import DeploymentMode

    if not DeploymentMode.is_cloud():
        async for chunk in stream_managed_service(json_body=payload):
            yield chunk
        return

    base_url, api_key = _cloud_gateway_credentials()

    def _on_unauthorized() -> None:
        raise ManagedServiceError(
            "GATEWAY_UNAUTHORIZED",
            "Managed AI gateway rejected the internal API key",
        )

    async for chunk in dispatch_managed_stream(
        url=base_url.rstrip("/") + "/v1/chat/completions",
        api_key=api_key,
        json_body=payload,
        timeout=300.0,
        on_unauthorized=_on_unauthorized,
    ):
        yield chunk


def response_content(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def response_usage(response: dict[str, Any]) -> dict[str, int]:
    usage = response.get("usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }
