"""
Trace adapter registry, base class, and shared utilities.

Adapters detect foreign OTLP attribute formats (OpenInference, OTel GenAI, etc.)
and normalize them to FutureAGI's fi.* convention before downstream processing.

Follows the same registry pattern as integrations/services/base.py.
"""

import json
from abc import ABC, abstractmethod
from typing import Any

import structlog

from tracer.utils.normalization import normalize_span

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Registry: list of (priority, name, adapter) tuples, sorted by priority on insert
_ADAPTER_REGISTRY: list[tuple[int, str, "BaseTraceAdapter"]] = []


def register_adapter(name: str, adapter: "BaseTraceAdapter", priority: int = 50):
    """Register a trace adapter with a given priority (lower = checked first)."""
    _ADAPTER_REGISTRY.append((priority, name, adapter))
    _ADAPTER_REGISTRY.sort(key=lambda x: x[0])


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class BaseTraceAdapter(ABC):
    """Base class for trace format adapters."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Identifier for the source format, e.g. 'openinference', 'fi_native'."""
        ...

    @abstractmethod
    def detect(self, attributes: dict[str, Any]) -> bool:
        """Return True if this span's attributes match this adapter's format."""
        ...

    @abstractmethod
    def normalize(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Remap foreign attribute keys to fi.* convention. Returns modified dict."""
        ...


# ---------------------------------------------------------------------------
# Shared utilities — used by multiple adapters
# ---------------------------------------------------------------------------

# Model prefix → provider name (matches fi_instrumentation FiLLMProviderValues).
# Shared across Langfuse adapter (which must guess provider from model name)
# and integrations/transformers/langfuse_transformer.py.
MODEL_PROVIDER_MAP: dict[str, str] = {
    "gpt-": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "chatgpt-": "openai",
    "claude-": "anthropic",
    "gemini-": "google",
    "command-": "cohere",
    "mistral": "mistralai",
    "mixtral": "mistralai",
    "llama": "meta",
    "deepseek": "deepseek",
}


def parse_json_attr(value: Any) -> Any:
    """Parse a JSON string attribute, returning the original value if not parseable."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


def first_not_none(*values: Any) -> Any:
    """Return the first value that is not None."""
    for v in values:
        if v is not None:
            return v
    return None


def guess_provider(model: str) -> str:
    """Guess LLM provider from model name using MODEL_PROVIDER_MAP."""
    model_lower = model.lower()
    for prefix, provider in MODEL_PROVIDER_MAP.items():
        if model_lower.startswith(prefix):
            return provider
    return ""


def strip_keys(attributes: dict[str, Any], *prefixes: str) -> None:
    """Remove all keys from attributes that start with any of the given prefixes."""
    to_remove = [k for k in attributes if any(k.startswith(p) for p in prefixes)]
    for k in to_remove:
        del attributes[k]


def set_io_value(
    attributes: dict[str, Any],
    key: str,
    value: Any,
) -> None:
    """
    Set input.value or output.value with appropriate MIME type.

    Args:
        attributes: The attributes dict to modify.
        key: Either "input" or "output".
        value: The parsed value (dict, list, or scalar).
    """
    if value is None:
        return
    if isinstance(value, (dict, list)):
        attributes[f"{key}.value"] = json.dumps(value, default=str)
        attributes[f"{key}.mime_type"] = "application/json"
    else:
        attributes[f"{key}.value"] = str(value)
        attributes[f"{key}.mime_type"] = "text/plain"


def _flatten_tool_calls(
    msg: dict[str, Any], msg_prefix: str, attrs: dict[str, Any]
) -> None:
    """Flatten tool calls from a message dict into dotted attributes.

    Handles both structured ``tool_calls`` lists and indexed key format
    (e.g. ``tool_calls.0.function.name``).
    """
    # Structured list format (e.g. tool call messages)
    tool_calls = msg.get("tool_calls")
    if isinstance(tool_calls, list):
        for ti, tc in enumerate(tool_calls):
            if not isinstance(tc, dict):
                continue
            tc_pfx = f"{msg_prefix}.tool_calls.{ti}.tool_call"
            if tc.get("id"):
                attrs[f"{tc_pfx}.id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                attrs[f"{tc_pfx}.function.name"] = fn["name"]
            if fn.get("arguments"):
                attrs[f"{tc_pfx}.function.arguments"] = fn["arguments"]

    # Indexed key format (e.g. from gen_ai.prompt.{i}.tool_calls.{j}.*)
    for mk, mv in msg.items():
        if mk.startswith("tool_calls."):
            parts = mk.split(".")
            if len(parts) >= 3:
                ti_idx, field = parts[1], ".".join(parts[2:])
                attrs[f"{msg_prefix}.tool_calls.{ti_idx}.tool_call.{field}"] = mv


def flatten_input_messages(messages: Any, attrs: dict[str, Any]) -> None:
    """
    Flatten input messages into llm.input_messages.{i}.message.* format.

    Handles:
      - Standard message dicts with role/content
      - Multi-part content blocks
      - Tool calls (both structured and indexed)
    """
    if not isinstance(messages, list):
        return
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if "role" not in msg and "text" in msg:
            role = "user"
            content = msg["text"]
        msg_pfx = f"llm.input_messages.{i}.message"
        attrs[f"{msg_pfx}.role"] = role
        attrs[f"{msg_pfx}.content"] = content

        # Multi-part content blocks
        if isinstance(content, list):
            for ci, block in enumerate(content):
                if isinstance(block, dict):
                    btype = block.get("type", "text")
                    pfx = f"{msg_pfx}.contents.{ci}.message_content"
                    attrs[f"{pfx}.type"] = btype
                    if btype == "text":
                        attrs[f"{pfx}.text"] = block.get("text", "")

        _flatten_tool_calls(msg, msg_pfx, attrs)


def flatten_output_messages(messages: Any, attrs: dict[str, Any]) -> None:
    """
    Flatten output into llm.output_messages.{i}.message.* format.

    Handles string, dict (single message), or list of message dicts.
    Also sets the ``response`` attribute from the first assistant content.
    """
    if isinstance(messages, str):
        attrs["llm.output_messages.0.message.role"] = "assistant"
        attrs["llm.output_messages.0.message.content"] = messages
        attrs["response"] = messages
        return

    if isinstance(messages, dict):
        content = messages.get("content") or messages.get("text") or ""
        has_tool_calls = "tool_calls" in messages
        if content or has_tool_calls:
            attrs["llm.output_messages.0.message.role"] = messages.get(
                "role", "assistant"
            )
            attrs["llm.output_messages.0.message.content"] = content
            if content:
                attrs["response"] = (
                    content
                    if isinstance(content, str)
                    else json.dumps(content, default=str)
                )
            _flatten_tool_calls(messages, "llm.output_messages.0.message", attrs)
        return

    if not isinstance(messages, list):
        return

    response_set = False
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "assistant")
        content = msg.get("content", "")
        attrs[f"llm.output_messages.{i}.message.role"] = role
        attrs[f"llm.output_messages.{i}.message.content"] = content

        # Set response from first non-empty content
        if not response_set and content:
            attrs["response"] = (
                content
                if isinstance(content, str)
                else json.dumps(content, default=str)
            )
            response_set = True

        _flatten_tool_calls(msg, f"llm.output_messages.{i}.message", attrs)


def extract_query(messages: Any, attrs: dict[str, Any]) -> None:
    """Extract the last user message content as the ``query`` attribute."""
    if not isinstance(messages, list):
        return
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            c = msg.get("content", "")
            attrs["query"] = c if isinstance(c, str) else json.dumps(c, default=str)
            break


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _detect_adapter(attributes: dict[str, Any]) -> BaseTraceAdapter | None:
    """Find the first adapter whose marker matches the span attributes."""
    for _priority, _name, adapter in _ADAPTER_REGISTRY:
        if adapter.detect(attributes):
            return adapter
    return None


def normalize_span_attributes(otel_data_list: list[dict[str, Any]]) -> None:
    """
    Normalize span attributes in-place for a batch of parsed OTLP spans.

    Iterates each span, detects its source format, and remaps attributes
    to the fi.* convention. Spans with no matching adapter pass through unchanged.

    After normalization, propagates well-known attributes to top-level span_data
    fields that the bulk converter reads from (e.g. metadata, session_name).
    """
    for span_data in otel_data_list:
        if not span_data.get("attributes"):
            continue
        attributes = normalize_span(span_data)

        adapter = _detect_adapter(attributes)
        if adapter is not None:
            span_data["attributes"] = adapter.normalize(attributes)

        normalize_span(span_data)  # legacy adapters must not restore summary aliases

        # Propagate adapter-set attributes to top-level fields that the bulk
        # converter reads from otel_span (not from attributes).
        _propagate_to_span_data(span_data)


def _propagate_to_span_data(span_data: dict[str, Any]) -> None:
    """
    Copy well-known attributes to top-level span_data fields.

    The bulk converter (_convert_single_span) reads some fields from the top-level
    span_data dict rather than from span attributes. For foreign formats where these
    values only exist in span attributes after adapter normalization, we need to
    propagate them up.
    """
    attributes = span_data.get("attributes", {})

    # metadata: bulk converter reads otel_span["metadata"], not attributes["metadata"]
    if not span_data.get("metadata") and attributes.get("metadata"):
        span_data["metadata"] = attributes["metadata"]
