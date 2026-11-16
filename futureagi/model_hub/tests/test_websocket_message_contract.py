"""Exercise worker payloads through validation, routing and socket delivery."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import pytest
import requests
from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.layers import get_channel_layer
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from structlog.testing import capture_logs

from accounts.models import OrgApiKey
from model_hub.utils import call_websocket
from sockets.consumer import DataConsumer
from tfc.utils.websocket_relay_auth import (
    RELAY_SIGNATURE_HEADER,
    RELAY_TIMESTAMP_HEADER,
    relay_headers,
    relay_signature,
)
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
            "generation_id": f"generate_{TARGET}",
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


def signed_meta(organization_id, raw, *, now=None):
    """The relay headers for ``raw`` as request META entries."""

    return {
        "HTTP_" + name.upper().replace("-", "_"): value
        for name, value in relay_headers(organization_id, raw, now=now).items()
    }


def post_relay(payload, *, raw=None, headers=None):
    """Call the relay as its internal caller: the system key and a signature."""

    raw = json.dumps(payload).encode() if raw is None else raw
    if headers is None:
        headers = relay_headers(ORG, raw)
    request = APIRequestFactory().post(
        "/call-websocket/",
        raw,
        content_type="application/json",
        **{
            "HTTP_" + name.upper().replace("-", "_"): value
            for name, value in headers.items()
        },
    )
    # The selected authenticated organization wins over both the user default
    # and any untrusted organization_id in the body.
    request.organization = SimpleNamespace(id=ORG)
    request.org_api_key = SimpleNamespace(type="system", organization_id=ORG)
    force_authenticate(
        request,
        user=SimpleNamespace(
            is_authenticated=True, organization=SimpleNamespace(id=OTHER_ORG)
        ),
    )
    return CallWebsocketView.as_view()(request)


def relay_http(url, *, data, headers, **kwargs):
    # Forward the exact bytes and signature that call_websocket sent.
    response = post_relay(
        None,
        raw=data,
        headers={
            name: headers[name]
            for name in (RELAY_SIGNATURE_HEADER, RELAY_TIMESTAMP_HEADER)
            if name in headers
        },
    )
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


# Channels closes old DB connections in a worker thread before dispatch, even
# with mocked ORM lookups. Use transactional access for the real ASGI consumers.
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("message,targeted", MESSAGES)
def test_helper_messages_reach_only_authenticated_organization(message, targeted):
    target = message.get("generation_id", TARGET)
    assert_helper_delivery(message, targeted, target)


def assert_helper_delivery(message, targeted, target):
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
                        "text": json.dumps({"type": "subscribe", "uuid": target}),
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
                    uuid=target if targeted else None,
                )
            assert result["status"] is True, result
            payload = json.loads(post.call_args.kwargs["data"])
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
@pytest.mark.parametrize("organization_attribute", ["missing", "none"])
def test_relay_requires_authenticated_organization(targeted, organization_attribute):
    raw = json.dumps(
        {
            "message": {"alert_title": "Synthetic notification"},
            "send_to_uuid": targeted,
            "uuid": TARGET,
            "organization_id": ORG,
        }
    ).encode()
    request = APIRequestFactory().post(
        "/call-websocket/",
        raw,
        content_type="application/json",
        **signed_meta(ORG, raw),
    )
    # Even a correctly signed system-key request needs its organization.
    request.org_api_key = SimpleNamespace(type="system", organization_id=ORG)
    if organization_attribute == "none":
        request.organization = None
    # A legacy user FK and an untrusted body cannot restore tenant authority.
    force_authenticate(
        request,
        user=SimpleNamespace(
            is_authenticated=True, organization=SimpleNamespace(id=ORG)
        ),
    )
    with patch("tfc.views.socket.get_channel_layer") as layer:
        layer.return_value.group_send = AsyncMock()
        response = CallWebsocketView.as_view()(request)
    assert response.status_code == 403
    assert response.data["code"] == "permission_denied"
    layer.assert_not_called()


@pytest.mark.parametrize(
    "key_type, key_org, signed_org, signed_body",
    [
        pytest.param("user", ORG, ORG, None, id="user-key"),
        pytest.param("mcp", ORG, ORG, None, id="mcp-key"),
        pytest.param(None, None, ORG, None, id="session-without-key"),
        pytest.param("system", OTHER_ORG, ORG, None, id="key-of-another-org"),
        pytest.param("system", ORG, OTHER_ORG, None, id="signed-for-another-org"),
        pytest.param("system", ORG, ORG, b"{}", id="signed-another-body"),
        pytest.param("system", ORG, None, None, id="unsigned"),
    ],
)
def test_relay_refuses_callers_other_than_the_internal_client(
    key_type, key_org, signed_org, signed_body
):
    raw = json.dumps({"message": {"alert_title": "Synthetic notification"}}).encode()
    headers = {}
    if signed_org is not None:
        headers.update(
            signed_meta(signed_org, raw if signed_body is None else signed_body)
        )
    request = APIRequestFactory().post(
        "/call-websocket/", raw, content_type="application/json", **headers
    )
    request.organization = SimpleNamespace(id=ORG)
    if key_type is not None:
        request.org_api_key = SimpleNamespace(type=key_type, organization_id=key_org)
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    with patch("tfc.views.socket.get_channel_layer") as layer:
        response = CallWebsocketView.as_view()(request)
    assert response.status_code == 403
    assert response.data["code"] == "permission_denied"
    layer.assert_not_called()


@pytest.mark.parametrize(
    "signature",
    [
        pytest.param("\u00e9" * 64, id="latin-1"),
        pytest.param("\u2603" + "0" * 63, id="non-latin"),
    ],
)
def test_relay_refuses_a_signature_with_non_ascii_characters(signature):
    raw = json.dumps({"message": {"alert_title": "Synthetic notification"}}).encode()
    with patch("tfc.views.socket.get_channel_layer") as layer:
        response = post_relay(
            None,
            raw=raw,
            headers={
                RELAY_SIGNATURE_HEADER: signature,
                RELAY_TIMESTAMP_HEADER: str(int(time.time())),
            },
        )
    assert response.status_code == 403
    assert response.data["code"] == "permission_denied"
    layer.assert_not_called()


def test_signature_check_treats_non_ascii_text_as_a_mismatch():
    from tfc.utils.websocket_relay_auth import relay_signature_is_valid

    raw = b"{}"
    now = 1_800_000_000
    valid = relay_signature(ORG, raw, now)
    signed_at = str(now)
    assert relay_signature_is_valid(ORG, raw, valid, signed_at, now=now)
    assert not relay_signature_is_valid(
        ORG, raw, valid[:-1] + "\u00e9", signed_at, now=now
    )
    assert not relay_signature_is_valid(
        ORG, raw, "\ud800" + valid[1:], signed_at, now=now
    )


@pytest.mark.parametrize(
    "signed_offset, timestamp",
    [
        pytest.param(-301, None, id="expired"),
        pytest.param(301, None, id="future-beyond-window"),
        pytest.param(0, "", id="missing"),
        pytest.param(0, "12.5", id="unparseable"),
        pytest.param(0, "-1", id="negative"),
    ],
)
def test_relay_refuses_a_signature_outside_its_time_window(signed_offset, timestamp):
    raw = json.dumps({"message": {"alert_title": "Synthetic notification"}}).encode()
    # A fixed clock, so the offset is exact however long signing takes.
    now = 1_800_000_000
    headers = relay_headers(ORG, raw, now=now + signed_offset)
    if timestamp is not None:
        headers[RELAY_TIMESTAMP_HEADER] = timestamp
    with (
        patch("tfc.utils.websocket_relay_auth.time.time", return_value=now),
        patch("tfc.views.socket.get_channel_layer") as layer,
    ):
        response = post_relay(None, raw=raw, headers=headers)
    assert response.status_code == 403
    layer.assert_not_called()


def test_captured_relay_request_is_refused_after_the_window():
    raw = json.dumps({"message": {"alert_title": "Synthetic notification"}}).encode()
    sent_at = 1_800_000_000
    headers = relay_headers(ORG, raw, now=sent_at)
    with patch("tfc.utils.websocket_relay_auth.time.time", return_value=sent_at + 5):
        assert post_relay(None, raw=raw, headers=headers).status_code == 200
    with (
        patch("tfc.utils.websocket_relay_auth.time.time", return_value=sent_at + 301),
        patch("tfc.views.socket.get_channel_layer") as layer,
    ):
        # The same bytes and headers, sent again later.
        assert post_relay(None, raw=raw, headers=headers).status_code == 403
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
        "websocket_relay_delivery_failed",
        error_type="ConnectionError",
        exception_traceback=ANY,
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
        exception_traceback=ANY,
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
        exception_traceback=ANY,
        status_code=400,
        validation_fields=["message", "uuid"],
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "action,id_field,prefix,data",
    [
        (
            "generate_prompt",
            "generation_id",
            "generate_",
            {"statement": "Synthetic task"},
        ),
        (
            "improve_prompt",
            "improve_id",
            "improve_",
            {
                "existing_prompt": "Synthetic prompt",
                "improvement_requirements": "Be concise",
            },
        ),
    ],
)
@pytest.mark.parametrize("streaming", [False, True])
def test_prompt_endpoint_identifiers_reach_subscribed_consumer(
    action, id_field, prefix, data, streaming
):
    from model_hub.views import prompt_template

    request = SimpleNamespace(
        data=data,
        headers={},
        workspace=None,
        user=SimpleNamespace(id=TARGET, organization=SimpleNamespace(id=ORG)),
    )
    # Exercise the real producer, replacing only billing/model work. The ID must
    # come from its response, not a test's approximation of the endpoint format.
    with (
        patch.object(prompt_template, "count_text_tokens", return_value=1),
        patch.object(prompt_template, "log_and_deduct_cost_for_api_request", None),
        patch.object(prompt_template, "PromptGenerator"),
        patch.object(prompt_template, "submit_with_retry") as submit,
        patch("tfc.ee_gating.check_ee_feature"),
    ):
        response = getattr(prompt_template.PromptTemplateViewSet(), action)(request)
    assert response.status_code == 200, response.data
    target = response.data["result"][id_field]
    assert target.startswith(prefix)
    assert submit.call_args.args[2][id_field] == target
    message = (
        {
            "type": id_field,
            "uuid": target,
            "chunk": "Synthetic chunk",
            "streaming_status": "running",
        }
        if streaming
        else {
            "type": action,
            id_field: target,
            "current_activity": "generate_initial_prompt",
            "status": "completed",
        }
    )
    assert_helper_delivery(message, True, target)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "target",
    [
        f"generate_{TARGET}",
        f"improve_{TARGET}",
        "generate_AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
        "improve_AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
    ],
)
def test_prefixed_target_normalization_matches_subscriber(target):
    assert_helper_delivery(
        {"type": "run_prompt", "uuid": target, "chunk": "hello"}, True, target
    )


@pytest.mark.parametrize(
    "target",
    [
        "generate_",
        "improve_not-a-uuid",
        f"unknown_{TARGET}",
        f"generate_improve_{TARGET}",
        f"generate_{TARGET}extra",
        [TARGET],
        42,
    ],
)
def test_invalid_prefixed_targets_are_rejected(target):
    with patch("tfc.views.socket.get_channel_layer") as layer:
        response = post_relay({"message": {}, "send_to_uuid": True, "uuid": target})
    assert response.status_code == 400
    layer.assert_not_called()


def diagnostic_cause():
    # No exception text, source line, request object, or local value may be logged.
    sensitive = "synthetic-private-credential"
    raise ValueError(sensitive)


def diagnostic_origin(*args, **kwargs):
    try:
        diagnostic_cause()
    except ValueError as exc:
        raise ConnectionError("synthetic-private-payload") from exc


@pytest.mark.parametrize("boundary", ["lookup", "create", "transport", "delivery"])
def test_failure_logs_retain_origin_and_cause_without_sensitive_values(boundary):
    with (
        capture_logs() as events,
        patch("model_hub.utils.OrgApiKey.no_workspace_objects") as keys,
        patch("model_hub.utils.requests.post") as post,
        patch("tfc.views.socket.get_channel_layer") as layer,
    ):
        keys.get.return_value = SimpleNamespace(
            api_key="synthetic-key", secret_key="synthetic-secret"
        )
        if boundary == "lookup":
            keys.get.side_effect = diagnostic_origin
        elif boundary == "create":
            keys.get.side_effect = OrgApiKey.DoesNotExist
            keys.create.side_effect = diagnostic_origin
        elif boundary == "transport":
            post.side_effect = diagnostic_origin
        else:
            layer.return_value.group_send = AsyncMock(side_effect=diagnostic_origin)
        if boundary == "delivery":
            assert post_relay({"message": {}}).status_code == 400
        else:
            assert (
                call_websocket(ORG, {"sensitive": "synthetic-private-message"})[
                    "status"
                ]
                == "error"
            )
    event = next(
        event for event in events if event["event"].startswith("websocket_relay_")
    )
    assert event["log_level"] == "error"
    trace = event["exception_traceback"]
    assert trace[0]["error_type"] == "ConnectionError"
    assert trace[0]["chained_by"] == "cause"
    assert trace[1]["error_type"] == "ValueError"
    for entry, function in zip(
        trace[:2], ["diagnostic_origin", "diagnostic_cause"], strict=True
    ):
        origin = entry["frames"][-1]
        assert origin["function"] == function
        assert origin["file"] == "test_websocket_message_contract.py"
        assert origin["line"] > 0
    rendered = json.dumps(event)
    assert "synthetic-private" not in rendered
    assert "synthetic-secret" not in rendered
    assert "synthetic-key" not in rendered
    assert "exc_info" not in event
