"""Worker live updates do not require customer credentials."""

import hashlib
import hmac
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from tfc.views.socket import CallWebsocketView

ORG = "00000000-0000-0000-0000-000000000123"
ROUTE = "/call-websocket/"
AUDIENCE = "fa-websocket-relay-v1"


def signed_request(*, body=None, timestamp=None, audience=AUDIENCE, route=ROUTE):
    body = body or {"organization_id": ORG, "message": {"progress": 1}}
    raw = json.dumps(body, separators=(",", ":")).encode()
    timestamp = str(time.time() if timestamp is None else timestamp)
    nonce = uuid.uuid4().hex
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"fa-relay-v1"
    ).derive(settings.SECRET_KEY.encode())
    digest = hashlib.sha256(raw).hexdigest()
    text = "\n".join(
        ("POST", route, audience, timestamp, nonce, body["organization_id"], digest)
    )
    signature = hmac.new(key, text.encode(), hashlib.sha256).hexdigest()
    return raw, {
        "HTTP_X_RELAY_AUDIENCE": audience,
        "HTTP_X_RELAY_TIMESTAMP": timestamp,
        "HTTP_X_RELAY_NONCE": nonce,
        "HTTP_X_RELAY_ORGANIZATION": body["organization_id"],
        "HTTP_X_RELAY_SIGNATURE": signature,
    }


def invoke(raw, headers):
    from tfc.views.socket import CallWebsocketView

    request = APIRequestFactory().post(
        ROUTE, raw, content_type="application/json", **headers
    )
    return CallWebsocketView.as_view()(request)


@override_settings(CHANNEL_LAYER_BACKEND="memory")
def test_signed_relay_delivers_without_customer_keys():
    raw, headers = signed_request()
    with patch("tfc.views.socket.send_message_to_channel") as send:
        response = invoke(raw, headers)
    assert response.status_code == 200
    send.assert_called_once_with(ORG, {"progress": 1})


@override_settings(CHANNEL_LAYER_BACKEND="memory")
@pytest.mark.parametrize(
    "change", ["body", "expired", "audience", "route", "org", "signature", "future"]
)
def test_relay_refuses_changed_envelope(change):
    kwargs = {}
    if change == "expired":
        kwargs["timestamp"] = time.time() - 61
    if change == "future":
        kwargs["timestamp"] = time.time() + 1
    if change == "audience":
        kwargs["audience"] = "other-service"
    if change == "route":
        kwargs["route"] = "/elsewhere/"
    raw, headers = signed_request(**kwargs)
    if change == "body":
        raw = raw.replace(b'"progress":1', b'"progress":2')
    if change == "org":
        headers["HTTP_X_RELAY_ORGANIZATION"] = str(uuid.uuid4())
    if change == "signature":
        headers["HTTP_X_RELAY_SIGNATURE"] = "0" * 64
    with patch("tfc.views.socket.send_message_to_channel") as send:
        response = invoke(raw, headers)
    assert response.status_code == 401
    send.assert_not_called()


@override_settings(CHANNEL_LAYER_BACKEND="memory")
def test_relay_consumes_nonce_once_in_window():
    raw, headers = signed_request()
    with patch("tfc.views.socket.send_message_to_channel") as send:
        assert invoke(raw, headers).status_code == 200
        assert invoke(raw, headers).status_code == 401
    assert send.call_count == 1


@override_settings(CHANNEL_LAYER_BACKEND="memory")
def test_relay_consumes_nonce_atomically_across_threads():
    raw, headers = signed_request()
    with patch("tfc.views.socket.send_message_to_channel") as send:
        with ThreadPoolExecutor(max_workers=8) as executor:
            statuses = list(
                executor.map(lambda _: invoke(raw, headers).status_code, range(16))
            )
    assert statuses.count(200) == 1
    assert statuses.count(401) == 15
    assert send.call_count == 1


@override_settings(CHANNEL_LAYER_BACKEND="memory")
def test_relay_restart_refuses_prestart_envelope():
    from tfc import websocket_relay

    raw, headers = signed_request()
    with patch.object(
        websocket_relay,
        "_nonces",
        websocket_relay.ConsumedNonces(started_at=time.time() + 0.01),
    ):
        with patch("tfc.views.socket.send_message_to_channel") as send:
            assert invoke(raw, headers).status_code == 401
    send.assert_not_called()


@override_settings(CHANNEL_LAYER_BACKEND="memory")
def test_interactive_session_is_not_relay_authority():
    from types import SimpleNamespace
    from tfc.views.socket import CallWebsocketView

    request = APIRequestFactory().post(ROUTE, {"message": "hello"}, format="json")
    force_authenticate(
        request,
        user=SimpleNamespace(
            is_authenticated=True, organization=SimpleNamespace(id=ORG)
        ),
    )
    with patch("tfc.views.socket.send_message_to_channel") as send:
        assert CallWebsocketView.as_view()(request).status_code == 401
    send.assert_not_called()


@override_settings(CHANNEL_LAYER_BACKEND="memory")
def test_personal_key_headers_are_not_relay_authority():
    with patch("tfc.views.socket.send_message_to_channel") as send:
        response = invoke(
            b'{"message":"hello"}',
            {"HTTP_X_API_KEY": "fixture", "HTTP_X_SECRET_KEY": "fixture"},
        )
    assert response.status_code == 401
    send.assert_not_called()


@pytest.mark.parametrize("backend", ["redis", "rabbitmq"])
@pytest.mark.parametrize("stream", [False, True])
def test_temporal_sync_worker_publishes_directly(backend, stream):
    from model_hub.utils import call_websocket
    from tfc import channel_layers

    layer = type("Layer", (), {"group_send": AsyncMock()})()
    with override_settings(CHANNEL_LAYER_BACKEND=backend):
        with (
            patch.object(channel_layers, "_worker_layer", return_value=layer),
            patch("model_hub.utils.requests.post") as post,
        ):
            with ThreadPoolExecutor(max_workers=1) as worker:
                result = worker.submit(
                    call_websocket, ORG, {"progress": 2}, stream, "task"
                ).result(10)
    assert result["status"] is True
    expected = f"uuid_org_{ORG}_task" if stream else f"org_{ORG}"
    layer.group_send.assert_awaited_once_with(
        expected, {"type": "send_data", "data": {"progress": 2}}
    )
    post.assert_not_called()


@pytest.mark.parametrize("backend", ["redis", "rabbitmq"])
def test_real_broker_delivers_sync_worker_message_without_database(backend):
    import asyncio
    from channels.layers import ChannelLayerManager
    from model_hub.utils import call_websocket
    from tfc import channel_layers

    if backend == "redis":
        layer_config = {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": ["redis://localhost:16379/0"],
                "prefix": "personal-key-relay-test",
            },
        }
    else:
        layer_config = {
            "BACKEND": "channels_rabbitmq.core.RabbitmqChannelLayer",
            "CONFIG": {
                "host": "amqp://guest:guest@localhost:15672/%2F",
                "ssl_context": None,
            },
        }

    async def exchange():
        receiver = ChannelLayerManager()["default"]
        channel = await receiver.new_channel()
        group = f"uuid_org_{ORG}_{uuid.uuid4().hex}"
        await receiver.group_add(group, channel)
        # This deliberately runs in a plain sync thread as a Temporal activity
        # would. No Django DB fixture is enabled: any key lookup fails the test.
        with patch("model_hub.utils.requests.post") as post:
            result = await asyncio.to_thread(
                call_websocket, ORG, {"progress": 42}, True, group.split("_")[-1]
            )
            assert result["status"] is True
            event = await asyncio.wait_for(receiver.receive(channel), timeout=10)
        post.assert_not_called()
        assert event == {"type": "send_data", "data": {"progress": 42}}
        await receiver.group_discard(group, channel)
        if hasattr(receiver, "close_pools"):
            await receiver.close_pools()

    # Each backend has an independent worker-owned persistent loop/layer.
    with override_settings(
        CHANNEL_LAYER_BACKEND=backend, CHANNEL_LAYERS={"default": layer_config}
    ):

        async def bounded_exchange():
            return await asyncio.wait_for(exchange(), timeout=30)

        with patch.object(channel_layers, "_worker_state", None):
            asyncio.run(bounded_exchange())


@override_settings(
    CHANNEL_LAYER_BACKEND="memory", WEBSOCKET_ENDPOINT="http://backend/call-websocket/"
)
def test_memory_worker_signs_payload_without_api_key_lookup():
    from model_hub.utils import call_websocket

    with patch("model_hub.utils.requests.post") as post:
        post.return_value.json.return_value = {"status": True}
        assert call_websocket(ORG, {"progress": 3}) == {"status": True}
    kwargs = post.call_args.kwargs
    assert "X-Relay-Signature" in kwargs["headers"]
    assert "X-Api-Key" not in kwargs["headers"]
    assert json.loads(kwargs["data"])["organization_id"] == ORG
    assert kwargs["allow_redirects"] is False
