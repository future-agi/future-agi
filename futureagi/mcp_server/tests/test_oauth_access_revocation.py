"""Revocation must outlive cached access metadata."""

import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from asgiref.sync import async_to_sync
from django.core.cache.backends.locmem import LocMemCache
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.responses import Response

from mcp_server import mcp_app, oauth_provider

pytestmark = pytest.mark.django_db


@pytest.fixture
def oauth_grant(user, workspace, monkeypatch):
    """Issue real encrypted grants through the SDK provider, with isolated cache."""
    cache = LocMemCache(f"oauth-revocation-{uuid4().hex}", {})
    monkeypatch.setattr(oauth_provider, "cache", cache)
    provider = oauth_provider.FutureAGIOAuthProvider(frontend_url="http://localhost")
    client = OAuthClientInformationFull(
        client_id="revocation-test-client",
        redirect_uris=[AnyUrl("http://localhost/callback")],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )
    async_to_sync(provider.register_client)(client)

    def issue():
        code = oauth_provider.FutureAGIAuthorizationCode(
            code=uuid4().hex,
            scopes=[],
            expires_at=time.time() + 600,
            client_id=client.client_id,
            code_challenge="regression-challenge",
            redirect_uri=client.redirect_uris[0],
            redirect_uri_provided_explicitly=True,
            resource=None,
            user_id=str(user.pk),
            organization_id=str(user.organization_id),
            workspace_id=str(workspace.pk),
        )
        return async_to_sync(provider.exchange_authorization_code)(client, code)

    issued = issue()
    other = issue()
    access = async_to_sync(provider.load_access_token)(issued.access_token)
    assert access is not None
    mcp_app._clear_context()
    try:
        yield SimpleNamespace(
            provider=provider,
            client=client,
            cache=cache,
            token=issued.access_token,
            access=access,
            other_token=other.access_token,
        )
    finally:
        mcp_app._clear_context()
        cache.clear()


def _authenticate(token, admission):
    mcp_app._clear_context()
    try:
        if admission == "provider":
            # A new instance must not forget a previous worker's revocations.
            provider = oauth_provider.FutureAGIOAuthProvider(
                frontend_url="http://localhost"
            )
            return async_to_sync(provider.load_access_token)(token)
        return mcp_app._authenticate_via_oauth(token)
    finally:
        mcp_app._clear_context()


@pytest.mark.parametrize("admission", ["provider", "mcp"])
@pytest.mark.parametrize("metadata", ["missing", "stale"])
def test_revoked_bearer_stays_denied_after_cache_restart(
    oauth_grant, admission, metadata
):
    grant = oauth_grant
    assert _authenticate(grant.token, admission) is not None
    async_to_sync(grant.provider.revoke_token)(grant.access)
    # Idempotent direct revocation must not undo the first revocation.
    async_to_sync(grant.provider.revoke_token)(grant.access)
    grant.cache.clear()
    if metadata == "stale":
        grant.cache.set(
            f"{oauth_provider.ACCESS_PREFIX}{grant.token}",
            grant.access.model_dump(mode="json"),
            oauth_provider.ACCESS_TTL,
        )

    # A cache restart still permits an unrelated unexpired grant for this user.
    assert _authenticate(grant.other_token, admission) is not None
    assert _authenticate(grant.token, admission) is None


@pytest.mark.parametrize("admission", ["provider", "mcp"])
@pytest.mark.parametrize("metadata", ["missing", "stale"])
def test_canonical_text_gate_precedes_admission(
    oauth_grant, monkeypatch, admission, metadata
):
    from unittest.mock import Mock

    from mcp_server import oauth_utils
    from mcp_server.models.oauth_revocation import MCPOAuthAccessRevocation

    grant = oauth_grant
    async_to_sync(grant.provider.revoke_token)(grant.access)
    grant.cache.clear()
    if metadata == "stale":
        grant.cache.set(
            f"{oauth_provider.ACCESS_PREFIX}{grant.token}",
            grant.access.model_dump(mode="json"),
            oauth_provider.ACCESS_TTL,
        )

    # Model the format decision at its boundary without constructing aliases.
    canonical = Mock(return_value=False)
    get_fernet = Mock(wraps=oauth_utils._get_fernet)
    cache_get = Mock(wraps=grant.cache.get)
    using = Mock(wraps=MCPOAuthAccessRevocation.objects.using)
    monkeypatch.setattr(
        oauth_utils, "is_canonical_oauth_token", canonical, raising=False
    )
    monkeypatch.setattr(oauth_utils, "_get_fernet", get_fernet)
    monkeypatch.setattr(grant.cache, "get", cache_get)
    monkeypatch.setattr(MCPOAuthAccessRevocation.objects, "using", using)

    assert _authenticate(grant.token, admission) is None
    canonical.assert_called_once_with(grant.token)
    get_fernet.assert_not_called()
    cache_get.assert_not_called()
    using.assert_not_called()


def test_mounted_revoke_denies_same_bearer_at_mcp(oauth_grant, monkeypatch):
    """Use the mounted SDK /revoke handler and the actual /mcp auth middleware.

    Only the downstream tool executor is stubbed; requests stay in-process.
    """
    grant = oauth_grant
    monkeypatch.setattr(mcp_app, "_oauth_app", None)
    monkeypatch.setattr(mcp_app, "mcp_public_base_url", lambda: "http://localhost")
    admitted = []

    async def tool_app(scope, receive, send):
        context = mcp_app._current_context()
        assert context is not None
        admitted.append(context.user.pk)
        await Response("authenticated")(scope, receive, send)

    monkeypatch.setattr(mcp_app, "get_mcp_streamable_app", lambda: tool_app)
    oauth_app = mcp_app.get_mcp_oauth_app()

    async def request(app, path, **kwargs):
        mcp_app._clear_context()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
            ) as client:
                return await client.post(path, **kwargs)
        finally:
            mcp_app._clear_context()

    def mcp_request():
        return async_to_sync(request)(
            mcp_app._mcp_streamable_with_auth,
            "/mcp",
            headers={"Authorization": f"Bearer {grant.token}"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "whoami", "arguments": {}},
            },
        )

    assert mcp_request().status_code == 200
    for _ in range(2):
        revoked = async_to_sync(request)(
            oauth_app,
            "/revoke",
            data={
                "token": grant.token,
                "token_type_hint": "access_token",
                "client_id": grant.client.client_id,
            },
        )
        assert revoked.status_code == 200
    grant.cache.clear()
    denied = mcp_request()
    assert denied.status_code == 401
    assert denied.json()["error"] == "invalid_token"
    assert len(admitted) == 1


def test_revocation_preserves_encrypted_expiry_and_only_stores_digest(
    oauth_grant, monkeypatch
):
    import hashlib
    from datetime import datetime, timedelta

    from mcp_server.models.oauth_revocation import MCPOAuthAccessRevocation
    from mcp_server.oauth_utils import decrypt_oauth_token

    grant = oauth_grant
    payload = decrypt_oauth_token(grant.token)
    expiry = datetime.fromisoformat(payload["expires_at"])
    async_to_sync(grant.provider.revoke_token)(grant.access)
    row = MCPOAuthAccessRevocation.objects.get()
    assert row.pk == hashlib.sha256(grant.token.encode()).hexdigest()
    assert row.expires_at == expiry
    assert {field.name for field in row._meta.fields} == {
        "token_digest", "expires_at"
    }
    # Do not drop the tombstone at the SDK's rounded integer-second expiry.
    monkeypatch.setattr(
        "django.utils.timezone.now", lambda: expiry - timedelta(microseconds=1)
    )
    assert MCPOAuthAccessRevocation.is_revoked(grant.token)
    monkeypatch.setattr(
        "django.utils.timezone.now", lambda: expiry + timedelta(microseconds=1)
    )
    assert not MCPOAuthAccessRevocation.is_revoked(grant.token)


def test_revocation_reads_and_writes_primary(oauth_grant, monkeypatch):
    from unittest.mock import Mock, call

    from django.db import DEFAULT_DB_ALIAS

    from mcp_server.models.oauth_revocation import MCPOAuthAccessRevocation

    using = Mock(wraps=MCPOAuthAccessRevocation.objects.using)
    monkeypatch.setattr(MCPOAuthAccessRevocation.objects, "using", using)
    MCPOAuthAccessRevocation.revoke(oauth_grant.token)
    assert MCPOAuthAccessRevocation.is_revoked(oauth_grant.token)
    assert using.call_args_list == [call(DEFAULT_DB_ALIAS), call(DEFAULT_DB_ALIAS)]


@pytest.mark.parametrize("operation", ["revoke", "provider", "mcp"])
def test_unavailable_revocation_store_never_succeeds(
    oauth_grant, monkeypatch, operation
):
    from unittest.mock import Mock

    from django.db import DatabaseError

    from mcp_server.models.oauth_revocation import MCPOAuthAccessRevocation

    monkeypatch.setattr(
        MCPOAuthAccessRevocation.objects,
        "using",
        Mock(side_effect=DatabaseError("revocation authority unavailable")),
    )
    with pytest.raises(DatabaseError, match="revocation authority unavailable"):
        if operation == "revoke":
            async_to_sync(oauth_grant.provider.revoke_token)(oauth_grant.access)
        else:
            _authenticate(oauth_grant.token, operation)
    assert oauth_grant.cache.get(
        f"{oauth_provider.ACCESS_PREFIX}{oauth_grant.token}"
    ) is not None
