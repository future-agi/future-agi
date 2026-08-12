"""Contract tests for the legacy `configuration` field on PromptConfigSerializer (GH-2076).

The backend reads model params from the legacy `configuration` compatibility
object (see async_prompt_runner / prompt_service), but the serializer did not
declare it, so the generated OpenAPI contract could not express the legacy
wire shape (`template_format` and friends nested under `configuration`).

These tests pin the field's presence and behaviour so the contract generator
keeps emitting it.
"""

import pytest

from model_hub.serializers.run_prompt import PromptConfigSerializer


def test_prompt_config_exposes_legacy_configuration_field():
    fields = PromptConfigSerializer().fields
    assert "configuration" in fields
    # Both wire shapes must stay expressible side by side.
    assert "run_prompt_config" in fields


def test_configuration_accepts_legacy_keys():
    serializer = PromptConfigSerializer(
        data={
            "model": "gpt-4o",
            "configuration": {
                "model": "gpt-4o",
                "template_format": "jinja",
                "temperature": 0.7,
                "max_tokens": 1024,
                "tools": [{"config": {"type": "function"}}],
            },
            "run_prompt_config": {"modelName": "gpt-4o"},
        }
    )
    assert serializer.is_valid(), serializer.errors
    data = serializer.validated_data
    assert data["configuration"]["template_format"] == "jinja"
    assert data["configuration"]["model"] == "gpt-4o"
    assert data["configuration"]["tools"] == [{"config": {"type": "function"}}]
    assert data["run_prompt_config"] == {"modelName": "gpt-4o"}


def test_configuration_is_optional():
    serializer = PromptConfigSerializer(data={"model": "gpt-4o"})
    assert serializer.is_valid(), serializer.errors
    assert "configuration" not in serializer.validated_data


def test_configuration_allows_null_values_like_run_prompt_config():
    serializer = PromptConfigSerializer(
        data={"configuration": {"temperature": None, "template_format": None}}
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["configuration"] == {
        "temperature": None,
        "template_format": None,
    }


@pytest.mark.parametrize("bad", [[1, 2], "string", 42])
def test_configuration_rejects_non_object(bad):
    serializer = PromptConfigSerializer(data={"configuration": bad})
    assert not serializer.is_valid()
