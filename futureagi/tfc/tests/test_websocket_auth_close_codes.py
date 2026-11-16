"""Socket handshakes separate refused credentials from unavailable authentication.

A refused credential closes with 4003, which the browser does not retry with
the same token. When the database or cache cannot be reached the credential
was never checked, so the socket closes with 1011 and the browser reconnects.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from asgiref.testing import ApplicationCommunicator
from django.db import InterfaceError, OperationalError
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.exceptions import AuthenticationFailed

from accounts.saml_credential import SamlCredentialInvalid
from tfc.middleware.jwt_auth import JWTAuthMiddleware

pytestmark = pytest.mark.django_db(transaction=True)

RETRYABLE = 1011
REFUSED = 4003


async def _inner(scope, receive, send):
    await send({"type": "websocket.accept"})


def handshake(query_string):
    async def run():
        app = ApplicationCommunicator(
            JWTAuthMiddleware(_inner),
            {
                "type": "websocket",
                "path": "/ws/connect/",
                "query_string": query_string,
            },
        )
        await app.send_input({"type": "websocket.connect"})
        events = [await app.receive_output(timeout=5)]
        if events[0]["type"] == "websocket.accept":
            if not await app.receive_nothing(timeout=0.2):
                events.append(await app.receive_output(timeout=5))
        await app.wait(timeout=5)
        return [
            (e["type"], e.get("code")) for e in events if e["type"] != "websocket.send"
        ]

    return asyncio.run(run())


@pytest.mark.parametrize(
    "failure",
    [
        OperationalError("synthetic database outage"),
        InterfaceError("synthetic closed connection"),
        RedisConnectionError("synthetic cache outage"),
    ],
    ids=["database", "connection", "cache"],
)
def test_unavailable_authentication_closes_with_a_retryable_code(failure):
    with patch("tfc.middleware.jwt_auth.decode_token", side_effect=failure):
        events = handshake(b"token=synthetic-token")
    assert events == [("websocket.accept", None), ("websocket.close", RETRYABLE)]


def test_unreachable_database_closes_with_a_retryable_code():
    unreachable = MagicMock()
    unreachable.cursor.side_effect = OperationalError("synthetic database outage")
    with (
        patch("tfc.middleware.jwt_auth.connection", unreachable),
        patch("tfc.middleware.jwt_auth.decode_token") as decode,
    ):
        events = handshake(b"token=synthetic-token")
    decode.assert_not_called()
    assert events == [("websocket.accept", None), ("websocket.close", RETRYABLE)]


@pytest.mark.parametrize(
    "refusal",
    [
        AuthenticationFailed("Invalid auth token"),
        SamlCredentialInvalid("token_inactive"),
    ],
    ids=["invalid-token", "saml-credential"],
)
def test_refused_credential_keeps_the_no_reconnect_code(refusal):
    with patch("tfc.middleware.jwt_auth.decode_token", side_effect=refusal):
        events = handshake(b"token=synthetic-token")
    assert events == [("websocket.accept", None), ("websocket.close", REFUSED)]


def test_missing_token_keeps_the_no_reconnect_code():
    with patch("tfc.middleware.jwt_auth.decode_token") as decode:
        events = handshake(b"")
    decode.assert_not_called()
    assert events == [("websocket.accept", None), ("websocket.close", REFUSED)]


def test_valid_token_reaches_the_consumer():
    user = MagicMock()
    with patch(
        "tfc.middleware.jwt_auth.decode_token",
        return_value=(user, "synthetic-token", None),
    ):
        events = handshake(b"token=synthetic-token")
    assert events == [("websocket.accept", None)]
