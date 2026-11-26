"""Retell LiveKit connector for the WebRTC bridge.

Retell uses LiveKit under the hood for their web calls.  The access_token
from their API is a LiveKit JWT, so we join their LiveKit room directly
and exchange audio via WebRTC tracks — no raw WebSocket needed.

Flow:
  1. POST /v2/create-web-call with agent_id → access_token + call_id
  2. Connect to Retell's LiveKit cloud with the token
  3. Subscribe to agent's audio track, publish local audio track
  4. Audio flows bidirectionally via LiveKit
"""

import asyncio
import logging

import aiohttp
from livekit import rtc

from ee.voice.services.livekit.bridge.connector import ProviderConnector

logger = logging.getLogger(__name__)

from django.conf import settings

RETELL_API_URL = settings.RETELL_API_URL
RETELL_LIVEKIT_URL = settings.RETELL_LIVEKIT_URL


class RetellConnector(ProviderConnector):
    """Connects to a Retell agent via their LiveKit-based web call."""

    def __init__(self, config: "ConnectorConfig"):
        self._api_key = config.api_key
        self._agent_id = config.assistant_id  # Retell calls it agent_id
        self._room: rtc.Room | None = None
        self._audio_source: rtc.AudioSource | None = None
        self._remote_track_future: asyncio.Future[rtc.RemoteAudioTrack] | None = None
        self._connected = False

    async def connect(self) -> None:
        """Create a Retell web call and join their LiveKit room."""
        # 1. Create web call via Retell API
        async with aiohttp.ClientSession() as session:
            async with session.post(
                RETELL_API_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={"agent_id": self._agent_id},
            ) as resp:
                if resp.status != 201:
                    body = await resp.text()
                    raise RuntimeError(
                        f"Retell web call creation failed ({resp.status}): {body}"
                    )
                data = await resp.json()

        access_token = data.get("access_token")
        call_id = data.get("call_id")
        if not access_token:
            raise RuntimeError(f"No access_token in Retell response: {data}")

        logger.info(
            "retell_connector_call_created",
            extra={"call_id": call_id},
        )

        # 2. Join Retell's LiveKit room
        self._room = rtc.Room()
        self._remote_track_future = asyncio.get_running_loop().create_future()

        self._agent_disconnected = asyncio.Event()

        @self._room.on("track_subscribed")
        def _on_track(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ):
            if (
                isinstance(track, rtc.RemoteAudioTrack)
                and self._remote_track_future
                and not self._remote_track_future.done()
            ):
                logger.info(
                    "retell_connector_agent_track_subscribed",
                    extra={"participant": participant.identity},
                )
                self._remote_track_future.set_result(track)

        @self._room.on("participant_disconnected")
        def _on_disconnect(participant: rtc.RemoteParticipant):
            logger.info(
                "retell_connector_agent_disconnected",
                extra={"participant": participant.identity},
            )
            self._agent_disconnected.set()

        @self._room.on("disconnected")
        def _on_room_disconnect():
            logger.info("retell_connector_room_disconnected")
            self._agent_disconnected.set()

        await self._room.connect(RETELL_LIVEKIT_URL, access_token)
        logger.info("retell_connector_room_connected")

        # 3. Publish local audio source for sending audio to Retell
        self._audio_source = rtc.AudioSource(48000, 1)
        local_track = rtc.LocalAudioTrack.create_audio_track(
            "bridge-audio", self._audio_source
        )
        pub_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self._room.local_participant.publish_track(local_track, pub_options)

        self._connected = True
        logger.info("retell_connector_ready")

    async def send_audio(self, data: bytes, sample_rate: int) -> None:
        """Send audio to Retell by capturing frames into the AudioSource.

        The AudioSource is created at 48kHz.  The bridge sends resampled
        48kHz frames, so sample_rate should match.  If not, we skip the
        frame to avoid LiveKit's InvalidState error.
        """
        if not self._audio_source or not self._connected:
            return
        if sample_rate != 48000:
            return  # Skip mismatched frames
        frame = rtc.AudioFrame(
            data=data,
            sample_rate=48000,
            num_channels=1,
            samples_per_channel=len(data) // 2,
        )
        await self._audio_source.capture_frame(frame)

    async def recv_audio(self):
        """Yield audio frames from Retell's agent.

        Stops when the agent disconnects from Retell's room, which makes
        the bridge clean up and the phone-user leave the simulator room,
        triggering the agent worker's disconnect handler.
        """
        if not self._remote_track_future:
            return

        # Wait for the agent's audio track
        try:
            remote_track = await asyncio.wait_for(
                self._remote_track_future, timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.error("retell_connector_no_agent_track")
            self._agent_disconnected.set()
            self._connected = False
            raise RuntimeError(
                "Retell agent did not publish an audio track within 30 seconds. "
                "Check that the Retell call was created successfully."
            )

        async for event in rtc.AudioStream(remote_track):
            if self._agent_disconnected.is_set():
                break
            frame = event.frame
            yield (frame.data.tobytes(), frame.sample_rate)

        self._connected = False

    async def disconnect(self) -> None:
        """Disconnect from Retell's LiveKit room."""
        self._connected = False
        if self._room:
            await self._room.disconnect()
        logger.info("retell_connector_disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected
