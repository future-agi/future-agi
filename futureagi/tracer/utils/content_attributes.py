"""Content precedence shared by the single-span and bulk Python converters."""

import json
from typing import Any, Literal


def has_native_content(attributes: dict[str, Any]) -> bool:
    """Whether presence-sensitive content precedence applies to this span."""
    return any(
        key in attributes
        for key in (
            "gen_ai.input.messages",
            "gen_ai.output.messages",
            "gen_ai.system_instructions",
            "gen_ai.tool.call.arguments",
            "gen_ai.tool.call.result",
            "anthropic.tool_result.content",
        )
    )


def content_attribute(
    attributes: dict[str, Any], direction: Literal["input", "output"]
) -> Any:
    """An explicit alias, even empty/redacted, must suppress native fallback."""
    if not has_native_content(attributes):
        # Ordinary legacy spans keep their established empty-placeholder fallback.
        value = attributes.get(f"fi.llm.{direction}") or attributes.get(
            f"{direction}.value"
        )
        if (
            value in (None, "", "[]", [])
            and attributes.get(f"raw.{direction}") is not None
        ):
            value = attributes[f"raw.{direction}"]
        return value if value else None

    for key in (f"{direction}.value", f"fi.llm.{direction}", f"raw.{direction}"):
        if key in attributes:
            value = attributes[key]
            return None if value == "" else value

    value = attributes.get(f"gen_ai.{direction}.messages")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if isinstance(value, list):
        messages = [message for message in value if isinstance(message, dict)]
        return messages or None
    return None
