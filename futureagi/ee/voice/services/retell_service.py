"""Retell as a customer voice provider engine.

In an outbound test the customer's own Retell agent dials our simulator's
number, so Retell — not the VAPI simulator — is the data plane for that call:
the dial trigger, status polling and result fetch all hit Retell's API using
the customer's key.

``RetellService`` is a ``VoiceServiceBlueprint`` engine registered in
``VoiceServiceManager.ENGINE_REGISTRY`` under ``ProviderChoices.RETELL``, so
every activity dispatches to it through the manager exactly like VAPI/Bland —
no provider-specific branches. Retell only ever plays the customer role (it
never runs the simulator), so the simulator-side blueprint methods
(``initiate_*`` and the recording/log/metric helpers used only by the system
engine or the client-call-matching path) raise ``NotImplementedError``.

Two things set Retell apart from the Bland engine this is modelled on:

* ``end_call`` IS supported. Retell exposes ``POST /v2/stop-call/{call_id}``,
  so an ongoing call can be hung up. (``call.delete`` is emphatically not a
  hangup — it destroys the call record and the transcript with it.)
* Conversation metrics reach VAPI-level fidelity. Retell returns per-word
  ``start``/``end`` timings and LLM token usage, so WPM, talk-ratio and
  latency pairs are all populated rather than left unset.

All monetary values in Retell's ``call_cost`` are in CENTS; the blueprint's
``CostBreakdown`` and ``FAGICallData.cost`` are in dollars, so everything is
divided by 100 on the way out.
"""

import asyncio
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import structlog
from retell import NoneType, Retell

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

logger = structlog.get_logger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
# The SDK retries 408/409/429/5xx itself. Keep the count low so a wedged
# provider cannot hold a Temporal activity slot past its heartbeat window.
_SDK_MAX_RETRIES = 2

# Retell reports every cost in cents; the blueprint contract is dollars.
_CENTS_PER_DOLLAR = 100.0

# Retell speaker roles -> CallExecution transcript roles. `transfer_target`,
# `tool_call_invocation`, `tool_call_result`, `node_transition`, `dtmf` and
# `sms` rows are deliberately absent: they are not speaker turns, and counting
# them would inflate turn counts and fabricate latency pairs.
_ROLE_TO_DB_ROLE = {
    "agent": "assistant",
    "assistant": "assistant",
    "user": "user",
}
_AGENT_ROLES = frozenset({"assistant", "agent"})
_CUSTOMER_ROLES = frozenset({"user"})

# call_cost.product_costs[].product is a free-form string in Retell's API, not
# a documented enum, so the per-stage split is keyword-matched. An unrecognised
# product contributes to `total` only — which stays correct regardless, because
# it is read from `combined_cost` rather than summed from the parts.
_PRODUCT_KEYWORDS_TO_STAGE = (
    (("tts", "voice", "elevenlabs", "cartesia", "playht", "openai_tts"), "tts"),
    (("stt", "transcri", "deepgram"), "stt"),
    (("llm", "gpt", "claude", "gemini"), "llm"),
    (("telephony", "twilio", "sip", "phone"), "transport"),
)


def _map_retell_status(
    raw_status: str, *, call_data_stored: bool
) -> CallExecutionStatus:
    """Map a Retell ``call_status`` to FAGI's ``CallExecutionStatus``.

    Mirrors ``VapiService``/``BlandService`` so the monitor's terminal-state
    detection ({ANALYZING, FAILED, CANCELLED}) behaves the same for a Retell
    customer as for a VAPI or Bland one.
    """
    status = (raw_status or "").lower()
    if status == "ended":
        return (
            CallExecutionStatus.COMPLETED
            if call_data_stored
            else CallExecutionStatus.ANALYZING
        )
    if status in {"error", "not_connected"}:
        return CallExecutionStatus.FAILED
    if status == "ongoing":
        return CallExecutionStatus.ONGOING
    if status == "registered":
        return CallExecutionStatus.REGISTERED
    return CallExecutionStatus.PENDING


def _retell_duration_seconds(raw: dict) -> float | None:
    """``duration_ms`` is milliseconds; the blueprint contract wants seconds."""
    duration_ms = raw.get("duration_ms")
    if duration_ms in (None, ""):
        return None
    try:
        return float(duration_ms) / 1000.0
    except (TypeError, ValueError):
        return None


def _cents_to_dollars(value: Any) -> float | None:
    """Retell cents -> dollars; None if the value is missing or unparseable."""
    if value in (None, ""):
        return None
    try:
        return float(value) / _CENTS_PER_DOLLAR
    except (TypeError, ValueError):
        return None


def _ms_to_datetime(value: Any) -> datetime | None:
    """Epoch-milliseconds -> aware datetime; None if unusable."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _ms_to_iso(value: Any) -> str | None:
    """Epoch-milliseconds -> ISO string; None if unusable."""
    parsed = _ms_to_datetime(value)
    return parsed.isoformat() if parsed else None


def _word_bounds_ms(row: dict) -> tuple[float, float] | None:
    """Per-message (start, end) in MILLISECONDS from Retell's per-word timings.

    Retell reports ``words[].start`` / ``words[].end`` in SECONDS. The
    conversation metrics calculator only sec->ms-converts LiveKit, so every
    other provider must hand it milliseconds — hence the ``* 1000`` here.
    Returns None when the row carries no usable timings.
    """
    words = [w for w in (row.get("words") or []) if isinstance(w, dict)]
    starts = [w["start"] for w in words if isinstance(w.get("start"), (int, float))]
    ends = [w["end"] for w in words if isinstance(w.get("end"), (int, float))]
    if not starts or not ends:
        return None
    return min(starts) * 1000.0, max(ends) * 1000.0


class RetellService(VoiceServiceBlueprint):
    """Retell engine for the customer side of an outbound test.

    Registered in ``ENGINE_REGISTRY`` under ``ProviderChoices.RETELL`` and
    constructed by ``VoiceServiceManager`` with the customer's API key.
    """

    PROVIDER_KEY = "retell"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        self._retell: Retell | None = None

    def _client(self) -> Retell:
        """Lazily build (and memoize) the SDK client.

        Deferred so constructing the engine with an empty key — as the manager
        does for registry lookups and system-side dispatch — never raises at
        import or registration time, matching BlandService's tolerant
        ``__init__``.
        """
        if self._retell is None:
            self._retell = Retell(
                api_key=self.api_key or "",
                timeout=_REQUEST_TIMEOUT_SECONDS,
                max_retries=_SDK_MAX_RETRIES,
            )
        return self._retell

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
        """Tell the customer's Retell agent to dial our simulator number.

        Unlike Bland, ``from_number`` is REQUIRED by Retell and must be a
        number the customer owns in (or has imported into) their Retell
        account, so it is passed through rather than omitted. ``assistant_id``
        is sent as ``override_agent_id``: a one-time override for this call
        that does not rebind the agent to the number.

        Returns Retell's payload with an ``id`` key set to the call id, so the
        initiate activity reads ``provider_call_id`` uniformly across
        providers. Raises on any failure so the activity fails loudly and
        Temporal's retry policy can act on transient errors.
        """
        response = self._client().call.create_phone_call(
            from_number=from_phone_number or "",
            to_number=to_phone_number or "",
            override_agent_id=assistant_id,
            metadata=metadata or {},
        )
        data = response.model_dump(mode="json") or {}
        call_id = data.get("call_id")
        if not call_id:
            raise RuntimeError("Retell did not return a call_id for the outbound call")
        return {**data, "id": call_id}

    # ------------------------------------------------------------------
    # Blueprint: call lifecycle
    # ------------------------------------------------------------------
    def _get_call(self, provider_call_id: str) -> dict:
        response = self._client().call.retrieve(provider_call_id)
        return response.model_dump(mode="json") or {}

    def normalize_call_data(
        self, raw_data: dict[str, Any], call_data_stored: bool
    ) -> FAGICallData:
        """Normalize already-fetched raw Retell data to ``FAGICallData``.

        Mapped directly from Retell's flat payload rather than through
        ``tracer.utils.retell.normalize_retell_data``: that function builds
        OTel span attributes, computes speech metrics and triggers a
        synchronous S3 rehost, all of which are observability-pipeline
        concerns this engine does not want on the call-monitoring hot path.

        Retell plays the customer side of an outbound test, so ``call_type``
        is fixed to OUTBOUND, ``system_phone_number`` is the number Retell
        dialled (ours) and ``customer_phone_number`` is the number it dialled
        from (theirs).
        """
        analysis = raw_data.get("call_analysis") or {}
        transcript = raw_data.get("transcript_with_tool_calls") or []
        recording_url = raw_data.get("recording_url")
        started_at = _ms_to_iso(raw_data.get("start_timestamp"))
        ended_at = _ms_to_iso(raw_data.get("end_timestamp"))
        return FAGICallData(
            call_id=str(raw_data.get("call_id") or ""),
            call_type=CallType.OUTBOUND,
            status=_map_retell_status(
                raw_data.get("call_status") or "", call_data_stored=call_data_stored
            ),
            assistant_id=str(raw_data.get("agent_id") or ""),
            system_phone_number=str(raw_data.get("to_number") or ""),
            customer_phone_number=str(raw_data.get("from_number") or ""),
            system_phone_number_id="",
            transcript_available=bool(transcript),
            recording_available=bool(recording_url),
            ended_reason=raw_data.get("disconnection_reason"),
            summary=analysis.get("call_summary"),
            recording_url=recording_url,
            log_url=raw_data.get("public_log_url"),
            cost=_cents_to_dollars(
                (raw_data.get("call_cost") or {}).get("combined_cost")
            ),
            duration_seconds=_retell_duration_seconds(raw_data),
            created_at=started_at,
            started_at=started_at,
            ended_at=ended_at,
            updated_at=ended_at,
            raw_log={self.PROVIDER_KEY: raw_data},
        )

    def get_call(self, input: GetCallInput) -> FAGICallData:
        """Fetch call from Retell and return normalized FAGICallData."""
        return self.normalize_call_data(
            self._get_call(input.call_id), input.call_data_stored
        )

    async def get_call_async(self, input: GetCallInput) -> FAGICallData:
        """Async version of get_call for the monitor + fetch activities."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.get_call(input))

    def end_call(self, input: EndCallInput) -> bool:
        """Hang up an ongoing Retell call via ``POST /v2/stop-call/{call_id}``.

        The SDK has no ``call.stop`` method as of 5.8.0, so this uses the
        client's documented generic-request escape hatch (see
        ``retell/_types.py``). Note that ``call.delete`` is NOT a hangup: it
        destroys the call record and its data, which would erase the very
        transcript the fetch step is about to read.
        """
        payload = input.provider_call_payload or {}
        call_id = payload.get("call_id") or payload.get("id")
        if not call_id:
            raise ValueError("Retell end_call requires a call_id in the payload")
        self._client().post(f"/v2/stop-call/{call_id}", cast_to=NoneType)
        return True

    def validate_api_key(self) -> bool:
        """Validate the customer's Retell key with a cheap authed list call."""
        try:
            self._client().call.list(limit=1)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Blueprint: provider-agnostic transcript data (metrics)
    # ------------------------------------------------------------------
    async def get_normalized_transcript_data(
        self, call_execution_id: str
    ) -> NormalizedTranscriptData:
        """Provider-agnostic transcript for ``ConversationMetricsCalculator``.

        Reads the raw ``provider_call_data["retell"]
        ["transcript_with_tool_calls"]`` rows. Times are in MILLISECONDS (the
        calculator only sec->ms-converts LiveKit). Unlike Bland, Retell gives
        real per-message start/end, so WPM and talk-ratio are populated.

        Retell reports only aggregate ``llm_token_usage.values`` — total tokens
        per request, with no input/output split — so the whole sum is
        attributed to ``prompt_tokens`` and ``completion_tokens`` stays 0.
        """
        from simulate.models.test_execution import CallExecution

        call = await CallExecution.objects.aget(id=call_execution_id)
        retell_data = (call.provider_call_data or {}).get(self.PROVIDER_KEY, {})
        rows = retell_data.get("transcript_with_tool_calls") or []

        messages: list[TranscriptMessage] = []
        last_time = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            role = _ROLE_TO_DB_ROLE.get((row.get("role") or "").lower())
            if role is None:
                # Tool calls, transfers, node transitions, DTMF and SMS are not
                # speaker turns; counting them would inflate turn counts.
                continue
            content = row.get("content") or ""
            if not content.strip():
                continue
            bounds = _word_bounds_ms(row)
            if bounds is None:
                # A row without usable word timings inherits the previous
                # message's time, so a stable sort keeps it in original order
                # instead of an ordinal index jumping ahead of real offsets.
                messages.append(
                    TranscriptMessage(role=role, content=content, time=last_time)
                )
                continue
            start_ms, end_ms = bounds
            last_time = start_ms
            messages.append(
                TranscriptMessage(
                    role=role,
                    content=content,
                    time=start_ms,
                    end_time=end_ms,
                    duration=end_ms - start_ms,
                )
            )

        usage = retell_data.get("llm_token_usage") or {}
        values = [v for v in (usage.get("values") or []) if isinstance(v, (int, float))]
        token_usage = (
            {"llm": {"prompt_tokens": sum(values), "completion_tokens": 0}}
            if values
            else {}
        )
        return NormalizedTranscriptData(messages=messages, token_usage=token_usage)

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
        """Fetch the call from Retell, store to CallExecution, save transcripts.

        Returns ``(message_count, has_agent_message, has_customer_message)``,
        matching the blueprint contract so the activity treats every provider
        identically. Fails open on a fetch error: the call is still marked with
        the status the monitor determined, so a provider blip cannot wedge the
        workflow.
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
                    "retell_fetch_call_data_failed",
                    call_id=call_execution_id,
                    error=str(e),
                )

        message_count = 0
        has_agent_message = False
        has_customer_message = False

        if raw:
            existing = call.provider_call_data or {}
            existing[self.PROVIDER_KEY] = raw
            call.provider_call_data = existing
            call.service_provider_call_id = raw.get("call_id") or provider_call_id
            call.call_summary = (raw.get("call_analysis") or {}).get("call_summary")
            call.assistant_id = raw.get("agent_id") or call.assistant_id
            call.customer_number = raw.get("from_number") or call.customer_number
            call.call_type = CallType.OUTBOUND.value
            update_fields += [
                "provider_call_data",
                "service_provider_call_id",
                "call_summary",
                "assistant_id",
                "customer_number",
                "call_type",
            ]

            call.ended_reason = raw.get("disconnection_reason") or end_reason
            update_fields.append("ended_reason")

            started_at = _ms_to_datetime(raw.get("start_timestamp"))
            ended_at = _ms_to_datetime(raw.get("end_timestamp"))
            if started_at:
                call.started_at = started_at
                update_fields.append("started_at")
            call.ended_at = ended_at or timezone.now()
            update_fields.append("ended_at")

            transcript_records = []
            for idx, row in enumerate(raw.get("transcript_with_tool_calls") or []):
                if not isinstance(row, dict):
                    continue
                raw_role = (row.get("role") or "").lower()
                role = _ROLE_TO_DB_ROLE.get(
                    raw_role, CallTranscript.SpeakerRole.UNKNOWN
                )
                content = row.get("content") or ""
                has_content = bool(content and content.strip())
                if has_content and role in _AGENT_ROLES:
                    has_agent_message = True
                if has_content and role in _CUSTOMER_ROLES:
                    has_customer_message = True
                # Retell gives real per-word timings; fall back to idx*1000
                # (Bland's ordinal scheme) only for rows that carry none, so
                # the read order stays stable either way.
                bounds = _word_bounds_ms(row)
                start_ms, end_ms = (
                    (int(bounds[0]), int(bounds[1]))
                    if bounds
                    else (idx * 1000, idx * 1000)
                )
                transcript_records.append(
                    CallTranscript(
                        call_execution=call,
                        speaker_role=role,
                        content=content,
                        start_time_ms=start_ms,
                        end_time_ms=end_ms,
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
        # flags gate UI and evaluation. Mirrors the VAPI and Bland engines.
        resolved_duration = duration_seconds
        if resolved_duration is None and raw:
            resolved_duration = _retell_duration_seconds(raw)
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
    async def extract_and_persist_recordings(
        self, call_execution_id: str
    ) -> RecordingUrls:
        """Rehost Retell's recordings to S3 and meter the stored bytes.

        Retell exposes two artifacts — a mono combined recording and a
        multi-channel (stereo) one — and populates both by the time
        ``call_status`` reaches ``ended``, so no re-poll is needed (unlike
        Bland, whose recording lands asynchronously).

        Reuses the provider-agnostic ``convert_audio_url_to_s3_async_with_size``
        so recordings are served from our storage — fixing the cross-origin
        access problem raw provider URLs have — and emits a
        ``VOICE_RECORDING_STORAGE`` usage event per artifact, at parity with
        the VAPI and Bland rehosts. Each artifact is isolated so one failure
        does not drop the other.
        """
        from simulate.models.test_execution import CallExecution
        from simulate.temporal.utils.async_storage import (
            convert_audio_url_to_s3_async_with_size,
        )

        call = await CallExecution.objects.select_related(
            "test_execution__agent_definition__observability_provider",
            "test_execution__run_test",
        ).aget(id=call_execution_id)
        retell_data = (call.provider_call_data or {}).get(self.PROVIDER_KEY, {})

        result = RecordingUrls()

        # Scope the S3 object to the owning project + provider so it lands under
        # call-recordings/{project}/retell/ (not unknown-project) and storage
        # billing is attributed correctly. observability_provider is optional
        # (agents can exist without one) — fall back to None rather than
        # dereferencing a missing relation.
        op = getattr(
            call.test_execution.agent_definition, "observability_provider", None
        )
        project_id = str(op.project_id) if op and op.project_id else None
        organization_id = str(call.test_execution.run_test.organization_id)

        artifacts = (
            ("recording", retell_data.get("recording_url"), "recording_url"),
            (
                "stereo_recording",
                retell_data.get("recording_multi_channel_url"),
                "stereo_recording_url",
            ),
        )

        for url_type, source_url, result_attr in artifacts:
            if not source_url:
                continue
            try:
                s3_url, payload_bytes = await convert_audio_url_to_s3_async_with_size(
                    str(call_execution_id),
                    source_url,
                    url_type,
                    provider=self.PROVIDER_KEY,
                    project_id=project_id,
                )
            except Exception:
                logger.warning(
                    "retell_recording_conversion_failed",
                    call_id=str(call_execution_id),
                    url_type=url_type,
                )
                continue

            setattr(result, result_attr, s3_url)
            if not payload_bytes:
                continue
            try:
                from ee.usage.schemas.event_types import BillingEventType
                from ee.usage.schemas.events import UsageEvent
                from ee.usage.services.emitter import emit

                emit(
                    UsageEvent(
                        # uuid5 keeps the meter idempotent across Temporal retries.
                        event_id=str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"futureagi:simulate-recording:{call_execution_id}:{url_type}",
                            )
                        ),
                        org_id=organization_id,
                        event_type=BillingEventType.VOICE_RECORDING_STORAGE,
                        amount=payload_bytes,
                        properties={
                            "source": "simulate",
                            "source_id": str(call_execution_id),
                            "artifact_type": url_type,
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
        """Retell's total call cost, plus a best-effort per-stage split.

        ``total`` always comes from ``combined_cost``, so it stays correct even
        when a product name is unrecognised by the keyword mapping. All values
        are converted from Retell's cents to the blueprint's dollars.
        """
        from simulate.models.test_execution import CallExecution

        call = await CallExecution.objects.aget(id=call_execution_id)
        retell_data = (call.provider_call_data or {}).get(self.PROVIDER_KEY, {})
        call_cost = retell_data.get("call_cost") or {}

        breakdown = CostBreakdown(
            total=_cents_to_dollars(call_cost.get("combined_cost")) or 0.0
        )
        for entry in call_cost.get("product_costs") or []:
            if not isinstance(entry, dict):
                continue
            product = str(entry.get("product") or "").lower()
            amount = _cents_to_dollars(entry.get("cost"))
            if amount is None:
                continue
            for keywords, stage in _PRODUCT_KEYWORDS_TO_STAGE:
                if any(keyword in product for keyword in keywords):
                    setattr(breakdown, stage, getattr(breakdown, stage) + amount)
                    break
        return breakdown

    # ------------------------------------------------------------------
    # Blueprint: simulator-side / client-matching methods Retell never plays.
    # A Retell customer's data is fetched and persisted by the methods above;
    # these are only reached when the engine is the SYSTEM simulator
    # (initiate_*) or via the fetch_client_call_data enrichment path
    # (get_recording_urls, persist_audio_to_s3, find_client_call,
    # get_customer_metrics, iter_call_logs), which skips Retell. Fail closed
    # and loud if a new caller ever routes here.
    # ------------------------------------------------------------------
    def initiate_inbound_call(self, input: InboundCallInput) -> Any:
        raise NotImplementedError(
            "Retell is a customer-only provider; inbound simulation runs through "
            "the VAPI system simulator, not RetellService."
        )

    def initiate_outbound_call(self, input: OutboundCallInput) -> OutboundCallResult:
        raise NotImplementedError(
            "Retell outbound is triggered via create_outbound_call; the "
            "simulator-side outbound setup runs on the system engine."
        )

    def get_recording_urls(self, payload: dict[str, Any] | None) -> RecordingPayload:
        raise NotImplementedError(
            "Retell recordings are rehosted in extract_and_persist_recordings."
        )

    def persist_audio_to_s3(self, input: PersistAudioInput) -> str:
        raise NotImplementedError("RetellService does not support persist_audio_to_s3.")

    def find_client_call(self, input: FindClientCallInput) -> str | None:
        raise NotImplementedError(
            "Retell outbound already holds the customer call id from the trigger; "
            "fetch_client_call_data skips Retell."
        )

    def get_customer_metrics(self, call_data: FAGICallData) -> CustomerMetrics:
        raise NotImplementedError(
            "Retell per-stage metrics are surfaced through the observability "
            "pipeline; fetch_client_call_data skips Retell."
        )

    def iter_call_logs(
        self, url: str, verify_ssl: bool, **kwargs: Any
    ) -> Iterable[dict]:
        raise NotImplementedError(
            "Retell's public_log_url is a plain text log, not a streamable "
            "call-log feed."
        )
