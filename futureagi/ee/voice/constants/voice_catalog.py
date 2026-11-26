"""Provider-agnostic voice resolution with adapter pattern.

Each TTS provider has an adapter that resolves human-readable voice names
(e.g. "marissa") to provider-specific IDs (e.g. a Cartesia UUID). The
Cartesia adapter prefetches the entire voice catalog on first use and
caches it in-memory so subsequent resolve() calls are instant.
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod

import requests
import structlog

from ee.voice.constants.tts_provider import TTSProvider

logger = structlog.get_logger(__name__)

_DEFAULT_TTS_PROVIDER = TTSProvider(
    os.environ.get("TTS_PROVIDER", TTSProvider.CARTESIA)
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _looks_like_provider_id(value: str) -> bool:
    """Return True if *value* already looks like a provider-specific voice ID."""
    return bool(_UUID_RE.match(value))


class VoiceAdapter(ABC):
    """Adapter interface for resolving voice names to provider-specific IDs."""

    @abstractmethod
    def prefetch(self) -> None:
        """Prefetch voice catalog from provider API. Called once at startup."""
        ...

    @abstractmethod
    def resolve(self, voice_name: str, voice_descriptor: dict | None = None) -> str:
        """Return a provider-specific voice identifier (UUID, name, etc.).

        Must be fast — should use prefetched data, not make API calls.
        """
        ...


class CartesiaVoiceAdapter(VoiceAdapter):
    """Resolves voice names to Cartesia voice IDs via prefetched catalog."""

    # Fallback static mapping for when API is unavailable
    FALLBACK_VOICE_IDS: dict[str, str] = {
        "steve": "9fb269e7-70fe-4cbe-aa3f-28bdb67e3e84",
        "sarah": "694f9389-aac1-45b6-b726-9d9369183238",
        "paul": "3e1ed423-17e5-4773-b87c-25b031106e41",
        "mark": "5619d38c-cf51-4d8e-9575-48f61a280413",
        "burt": "56df0456-8f47-4f7a-ac26-40c2f9797104",
        "phillip": "55e2a153-c61e-4784-85c8-e954cb22fe29",
        "joseph": "2948c301-9211-4112-8f36-4c3fc836ef12",
        "ryan": "41f3c367-e0a8-4a85-89e0-c27bae9c9b6d",
        "drew": "374b80da-e622-4dfc-90f6-1eeb13d331c9",
        "mrb": "87748186-23bb-4158-a1eb-332911b0b708",
        "marissa": "f0377496-2708-4cc9-b2f8-1b7fdb5e1a2a",
        "andrea": "e4d5f4c4-6601-4779-bee1-b3c14d629dc6",
        "myra": "6adbb439-0865-468c-9e68-adbb0eb2e71c",
        "paula": "489b647b-5662-408f-8c95-82e26ef8d29e",
        "matilda": "f80e7298-93f5-46d0-86f2-b8f29cfc88bd",
    }
    DEFAULT = "f786b574-daa5-4673-aa0c-cbe3e8534c02"

    def __init__(self) -> None:
        self._catalog: dict[str, str] = {}  # name.lower() -> voice_id
        self._prefetched = False

    def prefetch(self) -> None:
        """Fetch all voices from Cartesia API and build in-memory index."""
        try:
            api_key = os.environ.get("CARTESIA_API_KEY")
            if not api_key:
                raise ValueError("CARTESIA_API_KEY not set")
            resp = requests.get(
                "https://api.cartesia.ai/voices",
                headers={
                    "X-API-Key": api_key,
                    "Cartesia-Version": "2024-06-10",
                },
                timeout=15,
            )
            resp.raise_for_status()
            for voice in resp.json():
                name = voice.get("name", "").lower()
                if name:
                    self._catalog[name] = voice["id"]
            self._prefetched = True
            logger.info("cartesia_voice_catalog_prefetched", count=len(self._catalog))
        except Exception:
            logger.warning("cartesia_voice_catalog_prefetch_failed", exc_info=True)
            self._catalog = dict(self.FALLBACK_VOICE_IDS)
            self._prefetched = True

    def resolve(self, voice_name: str, voice_descriptor: dict | None = None) -> str:
        if not self._prefetched:
            self.prefetch()

        # If already a provider-specific ID (UUID), pass through
        if _looks_like_provider_id(voice_name):
            return voice_name

        # In-memory lookup — zero network latency
        resolved = self._catalog.get(voice_name.lower())
        if resolved:
            return resolved

        return self.FALLBACK_VOICE_IDS.get(voice_name.lower(), self.DEFAULT)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ADAPTERS: dict[TTSProvider, VoiceAdapter] = {
    TTSProvider.CARTESIA: CartesiaVoiceAdapter(),
}


def resolve_voice_id(
    voice_name: str,
    provider: TTSProvider = _DEFAULT_TTS_PROVIDER,
    voice_descriptor: dict | None = None,
) -> str:
    """Resolve a human-readable voice name to a provider-specific ID."""
    adapter = _ADAPTERS.get(provider)
    if not adapter:
        raise ValueError(f"Unsupported TTS provider: {provider}")
    return adapter.resolve(voice_name, voice_descriptor)


def prefetch_voice_catalog(provider: TTSProvider = _DEFAULT_TTS_PROVIDER) -> None:
    """Call at app startup to warm the voice catalog cache."""
    adapter = _ADAPTERS.get(provider)
    if adapter:
        adapter.prefetch()
