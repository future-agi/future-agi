"""VoiceServiceManager — provider-agnostic voice service router.

Thin factory + proxy. All provider-specific logic lives in the engine
(VapiService, future LivekitService, etc.). To add a provider:
implement VoiceServiceBlueprint, register in ENGINE_REGISTRY.
"""

import warnings
from typing import Any, Iterable

import structlog

from ee.voice.semantics import FAGICallData, RecordingPayload
from ee.voice.services.livekit import LivekitService
from ee.voice.services.types.voice import (
    CallResult,
    CostBreakdown,
    CustomerMetrics,
    EndCallInput,
    FindClientCallInput,
    GetCallInput,
    InboundCallInput,
    NormalizedTranscriptData,
    OutboundCallInput,
    OutboundCallResult,
    PersistAudioInput,
    RecordingUrls,
)
from ee.voice.services.bland_service import BlandService
from ee.voice.services.vapi_service import VapiService
from ee.voice.services.voice_engine import VoiceServiceBlueprint
from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)


class VoiceServiceManager:
    """Provider-agnostic voice service router.

    Thin factory + proxy. Delegates all work to ``self.engine``.
    To add a provider: implement VoiceServiceBlueprint, register in ENGINE_REGISTRY.
    """

    ENGINE_REGISTRY: dict[ProviderChoices, type[VoiceServiceBlueprint]] = {
        ProviderChoices.VAPI: VapiService,
        ProviderChoices.LIVEKIT: LivekitService,
        ProviderChoices.BLAND: BlandService,
    }

    def __init__(
        self,
        api_key: str = "",
        system_voice_provider: ProviderChoices = ProviderChoices.VAPI,
    ):
        self.system_voice_provider = system_voice_provider
        engine_cls = self.ENGINE_REGISTRY.get(system_voice_provider)
        if not engine_cls:
            raise ValueError(f"Unsupported Provider: {system_voice_provider}")

        self.engine: VoiceServiceBlueprint = engine_cls(api_key=api_key)

    # ------------------------------------------------------------------
    # Backward compatibility
    # ------------------------------------------------------------------

    @property
    def system_voice_provider_instance(self):
        """Deprecated: access the engine directly via VSM methods.

        Kept for backward compatibility with callers that use low-level
        SDK methods like create_assistant(), delete_assistant(), etc.
        """
        warnings.warn(
            "system_voice_provider_instance is deprecated. "
            "Use VoiceServiceManager methods directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.engine

    # ------------------------------------------------------------------
    # Call lifecycle
    # ------------------------------------------------------------------

    def get_call(self, call_id: str, call_data_stored: bool) -> FAGICallData:
        """Fetch call from provider and return normalized FAGICallData."""
        return self.engine.get_call(
            GetCallInput(call_id=call_id, call_data_stored=call_data_stored)
        )

    async def get_call_async(
        self, call_id: str, call_data_stored: bool
    ) -> FAGICallData:
        """Async version of get_call."""
        return await self.engine.get_call_async(
            GetCallInput(call_id=call_id, call_data_stored=call_data_stored)
        )

    def initiate_inbound_call(self, input: InboundCallInput) -> CallResult:
        """Initiate an inbound call (FutureAGI calls user's agent)."""
        return self.engine.initiate_inbound_call(input)

    def initiate_outbound_call(self, input: OutboundCallInput) -> OutboundCallResult:
        """Setup to receive outbound call from user."""
        return self.engine.initiate_outbound_call(input)

    def end_call(self, input: EndCallInput) -> bool:
        """Terminate an active call."""
        return self.engine.end_call(input)

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------

    def get_recording_urls(self, payload: dict[str, Any] | None) -> RecordingPayload:
        """Extract recording URLs from provider-specific payload."""
        return self.engine.get_recording_urls(payload)

    def persist_audio_to_s3(self, input: PersistAudioInput) -> str:
        """Download provider audio and re-upload to S3. Returns S3 URL."""
        return self.engine.persist_audio_to_s3(input)

    # ------------------------------------------------------------------
    # Client call matching
    # ------------------------------------------------------------------

    def find_client_call(self, input: FindClientCallInput) -> str | None:
        """Find matching call in customer's provider account."""
        return self.engine.find_client_call(input)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_customer_metrics(self, call_data: FAGICallData) -> CustomerMetrics:
        """Normalize provider metrics/costs to FAGI convention."""
        return self.engine.get_customer_metrics(call_data)

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def iter_call_logs(
        self,
        url: str,
        verify_ssl: bool,
        **kwargs: Any,
    ) -> Iterable[dict]:
        """Yield parsed log entries from provider log URL."""
        return self.engine.iter_call_logs(url, verify_ssl, **kwargs)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def normalize_call_data(
        self, raw_data: dict[str, Any], call_data_stored: bool
    ) -> FAGICallData:
        """Normalize already-fetched raw provider data to FAGICallData."""
        return self.engine.normalize_call_data(raw_data, call_data_stored)

    def validate_api_key(self) -> bool:
        """Validate the API key by making a simple request."""
        return self.engine.validate_api_key()

    # ------------------------------------------------------------------
    # Provider-agnostic data extraction (for Temporal activities)
    # ------------------------------------------------------------------

    async def get_normalized_transcript_data(
        self, call_execution_id: str
    ) -> NormalizedTranscriptData:
        """Return provider-agnostic transcript + usage data."""
        return await self.engine.get_normalized_transcript_data(call_execution_id)

    async def extract_and_persist_recordings(
        self, call_execution_id: str
    ) -> RecordingUrls:
        """Extract recordings from provider and persist to S3."""
        return await self.engine.extract_and_persist_recordings(call_execution_id)

    async def extract_costs(self, call_execution_id: str) -> CostBreakdown:
        """Extract cost breakdown from provider data."""
        return await self.engine.extract_costs(call_execution_id)

    async def fetch_and_store_call_data(
        self,
        call_execution_id: str,
        provider_call_id: str,
        status: str,
        duration_seconds: float | None = None,
        end_reason: str | None = None,
        provider_data: dict[str, Any] | None = None,
    ) -> tuple[int, bool, bool]:
        """Fetch provider data, store to CallExecution, save transcripts."""
        return await self.engine.fetch_and_store_call_data(
            call_execution_id=call_execution_id,
            provider_call_id=provider_call_id,
            status=status,
            duration_seconds=duration_seconds,
            end_reason=end_reason,
            provider_data=provider_data,
        )
