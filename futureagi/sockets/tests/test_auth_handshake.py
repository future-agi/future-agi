from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from saml2_auth.tests.ws_helpers import WebsocketCommunicator
from tfc.middleware.jwt_auth import JWTAuthMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/ws/connect/", "/ws/falcon-ai/"])
async def test_new_and_legacy_handshakes_deliver_the_same_scope(path, monkeypatch):
    user = object()
    identity = SimpleNamespace(token_id="id")
    scopes = []

    async def consumer(scope, receive, send):
        scopes.append(scope)
        assert (await receive())["type"] == "websocket.connect"
        await send({"type": "websocket.accept"})

    middleware = JWTAuthMiddleware(consumer)
    validate = AsyncMock(return_value=(user, identity, "padded=="))
    monkeypatch.setattr(middleware, "get_user_from_token", validate)
    for protocols, query, selected in (
        ([], "workspace_id=w&token=padded%3D%3D", None),
        (["fi.auth.v1", "bearer.padded%3D%3D"], "workspace_id=w", "fi.auth.v1"),
    ):
        socket = WebsocketCommunicator(
            middleware, f"{path}?{query}", subprotocols=protocols
        )
        assert await socket.connect() == (True, selected)
        await socket.wait()
        validate.assert_awaited_once_with("padded==")
        validate.reset_mock()
        assert scopes[-1]["user"] is user
        assert scopes[-1]["auth_scope"] is identity
        assert scopes[-1]["auth_token_id"] == "id"
        assert scopes[-1]["query_string"] == query.encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("offer", [[], ["bearer.invalid"]], ids=["missing", "rejected"])
async def test_rejected_handshake_selects_the_protocol_then_closes(offer, monkeypatch):
    inner = AsyncMock()
    middleware = JWTAuthMiddleware(inner)
    validate = AsyncMock(return_value=None)
    monkeypatch.setattr(middleware, "get_user_from_token", validate)
    socket = WebsocketCommunicator(
        middleware, "/ws/connect/?token=legacy", subprotocols=["fi.auth.v1", *offer]
    )

    assert await socket.connect() == (True, "fi.auth.v1")
    closed = await socket.receive_output()
    assert closed["type"] == "websocket.close" and closed["code"] == 4003
    await socket.wait()
    inner.assert_not_awaited()
    if offer:
        validate.assert_awaited_once_with("invalid")
    else:
        validate.assert_not_awaited()
