from __future__ import annotations

from typing import Any

from ee.licensing.activation_client import call_managed_service


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


def chat_completion(payload: dict[str, Any]) -> dict[str, Any]:
    model = payload.get("model")
    if not is_managed_model(model):
        raise ValueError(f"Model {model!r} is not a FutureAGI-managed model")
    return call_managed_service(
        path="/v1/chat/completions",
        json_body=payload,
    )


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
