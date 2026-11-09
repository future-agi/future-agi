"""Exercise worker payloads through validation, routing and socket delivery."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import requests
from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.layers import get_channel_layer
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import OrgApiKey
from model_hub.utils import call_websocket
from sockets.consumer import DataConsumer
from tfc.views.socket import CallWebsocketView

ORG = "00000000-0000-4000-8000-000000000001"
OTHER_ORG = "00000000-0000-4000-8000-000000000002"
TARGET = "00000000-0000-4000-8000-000000000003"

# Wire shapes from usage_entries.WebSocketMessage, llm.py, prompt_generate.py
# and websocket_manager.py. Values are deliberately synthetic.
MESSAGES = [
    pytest.param(
        {
            "alert_title": "Evaluation limit reached",
            "alert_description": "Try again later",
            "subscription_title": "Usage allowance",
            "subscription_description": "Review your plan",
        },
        False,
        id="usage-rate-resource-and-credit-notification",
    ),
    pytest.param(
        {
            "type": "run_prompt",
            "uuid": TARGET,
            "chunk": "hello",
            "chunk_pos": "0",
            "streaming_status": "running",
            "current_activity": None,
        },
        True,
        id="llm-stream-chunk",
    ),
    pytest.param(
        {
            "type": "generate_prompt",
            "generation_id": TARGET,
            "current_activity": "generate_initial_prompt",
            "status": "completed",
            "prompt": "Synthetic prompt",
        },
        True,
        id="prompt-generation",
    ),
    pytest.param(
        {
            "type": "run_prompt",
            "template_id": TARGET,
            "session_uuid": TARGET,
            "version": "1",
            "streaming_status": "started",
            "output_format": {"type": "json", "fields": ["answer"]},
        },
        True,
        id="template-streaming",
    ),
    pytest.param(
        {
            "type": "billing_banner",
            "severity": "warning",
            "message": "Synthetic credit notice",
            "action": {"label": "Review usage", "url": "/usage"},
            "dismissible": True,
        },
        False,
        id="billing-banner",
    ),
]


@pytest.fixture(autouse=True)
def relay_settings():
    with override_settings(
        CLOUD_DEPLOYMENT="",
        WEBSOCKET_ENDPOINT="http://localhost:8000/call-websocket/",
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    ):
        yield


def post_relay(payload):
    request = APIRequestFactory().post("/call-websocket/", payload, format="json")
    # The selected authenticated organization wins over both the user default
    # and any untrusted organization_id in the body.
    request.organization = SimpleNamespace(id=ORG)
    force_authenticate(
        request,
        user=SimpleNamespace(
            is_authenticated=True, organization=SimpleNamespace(id=OTHER_ORG)
        ),
    )
    return CallWebsocketView.as_view()(request)


def relay_http(url, *, json, **kwargs):
    response = post_relay(json)
    response.render()
    http = requests.Response()
    http.status_code = response.status_code
    http._content = response.content
    return http


async def connect_socket(org_id):
    socket = ApplicationCommunicator(
        DataConsumer.as_asgi(),
        {
            "type": "websocket",
            "path": "/ws/connect/",
            "user": SimpleNamespace(id=org_id, organization_id=org_id),
        },
    )
    await socket.send_input({"type": "websocket.connect"})
    assert (await socket.receive_output())["type"] == "websocket.accept"
    return socket


async def receive_message(socket):
    event = await socket.receive_output()
    assert event["type"] == "websocket.send"
    return json.loads(event["text"])


@pytest.mark.parametrize("message,targeted", MESSAGES)
def test_helper_messages_reach_only_authenticated_organization(message, targeted):
    async def resolve_org(scope, user):
        return user.organization_id

    async def run():
        # No auth_scope: cover ordinary non-SAML sessions, not just scoped ones.
        own = await connect_socket(ORG)
        other = await connect_socket(OTHER_ORG)
        try:
            for socket in (own, other):
                await socket.send_input(
                    {
                        "type": "websocket.receive",
                        "text": json.dumps({"type": "subscribe", "uuid": TARGET}),
                    }
                )
                assert (await receive_message(socket))[
                    "type"
                ] == "subscription_confirmed"
            with (
                patch("model_hub.utils.OrgApiKey.no_workspace_objects") as keys,
                patch("model_hub.utils.requests.post", side_effect=relay_http) as post,
            ):
                keys.get.return_value = SimpleNamespace(
                    api_key="synthetic-key", secret_key="synthetic-secret"
                )
                result = await sync_to_async(call_websocket)(
                    ORG,
                    message,
                    send_to_uuid=targeted,
                    uuid=TARGET if targeted else None,
                )
            assert result["status"] is True, result
            payload = post.call_args.kwargs["json"]
            assert "organization_id" not in payload
            if not targeted:
                assert "uuid" not in payload
            assert await receive_message(own) == message
            assert await other.receive_nothing(timeout=0.1)
        finally:
            for socket in (own, other):
                await socket.send_input({"type": "websocket.disconnect", "code": 1000})
                await socket.wait()

    with patch("sockets.consumer.resolve_socket_organization", side_effect=resolve_org):
        asyncio.run(run())


@pytest.mark.parametrize(
    "extra", [{}, {"uuid": None}, {"send_to_uuid": False, "uuid": None}]
)
def test_broadcast_accepts_missing_and_legacy_null_uuid(extra):
    async def run():
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add(f"org_{ORG}", channel)
        message = {
            "alert_title": "Limit reached",
            "nested": {"items": [1, None, False]},
        }
        response = await sync_to_async(post_relay)(
            {"message": message, "organization_id": OTHER_ORG, **extra}
        )
        assert response.status_code == 200, response.data
        event = await asyncio.wait_for(layer.receive(channel), timeout=1)
        assert event == {"type": "send_data", "data": message}

    asyncio.run(run())


@pytest.mark.parametrize("message", [None, [], [1], "text", 1, True])
def test_non_object_messages_are_rejected(message):
    with patch("tfc.views.socket.get_channel_layer") as layer:
        response = post_relay({"message": message})
    assert response.status_code == 400
    layer.assert_not_called()


@pytest.mark.parametrize(
    "extra", [{}, {"uuid": None}, {"uuid": ""}, {"uuid": "not-a-uuid"}]
)
def test_targeted_delivery_requires_uuid(extra):
    with patch("tfc.views.socket.get_channel_layer") as layer:
        response = post_relay({"message": {}, "send_to_uuid": True, **extra})
    assert response.status_code == 400
    layer.assert_not_called()


def test_relay_requires_authentication():
    request = APIRequestFactory().post(
        "/call-websocket/", {"message": {}}, format="json"
    )
    with patch("tfc.views.socket.get_channel_layer") as layer:
        response = CallWebsocketView.as_view()(request)
    assert response.status_code in (401, 403)
    layer.assert_not_called()


@pytest.mark.parametrize("targeted", [False, True])
def test_enqueue_failure_is_not_reported_as_success(targeted):
    with (
        patch("tfc.views.socket.get_channel_layer") as layer,
        patch("tfc.views.socket.logger") as logger,
    ):
        layer.return_value.group_send = AsyncMock(
            side_effect=ConnectionError("private delivery detail")
        )
        response = post_relay({"message": {}, "send_to_uuid": targeted, "uuid": TARGET})
    assert response.status_code == 400
    logger.error.assert_called_once_with(
        "websocket_relay_delivery_failed", error_type="ConnectionError"
    )
    assert "private delivery detail" not in str(response.data)


@pytest.mark.parametrize("failure", ["lookup", "create", "transport"])
def test_notification_failures_are_contained_and_sanitized(failure):
    with (
        patch("model_hub.utils.OrgApiKey.no_workspace_objects") as keys,
        patch("model_hub.utils.requests.post") as post,
        patch("model_hub.utils.logger") as logger,
    ):
        keys.get.return_value = SimpleNamespace(
            api_key="synthetic-key", secret_key="synthetic-secret"
        )
        if failure == "lookup":
            keys.get.side_effect = RuntimeError("private lookup detail")
        elif failure == "create":
            keys.get.side_effect = OrgApiKey.DoesNotExist
            keys.create.side_effect = RuntimeError("private creation detail")
        else:
            post.side_effect = requests.ConnectionError("private transport detail")
        result = call_websocket(ORG, {"alert_title": "Limit reached"})
    assert result["status"] == "error"
    logger.error.assert_called_once()
    assert "private" not in str(logger.error.call_args)
    assert "private" not in str(result)
    if failure != "transport":
        post.assert_not_called()


@override_settings(WEBSOCKET_ENDPOINT="http://[invalid")
def test_malformed_endpoint_is_a_contained_notification_failure():
    with (
        patch("model_hub.utils.OrgApiKey.no_workspace_objects") as keys,
        patch("model_hub.utils.logger") as logger,
    ):
        result = call_websocket(ORG, {})
    assert result["status"] == "error"
    keys.get.assert_not_called()
    logger.error.assert_called_once_with(
        "websocket_relay_failed",
        error_type="ValueError",
        status_code=None,
        validation_fields=[],
    )


def test_http_validation_log_contains_only_status_and_known_field_names():
    response = requests.Response()
    response.status_code = 400
    response._content = json.dumps(
        {
            "result": {
                "uuid": ["private validation text"],
                "message": ["private message"],
                "private-field": ["secret"],
            }
        }
    ).encode()
    with (
        patch("model_hub.utils.OrgApiKey.no_workspace_objects") as keys,
        patch("model_hub.utils.requests.post", return_value=response),
        patch("model_hub.utils.logger") as logger,
    ):
        keys.get.return_value = SimpleNamespace(
            api_key="synthetic-key", secret_key="synthetic-secret"
        )
        result = call_websocket(ORG, {})
    assert result["status"] == "error"
    logger.error.assert_called_once_with(
        "websocket_relay_failed",
        error_type="HTTPError",
        status_code=400,
        validation_fields=["message", "uuid"],
    )
