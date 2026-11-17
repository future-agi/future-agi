"""Delegated batch identity is signed, never taken from caller query strings."""

import inspect
import time
from unittest.mock import patch

import httpx
import pytest

from agentcc.services.delegated_calls import Delegation
from agentcc.services.gateway_client import GatewayClient
from tfc.envelope_nonces import MemoryNonceStore
from tfc.service_envelope import verify_request
from tfc.tests.test_service_envelope import VECTORS


@pytest.mark.parametrize("operation", ["list", "get", "cancel"])
@pytest.mark.parametrize(
    "query",
    [
        {"org_id": "foreign", "key_id": "foreign", "batch_id": "foreign"},
        {"params": {"org_id": "foreign", "unknown": "value"}, "limit": 1},
    ],
)
def test_delegated_batch_clients_omit_all_caller_queries(operation, query):
    vector = next(v for v in VECTORS["valid_macs"] if v["name"] == "batch")
    delegation = Delegation(vector["token"], vector["claims"])
    secret = VECTORS["test_only_pair_secret"]
    client = GatewayClient("http://127.0.0.1:9", admin_token=secret)
    name = {
        "list": "list_delegated_batches",
        "get": "get_delegated_batch",
        "cancel": "cancel_delegated_batch",
    }[operation]
    assert hasattr(client, name), "Delegated batch operation is missing"
    method = getattr(client, name)
    assert "delegation" in inspect.signature(method).parameters
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={"batches": []} if operation == "list" else {"status": "processing"},
        )

    real_client = httpx.Client

    def in_memory(**kwargs):
        return real_client(
            transport=httpx.MockTransport(handler), trust_env=False, **kwargs
        )

    with patch(
        "agentcc.services.gateway_client.httpx.Client", side_effect=in_memory
    ) as transport:
        args = (
            [delegation] if operation == "list" else [delegation, "batch-abcdef012345"]
        )
        method(*args, **query)
    transport.assert_called_once()
    assert len(seen) == 1
    request = seen[0]
    assert request.url.query == b""
    assert request.content == b""
    assert request.method == ("POST" if operation == "cancel" else "GET")
    assert (
        request.url.path
        == {
            "list": "/-/batches",
            "get": "/-/batches/batch-abcdef012345",
            "cancel": "/-/batches/batch-abcdef012345/cancel",
        }[operation]
    )
    assert request.headers["x-fa-grant"] == delegation.token
    claims = verify_request(
        request.headers["x-fa-envelope"],
        keys={("fa", "gw-delegated", "k1"): secret.encode()},
        audience="gw-delegated",
        method=request.method,
        route=request.url.path,
        body=request.content,
        grant=delegation.token,
        nonces=MemoryNonceStore(epoch=int(time.time()) - 10),
    )
    assert claims["org"] == delegation.claims["org"]
    assert claims["user"] == delegation.claims["user"]
    assert claims["scope"] == "batch"
