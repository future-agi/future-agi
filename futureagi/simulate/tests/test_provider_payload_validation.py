"""Regression tests for simulate provider-payload validation (GH-2662).

Twilio is a shared ``ProviderChoices`` value but was absent from simulate's
``SupportedProviders`` set, so a call execution carrying Twilio-keyed
``provider_call_data`` failed validation with a forbidden-keys error and the
request was refused.
"""

import pytest
from django.core.exceptions import ValidationError

from simulate.models.test_execution import CallExecution
from simulate.semantics import (
    SupportedProviders,
    validate_allowed_keys,
    validate_provider_sent_objects,
)
from simulate.serializers.alk_simulate_ingestion import ALKSimulateResultSerializer
from simulate.serializers.test_execution import CallExecutionSerializer
from tracer.models.observability_provider import ProviderChoices


def test_every_shared_provider_value_is_supported():
    """Simulate's set must not diverge from the shared provider enum."""
    missing = {choice.value for choice in ProviderChoices} - SupportedProviders
    assert not missing, f"shared provider values missing from simulate's set: {missing}"


def test_tool_calling_provider_list_does_not_gain_twilio():
    from simulate.semantics import ToolCallingSupportedProviders

    assert ProviderChoices.TWILIO not in ToolCallingSupportedProviders


class TestFieldValidator:
    def test_twilio_payload_is_accepted(self):
        validate_provider_sent_objects({"twilio": {"call_sid": "CA123"}})

    def test_existing_provider_payloads_are_accepted(self):
        for name in sorted(SupportedProviders - {"twilio"}):
            validate_provider_sent_objects({name: {"id": "x"}})

    def test_unknown_provider_is_still_rejected_with_existing_error(self):
        with pytest.raises(ValidationError) as exc:
            validate_provider_sent_objects({"not_a_provider": {"id": "x"}})
        message = str(exc.value)
        assert "Invalid provider keys" in message
        assert "not_a_provider" in message

    def test_non_dict_payload_is_still_rejected(self):
        with pytest.raises(ValidationError):
            validate_provider_sent_objects(["not", "a", "dict"])


class TestAllowedKeys:
    def test_twilio_payload_is_accepted(self):
        payload = {"twilio": {"call_sid": "CA123"}}
        assert validate_allowed_keys(payload) == payload

    def test_unknown_provider_is_still_rejected(self):
        with pytest.raises(ValueError, match="Contains forbidden keys"):
            validate_allowed_keys({"not_a_provider": {}})


class TestModelValidation:
    def test_model_clean_rejects_unknown_provider(self):
        call = CallExecution(provider_call_data={"not_a_provider": {}})
        with pytest.raises(ValidationError) as exc:
            call.full_clean(validate_unique=False, validate_constraints=False)
        assert "provider_call_data" in exc.value.message_dict

    def test_model_clean_accepts_twilio(self):
        call = CallExecution(provider_call_data={"twilio": {"call_sid": "CA123"}})
        try:
            call.full_clean(validate_unique=False, validate_constraints=False)
        except ValidationError as exc:
            # Required FKs are unset on this unsaved row; only provider keys
            # are under test here.
            assert "provider_call_data" not in exc.message_dict


class TestIngestionSerializers:
    def test_call_execution_write_accepts_twilio(self):
        serializer = CallExecutionSerializer(
            data={"provider_call_data": {"twilio": {"call_sid": "CA123"}}},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors

    def test_call_execution_write_still_rejects_unknown_provider(self):
        serializer = CallExecutionSerializer(
            data={"provider_call_data": {"not_a_provider": {}}},
            partial=True,
        )
        assert not serializer.is_valid()
        assert "provider_call_data" in serializer.errors

    def test_alk_result_serializer_accepts_twilio(self):
        serializer = ALKSimulateResultSerializer(
            data={
                "status": "completed",
                "provider_call_data": {"twilio": {"call_sid": "CA123"}},
            }
        )
        assert serializer.is_valid(), serializer.errors
