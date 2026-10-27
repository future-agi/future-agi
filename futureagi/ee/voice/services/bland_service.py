"""Bland.ai as a customer voice provider engine.

In an outbound test the customer's own Bland agent (a Conversational Pathway)
dials our simulator's number, so Bland — not the VAPI simulator — is the data
plane for that call: the dial trigger, status polling and result fetch all hit
Bland's API using the customer's key.

``BlandService`` is a ``VoiceServiceBlueprint`` engine registered in
``VoiceServiceManager.ENGINE_REGISTRY`` under ``ProviderChoices.BLAND``, so every
activity dispatches to it through the manager exactly like VAPI/LiveKit — no
provider-specific branches. Bland only ever plays the customer role (it never
runs the simulator), so the simulator-side blueprint methods (``initiate_*``,
``end_call``, and the recording/log/metric helpers used only by the system
engine or the client-call-matching path) raise ``NotImplementedError``.

Bland exposes no per-token usage, no per-message timing and no per-stage cost,
so conversation WPM / talk-ratio stay unset and cost degrades to a single
total — at parity with how much of the customer-side enrichment for other
non-VAPI providers is unimplemented today.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Iterable

import requests
import structlog

from ee.voice.semantics import FAGICallData, RecordingPayload
from ee.voice.services.types.voice import (
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
    TranscriptMessage,
)
from ee.voice.services.voice_engine import VoiceServiceBlueprint
from simulate.semantics import CallExecutionStatus, CallType
from tracer.constants.external_endpoints import ObservabilityRoutes
from tracer.utils.bland import normalize_bland_data

logger = structlog.get_logger(__name__)

# Bland authenticates with a raw ``authorization: <key>`` header (no Bearer),
# matching the inbound verification path.
_REQUEST_TIMEOUT_SECONDS = 30

# Bland generates the recording asynchronously after marking the call complete,
# so a short call is fetched before the URL exists. Bounded re-poll (~60s) in the
# recording step only — the monitor stays status-terminal so it doesn't hold the
# concurrency slot / phone number while waiting.
_RECORDING_POLL_MAX_ATTEMPTS = 6
_RECORDING_POLL_INTERVAL_SECONDS = 10

# Bland speaker labels → CallExecution transcript roles. Stored raw-ish;
# direction-aware interpretation happens at the read-time serializer, exactly
# like the VAPI path.
_ROLE_TO_DB_ROLE = {
    "assistant": "assistant",
    "bot": "assistant",
    "agent": "assistant",
    "user": "user",
    "customer": "user",
}
_AGENT_ROLES = frozenset({"assistant", "bot", "agent"})
_CUSTOMER_ROLES = frozenset({"user", "customer"})


def _map_bland_status(
    raw_status: str, *, completed: bool, call_data_stored: bool
) -> CallExecutionStatus:
    """Map a Bland call status to FAGI's CallExecutionStatus.

    Mirrors ``VapiService._normalize_to_fagi_call_data`` so the monitor's
    terminal-state detection ({ANALYZING, FAILED, CANCELLED}) behaves the
    same for a Bland customer as for a VAPI one.
    """
    status = (raw_status or "").lower()
    if status in {"completed", "complete", "ended"}:
        return (
            CallExecutionStatus.COMPLETED
            if call_data_stored
            else CallExecutionStatus.ANALYZING
        )
    if status in {"failed", "error", "no-answer", "busy"}:
        return CallExecutionStatus.FAILED
    if status in {"cancelled", "canceled"}:
        return CallExecutionStatus.CANCELLED
    if status in {"in-progress", "started", "ongoing"}:
        return CallExecutionStatus.ONGOING
    if status in {"queued", "ringing", "scheduled"}:
        return CallExecutionStatus.REGISTERED
    # Bland sometimes reports a bare ``completed`` boolean with no status.
    if completed:
        return (
            CallExecutionStatus.COMPLETED
            if call_data_stored
            else CallExecutionStatus.ANALYZING
        )
    return CallExecutionStatus.PENDING


def _bland_duration_seconds(raw: dict) -> float | None:
    """``call_length`` is in MINUTES (float) per Bland's API."""
    call_length = raw.get("call_length")
    if call_length in (None, ""):
        return None
    try:
        return float(call_length) * 60
    except (TypeError, ValueError):
        return None


def _parse_ms(value: Any) -> float | None:
    """Parse a Bland ISO timestamp to epoch milliseconds; None if unparseable."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp() * 1000.0


class BlandService(VoiceServiceBlueprint):
    """Bland.ai engine for the customer side of an outbound test.

    Registered in ``ENGINE_REGISTRY`` under ``ProviderChoices.BLAND`` and
    constructed by ``VoiceServiceManager`` with the customer's API key.
    """

    PROVIDER_KEY = "bland"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    @property
    def _headers(self) -> dict[str, str]:
        return {"authorization": self.api_key or ""}

    # ------------------------------------------------------------------
    # Provider-specific trigger (engine method, à la VapiService.create_outbound_call)
    # ------------------------------------------------------------------
    def create_outbound_call(
        self,
        assistant_id: str,
        from_phone_number: str | None = None,
        to_phone_number: str | None = None,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Tell the customer's Bland agent to dial our simulator number.

        ``assistant_id`` is the Bland Conversational Pathway id. Returns the
        Bland response with an ``id`` key set to the call id (so the initiate
        activity reads ``provider_call_id`` uniformly across providers). Raises
        on any non-success response so the initiate activity fails loudly
        (Temporal retries transient errors, mirroring the VAPI trigger).
        """
        # from_phone_number is accepted for signature parity but deliberately
        # NOT sent: it's the simulator's number, not one purchased for outbound
        # in the customer's Bland account, so sending it makes Bland accept the
        # trigger then silently fail to dial. Omitting it dials from Bland's own.
        body: dict = {
            "phone_number": to_phone_number,
            "pathway_id": assistant_id,
            "record": True,
            "metadata": metadata or {},
        }
        response = self._make_api_request_with_retry(
            requests.post,
            ObservabilityRoutes.BLAND_CALLS_URL.value,
            headers=self._headers,
            json=body,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            # Surface Bland's body — the common failure is a `from` number not
            # owned by the account, which raise_for_status() would bury.
            raise RuntimeError(
                f"Bland rejected the outbound call ({response.status_code}): "
                f"{response.text[:300]}"
            )
        data = response.json() or {}
        if data.get("status") != "success":
            raise RuntimeError(
                f"Bland outbound call was not accepted: {data.get('message') or data}"
            )
        call_id = data.get("call_id")
        if not call_id:
            raise RuntimeError("Bland did not return a call_id for the outbound call")
        return {**data, "id": call_id}

    # ------------------------------------------------------------------
    # Blueprint: call lifecycle
    # ------------------------------------------------------------------
    def _get_call(self, provider_call_id: str) -> dict:
        response = self._make_api_request_with_retry(
            requests.get,
            f"{ObservabilityRoutes.BLAND_CALLS_URL.value}/{provider_call_id}",
            headers=self._headers,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json() or {}

    def normalize_call_data(
        self, raw_data: dict[str, Any], call_data_stored: bool
    ) -> FAGICallData:
        """Normalize already-fetched raw Bland data to FAGICallData.

        Only the fields the monitor and fetch paths read are populated
        (status/duration/ended_reason/recording); the rest default. Bland is
        outbound-only, so ``call_type`` is fixed to OUTBOUND.
        """
        normalized = normalize_bland_data(raw_data)
        meta = normalized.get("metadata") or {}
        transcript = (normalized.get("input") or {}).get("transcript") or []
        started_at = normalized.get("start_time")
        ended_at = normalized.get("end_time")
        return FAGICallData(
            call_id=str(normalized.get("id") or raw_data.get("call_id") or ""),
            call_type=CallType.OUTBOUND,
            status=_map_bland_status(
                raw_data.get("status") or "",
                completed=bool(raw_data.get("completed")),
                call_data_stored=call_data_stored,
            ),
            assistant_id=str(raw_data.get("pathway_id") or ""),
            system_phone_number=str(meta.get("to") or ""),
            customer_phone_number=str(meta.get("from") or ""),
            system_phone_number_id="",
            transcript_available=bool(transcript),
            recording_available=bool(meta.get("recording_url")),
            ended_reason=meta.get("error_message") or raw_data.get("ended_reason"),
            summary=meta.get("summary"),
            recording_url=meta.get("recording_url"),
            log_url=None,  # Bland has no per-call log URL endpoint.
            cost=normalized.get("cost"),
            duration_seconds=_bland_duration_seconds(raw_data),
            created_at=started_at.isoformat()
            if isinstance(started_at, datetime)
            else None,
            started_at=started_at.isoformat()
            if isinstance(started_at, datetime)
            else None,
            ended_at=ended_at.isoformat() if isinstance(ended_at, datetime) else None,
            updated_at=ended_at.isoformat() if isinstance(ended_at, datetime) else None,
            raw_log={self.PROVIDER_KEY: raw_data},
        )

    def get_call(self, input: GetCallInput) -> FAGICallData:
        """Fetch call from Bland and return normalized FAGICallData."""
        return self.normalize_call_data(
            self._get_call(input.call_id), input.call_data_stored
        )

    async def get_call_async(self, input: GetCallInput) -> FAGICallData:
        """Async version of get_call for the monitor + fetch activities."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.get_call(input))

    def validate_api_key(self) -> bool:
        """Validate the customer's Bland key with a lightweight authed GET."""
        try:
            response = requests.get(
                ObservabilityRoutes.BLAND_CALLS_URL.value,
                headers=self._headers,
                timeout=10,
            )
            return response.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Blueprint: provider-agnostic transcript data (metrics)
    # ------------------------------------------------------------------
    async def get_normalized_transcript_data(
        self, call_execution_id: str
    ) -> NormalizedTranscriptData:
        """Provider-agnostic transcript for ConversationMetricsCalculator.

        Reads the raw ``provider_call_data["bland"]["transcripts"]`` rows
        (``{"user", "text", "created_at"?}``) — the raw rows, not
        ``normalize_bland_data`` output, because the normalizer drops
        ``created_at``. Times are in MILLISECONDS (the calculator only
        sec→ms-converts LiveKit). Bland exposes no per-message duration and no
        token usage, so ``duration`` stays None (WPM / talk-ratio are
        intentionally unset) and ``token_usage`` is empty.
        """
        from simulate.models.test_execution import CallExecution

        call = await CallExecution.objects.aget(id=call_execution_id)
        bland_data = (call.provider_call_data or {}).get(self.PROVIDER_KEY, {})
        rows = bland_data.get("transcripts") or []

        messages: list[TranscriptMessage] = []
        base_ms: float | None = None
        last_time: float = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            content = row.get("text") or ""
            if not content.strip():
                continue
            role = _ROLE_TO_DB_ROLE.get((row.get("user") or "").lower())
            if role is None:
                # Non-speaker rows (narrator / webhook / agent-action) are
                # stored as UNKNOWN; counting them as customer turns would
                # inflate user message/turn counts and fabricate latency pairs.
                continue
            # Bland gives one timestamp per row and no duration, so time only
            # affects ordering. A row without a usable timestamp inherits the
            # previous message's time, so a stable sort keeps it in original
            # order instead of an ordinal index jumping ahead of real offsets.
            ts = _parse_ms(row.get("created_at"))
            if ts is not None:
                if base_ms is None:
                    base_ms = ts
                last_time = ts - base_ms
            messages.append(
                TranscriptMessage(role=role, content=content, time=last_time)
            )
        return NormalizedTranscriptData(messages=messages, token_usage={})

    # ------------------------------------------------------------------
    # Blueprint: fetch + store (fetch_and_persist_call_result)
    # ------------------------------------------------------------------
    async def fetch_and_store_call_data(
        self,
        call_execution_id: str,
        provider_call_id: str,
        status: str,
        duration_seconds: float | None = None,
        end_reason: str | None = None,
        provider_data: dict | None = None,
    ) -> tuple[int, bool, bool]:
        """Fetch the call from Bland, store to CallExecution, save transcripts.

        Returns ``(message_count, has_agent_message, has_customer_message)``,
        matching the blueprint contract so the activity treats every provider
        identically.
        """
        from django.utils import timezone

        from simulate.models.test_execution import CallExecution, CallTranscript

        call = await CallExecution.objects.aget(id=call_execution_id)
        update_fields: list[str] = []

        raw: dict | None = None
        if provider_call_id:
            try:
                loop = asyncio.get_running_loop()
                raw = await loop.run_in_executor(None, self._get_call, provider_call_id)
            except Exception as e:
                logger.warning(
                    "bland_fetch_call_data_failed",
                    call_id=call_execution_id,
                    error=str(e),
                )

        message_count = 0
        has_agent_message = False
        has_customer_message = False

        if raw:
            normalized = normalize_bland_data(raw)

            existing = call.provider_call_data or {}
            existing[self.PROVIDER_KEY] = raw
            call.provider_call_data = existing
            call.service_provider_call_id = normalized.get("id") or provider_call_id
            call.call_summary = (normalized.get("metadata") or {}).get("summary")
            update_fields += [
                "provider_call_data",
                "service_provider_call_id",
                "call_summary",
            ]

            if raw.get("ended_reason") or raw.get("error_message"):
                call.ended_reason = raw.get("ended_reason") or raw.get("error_message")
            elif end_reason:
                call.ended_reason = end_reason
            update_fields.append("ended_reason")

            started_at = normalized.get("start_time")
            ended_at = normalized.get("end_time")
            if isinstance(started_at, datetime):
                call.started_at = started_at
                update_fields.append("started_at")
            call.ended_at = ended_at if isinstance(ended_at, datetime) else timezone.now()
            update_fields.append("ended_at")

            # No per-message timing from Bland; idx*1000 keeps the read order
            # (by start_time_ms) stable with headroom against a retry re-insert.
            messages = (normalized.get("input") or {}).get("transcript") or []
            transcript_records = []
            for idx, msg in enumerate(messages):
                raw_role = (msg.get("role") or "").lower()
                content = msg.get("message") or ""
                role = _ROLE_TO_DB_ROLE.get(
                    raw_role, CallTranscript.SpeakerRole.UNKNOWN
                )
                has_content = bool(content and content.strip())
                if has_content and role in _AGENT_ROLES:
                    has_agent_message = True
                if has_content and role in _CUSTOMER_ROLES:
                    has_customer_message = True
                transcript_records.append(
                    CallTranscript(
                        call_execution=call,
                        speaker_role=role,
                        content=content,
                        start_time_ms=idx * 1000,
                        end_time_ms=idx * 1000,
                    )
                )
            if transcript_records:
                await CallTranscript.objects.abulk_create(transcript_records)
                message_count = len(transcript_records)
        else:
            if end_reason:
                call.ended_reason = end_reason
                update_fields.append("ended_reason")
            call.ended_at = timezone.now()
            update_fields.append("ended_at")

        # duration_seconds gates billing (deduct_call_cost); the transcript
        # flags gate UI and evaluation. Mirrors the VAPI engine.
        resolved_duration = duration_seconds
        if resolved_duration is None and raw:
            resolved_duration = _bland_duration_seconds(raw)
        if resolved_duration is not None:
            call.duration_seconds = int(resolved_duration)
            update_fields.append("duration_seconds")

        call.status = status
        call.transcript_available = message_count > 0
        call.message_count = message_count
        update_fields += ["status", "transcript_available", "message_count"]

        await call.asave(update_fields=update_fields)

        return message_count, has_agent_message, has_customer_message

    # ------------------------------------------------------------------
    # Blueprint: recordings (fetch_and_persist_call_result)
    # ------------------------------------------------------------------
    @staticmethod
    def _recording_expected(bland_data: dict) -> bool:
        """A recording exists only for a call that was recorded, completed, and
        had non-zero duration — busy / no-answer / failed calls never get one, so
        the re-poll adds no latency to them."""
        if bland_data.get("record") is not True:
            return False
        status = str(bland_data.get("status") or "").lower()
        if not (
            bland_data.get("completed") or status in {"completed", "complete", "ended"}
        ):
            return False
        return (_bland_duration_seconds(bland_data) or 0) > 0

    async def _await_recording_url(self, provider_call_id: str) -> str | None:
        """Poll Bland for the async-generated recording URL after completion.

        Fail-open: returns the URL once present, or None on give-up (the caller
        keeps the empty state and logs). Runs inside fetch_and_persist_call_result
        under its Heartbeater, so the sleeps are covered by background heartbeats.
        """
        loop = asyncio.get_running_loop()
        for attempt in range(_RECORDING_POLL_MAX_ATTEMPTS):
            await asyncio.sleep(_RECORDING_POLL_INTERVAL_SECONDS)
            try:
                raw = await loop.run_in_executor(None, self._get_call, provider_call_id)
            except Exception as e:
                logger.warning(
                    "bland_recording_poll_failed",
                    call_id=provider_call_id,
                    attempt=attempt,
                    error=str(e),
                )
                continue
            url = (raw or {}).get("recording_url")
            if url:
                return url
        logger.warning(
            "bland_recording_not_ready_after_wait",
            call_id=provider_call_id,
            attempts=_RECORDING_POLL_MAX_ATTEMPTS,
        )
        return None

    async def extract_and_persist_recordings(
        self, call_execution_id: str
    ) -> RecordingUrls:
        """Rehost Bland's recording URL to S3 and meter the stored bytes.

        Reuses the provider-agnostic ``convert_audio_url_to_s3_async_with_size``
        so the recording is served from our storage — fixing the cross-origin
        access problem raw provider recording URLs have — and emits a
        ``VOICE_RECORDING_STORAGE`` usage event for the uploaded bytes, at parity
        with the VAPI rehost (dropping it would leave Bland recording storage
        unbilled). The converter returns the original provider URL with zero
        bytes if the download fails, so a failed rehost bills nothing and falls
        back to the raw URL.
        """
        from simulate.models.test_execution import CallExecution
        from simulate.temporal.utils.async_storage import (
            convert_audio_url_to_s3_async_with_size,
        )

        call = await CallExecution.objects.select_related(
            "test_execution__agent_definition__observability_provider",
            "test_execution__run_test",
        ).aget(id=call_execution_id)
        bland_data = (call.provider_call_data or {}).get(self.PROVIDER_KEY, {})
        recording_url = bland_data.get("recording_url")

        result = RecordingUrls()

        # Bland's recording lands a few seconds after completion, so a short call
        # is fetched before the URL exists. Re-poll (bounded) when a recording is
        # expected, and refresh the stored raw payload so it doesn't keep an
        # empty URL (the rehost below still serves the durable S3 copy).
        if not recording_url and self._recording_expected(bland_data):
            provider_call_id = bland_data.get("call_id") or call.service_provider_call_id
            if provider_call_id:
                recording_url = await self._await_recording_url(provider_call_id)
                if recording_url:
                    full = dict(call.provider_call_data or {})
                    full[self.PROVIDER_KEY] = {**bland_data, "recording_url": recording_url}
                    result.provider_call_data = full

        if not recording_url:
            return result

        # Scope the S3 object to the owning project + provider so it lands under
        # call-recordings/{project}/bland/ (not unknown-project) and storage
        # billing is attributed correctly, matching the VAPI rehost.
        # observability_provider is optional (agents can exist without one) —
        # fall back to None instead of dereferencing a missing relation.
        op = getattr(
            call.test_execution.agent_definition, "observability_provider", None
        )
        project_id = str(op.project_id) if op and op.project_id else None
        organization_id = str(call.test_execution.run_test.organization_id)

        try:
            s3_url, payload_bytes = await convert_audio_url_to_s3_async_with_size(
                str(call_execution_id),
                recording_url,
                "recording",
                provider=self.PROVIDER_KEY,
                project_id=project_id,
            )
        except Exception:
            logger.warning(
                "bland_recording_conversion_failed",
                call_id=str(call_execution_id),
            )
            return result

        result.recording_url = s3_url
        if payload_bytes:
            try:
                from ee.usage.schemas.event_types import BillingEventType
                from ee.usage.schemas.events import UsageEvent
                from ee.usage.services.emitter import emit

                emit(
                    UsageEvent(
                        event_id=str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"futureagi:simulate-recording:{call_execution_id}:recording",
                            )
                        ),
                        org_id=organization_id,
                        event_type=BillingEventType.VOICE_RECORDING_STORAGE,
                        amount=payload_bytes,
                        properties={
                            "source": "simulate",
                            "source_id": str(call_execution_id),
                            "artifact_type": "recording",
                        },
                    )
                )
            except Exception:
                logger.exception("simulation_recording_storage_usage_failed")
        return result

    # ------------------------------------------------------------------
    # Blueprint: costs (fetch_and_persist_call_result)
    # ------------------------------------------------------------------
    async def extract_costs(self, call_execution_id: str) -> CostBreakdown:
        """Extract Bland's total call price. No per-stage breakdown exists."""
        from simulate.models.test_execution import CallExecution

        call = await CallExecution.objects.aget(id=call_execution_id)
        bland_data = (call.provider_call_data or {}).get(self.PROVIDER_KEY, {})
        price = bland_data.get("price")
        total = float(price) if price not in (None, "") else 0.0
        return CostBreakdown(total=total)

    # ------------------------------------------------------------------
    # Blueprint: simulator-side / client-matching methods Bland never plays.
    # A Bland customer's data is fetched and persisted by the methods above;
    # these are only reached when the engine is the SYSTEM simulator (initiate_*,
    # end_call) or the fetch_client_call_data enrichment path (get_recording_urls,
    # persist_audio_to_s3, find_client_call, get_customer_metrics, iter_call_logs),
    # which skips Bland. Fail closed and loud if a new caller ever routes here.
    # ------------------------------------------------------------------
    def initiate_inbound_call(self, input: InboundCallInput) -> Any:
        raise NotImplementedError(
            "Bland is a customer-only provider; inbound simulation runs through "
            "the VAPI system simulator, not BlandService."
        )

    def initiate_outbound_call(self, input: OutboundCallInput) -> OutboundCallResult:
        raise NotImplementedError(
            "Bland outbound is triggered via create_outbound_call; the "
            "simulator-side outbound setup runs on the system engine."
        )

    def end_call(self, input: EndCallInput) -> bool:
        raise NotImplementedError("BlandService does not support end_call.")

    def get_recording_urls(self, payload: dict[str, Any] | None) -> RecordingPayload:
        raise NotImplementedError(
            "Bland recordings are rehosted in extract_and_persist_recordings."
        )

    def persist_audio_to_s3(self, input: PersistAudioInput) -> str:
        raise NotImplementedError("BlandService does not support persist_audio_to_s3.")

    def find_client_call(self, input: FindClientCallInput) -> str | None:
        raise NotImplementedError(
            "Bland outbound already holds the customer call id from the trigger; "
            "fetch_client_call_data skips Bland."
        )

    def get_customer_metrics(self, call_data: FAGICallData) -> CustomerMetrics:
        raise NotImplementedError(
            "Bland exposes no per-stage metrics; fetch_client_call_data skips Bland."
        )

    def iter_call_logs(
        self, url: str, verify_ssl: bool, **kwargs: Any
    ) -> Iterable[dict]:
        raise NotImplementedError("Bland has no call-log URL endpoint.")
