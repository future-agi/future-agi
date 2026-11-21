"""LivekitService — VoiceServiceBlueprint implementation for LiveKit.

Handles inbound calls only (we call user's agent). Outbound deferred to TH-3208/TH-3220.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypedDict

import structlog
from asgiref.sync import sync_to_async
from django.core import signing
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
from tfc.utils.media_tools import find_media_tool_unavailable
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


RECORDING_SPLIT_FAILED = "recording_split_failed"


class _RecordingSource(TypedDict):
    call_id: str
    egress_id: str


class _TrackReceipt(_RecordingSource):
    url: str


class _CleanupConfirmation(_RecordingSource):
    recording: dict[str, str]


def _verified_tracks(
    lk: dict[str, Any], identity: _RecordingSource, kinds: Iterable[str]
) -> dict[str, str]:
    """Read verified outputs, never infer identity from a playback URL."""
    visible = lk.get("recording")
    source = lk.get("recording_source")
    if not isinstance(visible, dict) or (source is not None and source != identity):
        return {}
    receipts = lk.get("verified_recording_tracks")
    tracks = {}
    for kind in kinds:
        url = visible.get(kind)
        if not isinstance(url, str) or not url.strip():
            continue
        if isinstance(receipts, dict):
            if receipts.get(kind) == _TrackReceipt(**identity, url=url):
                tracks[kind] = url
        elif receipts is None and source == identity:
            # Previous whole-map format proves track origin, not column authorship.
            tracks[kind] = url
    return tracks


def _source_for_call(value, call_id: str) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"call_id", "egress_id"}
        and value["call_id"] == call_id
        and isinstance(value["egress_id"], str)
        and bool(value["egress_id"].strip())
    )


@sync_to_async
def _recording_tenant(call):
    run = call.test_execution.run_test
    return {
        "organization_id": str(run.organization_id),
        "workspace_id": str(run.workspace_id) if run.workspace_id else None,
    }


def _source_tracks(lk, tenant, identity, fields):
    history = lk.get("recording_history")
    egress = identity["egress_id"]
    if not isinstance(egress, str):
        return {}
    entry = history.get(egress) if isinstance(history, dict) else None
    binding = {**tenant, **identity}
    archived = {}
    if isinstance(entry, dict) and entry.get("source") == binding:
        urls = entry.get("tracks")
        if isinstance(urls, dict):
            archived = {
                k: v
                for k, v in urls.items()
                if k in fields and isinstance(v, str) and v.strip()
            }
    return {**archived, **_verified_tracks(lk, identity, fields)}


def _merge_track_view(lk, tenant, identity, fields, merged):
    """Archive verified origins independently of the last visible URL per kind."""
    updated = lk.copy()
    original = lk.get("recording")
    visible = original.copy() if isinstance(original, dict) else {}
    source = lk.get("recording_source")
    prior = lk.get("verified_recording_tracks")
    receipts = prior.copy() if isinstance(prior, dict) else {}
    history = lk.get("recording_history")
    history = history.copy() if isinstance(history, dict) else {}
    origins = [source]
    if isinstance(prior, dict):
        origins.extend(
            {k: receipt.get(k) for k in ("call_id", "egress_id")}
            for receipt in prior.values()
            if isinstance(receipt, dict)
        )
    for origin in origins:
        # Upgrade only explicitly bound whole maps or matching per-kind receipts.
        if not _source_for_call(origin, identity["call_id"]):
            continue
        previous = _verified_tracks(lk, origin, fields)
        if previous:
            history[origin["egress_id"]] = {
                "source": {**tenant, **origin},
                "tracks": _source_tracks(lk, tenant, origin, fields),
            }
            receipts.update(
                {k: _TrackReceipt(**origin, url=v) for k, v in previous.items()}
            )
    if merged:
        history[identity["egress_id"]] = {
            "source": {**tenant, **identity},
            "tracks": merged,
        }
    if history:
        updated["recording_history"] = history
        updated["recording_scope"] = {**tenant, "call_id": identity["call_id"]}
    visible.update(merged)
    updated["recording"] = visible
    receipts.update({k: _TrackReceipt(**identity, url=v) for k, v in merged.items()})
    if receipts:
        updated["verified_recording_tracks"] = receipts
    else:
        updated.pop("verified_recording_tracks", None)
    if visible == merged and merged:
        updated["recording_source"] = identity
    elif merged:
        updated.pop("recording_source", None)
    for kind in ("assistant", "customer"):
        if kind in merged:
            updated[fields[kind]] = merged[kind]
    return updated


def _owns_playback(owner, tracks, name, call_id, url) -> bool:
    # A produced track need never have been installed in the playback column.
    # Require both the actual column-write receipt and its matching track.
    return (
        isinstance(owner, dict)
        and owner.get("call_id") == call_id
        and isinstance(owner.get("egress_id"), str)
        and bool(owner["egress_id"].strip())
        and owner.get("url") == url
        and isinstance(tracks, dict)
        and owner == tracks.get(name)
    )


def _recording_error(failures: dict[str, Exception]) -> dict[str, Any]:
    """What provider_call_data records when recording tracks failed.

    A missing ffmpeg keeps its named message and code. Other errors (storage,
    an ffmpeg exit) get a fixed message: their text can carry bucket paths
    or ffmpeg output, which stay in the logs."""
    for exc in failures.values():
        missing = find_media_tool_unavailable(exc)
        if missing is not None:
            return {
                "code": missing.code,
                "message": str(missing),
                "tracks": sorted(failures),
            }
    return {
        "code": RECORDING_SPLIT_FAILED,
        "message": "Some call recording tracks could not be created.",
        "tracks": sorted(failures),
    }


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
        from ee.voice.semantics import FAGICallData
        from simulate.semantics import CallExecutionStatus, CallType

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
        from ee.voice.semantics import FAGICallData
        from simulate.semantics import CallExecutionStatus, CallType

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
        from livekit.api import ListEgressRequest
        from livekit.protocol.egress import EgressStatus

        from ee.voice.services.livekit.recording import (
            delete_egress_recording,
            poll_egress_completion,
            stream_egress_to_s3,
        )
        from simulate.models.test_execution import CallExecution

        call_id_str = str(call_execution_id)

        call = await CallExecution.objects.aget(id=call_execution_id)
        provider_data = (
            call.provider_call_data.get(PROVIDER_KEY, {})
            if call.provider_call_data
            else {}
        )
        egress_id = provider_data.get("egress_id")

        # fetch_and_store_call_data may already have replaced egress_id. Bind
        # the outputs to their source independently of that mutable field.
        identity = _RecordingSource(call_id=call_id_str, egress_id=egress_id)
        fields = {
            "stereo": "stereo_recording_url",
            "combined": "recording_url",
            "assistant": "assistant_recording_url",
            "customer": "customer_recording_url",
        }
        saved = provider_data.get("recording")
        source = provider_data.get("recording_source")
        valid_source = _source_for_call(source, call_id_str)
        playback_fields = (
            "recording_url",
            "stereo_recording_url",
            "recording_available",
        )
        # Snapshot before any external I/O. A CAS retry may refresh its SQL
        # predicate, but must never adopt a peer's playback change as our baseline.
        initial_playback = {key: getattr(call, key) for key in playback_fields}
        tenant = await _recording_tenant(call)
        scope = {**tenant, "call_id": call_id_str}
        if provider_data.get("recording_scope", scope) != scope:
            return RecordingUrls(
                recording_fields_persisted=True,
                error={
                    "code": "recording_source_changed",
                    "message": "Recording scope changed.",
                    "tracks": [],
                },
            )
        tracks = _source_tracks(provider_data, tenant, identity, fields)
        if (
            not valid_source
            and not tracks
            and (call.recording_url or call.stereo_recording_url)
            and isinstance(saved, dict)
            and all(isinstance(saved.get(k), str) and saved[k].strip() for k in fields)
        ):
            # Legacy object keys include the call ID but not the egress ID. The
            # egress-ended webhook can replace egress_id before the feeder even
            # reads this row. Neither its current value nor room_name proves
            # which egress produced these four tracks. Leave the completed call
            # visible; return no URLs or metadata update for the activity to save.
            return RecordingUrls(
                recording_fields_persisted=True,
                error={
                    "code": "recording_source_unverified",
                    "message": "Existing call recordings were kept; their egress could not be verified.",
                    "tracks": [],  # no split was attempted
                },
            )
        if not isinstance(egress_id, str) or not egress_id.strip():
            logger.warning("livekit_no_egress_id", call_id=call_id_str)
            return RecordingUrls(recording_fields_persisted=True)
        # Bridges can expose a customer-side display ID; egress still runs
        # in call_<id>. SIP outbound can instead use the persisted SIP room.
        call_rooms = {_room_name(call_id_str)}
        if call.service_provider_call_id and call.service_provider_call_id.startswith(
            "sip_"
        ):
            call_rooms.add(call.service_provider_call_id)

        def source_key(info):
            if (
                info
                and info.egress_id == egress_id
                and info.room_name in call_rooms
                and info.status == EgressStatus.EGRESS_COMPLETE
                and info.file_results
            ):
                key = info.file_results[0].filename
                return key if isinstance(key, str) and key.strip() else None
            return None

        uploaded: dict[str, str] = {}
        upload_keys: dict[str, str] = {}
        failures: dict[str, Exception] = {}

        signer = signing.Signer(salt="livekit.raw-cleanup.v1")
        intent_field = "recording_cleanup_intent"

        def intent_binding(complete):
            return {
                **tenant,
                **identity,
                "version": 1,
                "recording": complete,
                "room": call.service_provider_call_id,
                "bucket": str(self.config.egress_bucket),
            }

        def verified_intent_key(token, complete):
            if not isinstance(token, str) or complete.keys() != fields.keys():
                return None
            try:
                payload = signer.unsign_object(token)
            except signing.BadSignature:
                return None
            if not isinstance(payload, dict):
                return None
            key = payload.pop("key", None)
            if (
                payload == intent_binding(complete)
                and isinstance(key, str)
                and key.strip()
            ):
                return key
            return None

        async def persist(
            *,
            cleanup_tracks=None,
            cleanup_error=None,
            reuse_confirmation=False,
            prepare_key=None,
            expected_intent=None,
            discovery_failed=False,
        ):
            # Only the read/compare/update is retried. Never hold a row lock
            # over LiveKit, S3, or the activity's subsequent cost lookup.
            cas_misses = 0
            while True:
                current = await CallExecution.objects.aget(id=call_execution_id)
                data = current.provider_call_data or {}
                lk = data.get(PROVIDER_KEY, {})
                source = lk.get("recording_source")
                current_map = lk.get("recording")
                verified = _source_tracks(lk, tenant, identity, fields)
                bound = source == identity or bool(verified)
                if (
                    current.service_provider_call_id != call.service_provider_call_id
                    or lk.get("egress_id") != egress_id
                    or lk.get("recording_scope", scope) != scope
                    or current.test_execution_id != call.test_execution_id
                    or await _recording_tenant(current) != tenant
                    or (
                        cleanup_tracks is not None
                        and (
                            _verified_tracks(lk, identity, fields) != cleanup_tracks
                            or current_map != cleanup_tracks
                            or (
                                expected_intent is not None
                                and lk.get(intent_field) != expected_intent
                            )
                            or (
                                reuse_confirmation
                                and lk.get("recording_cleanup_success")
                                != _CleanupConfirmation(
                                    **identity, recording=cleanup_tracks
                                )
                            )
                        )
                    )
                ):
                    logger.warning(
                        "livekit_superseded_recording_retained",
                        call_id=call_id_str,
                        egress_id=egress_id,
                        retained_output_keys=list(upload_keys.values()),
                    )
                    return RecordingUrls(
                        provider_call_data=data,
                        recording_fields_persisted=True,
                        error={
                            "code": "recording_source_changed",
                            "message": "Source changed; current recordings were kept.",
                            "tracks": [],
                        },
                    )

                merged = {**uploaded, **verified}
                updated = lk.copy()
                confirmation = _CleanupConfirmation(**identity, recording=merged)
                confirmed = (
                    merged.keys() == fields.keys()
                    and current_map == merged
                    and _verified_tracks(lk, identity, fields) == merged
                    and lk.get("recording_cleanup_success") == confirmation
                )
                error = cleanup_error
                if cleanup_tracks is None:
                    updated = _merge_track_view(lk, tenant, identity, fields, merged)
                    if (
                        not confirmed
                        and merged.keys() == fields.keys()
                        and object_key
                        and not verified_intent_key(lk.get(intent_field), merged)
                    ):
                        updated[intent_field] = signer.sign_object(
                            {**intent_binding(merged), "key": object_key}
                        )
                    if not confirmed:
                        updated.pop("recording_cleanup_success", None)
                    missing = {
                        name: failures.get(name, ValueError("Missing recording track"))
                        for name in ("mono", "stereo", "assistant", "customer")
                        if ("combined" if name == "mono" else name) not in merged
                    }
                    error = _recording_error(missing) if missing else None
                    prior_error = lk.get("recording_error")
                    if (
                        not missing
                        and bound
                        and current_map == merged
                        and not confirmed
                        and isinstance(prior_error, dict)
                        and prior_error.get("code") == "recording_cleanup_failed"
                    ):
                        error = prior_error
                elif confirmed:
                    error = None  # A later timeout cannot undo a confirmed deletion.
                elif discovery_failed and verified_intent_key(
                    lk.get(intent_field), cleanup_tracks
                ):
                    # Discovery raced a peer's prepare. Return its verified token
                    # for one delete, not a confirmation or a stale lookup error.
                    return RecordingUrls(
                        provider_call_data=data, recording_fields_persisted=True
                    )
                elif prepare_key is not None:
                    updated["recording_scope"] = scope
                    if not verified_intent_key(lk.get(intent_field), merged):
                        updated[intent_field] = signer.sign_object(
                            {**intent_binding(merged), "key": prepare_key}
                        )
                elif cleanup_error is None:
                    updated["recording_cleanup_success"] = confirmation
                else:
                    updated.pop("recording_cleanup_success", None)
                if error:
                    updated["recording_error"] = error
                else:
                    updated.pop("recording_error", None)

                result = RecordingUrls(
                    **{field: merged.get(name) for name, field in fields.items()},
                    recording_fields_persisted=True,
                    error=error,
                )
                changes: dict[str, Any] = {}
                predicate = {
                    "id": call_execution_id,
                    "test_execution_id": call.test_execution_id,
                    "provider_call_data": current.provider_call_data,
                    "service_provider_call_id": current.service_provider_call_id,
                }
                if cleanup_tracks is None:
                    observed = {key: getattr(current, key) for key in playback_fields}
                    predicate.update(observed, call_metadata=current.call_metadata)
                    metadata = current.call_metadata
                    artifacts = (
                        metadata.get("alk_recording_artifacts")
                        if isinstance(metadata, dict)
                        else None
                    )
                    # Merge the source-bound map independently of playback.
                    # A peer's column wins even if it never touched provider JSON.
                    owners = lk.get("recording_playback_writes")
                    owners = owners.copy() if isinstance(owners, dict) else {}
                    for name in ("combined", "stereo"):
                        field = fields[name]
                        # An ALK upload can predate this attempt. Its per-kind
                        # receipt owns only the matching URL on this call.
                        artifact = (
                            artifacts.get(name) if isinstance(artifacts, dict) else None
                        )
                        owner = owners.get(name)
                        if not _owns_playback(
                            owner,
                            lk.get("verified_recording_tracks"),
                            name,
                            call_id_str,
                            observed[field],
                        ):
                            owners.pop(name, None)
                        if (
                            isinstance(artifact, dict)
                            and artifact.get("sha256")
                            and artifact.get("object_key")
                            and observed[field]
                            and artifact.get("recording_url") == observed[field]
                        ):
                            owners.pop(name, None)
                            continue
                        value = merged.get(name)
                        if (
                            value
                            and observed[field] == initial_playback[field] != value
                            and (not observed[field] or name in owners)
                        ):
                            changes[field] = value
                            owners[name] = _TrackReceipt(**identity, url=value)
                    if owners:
                        updated["recording_playback_writes"] = owners
                    else:
                        updated.pop("recording_playback_writes", None)
                    available = any(
                        changes.get(field, observed[field])
                        for field in playback_fields[:2]
                    )
                    if observed["recording_available"] != available:
                        changes["recording_available"] = available
                if updated != lk:
                    changes["provider_call_data"] = {**data, PROVIDER_KEY: updated}
                if changes and cleanup_tracks is not None and cas_misses >= 3:
                    # A continuously changing row wins. Do not keep retrying its
                    # error/receipt write, or claim the proposed write succeeded.
                    result.provider_call_data = data
                    result.error = {
                        "code": "recording_source_changed",
                        "message": "Recording state changed; current recordings were kept.",
                        "tracks": [],
                    }
                    return result
                if changes and not await CallExecution.objects.filter(
                    **predicate
                ).aupdate(**changes):
                    cas_misses += 1
                    continue
                if cleanup_tracks is None:
                    unused = [
                        upload_keys[k] for k in uploaded if uploaded[k] != merged[k]
                    ]
                    if unused:
                        logger.warning(
                            "livekit_duplicate_recording_retained",
                            call_id=call_id_str,
                            egress_id=egress_id,
                            retained_output_keys=unused,
                        )
                result.provider_call_data = changes.get("provider_call_data", data)
                return result

        async def cleanup(snapshot, object_key=None):
            complete = snapshot["recording"]
            if snapshot.get("recording_cleanup_success") == _CleanupConfirmation(
                **identity, recording=complete
            ):
                return await persist(cleanup_tracks=complete, reuse_confirmation=True)
            token = snapshot.get(intent_field)
            durable_key = verified_intent_key(token, complete)
            failure = {
                "code": "recording_cleanup_failed",
                "message": "Tracks are available; raw recording cleanup could not be confirmed.",
                "tracks": [],
            }
            if durable_key is None:
                if object_key is None:
                    # A pre-intent map needs either verified provider evidence
                    # or an exact-map intent installed while discovery awaited.
                    try:
                        api = self._create_api()
                        try:
                            response = await api.egress.list_egress(
                                ListEgressRequest(egress_id=egress_id)
                            )
                            for info in response.items:
                                object_key = source_key(info)
                                if object_key:
                                    break
                        finally:
                            await api.aclose()
                    except Exception:  # noqa: BLE001 - recheck durable evidence after provider failure
                        object_key = None
                prepared = await persist(
                    cleanup_tracks=complete,
                    prepare_key=object_key,
                    discovery_failed=not object_key,
                    cleanup_error=None if object_key else failure,
                )
                if prepared.error:
                    return prepared
                pending = prepared.provider_call_data[PROVIDER_KEY]
                if pending.get("recording_cleanup_success") == _CleanupConfirmation(
                    **identity, recording=complete
                ):
                    return prepared
                token = pending.get(intent_field)
                durable_key = verified_intent_key(token, complete)
                if not durable_key:
                    # No valid authority: never delete or report success.
                    prepared.error = failure
                    return prepared
            error = None
            try:
                # S3 DeleteObject succeeds for a missing key. No HEAD/GET race.
                await asyncio.to_thread(
                    delete_egress_recording, self.config, durable_key
                )
            except Exception:  # noqa: BLE001 - cleanup must not discard completed tracks
                logger.warning(
                    "livekit_egress_cleanup_failed",
                    call_id=call_id_str,
                    egress_id=egress_id,
                )
                error = failure
            return await persist(
                cleanup_tracks=complete,
                cleanup_error=error,
                expected_intent=token,
            )

        if tracks.keys() == fields.keys():
            if (
                saved == tracks
                and _verified_tracks(provider_data, identity, fields) == tracks
            ):
                return await cleanup(provider_data)
            object_key = None
            restored = await persist()
            if restored.error:
                return restored
            return await cleanup(restored.provider_call_data[PROVIDER_KEY])

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
            object_key = None
        elif not egress_info.file_results:
            logger.warning(
                "livekit_egress_no_file_results",
                call_id=call_id_str,
                egress_id=egress_id,
                status=egress_info.status,
                error=str(egress_info.error),
            )
            object_key = None
        else:
            object_key = source_key(egress_info)
        loop = asyncio.get_running_loop()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        s3_prefix = f"call-recordings/{call_id_str}/{egress_id}/{ts}-{uuid.uuid4().hex}"

        # 2. Stream missing tracks concurrently (each reads from S3 independently).
        #    return_exceptions=True so one failure doesn't cancel the others.
        track_specs = [
            ("stereo", f"{s3_prefix}_stereo_recording.mp3", None),
            ("mono", f"{s3_prefix}_recording.mp3", ["-ac", "1"]),
            # The agent worker records the room with DUAL_CHANNEL_AGENT audio
            # mixing: the agent participant, our simulator (the customer), on
            # the left channel and everyone else, the tested agent (the
            # assistant), on the right.
            (
                "assistant",
                f"{s3_prefix}_assistant_recording.mp3",
                ["-filter_complex", "pan=mono|c0=c1"],
            ),
            (
                "customer",
                f"{s3_prefix}_customer_recording.mp3",
                ["-filter_complex", "pan=mono|c0=c0"],
            ),
        ]

        track_specs = [
            spec
            for spec in track_specs
            if ("combined" if spec[0] == "mono" else spec[0]) not in tracks
        ]
        if object_key:
            results = await asyncio.gather(
                *(
                    loop.run_in_executor(
                        None,
                        stream_egress_to_s3,
                        self.config,
                        object_key,
                        s3_key,
                        ffargs,
                    )
                    for _, s3_key, ffargs in track_specs
                ),
                return_exceptions=True,
            )
        else:
            results = [ValueError("No completed egress source") for _ in track_specs]

        for (track_name, destination, _), outcome in zip(
            track_specs, results, strict=True
        ):
            if isinstance(outcome, BaseException) and not isinstance(
                outcome, Exception
            ):
                raise outcome
            if not isinstance(outcome, Exception) and not (
                isinstance(outcome, str) and outcome.strip()
            ):
                outcome = ValueError("Recording upload returned no URL")
            if isinstance(outcome, Exception):
                failures[track_name] = outcome
                missing = find_media_tool_unavailable(outcome)
                if missing is not None:
                    # The named message is the whole story; no traceback.
                    logger.error(
                        "livekit_stream_failed",
                        call_id=call_id_str,
                        track=track_name,
                        code=missing.code,
                        missing_tool=missing.tool,
                        error=str(missing),
                    )
                else:
                    logger.exception(
                        "livekit_stream_failed",
                        call_id=call_id_str,
                        track=track_name,
                        exc_info=outcome,
                    )
                continue
            name = "combined" if track_name == "mono" else track_name
            uploaded[name] = outcome
            upload_keys[name] = destination

        result = await persist()
        if result.error and result.error["code"] != "recording_cleanup_failed":
            logger.warning(
                "livekit_egress_cleanup_skipped",
                call_id=call_id_str,
                object_key=object_key,
                failed_tracks=result.error["tracks"],
                code=result.error["code"],
            )
        else:
            result = await cleanup(result.provider_call_data[PROVIDER_KEY], object_key)

        logger.info(
            "livekit_recordings_processed",
            call_id=call_id_str,
            recording_url=bool(result.recording_url),
            stereo_recording_url=bool(result.stereo_recording_url),
            assistant_recording_url=bool(result.assistant_recording_url),
            customer_recording_url=bool(result.customer_recording_url),
            recording_error=result.error["code"] if result.error else None,
        )

        return result

    async def extract_costs(self, call_execution_id: str) -> CostBreakdown:
        """Compute LiveKit cost breakdown from agent-tracked usage quantities.

        Multiplies raw usage (duration, tokens, characters) by per-model
        pricing rates.  LLM rates come from litellm; STT/TTS/storage rates
        are configured in ``simulate.services.livekit.pricing.RATES``.
        """
        from ee.voice.services.livekit.pricing import calculate_call_costs
        from simulate.models.test_execution import CallExecution

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
