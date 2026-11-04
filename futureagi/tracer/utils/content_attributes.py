"""Content precedence shared by the single-span and bulk Python converters."""

import json
from typing import Any, Literal


def content_attribute(
    attributes: dict[str, Any], direction: Literal["input", "output"]
) -> Any:
    """An explicit alias, even empty/redacted, must suppress native fallback."""
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
