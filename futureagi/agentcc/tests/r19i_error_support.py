"""Synthetic failures for R19i; never touch a database or a transport."""

import json
import socket
from unittest.mock import patch

SENTINEL = (
    "r19i-db.internal:5432 SELECT token FROM keys https://host.invalid/?key=r19i-secret"
)


def block_network(stack):
    def refused(*args, **kwargs):
        raise AssertionError("R19i tests must not use network or DNS")

    for name in ("create_connection", "getaddrinfo"):
        stack.enter_context(patch.object(socket, name, side_effect=refused))
    for name in ("connect", "connect_ex"):
        stack.enter_context(patch.object(socket.socket, name, side_effect=refused))


def assert_private(response, status):
    body = json.dumps(response.data, default=str)
    assert SENTINEL not in body
    for fragment in ("r19i-db.internal", "SELECT token", "r19i-secret"):
        assert fragment not in body
    assert response.status_code == status, response.data
    return body


def assert_unexpected(response, logger, failure):
    body = assert_private(response, 500)
    assert "Something went wrong. Reference: agentcc_internal_error" in body
    assert response.data["code"] == "agentcc_internal_error"
    logger.exception.assert_called_once()
    assert logger.exception.call_args.kwargs["exc_info"][1] is failure
