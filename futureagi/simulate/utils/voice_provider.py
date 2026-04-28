"""Resolve the system (simulator-side) voice provider for a call.

The simulator-side provider can differ from the customer agent's provider.
For LiveKit agents we always mirror so we stay native end-to-end and
avoid a cross-provider bridge — this is non-overridable. For everything
else, SYSTEM_VOICE_PROVIDER can pin the simulator's provider; default Vapi.
"""

from __future__ import annotations

import os


def resolve_system_voice_provider(client_provider: str | None) -> str:
    if client_provider in ("livekit", "livekit_bridge"):
        return "livekit"
    return os.getenv("SYSTEM_VOICE_PROVIDER", "vapi")
