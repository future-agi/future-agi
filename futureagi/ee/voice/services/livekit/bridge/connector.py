"""Abstract base class for provider connectors and connector registry.

A ProviderConnector represents one side of the WebRTC bridge — the connection
to the customer's voice agent on their provider (Vapi, Retell, etc.).
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


class ProviderConnector(ABC):
    """Bidirectional audio connection to a customer's voice agent provider."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the provider (create call + open WebSocket).

        Raises on failure.
        """

    @abstractmethod
    async def send_audio(self, data: bytes, sample_rate: int) -> None:
        """Send audio bytes TO the provider (voice-simulator's voice).

        Implementations handle downsampling if the provider expects a different
        sample rate than the LiveKit room (48kHz).
        """

    @abstractmethod
    async def recv_audio(self) -> AsyncIterator[tuple[bytes, int]]:
        """Yield audio frames FROM the provider (customer agent's voice).

        Each item is ``(pcm_bytes, sample_rate)``.  Non-audio messages
        (JSON control, interrupts) are handled internally and not yielded.
        """
        yield b"", 0  # pragma: no cover — makes this a valid async generator

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close the provider connection."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """True while the provider connection is alive."""


@dataclass
class ConnectorConfig:
    """Typed configuration for building a provider connector."""

    api_key: str = ""
    assistant_id: str = ""
    livekit_url: str = ""
    livekit_api_secret: str = ""
    room_name: str = ""
    call_id: str = ""


def get_connector_registry() -> dict[str, type[ProviderConnector]]:
    """Lazily build and return the connector registry.

    ``web_livekit_bridge`` uses a bridge connector that pipes audio
    between the customer's LiveKit room and our room for guaranteed
    recording via our egress.
    """
    from ee.voice.services.livekit.bridge.livekit_connector import (
        LiveKitBridgeConnector,
    )
    from ee.voice.services.livekit.bridge.retell_connector import RetellConnector
    from ee.voice.services.livekit.bridge.vapi_connector import VapiConnector

    return {
        "web_vapi": VapiConnector,
        "web_retell": RetellConnector,
        "web_livekit_bridge": LiveKitBridgeConnector,
    }
