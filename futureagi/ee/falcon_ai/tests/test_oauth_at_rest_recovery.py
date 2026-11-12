"""r2b credential refusal boundaries and confidential-client recovery."""

import asyncio
import secrets
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.db import router
from rest_framework.test import APIRequestFactory, force_authenticate

from agentcc.services.credential_manager import encrypt_token
from ee.falcon_ai import oauth_client, views_connectors
from ee.falcon_ai.credentials import FalconCredentialError, decrypt_falcon_token
from ee.falcon_ai.mcp_proxy import MCPConnectorProxy
from ee.falcon_ai.tests import test_oauth_at_rest as support
from ee.falcon_ai.tests.test_oauth_at_rest import check, row
from ee.falcon_ai.tests.test_oauth_at_rest_preflight import (
    persistent_test_key,  # noqa: F401 -- pytest fixture
)

pytestmark = pytest.mark.django_db
connector = support.connector
AUTH_REQUIRED = "CONNECTOR_AUTH_REQUIRED"


def request_for(user, connector, method="post", data=None):
    request = getattr(APIRequestFactory(), method)("/test/", data or {}, format="json")
    force_authenticate(request, user=user)
    request.organization = user.organization
    request.workspace = connector.workspace
    return request


@pytest.mark.parametrize(
    "operation",
    [
        "discover_tools",
        "discover_tools_sync",
        "execute_tool",
        "execute_tool_sync",
        "test_connection_sync",
    ],
)
def test_proxy_preserves_static_credential_refusal(connector, operation):
    proxy = MCPConnectorProxy()
    private = secrets.token_urlsafe(32)
    with (
        patch.object(
            proxy, "_build_headers", side_effect=FalconCredentialError(private)
        ),
        patch.object(proxy, "_save_error") as save_error,
        patch.object(proxy, "_post_with_fallback") as sync_post,
        patch.object(proxy, "_async_post_with_fallback") as async_post,
        patch("ee.falcon_ai.mcp_proxy.logger") as logger,
    ):
        args = (
            (connector, "test-tool", {})
            if operation.startswith("execute")
            else (connector,)
        )
        result = getattr(proxy, operation)(*args)
        if operation in ("discover_tools", "execute_tool"):
            result = asyncio.run(result)
    check(
        result.get("code") == AUTH_REQUIRED,
        "credential failure lost auth-required code",
    )
    check(private not in str(result), "exception content returned")
    check(private not in str(logger.mock_calls), "exception content logged")
    check(
        not sync_post.called and not async_post.called,
        "credential refusal sent request",
    )
    if operation.startswith("discover"):
        check(save_error.call_count == 1, "discovery refusal not recorded")
        check(save_error.call_args.args[1] == AUTH_REQUIRED, "unsafe persisted error")


@pytest.mark.parametrize(
    "view",
    [
        "MCPConnectorDiscoverView",
        "MCPConnectorTestView",
        "MCPConnectorAuthenticateView",
    ],
)
def test_api_maps_proxy_refusal_to_auth_required(connector, user, view):
    connector.auth_type = "bearer"
    connector.auth_header_value = "enc::invalid-test-only"
    connector.save()
    with patch.object(MCPConnectorProxy, "_post_with_fallback") as post:
        response = getattr(views_connectors, view).as_view()(
            request_for(user, connector), connector_id=connector.pk
        )
    check(
        response.status_code == 409,
        "credential refusal returned generic gateway failure",
    )
    check(response.data.get("code") == AUTH_REQUIRED, "API lost auth-required code")
    check(not post.called, "unreadable credential sent to provider")


@pytest.mark.parametrize("view", ["MCPConnectorListView", "MCPConnectorDetailView"])
def test_api_runtime_write_refusal_is_static(connector, user, settings, view):
    settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = True
    data = {"auth_header_value": secrets.token_urlsafe(32)}
    kwargs = {}
    method = "patch"
    if view == "MCPConnectorListView":
        method = "post"
        data.update(
            name="new-test",
            server_url=connector.server_url,
            transport="streamable_http",
            auth_type="bearer",
        )
    else:
        kwargs["connector_id"] = connector.pk
    before = row(connector).auth_header_value
    response = getattr(views_connectors, view).as_view()(
        request_for(user, connector, method, data), **kwargs
    )
    check(response.status_code == 409, "credential write refusal not mapped")
    check(response.data.get("code") == AUTH_REQUIRED, "write refusal lost code")
    check(row(connector).auth_header_value == before, "refused write changed row")


def test_authenticate_unsafe_key_refuses_before_registration(connector, user, settings):
    settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = True
    with patch.object(oauth_client, "register_client") as register:
        response = views_connectors.MCPConnectorAuthenticateView.as_view()(
            request_for(user, connector), connector_id=connector.pk
        )
    check(response.status_code == 409, "setup key refusal not mapped")
    check(response.data.get("code") == AUTH_REQUIRED, "setup key refusal lost code")
    check(not register.called, "registered client before key preflight")


@pytest.mark.parametrize("public_replacement", [False, True])
def test_authenticate_replaces_unreadable_registration_and_callback_works(
    connector, user, public_replacement
):
    connector.oauth_client_id = "old-test-client"
    connector.oauth_client_secret = "enc::invalid-test-only"
    connector.save()
    replacement = {"client_id": "fresh-test-client"}
    secret = secrets.token_urlsafe(32)
    if not public_replacement:
        replacement["client_secret"] = secret
    with patch.object(
        oauth_client, "register_client", return_value=replacement
    ) as register:
        response = views_connectors.MCPConnectorAuthenticateView.as_view()(
            request_for(user, connector), connector_id=connector.pk
        )
    check(response.status_code == 200, "reauthorization failed")
    check(register.call_count == 1, "unreadable registration reused")
    params = parse_qs(urlparse(response.data["authorization_url"]).query)
    check(params["client_id"] == ["fresh-test-client"], "old client id reused")
    stored = row(connector)
    check(
        decrypt_falcon_token(stored.oauth_client_secret)
        == ("" if public_replacement else secret),
        "replacement secret not stored",
    )
    token_response = Mock()
    token_response.json.return_value = {"access_token": secrets.token_urlsafe(32)}
    with patch.object(oauth_client.httpx, "post", return_value=token_response) as post:
        oauth_client.exchange_code_for_tokens(
            stored, "test-code", "https://app.example.invalid/cb"
        )
    sent = post.call_args.kwargs["data"]
    check(sent["client_id"] == "fresh-test-client", "callback reused old client")
    check(
        sent.get("client_secret", "") == ("" if public_replacement else secret),
        "callback secret wrong",
    )


def test_readable_registration_is_reused(connector):
    connector.oauth_client_id = "existing-test-client"
    cipher = encrypt_token(secrets.token_urlsafe(32))
    connector.oauth_client_secret = cipher
    connector.save()  # Reuse is decided from the persisted WRITE registration.
    with patch.object(oauth_client, "register_client") as register:
        oauth_client.build_authorization_url(
            connector, "https://app.example.invalid/cb"
        )
    check(not register.called, "readable registration replaced")
    check(row(connector).oauth_client_secret == cipher, "existing secret changed")


@pytest.mark.parametrize(
    "operation",
    ["authorization", "exchange", "refresh", "discovery_error", "discovery_success"],
)
def test_replica_loaded_object_credential_writes_use_write_alias(connector, operation):
    alias = router.db_for_write(type(connector))
    connector._state.db = "r2b_forbidden_replica"
    token_response = Mock()
    token_response.json.return_value = {
        "access_token": "test-access",
        "refresh_token": "test-refresh",
    }
    connector.oauth_client_secret = encrypt_token("test-client-secret")
    connector.oauth_refresh_token = encrypt_token("test-refresh")
    connector.save(using=alias)
    connector._state.db = "r2b_forbidden_replica"
    with (
        patch.object(router, "db_for_write", return_value=alias) as write_router,
        patch.object(
            oauth_client,
            "register_client",
            return_value={
                "client_id": "fresh-test-client",
                "client_secret": "test-secret",
            },
        ),
        patch.object(oauth_client.httpx, "post", return_value=token_response),
    ):
        if operation == "authorization":
            oauth_client.build_authorization_url(
                connector, "https://app.example.invalid/cb"
            )
        elif operation == "exchange":
            oauth_client.exchange_code_for_tokens(
                connector, "test-code", "https://app.example.invalid/cb"
            )
        elif operation == "refresh":
            oauth_client.refresh_access_token(connector)
        elif operation == "discovery_error":
            MCPConnectorProxy()._save_error(connector, AUTH_REQUIRED)
        else:
            MCPConnectorProxy()._save_discovery(connector, [])
    check(write_router.called, "write router not consulted")
    check(
        all(not call.kwargs.get("instance") for call in write_router.call_args_list),
        "replica instance biased write route",
    )
    check(connector._state.db == alias, "write went to replica")


def test_callback_credential_refusal_is_static_auth_required(connector):
    connector.oauth_state = "test-state"
    connector.oauth_client_secret = "enc::invalid-test-only"
    connector.save()
    request = APIRequestFactory().get(
        "/callback/", {"code": "test-code", "state": "test-state"}
    )
    with patch.object(oauth_client.httpx, "post") as post:
        response = views_connectors.MCPConnectorOAuthCallbackView.as_view()(
            request, connector_id=connector.pk
        )
    check(response.status_code == 409, "callback refusal not mapped")
    check(
        AUTH_REQUIRED.encode() in response.content, "callback lost auth-required code"
    )
    check(
        row(connector).last_error == AUTH_REQUIRED, "callback persisted generic error"
    )
    check(not post.called, "callback sent unreadable credential")
