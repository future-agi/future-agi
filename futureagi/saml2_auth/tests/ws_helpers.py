"""ASGI websocket helpers that retain the production JWT middleware path."""

from __future__ import annotations

import sys
import types
from typing import Any

try:
    from channels.testing import WebsocketCommunicator
except ModuleNotFoundError as exc:
    if exc.name != "daphne":
        raise
    # Channels imports its optional live-server class from daphne before it
    # exposes WebsocketCommunicator.  The supplied backend venv intentionally
    # omits daphne; this tiny import shim is never used by the communicator,
    # which still drives the real ASGI application below.
    daphne = types.ModuleType("daphne")
    daphne_testing = types.ModuleType("daphne.testing")

    class DaphneProcess:  # pragma: no cover - import-only optional dependency
        pass

    daphne_testing.DaphneProcess = DaphneProcess
    sys.modules["daphne"] = daphne
    sys.modules["daphne.testing"] = daphne_testing
    from channels.testing import WebsocketCommunicator


async def graph_socket(token: str) -> WebsocketCommunicator:
    """Connect to the real graph route through ``tfc.asgi.application``."""

    from tfc.asgi import application

    communicator = WebsocketCommunicator(application, f"/ws/graphs/?token={token}")
    connected, detail = await communicator.connect()
    assert connected, f"graph websocket was rejected: {detail!r}"
    return communicator


async def receive_close_code(
    communicator: WebsocketCommunicator, *, timeout: float = 0.2
) -> int | None:
    """Return a close code, or ``None`` when the socket remains open."""

    try:
        message: dict[str, Any] = await communicator.receive_output(timeout=timeout)
    except TimeoutError:
        return None
    if message.get("type") == "websocket.close":
        return message.get("code")
    return None


async def no_outbound_frame(
    communicator: WebsocketCommunicator, *, timeout: float = 0.05
) -> bool:
    """Whether the real ASGI application has no message ready for the client."""

    return await communicator.receive_nothing(timeout=timeout)
