from enum import Enum
from functools import partial
from typing import Annotated, Any

from pydantic import AfterValidator

from tracer.models.observability_provider import ProviderChoices


class CallExecutionStatus(str, Enum):
    PENDING = "pending"
    REGISTERED = "queued"
    ONGOING = "ongoing"
    COMPLETED = "completed"
    FAILED = "failed"
    ANALYZING = "analyzing"
    CANCELLED = "cancelled"


class CallType(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    WEB_CALL = "web"


SupportedProviders = {
    ProviderChoices.VAPI.value,
    ProviderChoices.RETELL.value,
    ProviderChoices.ELEVEN_LABS.value,
    ProviderChoices.LIVEKIT.value,
    ProviderChoices.BLAND.value,
    ProviderChoices.OTHERS.value,
}

RecordingTypes = {"stereo", "assistant", "customer", "combined"}


def validate_allowed_keys(
    v: dict[str, Any], allowed_keys: set[str] | None = None
) -> dict[str, Any]:
    """
    Validate that the dict has only allowed keys.

    Args:
        v: dictionary to validate
        allowed_keys: explicit allowed keys; defaults to SupportedProviders
    """
    permitted = allowed_keys or SupportedProviders
    extra_keys = set(v.keys()) - permitted
    if extra_keys:
        raise ValueError(f"Contains forbidden keys: {extra_keys}")
    return v


ProviderPayload = Annotated[
    dict[str, Any],
    AfterValidator(partial(validate_allowed_keys, allowed_keys=SupportedProviders)),
]

ToolCallingSupportedProviders = [ProviderChoices.VAPI]


def validate_provider_sent_objects(value):
    """
    Validate that provider_call_data is Optional[dict[Literal[SupportedProviders], Any]].

    Ensures keys are only from SupportedProviders. Values can be anything.

    Args:
        value: The JSON data to validate

    Raises:
        ValidationError: If the data is not a dict or contains invalid keys
    """
    from django.core.exceptions import ValidationError

    if value is None:
        return  # Allow None values

    if not isinstance(value, dict):
        raise ValidationError(
            f"provider_call_data must be a dict, got {type(value).__name__}"
        )

    allowed_providers = set(SupportedProviders)
    actual_keys = set(value.keys())
    invalid_keys = actual_keys - allowed_providers

    if invalid_keys:
        raise ValidationError(
            f"Invalid provider keys in provider_call_data: {invalid_keys}. "
            f"Only these providers are allowed: {allowed_providers}"
        )
