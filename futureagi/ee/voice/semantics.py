"""Voice-specific semantic types for call data.

Moved from simulate.semantics as part of the EE voice module extraction.
"""

from functools import partial
from typing import Annotated, Any, Optional

from pydantic import AfterValidator, BaseModel, Field

from simulate.semantics import (
    CallExecutionStatus,
    CallType,
    ProviderPayload,
    SupportedProviders,
    validate_allowed_keys,
)

RecordingTypes = {"stereo", "assistant", "customer", "combined"}

RecordingPayload = Annotated[
    dict[str, str],
    AfterValidator(partial(validate_allowed_keys, allowed_keys=RecordingTypes)),
]


class FAGICallData(BaseModel):
    """
    Future AGI convention for complete call data received from the provider

    Few important conventions to follow for any type of call mentioned in the CallType Enum
    1. System in this program refers to the simulator agent (i.e. FAGI simulator agent)
    2. Customer in this program refers to the client's agent (i.e. the one being tested using FAGI simulator agent)
    """

    # Required fields
    call_id: str = Field(..., description="Provider's call ID")
    call_type: CallType
    status: CallExecutionStatus
    assistant_id: str = Field(..., description="Assistant used in the call")
    system_phone_number: str = Field(..., description="Phone number used in the call")
    customer_phone_number: str = Field(
        ..., description="Phone number of the customer of this call"
    )
    system_phone_number_id: str = Field(
        ..., description="Phone number id. Only available for the system side"
    )
    transcript_available: bool = False
    recording_available: bool = False

    # Optional Fields
    ended_reason: Optional[str] = Field(None, description="Reason of call ending")
    summary: Optional[str] = Field(None, description="Summary of the call")
    cost_breakdown: Optional[ProviderPayload] = Field(
        dict,
        description="Cost breakdown object sent by provider. Structure can vary provider to provider",
    )
    transcript: Optional[ProviderPayload] = Field(
        dict,
        description="Transcript of the call. Structure can vary provider to provider",
    )
    recording_url: Optional[str] = Field(
        ..., description="Recording url for the call execution"
    )
    recording: Optional[ProviderPayload] = Field(
        dict,
        description="Recording object sent by the provider. Structure can vary provider to provider",
    )
    log_url: Optional[str] = Field(
        ..., description="Log url (if any) as sent from provider"
    )
    analysis_data: Optional[ProviderPayload] = Field(
        dict,
        description="Analysis object sent by the provider. Structure can vary provider to provider",
    )
    evaluation_data: Optional[ProviderPayload] = Field(
        dict,
        description="Evaluation object sent by the provider. Structure can vary provider to provider",
    )
    metadata: Optional[ProviderPayload] = Field(
        dict,
        description="Metadata. Structure can vary provider to provider",
    )
    created_at: Optional[str] = Field(
        ..., description="ISO timestamp of when the call was created"
    )
    started_at: Optional[str] = Field(
        ..., description="ISO timestamp of when the call started"
    )
    ended_at: Optional[str] = Field(
        ..., description="ISO timestamp of when the call ended"
    )
    updated_at: Optional[str] = Field(
        ..., description="ISO timestamp of when the call was updated"
    )
    performance_metrics: Optional[ProviderPayload] = Field(
        dict,
        description="Performance metrics of the call (if any) sent by the provider. Structure can vary provider to provider",
    )
    cost: Optional[float] = Field(..., description="Cost of the call")
    duration_seconds: Optional[float] = Field(0, description="Duration of the call")
    raw_log: ProviderPayload = Field(
        dict, description="Logs as received from the provider, without normalization"
    )
