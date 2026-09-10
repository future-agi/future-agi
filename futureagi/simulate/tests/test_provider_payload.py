"""Provider payload validation shared by Simulate calls and snapshots."""

import pytest
from django.core.exceptions import ValidationError
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from simulate.models.test_execution import CallExecution, CallExecutionSnapshot
from simulate.semantics import ProviderPayload
from tracer.models.observability_provider import ProviderChoices


@pytest.mark.unit
@pytest.mark.parametrize("provider", ProviderChoices.values)
def test_provider_payload_accepts_shared_providers(provider):
    payload = {provider: {"id": "existing-call"}}
    assert TypeAdapter(ProviderPayload).validate_python(payload) == payload
    for model in (CallExecution, CallExecutionSnapshot):
        assert (
            model._meta.get_field("provider_call_data").clean(payload, model())
            == payload
        )


@pytest.mark.unit
def test_provider_payload_rejects_unknown_key():
    payload = {"twilio": {}, "unknown_provider": {}}
    with pytest.raises(PydanticValidationError, match="Contains forbidden keys"):
        TypeAdapter(ProviderPayload).validate_python(payload)
    for model in (CallExecution, CallExecutionSnapshot):
        with pytest.raises(ValidationError, match="Invalid provider keys"):
            model._meta.get_field("provider_call_data").clean(payload, model())
