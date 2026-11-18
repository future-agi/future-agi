"""Contract tests for `response_format` declaration sites (TH-5941).

`response_format` is legitimately ``string | object`` at the provider level
(LiteLLM/OpenAI accept ``"text"`` / ``"json_object"`` strings AND structured
JSON-schema objects). Each declaration site must therefore use
``StringOrObjectField`` so that:

* the OpenAPI schema carries ``x-string-or-object`` (FE runtime validator and
  orval post-processor both generate the union from it), and
* the backend rejects arrays / numbers / booleans at runtime for callers that
  bypass the FE contract validator (SDK, curl, internal).

These tests pin every site so a future field rename can't silently flip one
back to object-only ``JSONField`` or too-loose ``JsonValueField``.
"""

import pytest
from rest_framework import serializers

from agent_playground.serializers.node import PromptTemplateDataSerializer
from model_hub.serializers.contracts import ColumnConfigResultSerializer
from model_hub.serializers.run_prompt import LitellmSerializer, PromptConfigSerializer
from tfc.utils.serializer_fields import StringOrObjectField

SITES = [
    LitellmSerializer,
    PromptConfigSerializer,
    ColumnConfigResultSerializer,
    PromptTemplateDataSerializer,
]


@pytest.mark.parametrize("serializer_cls", SITES)
def test_response_format_is_string_or_object_field(serializer_cls):
    field = serializer_cls().fields["response_format"]
    assert isinstance(field, StringOrObjectField), (
        f"{serializer_cls.__name__}.response_format must stay a "
        "StringOrObjectField — plain JSONField advertises object-only in the "
        "contract, JsonValueField degrades it to z.any()."
    )


@pytest.mark.parametrize("serializer_cls", SITES)
def test_response_format_accepts_string_and_object(serializer_cls):
    field = serializer_cls().fields["response_format"]
    assert field.run_validation("text") == "text"
    assert field.run_validation({"type": "json_object"}) == {"type": "json_object"}


@pytest.mark.parametrize("serializer_cls", SITES)
@pytest.mark.parametrize("bad", [[], [1, 2], 42, 1.5, True])
def test_response_format_rejects_non_string_non_object(serializer_cls, bad):
    field = serializer_cls().fields["response_format"]
    with pytest.raises(serializers.ValidationError):
        field.run_validation(bad)
