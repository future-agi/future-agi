"""
OTEL GenAI adapter — handles spans using standard OpenTelemetry GenAI semantic
conventions (gen_ai.* namespace) WITHOUT traceloop.* markers.

This covers the traceai SDK and any other SDK emitting plain OTEL GenAI attributes.

Unlike the OpenLLMetry adapter, this adapter preserves the gen_ai.* keys as-is
(they are already the standard convention) and only adds the legacy llm.* aliases
that downstream code expects.

Detection: any gen_ai.* attribute present AND no traceloop.* attribute.
(Spans with traceloop.* go through the OpenLLMetry adapter instead.)
"""

import json
import re
from typing import Any

from tracer.utils.adapters.base import (
    BaseTraceAdapter,
    extract_query,
    first_not_none,
    flatten_input_messages,
    flatten_output_messages,
    parse_json_attr,
    register_adapter,
    set_io_value,
)
from tracer.utils.otel import SpanAttributes
from tracer.utils.semantic_conventions import AttributeRegistry

# gen_ai.operation.name → span kind
_OPERATION_KIND_MAP = {
    "chat": "LLM",
    "completion": "LLM",
    "embedding": "EMBEDDING",
    "embeddings": "EMBEDDING",
    "rerank": "RERANKER",
    "execute_tool": "TOOL",
}

# gen_ai.system values → normalized provider name
_SYSTEM_PROVIDER_MAP = {
    "openai": "openai",
    "anthropic": "anthropic",
    "cohere": "cohere",
    "mistralai": "mistralai",
    "google": "google",
    "ollama": "ollama",
    "groq": "groq",
    "deepseek": "deepseek",
    "meta": "meta",
    "bedrock": "aws_bedrock",
    "azure": "azure_openai",
}

# Regex for indexed prompt/completion attributes
_PROMPT_RE = re.compile(r"^gen_ai\.prompt\.(\d+)\.(.+)$")
_COMPLETION_RE = re.compile(r"^gen_ai\.completion\.(\d+)\.(.+)$")


def _extract_indexed_messages(
    attributes: dict[str, Any], prefix_re: re.Pattern
) -> list[dict[str, Any]]:
    """Extract indexed message attributes into a list of message dicts."""
    messages: dict[int, dict[str, Any]] = {}
    for key, val in attributes.items():
        m = prefix_re.match(key)
        if not m:
            continue
        idx = int(m.group(1))
        field = m.group(2)
        if idx not in messages:
            messages[idx] = {}
        if field == "content" and isinstance(val, str):
            parsed = parse_json_attr(val)
            messages[idx][field] = parsed
        else:
            messages[idx][field] = val

    if not messages:
        return []

    result = []
    for idx in sorted(messages.keys()):
        msg = messages[idx]
        result.append(
            {
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
                **{k: v for k, v in msg.items() if k not in ("role", "content")},
            }
        )
    return result


def _content_parts(value: Any) -> list[Any]:
    """Alias GenAI text parts for the chat renderer; retain non-text parts intact."""
    parts = parse_json_attr(value)
    if not isinstance(parts, list):
        return []
    return [
        {**{k: v for k, v in part.items() if k != "content"}, "text": part["content"]}
        if isinstance(part, dict) and part.get("type") == "text" and "content" in part
        else part
        for part in parts
    ]


def _extract_messages(
    attributes: dict[str, Any], key: str, legacy_prefix: re.Pattern
) -> list[dict[str, Any]]:
    # Presence, not truthiness: an explicitly empty/redacted modern attribute
    # must not resurrect stale indexed content.
    if key not in attributes:
        return _extract_indexed_messages(attributes, legacy_prefix)
    messages = parse_json_attr(attributes[key])
    if not isinstance(messages, list):
        return []
    result = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if "parts" in message:
            message = {
                **{k: v for k, v in message.items() if k != "parts"},
                "content": _content_parts(message["parts"]),
            }
        result.append(message)
    return result


class OtelGenAIAdapter(BaseTraceAdapter):
    @property
    def source_name(self) -> str:
        return "traceai"

    def detect(self, attributes: dict[str, Any]) -> bool:
        # Match standard OTEL GenAI spans (gen_ai.*) that are NOT OpenLLMetry
        return any(k.startswith("gen_ai.") for k in attributes)

    def normalize(self, attributes: dict[str, Any]) -> dict[str, Any]:
        # --- Extract values (gen_ai.* keys are PRESERVED, not stripped) ---

        operation_name = attributes.get("gen_ai.operation.name", "").lower()

        # Model (response model takes priority)
        model = first_not_none(
            attributes.get("gen_ai.response.model"),
            attributes.get("gen_ai.request.model"),
        )

        # Provider
        system = first_not_none(
            attributes.get("gen_ai.system"),
            attributes.get("gen_ai.provider.name"),
        )
        if system:
            system = system.lower()

        # Token counts
        prompt_tokens = first_not_none(
            attributes.get("gen_ai.usage.input_tokens"),
            attributes.get("gen_ai.usage.prompt_tokens"),
        )
        completion_tokens = first_not_none(
            attributes.get("gen_ai.usage.output_tokens"),
            attributes.get("gen_ai.usage.completion_tokens"),
        )
        total_tokens = attributes.get("gen_ai.usage.total_tokens")

        has_input = any(
            key in attributes for key in ("input.value", "fi.llm.input", "raw.input")
        )
        has_output = any(
            key in attributes for key in ("output.value", "fi.llm.output", "raw.output")
        )
        input_messages = (
            []
            if has_input
            else _extract_messages(attributes, "gen_ai.input.messages", _PROMPT_RE)
        )
        output_messages = (
            []
            if has_output
            else _extract_messages(attributes, "gen_ai.output.messages", _COMPLETION_RE)
        )
        native_input = attributes.get("gen_ai.input.messages")
        if not has_input and (
            "gen_ai.input.messages" not in attributes
            or isinstance(parse_json_attr(native_input), list)
        ):
            instructions = _content_parts(attributes.get("gen_ai.system_instructions"))
            if instructions:
                input_messages.insert(0, {"role": "system", "content": instructions})

        # --- Determine span kind ---
        span_kind = next(
            (
                attributes[k]
                for k in (
                    "fi.span.kind",
                    "gen_ai.span.kind",
                    "llm.request.type",
                    "openinference.span.kind",
                )
                if attributes.get(k)
            ),
            "",
        )
        if not span_kind and operation_name in _OPERATION_KIND_MAP:
            span_kind = _OPERATION_KIND_MAP[operation_name]
        elif not span_kind and model:
            span_kind = "LLM"

        # Use the same alias/case rules as downstream observation conversion.
        # An explicit non-LLM (including an unknown kind) still beats the model.
        normalized_kind = AttributeRegistry.normalize_span_kind(span_kind)
        if normalized_kind != "unknown":
            span_kind = normalized_kind.upper()
        else:
            span_kind = _OPERATION_KIND_MAP.get(span_kind.lower().strip(), span_kind)
        if span_kind:
            attributes[SpanAttributes.SPAN_KIND] = span_kind
        is_llm = span_kind == "LLM"

        # --- input.value / output.value ---
        if is_llm and input_messages:
            set_io_value(attributes, "input", input_messages)
        if is_llm and output_messages:
            obs_output = (
                output_messages[0] if len(output_messages) == 1 else output_messages
            )
            set_io_value(attributes, "output", obs_output)

        if span_kind == "TOOL":
            for key, present, value in (
                ("input", has_input, attributes.get("gen_ai.tool.call.arguments")),
                (
                    "output",
                    has_output,
                    attributes.get(
                        "gen_ai.tool.call.result",
                        attributes.get("anthropic.tool_result.content"),
                    ),
                ),
            ):
                if not present and value is not None:
                    # Keep JSON scalars (notably false and zero) decodable too.
                    if isinstance(value, bool | int | float):
                        value = json.dumps(value)
                    set_io_value(attributes, key, value)

        # --- Add legacy llm.* aliases for downstream compat ---
        if is_llm:
            if model:
                attributes[SpanAttributes.LLM_MODEL_NAME] = model
            provider = _SYSTEM_PROVIDER_MAP.get(system, system or "")
            if provider:
                attributes[SpanAttributes.LLM_PROVIDER] = provider
                attributes[SpanAttributes.LLM_SYSTEM] = provider

            if prompt_tokens is not None:
                attributes[SpanAttributes.LLM_TOKEN_COUNT_PROMPT] = int(prompt_tokens)
            if completion_tokens is not None:
                attributes[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION] = int(
                    completion_tokens
                )
            if total_tokens is not None:
                attributes[SpanAttributes.LLM_TOKEN_COUNT_TOTAL] = int(total_tokens)
            elif prompt_tokens is not None and completion_tokens is not None:
                attributes[SpanAttributes.LLM_TOKEN_COUNT_TOTAL] = int(
                    prompt_tokens
                ) + int(completion_tokens)

            # Flatten messages for downstream
            if input_messages:
                flatten_input_messages(input_messages, attributes)
                extract_query(input_messages, attributes)
                attributes[SpanAttributes.RAW_INPUT] = input_messages

            if output_messages:
                flatten_output_messages(output_messages, attributes)
                raw = (
                    output_messages[0] if len(output_messages) == 1 else output_messages
                )
                attributes[SpanAttributes.RAW_OUTPUT] = raw

        attributes["gen_ai.trace.source"] = self.source_name
        return attributes


# Priority 9: after OpenLLMetry (8) so traceloop.* spans go to OpenLLMetry first,
# but OpenLLMetry now only matches traceloop.*, so this catches remaining gen_ai.* spans.
register_adapter("otel_genai", OtelGenAIAdapter(), priority=9)
