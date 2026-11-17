"""Legacy kind aliases must agree with downstream LLM classification."""

import json

import pytest

from tracer.utils.adapters import normalize_span_attributes
from tracer.utils.adapters.otel_genai import OtelGenAIAdapter
from tracer.utils.otel import SpanAttributes


@pytest.mark.parametrize(
    "key",
    ["llm.request.type", "fi.span.kind", "gen_ai.span.kind", "openinference.span.kind"],
)
@pytest.mark.parametrize(
    "kind",
    [
        "chat",
        "llm",
        "LLM",
        " Llm ",
        "generate_content",
        "text_completion",
        "completion",
    ],
)
def test_kind_aliases_preserve_indexed_content_and_legacy_fields(key, kind):
    span = {
        "scope": {"name": "unqualified-sdk", "version": "1.0"},
        "attributes": {
            key: kind,
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "synthetic-model",
            "gen_ai.system": "openai",
            "gen_ai.prompt.0.role": "user",
            "gen_ai.prompt.0.content": "synthetic input",
            "gen_ai.completion.0.role": "assistant",
            "gen_ai.completion.0.content": "synthetic output",
            "gen_ai.usage.input_tokens": 10,
            "gen_ai.usage.output_tokens": 5,
        },
    }
    # Some explicit convention markers route through their own adapters. Check
    # both the changed adapter and the actual registry path.
    attrs = OtelGenAIAdapter().normalize(dict(span["attributes"]))
    assert attrs[SpanAttributes.SPAN_KIND] == "LLM"
    assert json.loads(attrs["input.value"])[0]["content"] == "synthetic input"
    assert json.loads(attrs["output.value"])["content"] == "synthetic output"
    assert attrs[SpanAttributes.RAW_INPUT][0]["content"] == "synthetic input"
    assert attrs[SpanAttributes.RAW_OUTPUT]["content"] == "synthetic output"
    assert attrs[SpanAttributes.LLM_MODEL_NAME] == "synthetic-model"
    assert attrs[SpanAttributes.LLM_TOKEN_COUNT_PROMPT] == 10
    assert attrs[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION] == 5
    if key in ("llm.request.type", "fi.span.kind", "gen_ai.span.kind"):
        normalize_span_attributes([span])
        assert (
            json.loads(span["attributes"]["input.value"])[0]["content"]
            == "synthetic input"
        )
        assert span["attributes"][SpanAttributes.LLM_TOKEN_COUNT_PROMPT] == 10


@pytest.mark.parametrize("kind", ["TOOL", "chain", "AGENT", "embedding", "custom_kind"])
def test_explicit_non_llm_kind_beats_operation_and_model(kind):
    attrs = OtelGenAIAdapter().normalize(
        {
            "fi.span.kind": kind,
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "synthetic-model",
            "gen_ai.prompt.0.content": "synthetic input",
            "gen_ai.completion.0.content": "synthetic output",
        }
    )
    assert attrs[SpanAttributes.SPAN_KIND] != "LLM"
    assert "input.value" not in attrs
    assert "output.value" not in attrs
    assert SpanAttributes.RAW_INPUT not in attrs
    assert SpanAttributes.LLM_MODEL_NAME not in attrs
