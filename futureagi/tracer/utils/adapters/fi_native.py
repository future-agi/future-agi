"""
FI Native adapter — normalizes spans using fi.* convention to gen_ai.*.

Registered at lowest priority so foreign-format adapters get checked first.
"""

import json
from typing import Any

from tracer.utils.adapters.base import (
    BaseTraceAdapter,
    parse_json_attr,
    register_adapter,
    set_io_value,
)
from tracer.utils.otel import SpanAttributes


class FiNativeAdapter(BaseTraceAdapter):
    @property
    def source_name(self) -> str:
        return "traceai"

    def detect(self, attributes: dict[str, Any]) -> bool:
        return "fi.span.kind" in attributes

    def normalize(self, attributes: dict[str, Any]) -> dict[str, Any]:
        # Normalize fi.span.kind → gen_ai.span.kind for downstream consistency
        fi_kind = attributes.get("fi.span.kind")
        if fi_kind and "gen_ai.span.kind" not in attributes:
            attributes[SpanAttributes.SPAN_KIND] = fi_kind

        # Normalize fi.llm.input/fi.llm.output → input.value/output.value
        # Older SDKs or manual instrumentation may use fi.llm.* keys
        for fi_key, std_key in [
            ("fi.llm.input", "input"),
            ("fi.llm.output", "output"),
        ]:
            fi_val = attributes.get(fi_key)
            if fi_val is not None and attributes.get(f"{std_key}.value") is None:
                parsed = parse_json_attr(fi_val)
                set_io_value(attributes, std_key, parsed)

        # Ensure mime_type is set when value exists but mime_type is missing
        for prefix in ("input", "output"):
            val = attributes.get(f"{prefix}.value")
            if val is not None and attributes.get(f"{prefix}.mime_type") is None:
                if isinstance(val, str):
                    try:
                        json.loads(val)
                        attributes[f"{prefix}.mime_type"] = "application/json"
                    except (json.JSONDecodeError, TypeError):
                        attributes[f"{prefix}.mime_type"] = "text/plain"
                else:
                    attributes[f"{prefix}.mime_type"] = "text/plain"

        attributes["gen_ai.trace.source"] = self.source_name
        return attributes


register_adapter("fi_native", FiNativeAdapter(), priority=100)
