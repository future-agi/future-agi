"""Backend serializer tests for PR #967 / TH-5882.

The FE contract tests under `frontend/src/api/contracts/__tests__/`
exercise the *generated zod* — that's the FE half of the contract. These
tests cover the *backend* half: the typed `MessageItem` / `PromptModelParams`
/ `PromptConfiguration` serializers and the `_ExtraFieldsMixin` they share.

The mixin's job is to copy undeclared keys from the inbound payload into
the validated output. ``additionalProperties: true`` on the swagger schema
is only half the fix — the FE contract accepts the keys, but DRF's stock
`Serializer.to_internal_value` would silently strip anything not declared
in `fields`. Without the mixin, the FE round-trips a provider-specific
``custom_provider_key`` key, the backend persists ``{}`` minus that key,
and the next read drops it on the floor. These tests pin that contract.
"""

import pytest
from rest_framework import serializers as drf_serializers

from model_hub.serializers.experiments import (
    MessageItemSerializer,
    PromptConfigurationSerializer,
    PromptModelParamsSerializer,
)


class TestExtraFieldsMixinPassthrough:
    """``_ExtraFieldsMixin`` copies undeclared keys into ``validated_data``.

    Exercised through the three host serializers that use it. Each test
    submits a payload with a typed-known key + an unknown provider-specific
    key and asserts the unknown key survives validation.
    """

    def test_prompt_model_params_passes_unknown_provider_key(self):
        # The canonical case the FE contract test asserts on the zod side:
        # model_params accepts arbitrary provider-specific keys.
        s = PromptModelParamsSerializer(
            data={"temperature": 0.7, "custom_provider_key": "value"}
        )
        assert s.is_valid(), s.errors
        # Typed key validated and round-trips…
        assert s.validated_data["temperature"] == 0.7
        # …and the unknown provider key is NOT stripped by DRF.
        assert s.validated_data["custom_provider_key"] == "value"

    def test_prompt_configuration_passes_unknown_key(self):
        s = PromptConfigurationSerializer(
            data={"tool_choice": "auto", "custom_field": "custom_value"}
        )
        assert s.is_valid(), s.errors
        assert s.validated_data["tool_choice"] == "auto"
        assert s.validated_data["custom_field"] == "custom_value"

    def test_message_item_passes_unknown_key(self):
        # MessageItem accepts arbitrary fields too — providers add metadata
        # like `name`, `tool_call_id`, etc. that the FE may pass through.
        s = MessageItemSerializer(
            data={
                "role": "user",
                "content": "Hello",
                "custom_meta": {"tag": "important"},
            }
        )
        assert s.is_valid(), s.errors
        assert s.validated_data["custom_meta"] == {"tag": "important"}

    def test_typed_key_validation_still_fires(self):
        # The mixin must not weaken validation on declared keys. ``temperature``
        # is typed as FloatField; a non-numeric value must still fail.
        s = PromptModelParamsSerializer(
            data={"temperature": "not-a-float", "custom_key": "still-ok"}
        )
        assert not s.is_valid()
        assert "temperature" in s.errors

    def test_payload_with_no_extras_still_works(self):
        # The mixin must not break the all-typed-keys-only case.
        s = PromptModelParamsSerializer(
            data={"temperature": 0.5, "max_tokens": 100}
        )
        assert s.is_valid(), s.errors
        assert s.validated_data == {"temperature": 0.5, "max_tokens": 100}


class TestMessageItemSerializerContentField:
    """``content`` uses ``StringOrArrayField`` — verify the runtime guard
    fires through the experiment serializer stack, not just on a bare host.

    This is the integration test for the field-level unit tests in
    ``tfc/utils/tests/test_serializer_fields.py`` — it exercises the same
    guard in the path the FE actually hits via experiment create.
    """

    def test_content_accepts_string(self):
        s = MessageItemSerializer(data={"role": "user", "content": "Hello"})
        assert s.is_valid(), s.errors

    def test_content_accepts_array(self):
        s = MessageItemSerializer(
            data={"role": "user", "content": [{"type": "text", "text": "Hi"}]}
        )
        assert s.is_valid(), s.errors

    def test_content_rejects_integer(self):
        # The contract says string|array; an SDK / curl that sends a number
        # past the FE validator must be rejected at the boundary.
        s = MessageItemSerializer(data={"role": "user", "content": 42})
        assert not s.is_valid()
        assert "content" in s.errors

    def test_content_rejects_dict(self):
        s = MessageItemSerializer(data={"role": "user", "content": {"foo": "bar"}})
        assert not s.is_valid()
        assert "content" in s.errors


class TestPromptModelParamsResponseFormat:
    """``response_format`` uses ``StringOrObjectField`` — verify the guard
    fires through the experiment serializer stack."""

    def test_response_format_accepts_string(self):
        s = PromptModelParamsSerializer(data={"response_format": "json_object"})
        assert s.is_valid(), s.errors

    def test_response_format_accepts_object(self):
        s = PromptModelParamsSerializer(
            data={"response_format": {"type": "json_schema"}}
        )
        assert s.is_valid(), s.errors

    def test_response_format_rejects_array(self):
        s = PromptModelParamsSerializer(data={"response_format": []})
        assert not s.is_valid()
        assert "response_format" in s.errors

    def test_response_format_rejects_integer(self):
        s = PromptModelParamsSerializer(data={"response_format": 42})
        assert not s.is_valid()
        assert "response_format" in s.errors
