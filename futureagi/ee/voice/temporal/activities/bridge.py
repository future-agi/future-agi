"""Long-running Temporal activity: WebRTC audio bridge.

Pipes audio bidirectionally between our LiveKit room (where the simulator
agent runs) and the customer's provider (Vapi, Retell, or LiveKit).
Runs in the Temporal worker's asyncio event loop for LiveKit FFI compatibility.
"""

import asyncio
import os
import time
from collections import deque

from temporalio import activity

from simulate.temporal.types.activities import RunBridgeInput, RunBridgeOutput

ROOM_SAMPLE_RATE = 48000
ROOM_CHANNELS = 1
WATCHDOG_TIMEOUT_S = 60
HEARTBEAT_INTERVAL_S = 15
TRACK_TIMEOUT_S = 30

# Frames captured by rtc.AudioStream are typically 10ms each, so 100 frames
# equals roughly 1 second of audio. Used to cap the pre-agent-ready buffer
# so memory stays bounded if the customer agent never joins.
FRAMES_PER_SECOND = 100
R2P_BUFFER_MAX_SECONDS = 30
R2P_BUFFER_MAX_FRAMES = R2P_BUFFER_MAX_SECONDS * FRAMES_PER_SECOND


@activity.defn(name="run_bridge")
async def run_bridge(input: RunBridgeInput) -> RunBridgeOutput:
    """Join our LiveKit room, connect to provider, pipe audio until done."""
    from livekit import rtc
    from livekit.api import AccessToken, VideoGrants

    from ee.voice.services.livekit.bridge.connector import get_connector_registry

    url = os.environ.get("LIVEKIT_URL", "")
    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")

    if not all([url, api_key, api_secret]):
        raise RuntimeError(
            "Missing LIVEKIT_URL, LIVEKIT_API_KEY, or LIVEKIT_API_SECRET"
        )

    from ee.voice.services.livekit.bridge.connector import ConnectorConfig

    # --- Build provider connector ---
    connector_cls = get_connector_registry().get(input.connection_type)
    if not connector_cls:
        raise RuntimeError(f"Unsupported connection_type: {input.connection_type}")

    config = ConnectorConfig(
        api_key=input.customer_api_key,
        assistant_id=input.customer_assistant_id,
    )
    if input.customer_livekit_url:
        config = ConnectorConfig(
            api_key=input.customer_livekit_api_key or input.customer_api_key,
            assistant_id=input.customer_assistant_id,
            livekit_url=input.customer_livekit_url,
            livekit_api_secret=input.customer_livekit_api_secret,
            room_name=f"customer_{input.room_name}",
            call_id=input.call_id,
        )
    connector = connector_cls(config)

    # --- Join our LiveKit room as SIP-kind participant ---
    token = AccessToken(api_key, api_secret)
    token.with_identity("phone-user")
    token.with_name("WebRTC Bridge")
    token.with_kind("sip")
    token.with_grants(
        VideoGrants(
            room_join=True,
            room=input.room_name,
            can_publish=True,
            can_subscribe=True,
        )
    )

    room = rtc.Room()
    loop = asyncio.get_running_loop()
    track_future: asyncio.Future[rtc.RemoteAudioTrack] = loop.create_future()
    # Set when our LiveKit room is closed externally (e.g. end_call calling
    # delete_room from the workflow, or the simulator agent calling
    # room.disconnect on conversation end). Without this signal the
    # TaskGroup would keep _provider_to_room running off customer-side
    # audio and never tear down the customer room — leaving an orphaned
    # room and the call stuck in "ongoing" until max_duration.
    room_disconnected: asyncio.Future[str] = loop.create_future()

    @room.on("track_subscribed")
    def _on_track(track, publication, participant):
        # Only latch onto the SIMULATOR agent's MICROPHONE track. Agents
        # can publish multiple audio tracks (diagnostics, pre-start
        # pings) and the first one is rarely the one carrying TTS.
        # Skip non-audio tracks and SIP participants silently to keep
        # log volume down; only log the outcome.
        if not isinstance(track, rtc.RemoteAudioTrack):
            return
        if track_future.done():
            return
        if publication.source != rtc.TrackSource.SOURCE_MICROPHONE:
            activity.logger.info(
                "bridge.track_skipped call_id=%s from=%s source=%s reason=not_microphone",
                input.call_id,
                getattr(participant, "identity", "?"),
                publication.source,
            )
            return
        if (
            getattr(participant, "kind", None)
            == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        ):
            return
        activity.logger.info(
            "bridge.track_latched call_id=%s from=%s",
            input.call_id,
            getattr(participant, "identity", "?"),
        )
        track_future.set_result(track)

    @room.on("disconnected")
    def _on_room_disconnected(reason=None):
        if not room_disconnected.done():
            room_disconnected.set_result(str(reason) if reason is not None else "")

    activity.logger.info(
        "bridge.our_room_connecting call_id=%s url=%s room=%s",
        input.call_id,
        url,
        input.room_name,
    )
    await room.connect(url, token.to_jwt())
    activity.logger.info(
        "bridge.our_room_connected call_id=%s remote_participants=%d",
        input.call_id,
        len(room.remote_participants),
    )

    # Check if agent already published before we registered the handler.
    if not track_future.done():
        for p in room.remote_participants.values():
            if getattr(p, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
                continue
            for pub in p.track_publications.values():
                if (
                    pub.track
                    and isinstance(pub.track, rtc.RemoteAudioTrack)
                    and pub.source == rtc.TrackSource.SOURCE_MICROPHONE
                ):
                    activity.logger.info(
                        "bridge.track_latched_from_preexisting from=%s",
                        getattr(p, "identity", "?"),
                    )
                    track_future.set_result(pub.track)
                    break
            if track_future.done():
                break

    # Publish bridge audio (source=MICROPHONE so agent's RoomIO accepts it)
    audio_source = rtc.AudioSource(ROOM_SAMPLE_RATE, ROOM_CHANNELS)
    local_track = rtc.LocalAudioTrack.create_audio_track("bridge-audio", audio_source)
    await room.local_participant.publish_track(
        local_track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    activity.logger.info("bridge.our_mic_published call_id=%s", input.call_id)

    activity.logger.info(
        "bridge.connector_connecting call_id=%s type=%s",
        input.call_id,
        input.connection_type,
    )
    await connector.connect()
    activity.logger.info("bridge.connector_connected call_id=%s", input.call_id)

    # --- Shared state ---
    last_audio_at = time.monotonic()
    bridge_latency_samples: deque[float] = deque(maxlen=200)
    _last_send_ts: float = 0.0
    _awaiting_response: bool = False
    _audioop_warned: bool = False

    # --- Audio tasks ---

    async def _send_silence():
        """Keep provider alive while waiting for agent's audio track."""
        is_lk = input.connection_type == "web_livekit_bridge"
        rate = ROOM_SAMPLE_RATE if is_lk else 16000
        frame = b"\x00" * int(rate * 0.02 * 2)
        while not track_future.done():
            await connector.send_audio(frame, rate)
            await asyncio.sleep(0.02)

    async def _room_to_provider():
        nonlocal last_audio_at, _last_send_ts, _awaiting_response
        activity.logger.info("bridge.r2p_started call_id=%s", input.call_id)
        silence = asyncio.create_task(_send_silence())
        try:
            track = await asyncio.wait_for(track_future, timeout=TRACK_TIMEOUT_S)
            activity.logger.info(
                "bridge.r2p_track_acquired call_id=%s",
                input.call_id,
            )
        except asyncio.TimeoutError:
            activity.logger.error(
                "bridge.r2p_track_timeout call_id=%s timeout_s=%d — no sim track ever appeared",
                input.call_id,
                TRACK_TIMEOUT_S,
            )
            raise RuntimeError(f"No audio track from agent after {TRACK_TIMEOUT_S}s")
        finally:
            silence.cancel()
            try:
                await silence
            except asyncio.CancelledError:
                pass

        first_rate_logged = False
        r2p_frame_count = 0

        # Buffer simulator frames while waiting for the customer agent
        # to join and publish a track. Once the agent's track is acquired,
        # it's also subscribed to our bridge-audio (auto_subscribe=True),
        # so the flush will be heard.
        buffered_frames: list[tuple[bytes, int]] = []
        buffer_cap_logged = False
        audio_stream = rtc.AudioStream(track)

        async for event in audio_stream:
            now = time.monotonic()
            last_audio_at = now
            _last_send_ts = now
            _awaiting_response = True
            if not first_rate_logged:
                activity.logger.info(
                    "bridge.r2p_first_frame call_id=%s rate=%d channels=%d samples=%d",
                    input.call_id,
                    event.frame.sample_rate,
                    event.frame.num_channels,
                    event.frame.samples_per_channel,
                )
                first_rate_logged = True
            frame_data = event.frame.data.tobytes()
            frame_rate = event.frame.sample_rate

            # Buffer until customer agent's track is acquired, capped at
            # R2P_BUFFER_MAX_SECONDS worth of frames to bound memory.
            # Frames beyond the cap are dropped silently by audio, but we
            # emit a one-shot warn log so operators can see it happened.
            if not connector.is_agent_ready:
                if len(buffered_frames) < R2P_BUFFER_MAX_FRAMES:
                    buffered_frames.append((frame_data, frame_rate))
                elif not buffer_cap_logged:
                    activity.logger.warning(
                        "bridge.r2p_buffer_cap_hit call_id=%s cap_frames=%d "
                        "cap_seconds=%d — dropping further frames until agent "
                        "joins",
                        input.call_id,
                        R2P_BUFFER_MAX_FRAMES,
                        R2P_BUFFER_MAX_SECONDS,
                    )
                    buffer_cap_logged = True
                r2p_frame_count += 1
                continue

            # Flush buffer once customer is ready.
            if buffered_frames:
                activity.logger.info(
                    "bridge.r2p_buffer_flush call_id=%s frames=%d",
                    input.call_id,
                    len(buffered_frames),
                )
                for buf_data, buf_rate in buffered_frames:
                    await connector.send_audio(buf_data, buf_rate)
                buffered_frames.clear()

            await connector.send_audio(frame_data, frame_rate)
            r2p_frame_count += 1

    async def _provider_to_room():
        nonlocal last_audio_at, _awaiting_response, _audioop_warned
        activity.logger.info("bridge.p2r_started call_id=%s", input.call_id)
        resample_state = None
        first_rate_logged = False
        p2r_frame_count = 0
        async for pcm, rate in connector.recv_audio():
            now = time.monotonic()
            last_audio_at = now
            p2r_frame_count += 1

            if not first_rate_logged:
                activity.logger.info(
                    "bridge.p2r_first_frame call_id=%s rate=%d bytes=%d",
                    input.call_id,
                    rate,
                    len(pcm),
                )
                first_rate_logged = True

            # Track bridge round-trip latency
            if _awaiting_response and _last_send_ts > 0:
                rtt = (now - _last_send_ts) * 1000
                if rtt < 500:
                    bridge_latency_samples.append(rtt)
                _awaiting_response = False

            # Resample if needed (Vapi sends 16kHz, room expects 48kHz)
            if rate != ROOM_SAMPLE_RATE:
                try:
                    import audioop

                    pcm, resample_state = audioop.ratecv(
                        pcm,
                        2,
                        ROOM_CHANNELS,
                        rate,
                        ROOM_SAMPLE_RATE,
                        resample_state,
                    )
                except ImportError:
                    if not _audioop_warned:
                        activity.logger.warning(
                            "audioop unavailable — skipping resample"
                        )
                        _audioop_warned = True

            t0 = time.monotonic()
            await audio_source.capture_frame(
                rtc.AudioFrame(
                    data=pcm,
                    sample_rate=ROOM_SAMPLE_RATE,
                    num_channels=ROOM_CHANNELS,
                    samples_per_channel=len(pcm) // 2,
                )
            )
            if p2r_frame_count % 500 == 0:
                capture_ms = (time.monotonic() - t0) * 1000
                activity.logger.info(
                    "bridge.p2r_capture_report call_id=%s p2r_frames=%d "
                    "last_capture_ms=%.1f",
                    input.call_id,
                    p2r_frame_count,
                    capture_ms,
                )

        activity.logger.warning(
            "bridge.p2r_provider_disconnected call_id=%s",
            input.call_id,
        )
        raise RuntimeError("Provider disconnected")

    async def _heartbeat():
        nonlocal last_audio_at
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            activity.heartbeat(f"room={input.room_name}")
            if time.monotonic() - last_audio_at > WATCHDOG_TIMEOUT_S:
                raise RuntimeError(f"No audio for {WATCHDOG_TIMEOUT_S}s")

    async def _max_duration():
        await asyncio.sleep(input.max_duration_seconds)

    async def _wait_for_room_disconnect():
        """Raise as soon as our LiveKit room is closed externally so the
        TaskGroup tears down (and the finally block calls
        ``connector.disconnect()``, which deletes the customer room)."""
        reason = await room_disconnected
        raise RuntimeError(f"OUR room disconnected: {reason}")

    # --- Run until any task completes or fails ---
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_room_to_provider())
            tg.create_task(_provider_to_room())
            tg.create_task(_heartbeat())
            tg.create_task(_max_duration())
            tg.create_task(_wait_for_room_disconnect())
    except* Exception:
        pass
    finally:
        # Always run both disconnects even if one raises — otherwise a
        # connector cleanup error would leave OUR room (and its
        # ``run_bridge`` activity heartbeat) hanging until max_duration.
        try:
            await connector.disconnect()
        finally:
            await room.disconnect()

    avg_latency = (
        round(sum(bridge_latency_samples) / len(bridge_latency_samples), 1)
        if bridge_latency_samples
        else 0.0
    )
    min_rtt = round(min(bridge_latency_samples), 1) if bridge_latency_samples else 0.0
    max_rtt = round(max(bridge_latency_samples), 1) if bridge_latency_samples else 0.0
    activity.logger.info(
        "bridge.final_latency call_id=%s avg_rtt_ms=%.1f min_rtt_ms=%.1f "
        "max_rtt_ms=%.1f samples=%d",
        input.call_id,
        avg_latency,
        min_rtt,
        max_rtt,
        len(bridge_latency_samples),
    )
    return RunBridgeOutput(
        success=True,
        room_name=input.room_name,
        bridge_latency_ms=avg_latency,
    )
