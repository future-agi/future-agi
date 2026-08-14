"""LivekitService — VoiceServiceBlueprint implementation for LiveKit.

Handles inbound calls only (we call user's agent). Outbound deferred to TH-3208/TH-3220.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable

import structlog
from livekit.api import (  # type: ignore[attr-defined]
    CreateAgentDispatchRequest,
    ListRoomsRequest,
    LiveKitAPI,
)

from ee.voice.services.livekit.config import LiveKitConfig
from ee.voice.services.livekit.rooms import create_room, delete_room
from ee.voice.services.livekit.sip import create_sip_participant
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
    TranscriptMessage,
)
from ee.voice.services.voice_engine import VoiceServiceBlueprint
from tracer.models.observability_provider import ProviderChoices

if TYPE_CHECKING:
    from ee.voice.semantics import FAGICallData, RecordingPayload

logger = structlog.get_logger(__name__)

# Shared thread pool for running async code from sync contexts
# (e.g., sync Temporal activities calling async LiveKit SDK).
_THREAD_POOL = ThreadPoolExecutor(max_workers=4)
_THREAD_POOL_TIMEOUT = 120  # seconds

AGENT_NAME = "voice-simulator"
PROVIDER_KEY = ProviderChoices.LIVEKIT.value  # "livekit"


def _room_name(call_id: str) -> str:
    """Deterministic room name from call execution ID."""
    return f"call_{call_id}"


def _run_async(coro):
    """Run an async coroutine from sync context.

    Handles both cases:
    - No running event loop → uses asyncio.run() directly
    - Running event loop (e.g., inside an async Temporal activity) →
      offloads to a thread with its own event loop to avoid
      'cannot be called from a running event loop' errors.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Already inside an event loop — run in a separate thread.
    future = _THREAD_POOL.submit(asyncio.run, coro)
    return future.result(timeout=_THREAD_POOL_TIMEOUT)


class LivekitService(VoiceServiceBlueprint):
    """LiveKit engine for VoiceServiceManager.

    Inbound only — outbound raises NotImplementedError.
    """

    def __init__(self, api_key: str = ""):
        # api_key is accepted for interface compatibility but unused;
        # LiveKit uses env-var based key/secret.
        self.config = LiveKitConfig.from_env()

    def validate_api_key(self) -> bool:
        """LiveKit uses env-var key/secret — always valid if config loads."""
        return True

    def _create_api(self) -> LiveKitAPI:
        """Create a fresh LiveKitAPI client."""
        return LiveKitAPI(
            url=self.config.http_url,
            api_key=self.config.api_key,
            api_secret=self.config.api_secret,
        )

    # ------------------------------------------------------------------
    # Call lifecycle
    # ------------------------------------------------------------------

    def initiate_inbound_call(self, input: InboundCallInput) -> CallResult:
        """Create room -> dispatch agent -> start egress -> SIP dial out."""
        return _run_async(self._initiate_inbound_call_async(input))

    async def _initiate_inbound_call_async(self, input: InboundCallInput) -> CallResult:
        room_name = _room_name(input.call_id)
        lkapi = self._create_api()
        try:
            # 1. Create room (idempotent — returns existing if name matches).
            #    On rerun the same call_execution_id is reused, so the old
            #    room may still exist. Clean it up if it has participants
            #    from a previous crashed run.
            existing_rooms = await lkapi.room.list_rooms(
                ListRoomsRequest(names=[room_name])
            )
            if existing_rooms.rooms:
                old_room = existing_rooms.rooms[0]
                if old_room.num_participants > 0:
                    logger.info(
                        "livekit_cleaning_stale_room_on_rerun",
                        room_name=room_name,
                        num_participants=old_room.num_participants,
                    )
                    await delete_room(lkapi, room_name)

            room_metadata = json.dumps(
                {"backend_url": self.config.backend_callback_url}
            )
            room = await create_room(lkapi, room_name, metadata=room_metadata)

            # Persist workflow_id so the room_finished webhook can signal
            # the correct Temporal workflow (normal vs rerun have different
            # workflow IDs).  Must happen before SIP dial — the call could
            # complete very quickly (busy signal) and the webhook must find it.
            workflow_id = (input.metadata or {}).get("workflow_id", "")
            if workflow_id:
                from simulate.models.test_execution import CallExecution

                ce = await CallExecution.objects.aget(id=input.call_id)
                pd = ce.provider_call_data or {}
                lk = pd.setdefault(PROVIDER_KEY, {})
                lk["workflow_id"] = workflow_id
                ce.provider_call_data = pd
                await ce.asave(update_fields=["provider_call_data"])

            # Egress (recording) is started by the agent worker, not here.
            # The agent worker starts egress in its entrypoint so recording
            # begins when the agent is ready, keeping timestamp alignment simple.

            # 2. Dispatch agent with persona metadata
            metadata = json.dumps(
                {
                    "system_prompt": input.system_prompt,
                    "voice_settings": input.voice_settings or {},
                    "call_id": input.call_id,
                    "backend_url": self.config.backend_callback_url,
                    **(input.metadata or {}),
                }
            )
            dispatch = await lkapi.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(
                    agent_name=AGENT_NAME,
                    room=room_name,
                    metadata=metadata,
                )
            )
            logger.info(
                "livekit_agent_dispatched",
                room_name=room_name,
                dispatch_id=dispatch.id,
            )

            # 3. Connect to user's agent (SIP or WebRTC bridge)
            connection_type = input.connection_type
            participant_id = None
            bridge_type = None

            if connection_type and connection_type.startswith("web_"):
                # All web bridge types (Vapi, Retell, LiveKit) use the
                # run_bridge Temporal activity. Just set bridge_type so the
                # workflow starts it — no daemon thread needed.
                participant_id = "phone-user"
                bridge_type = connection_type
            else:
                # SIP call — existing path, UNCHANGED
                phone_number = self._format_phone_number_e164(input.user_phone_number)
                participant_id = await create_sip_participant(
                    lkapi,
                    sip_trunk_id=self.config.sip_inbound_trunk_id,
                    phone_number=phone_number,
                    room_name=room_name,
                )

            # For livekit_bridge, surface the customer-side room name as
            # the provider_call_id — that's what the user can look up in
            # their own LiveKit Cloud dashboard. For native livekit / SIP,
            # use our room name (which IS the provider room).
            if connection_type == "web_livekit_bridge":
                display_call_id = f"customer_{room_name}"
            else:
                display_call_id = room_name

            return CallResult(
                success=True,
                provider_call_id=display_call_id,
                assistant_id=AGENT_NAME,
                provider_data={
                    "room_name": room_name,
                    "room_sid": room.sid,
                    "participant_id": participant_id,
                    "dispatch_id": dispatch.id,
                    **({"bridge_type": bridge_type} if bridge_type else {}),
                },
            )

        except Exception as exc:
            logger.exception("livekit_initiate_inbound_call_failed")
            # Clean up resources that were partially created
            try:
                await delete_room(lkapi, room_name)
            except Exception:
                logger.warning("livekit_room_cleanup_failed", room_name=room_name)
            # Re-raise so Temporal's retry policy can retry the activity
            # on transient errors (e.g. TwirpError 503 from agent overload)
            raise
        finally:
            await lkapi.aclose()

    def initiate_outbound_call(self, input: OutboundCallInput) -> OutboundCallResult:
        """Pre-create room, dispatch agent, store metadata.

        For outbound calls the tested agent (VAPI) calls our phone number.
        The SIP call arrives via the SIP trunk -> TwiML webhook -> LiveKit SIP bridge.
        The TwiML webhook resolves the phone number to the pre-created room
        name via the phone-resolution API so the SIP participant joins the
        room where the agent is already waiting.

        This mirrors the inbound flow (create room -> dispatch agent) so
        the agent worker receives full config via dispatch metadata and
        does not need the SIP auto-dispatch path or a separate phone
        resolution call.
        """
        return _run_async(self._initiate_outbound_call_async(input))

    async def _initiate_outbound_call_async(
        self, input: OutboundCallInput
    ) -> OutboundCallResult:
        from simulate.models.test_execution import CallExecution

        room_name = _room_name(input.call_execution_id)
        lkapi = self._create_api()
        try:
            # 1. Clean stale room on rerun (same as inbound)
            existing_rooms = await lkapi.room.list_rooms(
                ListRoomsRequest(names=[room_name])
            )
            if existing_rooms.rooms:
                old_room = existing_rooms.rooms[0]
                if old_room.num_participants > 0:
                    logger.info(
                        "livekit_cleaning_stale_room_on_rerun",
                        room_name=room_name,
                        num_participants=old_room.num_participants,
                    )
                    await delete_room(lkapi, room_name)

            # 2. Create room with backend_url in metadata
            room_metadata = json.dumps(
                {"backend_url": self.config.backend_callback_url}
            )
            room = await create_room(lkapi, room_name, metadata=room_metadata)

            # 3. Store workflow_id and outbound flag in DB
            workflow_id = (input.metadata or {}).get("workflow_id", "")
            call_execution = await CallExecution.objects.aget(
                id=input.call_execution_id
            )
            provider_data = call_execution.provider_call_data or {}
            lk_meta = provider_data.setdefault(PROVIDER_KEY, {})
            lk_meta["outbound_sip"] = True
            lk_meta["backend_url"] = self.config.backend_callback_url
            lk_meta["room_name"] = room_name
            if workflow_id:
                lk_meta["workflow_id"] = workflow_id
            call_execution.provider_call_data = provider_data
            await call_execution.asave(update_fields=["provider_call_data"])

            # 4. Dispatch agent with full config (same as inbound)
            dispatch_metadata = json.dumps(
                {
                    "system_prompt": input.system_prompt,
                    "voice_settings": input.voice_settings or {},
                    "call_id": input.call_execution_id,
                    "backend_url": self.config.backend_callback_url,
                    "outbound_sip": True,
                    **(input.metadata or {}),
                }
            )
            dispatch = await lkapi.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(
                    agent_name=AGENT_NAME,
                    room=room_name,
                    metadata=dispatch_metadata,
                )
            )
            logger.info(
                "livekit_outbound_agent_dispatched",
                room_name=room_name,
                dispatch_id=dispatch.id,
                call_id=input.call_execution_id,
                phone_number=input.phone_number,
            )

            return OutboundCallResult(
                success=True,
                phone_number_id=input.provider_phone_id,
                phone_number=input.phone_number,
            )

        except Exception as exc:
            logger.exception("livekit_outbound_call_setup_failed")
            try:
                await delete_room(lkapi, room_name)
            except Exception:
                logger.warning(
                    "livekit_outbound_room_cleanup_failed", room_name=room_name
                )
            raise
        finally:
            await lkapi.aclose()

    def end_call(self, input: EndCallInput) -> bool:
        """Terminate call by deleting the LiveKit room."""
        return _run_async(self._end_call_async(input))

    async def _end_call_async(self, input: EndCallInput) -> bool:
        payload = input.provider_call_payload or {}
        room_name = payload.get("room_name")
        if not room_name:
            logger.warning("livekit_end_call_no_room_name", payload=payload)
            return False

        lkapi = self._create_api()
        try:
            await delete_room(lkapi, room_name)
            return True
        except Exception:
            logger.exception("livekit_end_call_failed", room_name=room_name)
            return False
        finally:
            await lkapi.aclose()

    # ------------------------------------------------------------------
    # Call data retrieval
    # ------------------------------------------------------------------

    def get_call(self, input: GetCallInput) -> FAGICallData:
        """Fetch call data from DB and normalize to FAGICallData.

        For LiveKit, call_id is the room name (call_{execution_id}).
        The service_provider_call_id on CallExecution stores this value.
        """
        from simulate.models.test_execution import CallExecution

        call_execution = CallExecution.objects.get(
            service_provider_call_id=input.call_id
        )
        return self._call_execution_to_fagi(call_execution)

    async def get_call_async(self, input: GetCallInput) -> FAGICallData:
        """Async version — reads CallExecution from DB."""
        from simulate.models.test_execution import CallExecution

        call_execution = await CallExecution.objects.aget(
            service_provider_call_id=input.call_id
        )
        return self._call_execution_to_fagi(call_execution)

    def normalize_call_data(
        self, raw_data: dict[str, Any], call_data_stored: bool
    ) -> FAGICallData:
        """Build FAGICallData from raw dict (already fetched from DB)."""
        from ee.voice.semantics import FAGICallData; from simulate.semantics import CallExecutionStatus, CallType

        lk_data = raw_data.get(PROVIDER_KEY, {})

        return FAGICallData(
            call_id=raw_data.get("call_id", ""),
            call_type=CallType(raw_data.get("call_type", "inbound")),
            status=CallExecutionStatus(raw_data.get("status", "completed")),
            assistant_id=raw_data.get("assistant_id", AGENT_NAME),
            system_phone_number=lk_data.get("system_phone_number", ""),
            customer_phone_number=lk_data.get("customer_phone_number", ""),
            system_phone_number_id=lk_data.get("system_phone_number_id", ""),
            transcript_available=raw_data.get("transcript_available", False),
            recording_available=raw_data.get("recording_available", False),
            ended_reason=raw_data.get("ended_reason"),
            summary=raw_data.get("summary"),
            cost_breakdown=raw_data.get("cost_breakdown"),
            transcript=raw_data.get("transcript"),
            recording_url=raw_data.get("recording_url"),
            recording=raw_data.get("recording"),
            log_url=raw_data.get("log_url"),
            analysis_data=raw_data.get("analysis_data"),
            evaluation_data=raw_data.get("evaluation_data"),
            metadata=raw_data.get("metadata"),
            created_at=raw_data.get("created_at"),
            started_at=raw_data.get("started_at"),
            ended_at=raw_data.get("ended_at"),
            updated_at=raw_data.get("updated_at"),
            performance_metrics=raw_data.get("performance_metrics"),
            cost=raw_data.get("cost"),
            duration_seconds=raw_data.get("duration_seconds", 0),
            raw_log=raw_data.get("raw_log", {}),
        )

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------

    def get_recording_urls(self, payload: dict[str, Any] | None) -> RecordingPayload:
        """Extract recording URLs from provider payload."""
        if not payload:
            return {}
        return payload.get("recording_urls", {})

    def persist_audio_to_s3(self, input: PersistAudioInput) -> str:
        """Sync wrapper — delegates to async implementation."""
        return _run_async(self._persist_audio_to_s3_async(input))

    async def _persist_audio_to_s3_async(self, input: PersistAudioInput) -> str:
        """Copy recording from egress bucket to main S3 bucket.

        LiveKit egress writes directly to S3/S3. The audio_url
        points to the object key in the egress bucket. We download
        it asynchronously and re-upload via the existing sync S3 utils.
        """
        from simulate.temporal.utils.async_storage import download_audio_from_url_async
        from tfc.utils.storage import upload_audio_to_s3

        source_url = input.audio_url
        if not source_url:
            return source_url

        # Download from egress bucket (async)
        audio_bytes = await download_audio_from_url_async(source_url)

        # Upload to main recordings bucket (sync — reuses existing infra).
        # Timestamp in key avoids overwrites on rerun (same call_id reused).
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        object_key = f"call-recordings/{input.call_id}/{ts}_{input.url_type}.mp3"
        audio_data = {"bytes": audio_bytes}
        s3_url = upload_audio_to_s3(audio_data, object_key=object_key)

        logger.info(
            "livekit_audio_persisted",
            call_id=input.call_id,
            dest=s3_url,
        )
        return s3_url

    # ------------------------------------------------------------------
    # Client call matching
    # ------------------------------------------------------------------

    def find_client_call(self, input: FindClientCallInput) -> str | None:
        """No client call matching needed.

        In the LiveKit setup we manage the room and both participants
        (our agent + the SIP bridge to user's phone). There is no
        separate provider account on the customer side to query.
        """
        return None

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_customer_metrics(self, call_data: FAGICallData) -> CustomerMetrics:
        """Build metrics from usage data stored in raw_log by the agent worker.

        Computes costs from raw usage quantities × pricing rates.
        """
        from ee.voice.services.livekit.pricing import calculate_call_costs

        raw_log = call_data.raw_log or {}
        lk_usage = raw_log.get(PROVIDER_KEY, {}).get("usage", {})

        if not lk_usage:
            return CustomerMetrics()

        costs = calculate_call_costs(lk_usage)

        cost_breakdown = {}
        for category in ("stt", "llm", "tts"):
            cat_data = lk_usage.get(category, {})
            if cat_data:
                cost_breakdown[category] = {
                    **cat_data,
                    "cost": float(costs.get(category, 0)),
                }

        return CustomerMetrics(
            system_metrics=lk_usage.get("system_metrics"),
            cost_breakdown=cost_breakdown,
            total_cost=float(costs["total"]),
        )

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def iter_call_logs(
        self,
        url: str,
        verify_ssl: bool,
        **kwargs: Any,
    ) -> Iterable[dict]:
        """Yield log entries from CallTranscript model (url is the call_execution_id)."""
        del kwargs
        from simulate.models.test_execution import CallTranscript

        call_execution_id = url  # url repurposed as execution ID
        transcripts = CallTranscript.objects.filter(
            call_execution_id=call_execution_id
        ).order_by("created_at")

        for t in transcripts:
            yield {
                "role": t.role,
                "content": t.content,
                "timestamp": t.created_at.isoformat() if t.created_at else None,
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_execution_to_fagi(self, call_execution) -> FAGICallData:
        """Convert a CallExecution model instance to FAGICallData.

        service_provider_call_id stores the room name (call_{execution_id}),
        which is what initiate_inbound_call returns as provider_call_id.
        """
        from ee.voice.semantics import FAGICallData; from simulate.semantics import CallExecutionStatus, CallType

        provider_data = call_execution.provider_call_data or {}
        lk_data = provider_data.get(PROVIDER_KEY, {})

        return FAGICallData(
            call_id=call_execution.service_provider_call_id
            or _room_name(str(call_execution.id)),
            call_type=CallType(call_execution.call_type or "inbound"),
            status=CallExecutionStatus(call_execution.status),
            assistant_id=AGENT_NAME,
            system_phone_number=lk_data.get("system_phone_number", ""),
            customer_phone_number=lk_data.get("customer_phone_number", ""),
            system_phone_number_id=lk_data.get("system_phone_number_id", ""),
            transcript_available=bool(lk_data.get("transcript")),
            recording_available=bool(call_execution.recording_url),
            ended_reason=call_execution.ended_reason,
            summary=call_execution.call_summary,
            cost_breakdown=provider_data.get("cost_breakdown"),
            transcript=lk_data.get("transcript"),
            recording_url=call_execution.recording_url,
            recording=lk_data.get("recording"),
            log_url=str(call_execution.id),  # used by iter_call_logs
            analysis_data=provider_data.get("analysis_data"),
            evaluation_data=provider_data.get("evaluation_data"),
            metadata=provider_data.get("metadata"),
            created_at=(
                call_execution.created_at.isoformat()
                if call_execution.created_at
                else None
            ),
            started_at=(
                call_execution.started_at.isoformat()
                if call_execution.started_at
                else None
            ),
            ended_at=(
                call_execution.ended_at.isoformat() if call_execution.ended_at else None
            ),
            updated_at=(
                call_execution.updated_at.isoformat()
                if call_execution.updated_at
                else None
            ),
            performance_metrics=provider_data.get("performance_metrics"),
            cost=call_execution.cost_cents / 100.0 if call_execution.cost_cents else 0,
            duration_seconds=call_execution.duration_seconds or 0,
            raw_log=provider_data,
        )

    # ------------------------------------------------------------------
    # Provider-agnostic data extraction (for Temporal activities)
    # ------------------------------------------------------------------

    # Base speaking rate in words per minute (average English conversational speech).
    # Source: National Center for Voice and Speech (NCVS) — ~150 WPM for American English.
    # Refs: https://virtualspeech.com/blog/average-speaking-rate-words-per-minute
    #       https://tfcs.baruch.cuny.edu/speaking-rate/
    # Scaled by the persona's conversation_speed multiplier (0.5–1.5).
    _BASE_WPM = 150
    _WORD_RE = re.compile(r"\b\w+\b")

    async def get_normalized_transcript_data(
        self, call_execution_id: str
    ) -> NormalizedTranscriptData:
        """Return provider-agnostic transcript + usage data from DB.

        LiveKit agent worker writes transcripts to CallTranscript table
        and usage to provider_call_data["livekit"]["usage"] during the call.

        Since LiveKit's conversation_item_added event does not provide
        utterance end times, we estimate speech duration from word count
        and the persona's conversation_speed (stored in call_metadata).
        """
        from asgiref.sync import sync_to_async

        from simulate.models.test_execution import CallExecution, CallTranscript

        call = await CallExecution.objects.aget(id=call_execution_id)

        # Derive speaking rate from persona's conversation_speed multiplier.
        # conversation_speed: 0.5 (very slow) … 1.0 (moderate) … 1.5 (very fast)
        voice_settings = (call.call_metadata or {}).get("voice_settings", {})
        speed_raw = voice_settings.get("conversation_speed", "1.0")
        # Handle both list (["1.0"]) and scalar ("1.0") formats
        if isinstance(speed_raw, list):
            speed_raw = speed_raw[0] if speed_raw else "1.0"
        try:
            conversation_speed = float(speed_raw)
        except (TypeError, ValueError):
            conversation_speed = 1.0
        effective_wpm = self._BASE_WPM * max(conversation_speed, 0.1)

        # Read transcripts from DB (written by agent worker).
        # See simulate/utils/transcript_roles.py for DB role conventions.
        transcripts = await sync_to_async(
            lambda: list(
                CallTranscript.objects.filter(call_execution_id=call_execution_id)
                .order_by("start_time_ms")
                .values("speaker_role", "content", "start_time_ms", "end_time_ms")
            )
        )()

        # Apply recording offset so transcript timestamps align with the
        # audio file's t=0 (egress start) rather than the agent worker's
        # call_start_time.  Offset is negative (call_start_time < egress_start)
        # since session starts before egress, shifting timestamps backward.
        provider_data_raw = (
            call.provider_call_data.get(PROVIDER_KEY, {})
            if call.provider_call_data
            else {}
        )
        recording_offset_ms = provider_data_raw.get("recording_offset_ms", 0)

        messages = []
        for t in transcripts:
            start_sec = max(0.0, (t["start_time_ms"] + recording_offset_ms) / 1000.0)

            if t["end_time_ms"]:
                end_sec = max(
                    start_sec, (t["end_time_ms"] + recording_offset_ms) / 1000.0
                )
            else:
                word_count = len(self._WORD_RE.findall(t["content"] or ""))
                estimated_duration = (
                    (word_count / effective_wpm) * 60 if word_count else 0.5
                )
                end_sec = max(start_sec, start_sec + estimated_duration)

            duration = end_sec - start_sec

            messages.append(
                TranscriptMessage(
                    role=t["speaker_role"],
                    content=t["content"],
                    time=start_sec,
                    end_time=end_sec,
                    duration=duration,
                )
            )

        # Read token usage from provider_call_data
        usage = provider_data_raw.get("usage", {})
        token_usage: dict[str, Any] = {}
        llm_usage = usage.get("llm", {})
        if llm_usage:
            token_usage["llm"] = {
                "prompt_tokens": llm_usage.get("prompt_tokens", 0)
                or llm_usage.get("promptTokens", 0),
                "completion_tokens": llm_usage.get("completion_tokens", 0)
                or llm_usage.get("completionTokens", 0),
            }

        return NormalizedTranscriptData(messages=messages, token_usage=token_usage)

    async def extract_and_persist_recordings(
        self, call_execution_id: str
    ) -> RecordingUrls:
        """Extract LiveKit egress recording, split into 4 tracks, persist to S3.

        Fully streaming — each track is an independent pipeline:
          S3 egress → (ffmpeg) → S3 multipart upload
        Peak memory is O(chunk_size), never O(file_size).

        Steps:
          1. Poll egress API until recording is complete
          2. Stream stereo directly to S3 (no ffmpeg)
          3. Stream mono/assistant/customer through ffmpeg to S3
          4. Store assistant/customer URLs in provider_call_data
          5. Clean up raw egress file from S3
        """
        from livekit.protocol.egress import EgressStatus

        from simulate.models.test_execution import CallExecution
        from ee.voice.services.livekit.recording import (
            delete_egress_recording,
            poll_egress_completion,
            stream_egress_to_s3,
        )

        result = RecordingUrls()
        call_id_str = str(call_execution_id)

        call = await CallExecution.objects.aget(id=call_execution_id)
        provider_data = (
            call.provider_call_data.get(PROVIDER_KEY, {})
            if call.provider_call_data
            else {}
        )
        egress_id = provider_data.get("egress_id")

        if not egress_id:
            logger.warning("livekit_no_egress_id", call_id=call_id_str)
            return result

        # 1. Stop egress (it stays ACTIVE until explicitly stopped), then poll
        lkapi = self._create_api()
        try:
            from livekit.api import StopEgressRequest

            try:
                await lkapi.egress.stop_egress(StopEgressRequest(egress_id=egress_id))
                logger.info(
                    "livekit_egress_stop_requested",
                    call_id=call_id_str,
                    egress_id=egress_id,
                )
            except Exception as exc:
                # May already be stopped/completed — safe to continue
                logger.warning(
                    "livekit_egress_stop_skipped",
                    call_id=call_id_str,
                    egress_id=egress_id,
                    error=str(exc),
                )

            egress_info = await poll_egress_completion(lkapi, egress_id)
        finally:
            await lkapi.aclose()

        if not egress_info or egress_info.status != EgressStatus.EGRESS_COMPLETE:
            logger.warning(
                "livekit_egress_not_complete",
                call_id=call_id_str,
                egress_id=egress_id,
                status=egress_info.status if egress_info else "poll_timeout",
                error=str(egress_info.error) if egress_info else "poll timed out",
                error_code=(
                    getattr(egress_info, "error_code", None) if egress_info else None
                ),
            )
            return result

        if not egress_info.file_results:
            logger.warning(
                "livekit_egress_no_file_results",
                call_id=call_id_str,
                egress_id=egress_id,
                status=egress_info.status,
                error=str(egress_info.error),
            )
            return result

        object_key = egress_info.file_results[0].filename
        loop = asyncio.get_running_loop()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        s3_prefix = f"call-recordings/{call_id_str}/{ts}"

        # 2. Stream all 4 tracks concurrently (each reads from S3 independently).
        #    return_exceptions=True so one failure doesn't cancel the others.
        track_specs = [
            ("stereo", f"{s3_prefix}_stereo_recording.mp3", None),
            ("mono", f"{s3_prefix}_recording.mp3", ["-ac", "1"]),
            (
                "assistant",
                f"{s3_prefix}_assistant_recording.mp3",
                ["-filter_complex", "pan=mono|c0=c0"],
            ),
            (
                "customer",
                f"{s3_prefix}_customer_recording.mp3",
                ["-filter_complex", "pan=mono|c0=c1"],
            ),
        ]

        results = await asyncio.gather(
            *(
                loop.run_in_executor(
                    None, stream_egress_to_s3, self.config, object_key, s3_key, ffargs
                )
                for _, s3_key, ffargs in track_specs
            ),
            return_exceptions=True,
        )

        for (track_name, _, _), outcome in zip(track_specs, results):
            if isinstance(outcome, Exception):
                logger.exception(
                    "livekit_stream_failed",
                    call_id=call_id_str,
                    track=track_name,
                    exc_info=outcome,
                )
                continue
            if track_name == "stereo":
                result.stereo_recording_url = outcome
            elif track_name == "mono":
                result.recording_url = outcome
            elif track_name == "assistant":
                result.assistant_recording_url = outcome
            elif track_name == "customer":
                result.customer_recording_url = outcome

        # 6. Store recording URLs in provider_call_data.
        # Individual keys for internal use, plus a "recording" dict with the
        # standard keys (stereo/assistant/customer/combined) that the
        # CallExecutionDetailSerializer.get_recordings() returns to the frontend.
        existing_data = call.provider_call_data or {}
        lk_data = existing_data.get(PROVIDER_KEY, {})
        if result.assistant_recording_url:
            lk_data["assistant_recording_url"] = result.assistant_recording_url
        if result.customer_recording_url:
            lk_data["customer_recording_url"] = result.customer_recording_url

        recording_dict: dict[str, str] = {}
        if result.stereo_recording_url:
            recording_dict["stereo"] = result.stereo_recording_url
        if result.assistant_recording_url:
            recording_dict["assistant"] = result.assistant_recording_url
        if result.customer_recording_url:
            recording_dict["customer"] = result.customer_recording_url
        if result.recording_url:
            recording_dict["combined"] = result.recording_url
        lk_data["recording"] = recording_dict

        existing_data[PROVIDER_KEY] = lk_data
        call.provider_call_data = existing_data
        await call.asave(update_fields=["provider_call_data"])

        # 7. Cleanup: delete raw egress file from S3
        try:
            await loop.run_in_executor(
                None, delete_egress_recording, self.config, object_key
            )
        except Exception:
            logger.warning(
                "livekit_egress_cleanup_failed",
                call_id=call_id_str,
                object_key=object_key,
            )

        logger.info(
            "livekit_recordings_processed",
            call_id=call_id_str,
            recording_url=bool(result.recording_url),
            stereo_recording_url=bool(result.stereo_recording_url),
            assistant_recording_url=bool(result.assistant_recording_url),
            customer_recording_url=bool(result.customer_recording_url),
        )

        return result

    async def extract_costs(self, call_execution_id: str) -> CostBreakdown:
        """Compute LiveKit cost breakdown from agent-tracked usage quantities.

        Multiplies raw usage (duration, tokens, characters) by per-model
        pricing rates.  LLM rates come from litellm; STT/TTS/storage rates
        are configured in ``simulate.services.livekit.pricing.RATES``.
        """
        from simulate.models.test_execution import CallExecution
        from ee.voice.services.livekit.pricing import calculate_call_costs

        call = await CallExecution.objects.aget(id=call_execution_id)
        provider_data = (
            call.provider_call_data.get(PROVIDER_KEY, {})
            if call.provider_call_data
            else {}
        )
        usage = provider_data.get("usage", {})

        if not usage:
            return CostBreakdown()

        costs = calculate_call_costs(
            usage,
            duration_seconds=call.duration_seconds or 0.0,
        )

        return CostBreakdown(
            total=float(costs["total"]),
            stt=float(costs["stt"]),
            llm=float(costs["llm"]),
            tts=float(costs["tts"]),
            storage=float(costs["storage"]),
        )

    async def fetch_and_store_call_data(
        self,
        call_execution_id: str,
        provider_call_id: str,
        status: str,
        duration_seconds: float | None = None,
        end_reason: str | None = None,
        provider_data: dict[str, Any] | None = None,
    ) -> tuple[int, bool, bool]:
        """Store LiveKit call data and count existing transcripts.

        Unlike VAPI, LiveKit agent worker already writes transcripts and
        usage data to DB during the call. This method:
        1. Cleans up LiveKit resources (room + egress) on failure/cancellation
        2. Stores provider_data (room_name, egress_id, etc.) to CallExecution
        3. Counts existing CallTranscript records
        4. Checks speaker presence

        Returns (message_count, has_agent_message, has_customer_message).
        """
        from asgiref.sync import sync_to_async

        from simulate.models.test_execution import CallExecution, CallTranscript

        call = await CallExecution.objects.aget(id=call_execution_id)
        update_fields: list[str] = []

        # Store provider data (room metadata from initiate_call)
        existing_data = call.provider_call_data or {}
        lk_data = existing_data.get(PROVIDER_KEY, {})
        if provider_data:
            lk_data.update(provider_data)
        existing_data[PROVIDER_KEY] = lk_data
        call.provider_call_data = existing_data
        update_fields.append("provider_call_data")

        # Clean up LiveKit resources on failure/cancellation
        failed_statuses = {
            CallExecution.CallStatus.FAILED,
            CallExecution.CallStatus.FAILED.value,
            CallExecution.CallStatus.CANCELLED,
            CallExecution.CallStatus.CANCELLED.value,
        }
        if status in failed_statuses:
            room_name = lk_data.get("room_name") or _room_name(str(call_execution_id))
            egress_id = lk_data.get("egress_id")
            lkapi = self._create_api()
            try:
                if egress_id:
                    try:
                        from livekit.api import StopEgressRequest

                        await lkapi.egress.stop_egress(
                            StopEgressRequest(egress_id=egress_id)
                        )
                    except Exception:
                        logger.warning(
                            "livekit_egress_stop_failed",
                            egress_id=egress_id,
                        )
                await delete_room(lkapi, room_name)
                logger.info(
                    "livekit_call_cleanup_on_failure",
                    room_name=room_name,
                    status=status,
                )
            except Exception:
                logger.warning(
                    "livekit_call_cleanup_failed",
                    room_name=room_name,
                    status=status,
                )
            finally:
                await lkapi.aclose()

        # Set room name as service_provider_call_id for record-keeping.
        # For outbound SIP calls, the agent worker already wrote the actual
        # sip_... room name to service_provider_call_id — don't overwrite it.
        if not call.service_provider_call_id:
            room_name = (
                lk_data.get("room_name")
                or (provider_data or {}).get("room_name")
                or _room_name(str(call_execution_id))
            )
            call.service_provider_call_id = room_name
            update_fields.append("service_provider_call_id")

        # Store basic fields
        if end_reason:
            call.ended_reason = end_reason
            update_fields.append("ended_reason")

        from django.utils import timezone

        call.ended_at = timezone.now()
        update_fields.append("ended_at")

        if duration_seconds is not None:
            call.duration_seconds = int(duration_seconds)
            update_fields.append("duration_seconds")

        # NOTE: Do NOT set call.status here. The Temporal workflow owns status
        # transitions via update_call_status activity (ONGOING → ANALYZING → COMPLETED).
        # Writing status here would cause a brief flash to COMPLETED between ANALYZING
        # and the workflow's final COMPLETED update.

        # Count existing transcripts (written by agent worker during call)
        transcript_qs = CallTranscript.objects.filter(
            call_execution_id=call_execution_id
        )
        message_count = await transcript_qs.acount()

        from simulate.utils.speaker_roles import SpeakerRoleResolver
        from tracer.models.observability_provider import ProviderChoices

        call_dir = (call.call_metadata or {}).get("call_direction", "")
        is_outbound = str(call_dir).strip().lower() == "outbound"
        agent_roles, customer_roles = SpeakerRoleResolver.get_transcript_role_sets(
            provider=ProviderChoices.LIVEKIT,
            is_outbound=is_outbound,
        )

        has_agent_message = await sync_to_async(
            lambda: (
                transcript_qs.filter(speaker_role__in=agent_roles)
                .exclude(content="")
                .exists()
            )
        )()

        has_customer_message = await sync_to_async(
            lambda: (
                transcript_qs.filter(speaker_role__in=customer_roles)
                .exclude(content="")
                .exists()
            )
        )()

        call.transcript_available = message_count > 0
        call.message_count = message_count
        update_fields.extend(["transcript_available", "message_count"])

        await call.asave(update_fields=update_fields)

        return message_count, has_agent_message, has_customer_message
