"""The SDK compatibility route must retain real client authentication and binding."""

import base64
from uuid import uuid4

import httpx
import pytest
from asgiref.sync import async_to_sync
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from mcp_server import mcp_app
from mcp_server.models.oauth_revocation import MCPOAuthAccessRevocation
from mcp_server.tests import test_oauth_access_revocation as _revocation_tests

# The access-revocation tests' grant fixture, shared with these route tests.
oauth_grant = _revocation_tests.oauth_grant

pytestmark = pytest.mark.django_db


@pytest.fixture
def revoke(oauth_grant, monkeypatch):
    monkeypatch.setattr(mcp_app, "_oauth_app", None)
    monkeypatch.setattr(mcp_app, "mcp_public_base_url", lambda: "http://localhost")
    app = mcp_app.get_mcp_oauth_app()

    async def request(data, headers=None):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            return await client.post("/revoke", data=data, headers=headers)

    return async_to_sync(request)


@pytest.mark.parametrize("method", ["client_secret_post", "client_secret_basic"])
def test_confidential_client_still_requires_its_secret(oauth_grant, revoke, method):
    grant = oauth_grant
    secret = uuid4().hex
    client = grant.client.model_copy(
        update={"token_endpoint_auth_method": method, "client_secret": secret}
    )
    async_to_sync(grant.provider.register_client)(client)
    data = {"client_id": client.client_id, "token": grant.token}

    for attempt in (data, {**data, "client_secret": "incorrect"}):
        response = revoke(attempt)
        assert response.status_code == 401
        assert not MCPOAuthAccessRevocation.is_revoked(grant.token)
        assert async_to_sync(grant.provider.load_access_token)(grant.token) is not None

    if method == "client_secret_basic":
        credentials = base64.b64encode(f"{client.client_id}:{secret}".encode()).decode()
        response = revoke(data, {"Authorization": f"Basic {credentials}"})
    else:
        response = revoke({**data, "client_secret": secret})
    assert response.status_code == 200, response.text
    assert MCPOAuthAccessRevocation.is_revoked(grant.token)
    assert async_to_sync(grant.provider.load_access_token)(grant.token) is None
    assert async_to_sync(grant.provider.load_access_token)(grant.other_token) is not None


def test_public_client_cannot_revoke_another_clients_token(oauth_grant, revoke):
    grant = oauth_grant
    stranger = OAuthClientInformationFull(
        client_id="other-public-client",
        redirect_uris=[AnyUrl("http://localhost/callback")],
        token_endpoint_auth_method="none",
    )
    async_to_sync(grant.provider.register_client)(stranger)
    response = revoke({"client_id": stranger.client_id, "token": grant.token})
    # RFC 7009 deliberately does not reveal whether a foreign token exists.
    assert response.status_code == 200, response.text
    assert not MCPOAuthAccessRevocation.is_revoked(grant.token)
    assert async_to_sync(grant.provider.load_access_token)(grant.token) is not None


@pytest.mark.parametrize("invalid", ["missing-token", "invalid-hint"])
def test_revocation_form_validation_is_not_relaxed(oauth_grant, revoke, invalid):
    grant = oauth_grant
    data = {"client_id": grant.client.client_id}
    if invalid == "invalid-hint":
        data.update(token=grant.token, token_type_hint="unsupported")
    response = revoke(data)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert not MCPOAuthAccessRevocation.is_revoked(grant.token)
    assert async_to_sync(grant.provider.load_access_token)(grant.token) is not None
