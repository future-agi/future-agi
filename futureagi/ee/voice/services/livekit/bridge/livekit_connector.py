"""LiveKit-to-LiveKit connector for the WebRTC bridge.

Connects to a customer's LiveKit server, dispatches their agent,
and exchanges audio via WebRTC tracks — same pattern as RetellConnector
since Retell also uses LiveKit under the hood.

Flow:
  1. Create room on customer's LiveKit server
  2. Dispatch customer's agent into the room
  3. Join the room as a participant, publish + subscribe audio tracks
  4. Audio flows bidirectionally via LiveKit WebRTC
"""

import asyncio
import json
import logging

from livekit import rtc
from livekit.api import (
    AccessToken,
    CreateAgentDispatchRequest,
    CreateRoomRequest,
    DeleteRoomRequest,
    LiveKitAPI,
    VideoGrants,
)

from ee.voice.services.livekit.bridge.audio_utils import resample_pcm
from ee.voice.services.livekit.bridge.connector import ProviderConnector

logger = logging.getLogger(__name__)


class LiveKitBridgeConnector(ProviderConnector):
    """Connects to a customer's LiveKit agent via their LiveKit server."""

    def __init__(self, config: "ConnectorConfig"):
        self._api_key = config.api_key
        self._api_secret = config.livekit_api_secret
        self._agent_name = config.assistant_id
        self._livekit_url = config.livekit_url
        self._room_name = config.room_name
        self._call_id = config.call_id
        self._room: rtc.Room | None = None
        self._audio_source: rtc.AudioSource | None = None
        self._remote_track_future: asyncio.Future[rtc.RemoteAudioTrack] | None = None
        self._connected = False
        self._agent_disconnected = asyncio.Event()
        self._agent_track_acquired = asyncio.Event()
        self._lkapi: LiveKitAPI | None = None
        self._sample_rate_warned = False

    @property
    def is_agent_ready(self) -> bool:
        """True once the customer agent has joined and published a track."""
        return self._agent_track_acquired.is_set()

    def _http_url(self) -> str:
        """Convert ws(s):// to http(s):// for REST API calls."""
        url = self._livekit_url
        if url.startswith("wss://"):
            return "https://" + url[len("wss://") :]
        if url.startswith("ws://"):
            return "http://" + url[len("ws://") :]
        return url

    async def connect(self) -> None:
        """Create room, dispatch agent, and join the customer's LiveKit room."""
        logger.info(
            "lk_connector.connect_start call_id=%s url=%s room=%s agent_name=%s",
            self._call_id,
            self._livekit_url,
            self._room_name,
            self._agent_name,
        )
        # 1. Validate credentials by creating room on customer's server
        self._lkapi = LiveKitAPI(
            url=self._http_url(),
            api_key=self._api_key,
            api_secret=self._api_secret,
        )
        try:
            room_request = CreateRoomRequest(
                name=self._room_name,
                empty_timeout=120,
            )
            await self._lkapi.room.create_room(room_request)
            logger.info("lk_connector.room_created room=%s", self._room_name)
        except Exception as e:
            logger.error(
                "lk_connector.room_create_failed room=%s err=%s",
                self._room_name,
                e,
            )
            try:
                await self._lkapi.aclose()
            except Exception:
                pass
            self._lkapi = None
            raise

        # 2. Dispatch customer's agent
        dispatch_meta = json.dumps(
            {
                "call_id": self._call_id,
            }
        )
        try:
            dispatch = await self._lkapi.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(
                    agent_name=self._agent_name,
                    room=self._room_name,
                    metadata=dispatch_meta,
                )
            )
            logger.info(
                "lk_connector.agent_dispatched agent=%s room=%s dispatch_id=%s",
                self._agent_name,
                self._room_name,
                getattr(dispatch, "id", "?"),
            )
        except Exception as e:
            logger.error(
                "lk_connector.agent_dispatch_failed agent=%s err=%s",
                self._agent_name,
                e,
            )
            raise RuntimeError(
                f"Failed to dispatch agent '{self._agent_name}' — "
                f"make sure an agent worker with this name is running "
                f"on {self._livekit_url}. Error: {e}"
            ) from e

        # 3. Generate token and join room
        token = AccessToken(self._api_key, self._api_secret)
        token.with_identity("fagi-bridge")
        token.with_name("FutureAGI Bridge")
        token.with_grants(
            VideoGrants(
                room_join=True,
                room=self._room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )

        self._room = rtc.Room()
        self._remote_track_future = asyncio.get_running_loop().create_future()

        @self._room.on("track_subscribed")
        def _on_track(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ):
            if not isinstance(track, rtc.RemoteAudioTrack):
                return
            if not self._remote_track_future or self._remote_track_future.done():
                return
            # LiveKit agents can publish multiple audio tracks back-to-back:
            # a SOURCE_UNKNOWN pre-roll / diagnostic track, then the real
            # SOURCE_MICROPHONE TTS track ~150ms later. Latching onto the
            # first one (SOURCE_UNKNOWN) gives us silence for the whole
            # call. Only accept MICROPHONE tracks.
            if publication.source != rtc.TrackSource.SOURCE_MICROPHONE:
                logger.info(
                    "lk_connector.track_skipped from=%s source=%s reason=not_microphone",
                    getattr(participant, "identity", "?"),
                    publication.source,
                )
                return
            logger.info(
                "lk_connector.track_latched from=%s",
                getattr(participant, "identity", "?"),
            )
            self._remote_track_future.set_result(track)
            self._agent_track_acquired.set()

        @self._room.on("participant_disconnected")
        def _on_disconnect(participant: rtc.RemoteParticipant):
            logger.info(
                "lk_connector.participant_disconnected identity=%s",
                getattr(participant, "identity", "?"),
            )
            self._agent_disconnected.set()

        @self._room.on("disconnected")
        def _on_room_disconnect():
            logger.info("lk_connector.room_disconnected room=%s", self._room_name)
            self._agent_disconnected.set()

        await self._room.connect(self._livekit_url, token.to_jwt())
        logger.info(
            "lk_connector.joined_room room=%s remote_participants=%d",
            self._room_name,
            len(self._room.remote_participants),
        )

        # 4. Publish local audio source for bridge audio
        self._audio_source = rtc.AudioSource(48000, 1)
        local_track = rtc.LocalAudioTrack.create_audio_track(
            "bridge-audio", self._audio_source
        )
        pub_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self._room.local_participant.publish_track(local_track, pub_options)
        logger.info("lk_connector.local_mic_published room=%s", self._room_name)

        self._connected = True

    async def send_audio(self, data: bytes, sample_rate: int) -> None:
        """Send audio to customer's room by capturing frames into AudioSource.

        The ``rtc.AudioSource`` was created at 48kHz; any incoming audio at
        a different rate (e.g. 24kHz from some TTS plugins) must be
        resampled before capture. Dropping frames here leaves the customer
        room silent and the customer agent's VAD never triggers.
        """
        if not self._audio_source or not self._connected:
            return
        if sample_rate != 48000:
            try:
                data = resample_pcm(data, sample_rate, 48000, channels=1)
            except RuntimeError:
                if not self._sample_rate_warned:
                    logger.warning(
                        "lk_connector.resample_unavailable %d->48000, dropping",
                        sample_rate,
                    )
                    self._sample_rate_warned = True
                return
        frame = rtc.AudioFrame(
            data=data,
            sample_rate=48000,
            num_channels=1,
            samples_per_channel=len(data) // 2,
        )
        await self._audio_source.capture_frame(frame)

    async def recv_audio(self):
        """Yield audio frames from the customer's agent.

        Stops when the agent disconnects, triggering bridge cleanup.
        """
        if not self._remote_track_future:
            logger.error("lk_connector.recv_audio_no_future")
            return

        logger.info(
            "lk_connector.recv_audio_waiting_for_track room=%s timeout_s=30",
            self._room_name,
        )
        # Wait for the agent's audio track
        try:
            remote_track = await asyncio.wait_for(
                self._remote_track_future, timeout=30.0
            )
            logger.info(
                "lk_connector.recv_audio_track_acquired room=%s", self._room_name
            )
        except asyncio.TimeoutError:
            logger.error(
                "lk_connector.recv_audio_track_timeout agent=%s room=%s url=%s",
                self._agent_name,
                self._room_name,
                self._livekit_url,
            )
            self._agent_disconnected.set()
            self._connected = False
            raise RuntimeError(
                f"Customer's agent '{self._agent_name}' did not join the room "
                f"within 30 seconds."
            )

        first_frame = True
        async for event in rtc.AudioStream(remote_track):
            if self._agent_disconnected.is_set():
                logger.info(
                    "lk_connector.recv_audio_stopping reason=agent_disconnected"
                )
                break
            frame = event.frame
            if first_frame:
                logger.info(
                    "lk_connector.recv_audio_first_frame rate=%d channels=%d samples=%d",
                    frame.sample_rate,
                    frame.num_channels,
                    frame.samples_per_channel,
                )
                first_frame = False
            yield (frame.data.tobytes(), frame.sample_rate)
        self._connected = False

    async def disconnect(self) -> None:
        """Disconnect from customer's room and clean up."""
        self._connected = False
        if self._room:
            await self._room.disconnect()
        # Delete the room on customer's server (stops their agent)
        if self._lkapi:
            try:
                await self._lkapi.room.delete_room(
                    DeleteRoomRequest(room=self._room_name)
                )
                logger.info(
                    "livekit_connector_room_deleted",
                    extra={"room": self._room_name},
                )
            except Exception as e:
                logger.warning(
                    "livekit_connector_room_delete_failed",
                    extra={"room": self._room_name, "error": str(e)},
                )
            try:
                await self._lkapi.aclose()
            except Exception:
                pass

    @property
    def is_connected(self) -> bool:
        return self._connected
