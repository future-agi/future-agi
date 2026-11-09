"""
Langfuse adapter — handles traces from Langfuse's Python/JS SDK (v3+) via OTLP.

The Langfuse SDK exclusively uses the ``langfuse.*`` namespace for span attributes,
with two exceptions: ``user.id`` and ``session.id`` (short names, no prefix).

It does NOT output gen_ai.*, openinference.*, or llm.* attributes — those are only
accepted on Langfuse's *receiving* side from third-party instrumentation.

Detection: any ``langfuse.*`` attribute present in span attributes.

Key attribute shapes:
  - langfuse.observation.type: "span" | "generation" | "event" | "embedding" | ...
  - langfuse.observation.usage_details: JSON string '{"input_tokens": 10, ...}'
  - langfuse.observation.model.parameters: JSON string
  - langfuse.observation.input / output: JSON-serialized strings
  - langfuse.observation.metadata: JSON string OR flattened as langfuse.observation.metadata.*
  - user.id, session.id: plain strings (already match fi.* convention)

Source: github.com/langfuse/langfuse-python — langfuse/_client/attributes.py
"""

from typing import Any

from tracer.utils.adapters.base import (
    BaseTraceAdapter,
    extract_query,
    first_not_none,
    flatten_input_messages,
    flatten_output_messages,
    guess_provider,
    parse_json_attr,
    register_adapter,
    set_io_value,
    strip_keys,
)
from tracer.utils.adapters.langfuse_skills import normalize_langfuse_skills
from tracer.utils.otel import SpanAttributes

# Langfuse observation type → fi.span.kind
_TYPE_MAP = {
    "generation": "LLM",
    "span": "CHAIN",
    "event": "UNKNOWN",
    "embedding": "EMBEDDING",
    "agent": "AGENT",
    "tool": "TOOL",
    "chain": "CHAIN",
    "retriever": "RETRIEVER",
    "evaluator": "EVALUATOR",
    "guardrail": "GUARDRAIL",
}

_METADATA_PREFIX = "langfuse.observation.metadata."


def _extract_metadata(attributes: dict[str, Any]) -> Any | None:
    """
    Extract metadata from Langfuse attributes.

    Langfuse SDK can output metadata in two forms:
    1. Single JSON string: ``langfuse.observation.metadata = '{"key": "value"}'``
    2. Flattened keys: ``langfuse.observation.metadata.key1 = "value1"``
    """
    single = attributes.get("langfuse.observation.metadata")
    if single is not None:
        return parse_json_attr(single)

    flattened = {}
    for key, val in attributes.items():
        if key.startswith(_METADATA_PREFIX):
            sub_key = key[len(_METADATA_PREFIX) :]
            if sub_key:
                flattened[sub_key] = parse_json_attr(val)

    return flattened if flattened else None


class LangfuseAdapter(BaseTraceAdapter):
    @property
    def source_name(self) -> str:
        return "langfuse"

    def detect(self, attributes: dict[str, Any]) -> bool:
        # Fast O(1) check for the most common Langfuse marker key, with
        # fallback to prefix scan only when the primary key is absent.
        if "langfuse.observation.type" in attributes:
            return True
        return any(k.startswith("langfuse.") for k in attributes)

    def normalize(self, attributes: dict[str, Any]) -> dict[str, Any]:
        retained_skill_metadata = normalize_langfuse_skills(attributes)
        # --- Extract values from langfuse.* attributes before stripping ---
        obs_type = attributes.get("langfuse.observation.type", "").lower()
        model = attributes.get("langfuse.observation.model.name")
        params_raw = attributes.get("langfuse.observation.model.parameters")
        usage_raw = attributes.get("langfuse.observation.usage_details")
        input_raw = attributes.get("langfuse.observation.input")
        output_raw = attributes.get("langfuse.observation.output")
        tags_raw = attributes.get("langfuse.trace.tags")
        prompt_name = attributes.get("langfuse.observation.prompt.name")
        prompt_version = attributes.get("langfuse.observation.prompt.version")

        # Compat aliases (SDK outputs user.id / session.id without prefix,
        # but server also accepts langfuse.user.id / langfuse.session.id)
        user_id = attributes.get("user.id") or attributes.get("langfuse.user.id")
        session_id = attributes.get("session.id") or attributes.get(
            "langfuse.session.id"
        )

        metadata = _extract_metadata(attributes)

        # Parse JSON string values
        obs_input = parse_json_attr(input_raw) if input_raw is not None else None
        obs_output = parse_json_attr(output_raw) if output_raw is not None else None
        params = parse_json_attr(params_raw) if params_raw is not None else None
        tags = parse_json_attr(tags_raw) if tags_raw is not None else None

        usage = None
        if usage_raw is not None:
            usage = parse_json_attr(usage_raw)
            if not isinstance(usage, dict):
                usage = None

        # --- Strip all langfuse.* keys ---
        strip_keys(attributes, "langfuse.")

        # --- fi.span.kind ---
        span_kind = _TYPE_MAP.get(obs_type, "")
        if not span_kind and model:
            span_kind = "LLM"
        if span_kind:
            attributes[SpanAttributes.FI_SPAN_KIND] = span_kind
        is_llm = span_kind == "LLM"

        # --- input.value / output.value + MIME types ---
        set_io_value(attributes, "input", obs_input)
        set_io_value(attributes, "output", obs_output)

        # --- LLM-specific attributes ---
        if is_llm:
            if model:
                attributes[SpanAttributes.LLM_MODEL_NAME] = model
                provider = guess_provider(model)
                if provider:
                    attributes[SpanAttributes.LLM_PROVIDER] = provider
                    attributes[SpanAttributes.LLM_SYSTEM] = provider

            if params is not None:
                attributes[SpanAttributes.LLM_INVOCATION_PARAMETERS] = params

            if usage:
                prompt_val = first_not_none(
                    usage.get("input_tokens"), usage.get("input")
                )
                if prompt_val is not None:
                    attributes[SpanAttributes.LLM_TOKEN_COUNT_PROMPT] = int(prompt_val)

                completion_val = first_not_none(
                    usage.get("output_tokens"), usage.get("output")
                )
                if completion_val is not None:
                    attributes[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION] = int(
                        completion_val
                    )

                total_val = first_not_none(
                    usage.get("total_tokens"), usage.get("total")
                )
                if total_val is not None:
                    attributes[SpanAttributes.LLM_TOKEN_COUNT_TOTAL] = int(total_val)
                elif prompt_val is not None and completion_val is not None:
                    attributes[SpanAttributes.LLM_TOKEN_COUNT_TOTAL] = int(
                        prompt_val
                    ) + int(completion_val)

            flatten_input_messages(obs_input, attributes)
            flatten_output_messages(obs_output, attributes)
            extract_query(obs_input, attributes)

            if obs_input is not None:
                attributes[SpanAttributes.RAW_INPUT] = obs_input
            if obs_output is not None:
                attributes[SpanAttributes.RAW_OUTPUT] = obs_output

        # --- Prompt template ---
        if prompt_name is not None:
            attributes[SpanAttributes.FI_PROMPT_TEMPLATE_NAME] = prompt_name
        if prompt_version is not None:
            attributes[SpanAttributes.LLM_PROMPT_TEMPLATE_VERSION] = prompt_version

        # --- User / Session ---
        if user_id:
            attributes[SpanAttributes.USER_ID] = user_id
        if session_id:
            attributes[SpanAttributes.SESSION_ID] = session_id

        # --- Tags ---
        if tags is not None:
            attributes[SpanAttributes.TAG_TAGS] = tags

        # --- Metadata ---
        if metadata is not None:
            attributes[SpanAttributes.METADATA] = metadata

        attributes["gen_ai.trace.source"] = self.source_name
        attributes.update(retained_skill_metadata)
        return attributes


register_adapter("langfuse", LangfuseAdapter(), priority=5)
