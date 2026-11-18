"""Vapi WebSocket connector for the WebRTC bridge.

Creates a Vapi web call via their API, opens a WebSocket, and streams
raw PCM audio bidirectionally.

Vapi WebSocket protocol:
- Send: binary frames (raw PCM s16le bytes)
- Receive: binary frames (audio) OR text frames (JSON control messages)
- Audio format: PCM s16le @ 16kHz mono
- Control messages: {"type": "hangup"}, {"type": "end-call"}, etc.
"""

import logging

import aiohttp

from ee.voice.services.livekit.bridge.audio_utils import resample_pcm
from ee.voice.services.livekit.bridge.connector import ProviderConnector

logger = logging.getLogger(__name__)

VAPI_SAMPLE_RATE = 16000
from django.conf import settings

VAPI_API_URL = settings.VAPI_API_URL


class VapiConnector(ProviderConnector):
    """Connects to a Vapi assistant via WebSocket transport."""

    def __init__(self, config: "ConnectorConfig"):
        self._api_key = config.api_key
        self._assistant_id = config.assistant_id
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._connected = False

    async def connect(self) -> None:
        """Create a Vapi web call and open the WebSocket connection."""
        self._session = aiohttp.ClientSession()

        # 1. Create call with WebSocket transport
        async with self._session.post(
            VAPI_API_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "assistantId": self._assistant_id,
                "transport": {
                    "provider": "vapi.websocket",
                    "audioFormat": {
                        "format": "pcm_s16le",
                        "container": "raw",
                        "sampleRate": VAPI_SAMPLE_RATE,
                    },
                },
            },
        ) as resp:
            if resp.status != 201:
                body = await resp.text()
                raise RuntimeError(f"Vapi call creation failed ({resp.status}): {body}")
            data = await resp.json()

        ws_url = data.get("transport", {}).get("websocketCallUrl")
        if not ws_url:
            raise RuntimeError(f"No websocketCallUrl in Vapi response: {data}")

        logger.info(
            "vapi_connector_call_created",
            extra={"call_id": data.get("id"), "ws_url": ws_url},
        )

        # 2. Open WebSocket
        self._ws = await self._session.ws_connect(ws_url)
        self._connected = True

        logger.info("vapi_connector_ws_connected")

    async def send_audio(self, data: bytes, sample_rate: int) -> None:
        """Send audio to Vapi. Downsamples from room rate (48kHz) to 16kHz."""
        if not self._ws or self._ws.closed:
            return
        if sample_rate != VAPI_SAMPLE_RATE:
            data = resample_pcm(data, sample_rate, VAPI_SAMPLE_RATE)
        await self._ws.send_bytes(data)

    async def recv_audio(self):
        """Yield audio frames from Vapi. Filters out JSON control messages."""
        if not self._ws:
            logger.warning("vapi_connector_recv_audio_no_ws")
            return
        if self._ws.closed:
            logger.warning("vapi_connector_recv_audio_ws_already_closed")
            return
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                yield (msg.data, VAPI_SAMPLE_RATE)
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

        self._connected = False

    async def disconnect(self) -> None:
        """Close WebSocket and HTTP session."""
        self._connected = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("vapi_connector_disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected
