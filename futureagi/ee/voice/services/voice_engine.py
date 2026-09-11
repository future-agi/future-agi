"""VoiceServiceBlueprint — abstract engine contract for voice providers.

Moved from simulate.semantics. All provider-specific implementations
(VapiService, future LivekitService, etc.) extend this class.

Only methods that EVERY engine must implement are declared here.
Provider-specific SDK methods (create_assistant, list_calls, etc.)
live on the concrete engine classes directly.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Iterable

import requests
import structlog

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

if TYPE_CHECKING:
    from ee.voice.semantics import FAGICallData, RecordingPayload

logger = structlog.get_logger(__name__)


class VoiceServiceBlueprint(ABC):
    """Abstract base for voice service engines.

    To add a new provider, subclass this and implement every @abstractmethod.
    Then register the class in VoiceServiceManager.ENGINE_REGISTRY.
    """

    # Language name/code → normalised BCP-47 code.
    # Class-level constant to avoid recreating on every call.
    _LANGUAGE_MAP: dict[str, str] = {
        "arabic": "ar",
        "ar": "ar",
        "ar-sa": "ar",
        "bulgarian": "bg",
        "bg": "bg",
        "catalan": "ca",
        "ca": "ca",
        "chinese": "zh",
        "chinese (mandarin, simplified)": "zh",
        "chinese (mandarin, traditional)": "zh-TW",
        "chinese (cantonese, traditional)": "zh-HK",
        "zh": "zh",
        "zh-cn": "zh",
        "zh-hans": "zh",
        "zh-tw": "zh-TW",
        "zh-hant": "zh-TW",
        "zh-hk": "zh-HK",
        "czech": "cs",
        "cs": "cs",
        "danish": "da",
        "da": "da",
        "da-dk": "da",
        "dutch": "nl",
        "nl": "nl",
        "english": "en-US",
        "en": "en-US",
        "en-us": "en-US",
        "en-au": "en-AU",
        "en-gb": "en-GB",
        "en-nz": "en-NZ",
        "en-in": "en-IN",
        "estonian": "et",
        "et": "et",
        "finnish": "fi",
        "fi": "fi",
        "flemish": "nl-BE",
        "nl-be": "nl-BE",
        "french": "fr",
        "fr": "fr",
        "fr-ca": "fr-CA",
        "german": "de",
        "de": "de",
        "de-ch": "de-CH",
        "greek": "el",
        "el": "el",
        "hindi": "hi",
        "hi": "hi",
        "hungarian": "hu",
        "hu": "hu",
        "indonesian": "id",
        "id": "id",
        "italian": "it",
        "it": "it",
        "japanese": "ja",
        "ja": "ja",
        "korean": "ko",
        "ko": "ko",
        "ko-kr": "ko",
        "latvian": "lv",
        "lv": "lv",
        "lithuanian": "lt",
        "lt": "lt",
        "malay": "ms",
        "ms": "ms",
        "norwegian": "no",
        "no": "no",
        "polish": "pl",
        "pl": "pl",
        "portuguese": "pt",
        "pt": "pt",
        "pt-br": "pt-BR",
        "pt-pt": "pt-PT",
        "romanian": "ro",
        "ro": "ro",
        "russian": "ru",
        "ru": "ru",
        "slovak": "sk",
        "sk": "sk",
        "spanish": "es",
        "es": "es",
        "es-419": "es-419",
        "swedish": "sv",
        "sv": "sv",
        "sv-se": "sv",
        "thai": "th",
        "th": "th",
        "th-th": "th",
        "turkish": "tr",
        "tr": "tr",
        "ukrainian": "uk",
        "uk": "uk",
        "vietnamese": "vi",
        "vi": "vi",
    }

    # ------------------------------------------------------------------
    # Concrete utility methods (shared across all engines)
    # ------------------------------------------------------------------

    def _format_phone_number_e164(self, phone_number: str) -> str:
        """Format phone number to E.164 format."""
        digits_only = "".join(filter(str.isdigit, phone_number))

        if len(digits_only) == 10:
            return f"+1{digits_only}"
        elif len(digits_only) == 11 and digits_only.startswith("1"):
            return f"+{digits_only}"
        elif phone_number.startswith("+"):
            return phone_number
        else:
            return f"+{digits_only}"

    def _normalize_language_code(self, language: str) -> str:
        """Normalize language codes to standard format."""
        if not language:
            return "en-US"
        return self._LANGUAGE_MAP.get(language.lower().strip(), "en-US")

    def _make_api_request_with_retry(
        self, request_func, *args, max_retries=30, retry_delay=5, **kwargs
    ):
        """Make API requests with retry logic for rate-limited responses."""
        for attempt in range(max_retries):
            try:
                response = request_func(*args, **kwargs)

                if response.status_code == 429 or "limit exceeded" in response.text:
                    if attempt < max_retries - 1:
                        logger.info(
                            f"Rate limit exceeded. Retrying in {retry_delay} seconds... (Attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(retry_delay)
                        continue
                    else:
                        logger.info(
                            f"Max retries ({max_retries}) exceeded for rate limit"
                        )
                        raise Exception("Rate limit exceeded.")

                return response

            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"Error in API request: {str(e)}")
                    raise
                else:
                    logger.warning(
                        f"Attempt {attempt + 1} failed: {str(e)}. Retrying..."
                    )
                    time.sleep(retry_delay)

    def _is_url_reachable(self, url: str, timeout: int = 5) -> bool:
        """Lightweight reachability check with default 5 sec timeout."""
        try:
            resp = requests.head(url, timeout=timeout, allow_redirects=True)
            if resp.status_code < 400:
                return True
            # HEAD failed — try GET with stream to avoid downloading body
            resp = requests.get(url, timeout=timeout, stream=True)
            try:
                return resp.status_code < 400
            finally:
                resp.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[BG] URL check failed for {url}: {e}")
            return False

    # ------------------------------------------------------------------
    # High-level abstract methods — the engine contract for VSM
    # ------------------------------------------------------------------

    # -- Call lifecycle --

    @abstractmethod
    def get_call(self, input: GetCallInput) -> FAGICallData:
        """Fetch call from provider and return normalized FAGICallData."""
        ...

    @abstractmethod
    async def get_call_async(self, input: GetCallInput) -> FAGICallData:
        """Async version of get_call for Temporal activities."""
        ...

    @abstractmethod
    def initiate_inbound_call(self, input: InboundCallInput) -> CallResult:
        """Full inbound flow: create simulator assistant + call user's agent."""
        ...

    @abstractmethod
    def initiate_outbound_call(self, input: OutboundCallInput) -> OutboundCallResult:
        """Full outbound setup: acquire phone + create assistant + assign to phone."""
        ...

    @abstractmethod
    def end_call(self, input: EndCallInput) -> bool:
        """Terminate an active call."""
        ...

    # -- Data extraction --

    @abstractmethod
    def get_recording_urls(self, payload: dict[str, Any] | None) -> RecordingPayload:
        """Extract recording URLs from provider-specific payload."""
        ...

    @abstractmethod
    def persist_audio_to_s3(self, input: PersistAudioInput) -> str:
        """Download provider audio and re-upload to S3. Returns S3 URL."""
        ...

    # -- Client call matching --

    @abstractmethod
    def find_client_call(self, input: FindClientCallInput) -> str | None:
        """Find matching call in customer's provider account."""
        ...

    # -- Metrics --

    @abstractmethod
    def get_customer_metrics(self, call_data: FAGICallData) -> CustomerMetrics:
        """Normalize provider metrics/costs to FAGI convention."""
        ...

    # -- Logs --

    @abstractmethod
    def iter_call_logs(
        self,
        url: str,
        verify_ssl: bool,
        **kwargs: Any,
    ) -> Iterable[dict]:
        """Yield parsed log entries from provider log URL.

        Provider-specific auth context may be passed as kwargs. Vapi uses
        ``call_id`` + ``api_key`` for the Bearer-authenticated
        ``/call/{id}/call-logs`` endpoint; providers whose logs come from
        webhooks or an internal store accept and ignore.
        """
        ...

    # -- Validation --

    @abstractmethod
    def normalize_call_data(
        self, raw_data: dict[str, Any], call_data_stored: bool
    ) -> FAGICallData:
        """Normalize raw provider data (already fetched) to FAGICallData.

        Use this instead of get_call() when you already have the raw data
        in the DB and don't want an extra API round-trip.
        """
        ...

    @abstractmethod
    def validate_api_key(self) -> bool:
        """Validate the API key against the provider."""
        ...

    # -- Provider-agnostic data extraction (for Temporal activities) --

    @abstractmethod
    async def get_normalized_transcript_data(
        self, call_execution_id: str
    ) -> NormalizedTranscriptData:
        """Return provider-agnostic transcript + usage data for metrics calculation.

        Each engine reads from its own source:
        - VAPI: from provider_call_data["vapi"]["messages"]
        - LiveKit: from CallTranscript DB records
        """
        ...

    @abstractmethod
    async def extract_and_persist_recordings(
        self, call_execution_id: str
    ) -> RecordingUrls:
        """Extract recordings from provider and persist to S3.

        Returns RecordingUrls with S3 URLs for each available recording type.
        """
        ...

    @abstractmethod
    async def extract_costs(self, call_execution_id: str) -> CostBreakdown:
        """Extract cost breakdown from provider data.

        Returns CostBreakdown with per-component costs in dollars.
        """
        ...

    @abstractmethod
    async def fetch_and_store_call_data(
        self,
        call_execution_id: str,
        provider_call_id: str,
        status: str,
        duration_seconds: float | None = None,
        end_reason: str | None = None,
        provider_data: dict[str, Any] | None = None,
    ) -> tuple[int, bool, bool]:
        """Fetch provider data, store to CallExecution, save transcripts.

        Returns (message_count, has_agent_message, has_customer_message).

        Each engine handles this differently:
        - VAPI: Fetches from VAPI API, stores provider_call_data, saves transcripts
        - LiveKit: Agent worker already wrote data; counts existing transcripts
        """
        ...
