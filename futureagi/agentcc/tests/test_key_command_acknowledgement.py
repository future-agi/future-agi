"""Strict committed-key transport contract. HTTP is mocked; no egress."""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.services.gateway_client import GatewayClient


@pytest.mark.parametrize("status_code, body, content", [
    (302, None, b""), (307, {"status": "revoked"}, b"fixture"),
    (200, None, b""), (200, [], b"fixture"), (200, {}, b"fixture"),
    (200, {"status": "revoked"}, b"fixture"),
    (200, {"status": "revoked", "scope": "replica"}, b"fixture"),
    (200, {"status": "revoked", "scope": "authoritative"}, b"fixture"),
    (204, None, b""), (500, {"status": "revoked"}, b"fixture"),
])
def test_redirect_empty_unknown_and_single_replica_ack_are_pending(status_code, body, content):
    response = MagicMock(status_code=status_code, content=content)
    response.json.return_value = body
    with patch("agentcc.services.gateway_client.httpx.Client") as constructor:
        transport = constructor.return_value.__enter__.return_value
        transport.request.return_value = response
        result = GatewayClient("http://fixture.invalid").revoke_key_confirmed("fixture-id")
    assert result is False
    assert constructor.call_args.kwargs["follow_redirects"] is False
    assert transport.request.call_args.kwargs["follow_redirects"] is False
    assert transport.request.call_args.args[:2] == (
        "DELETE", "http://fixture.invalid/-/keys/fixture-id"
    )
    transport.request.assert_called_once()


@pytest.mark.parametrize("point", ["construct", "request", "decode"])
def test_transport_or_parse_exception_is_pending(point):
    with patch("agentcc.services.gateway_client.httpx.Client") as constructor:
        transport = constructor.return_value.__enter__.return_value
        response = MagicMock(status_code=200, content=b"fixture")
        response.json.return_value = {"status": "revoked"}
        transport.request.return_value = response
        if point == "construct":
            constructor.side_effect = ValueError("synthetic private configuration")
        elif point == "request":
            transport.request.side_effect = RuntimeError("synthetic transport detail")
        else:
            response.json.side_effect = ValueError("synthetic invalid JSON")
        assert GatewayClient("http://fixture.invalid").revoke_key_confirmed("fixture-id") is False


@pytest.mark.parametrize("status_code, body, expected", [
    (200, {"received": 1, "loaded": 1}, True),
    (302, {"received": 1, "loaded": 1}, False),
    (201, {"received": 1, "loaded": 1}, False),
    (200, {"received": 1, "loaded": 0}, False),
    (200, {"received": True, "loaded": True}, False),
    (200, {}, False),
])
def test_hash_import_requires_the_expected_count_ack(status_code, body, expected):
    response = MagicMock(status_code=status_code, content=b"fixture")
    response.json.return_value = body
    key = {
        "id": "fixture-id", "name": "fixture", "owner": "fixture",
        "key_hash": "a" * 64, "key_prefix": "fixture", "models": [],
        "providers": [], "metadata": {"org_id": "fixture-org", "user_id": "fixture-user"},
        "expires_at": None,
    }
    with patch("agentcc.services.gateway_client.httpx.Client") as constructor:
        transport = constructor.return_value.__enter__.return_value
        transport.request.return_value = response
        assert GatewayClient("http://fixture.invalid").import_committed_key(key) is expected
    assert transport.request.call_args.kwargs["follow_redirects"] is False
