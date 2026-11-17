"""A tool session's refresh token mints a new access token only for an active
account.

Both places that take a refresh token answer an inactive account's token as one
they do not accept: the token endpoint with 400 ``invalid_grant`` and no access
token, and the OAuth provider by loading no refresh token. An active account's
refresh token still mints a new access token at the token endpoint and is
loaded by the provider.

When the provider looks up an inactive account's refresh token it removes that
stored token, and only that one, so the token stays refused once the account
is active again. An active account's refresh token stays stored, and a lookup
of an unknown token removes nothing.

When the account read fails with a database error, the provider raises it
instead of answering that there is no such token, and the stored refresh token
stays. A stored record that is not a refresh token record, or an unknown token,
still loads none.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.core.cache.backends.locmem import LocMemCache
from django.db import DatabaseError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from accounts.models.user import User
from mcp_server import oauth_provider
from mcp_server.models.connection import MCPConnection
from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.oauth_utils import generate_refresh_token, hash_client_secret
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db

TOKEN_URL = "/mcp/oauth/token/"
CLIENT_SECRET = "refresh-account-secret"


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


def _account(user, *, active):
    """The signed-up account, deactivated first when ``active`` is false."""
    clear_workspace_context()
    if not active:
        user.is_active = False
        user.save(update_fields=["is_active"])
    clear_workspace_context()
    return user


def _refresh_at_the_token_endpoint(api_client, user, workspace, *, active):
    """Status, error and whether an access token came back from a refresh,
    read without keeping the body."""
    account = _account(user, active=active)
    client = MCPOAuthClient.objects.create(
        client_id=f"refresh-account-{uuid.uuid4().hex}",
        client_secret_hash=hash_client_secret(CLIENT_SECRET),
        name="Refresh account client",
        redirect_uris=["https://example.test/callback"],
    )
    refresh_token = generate_refresh_token(
        account.pk, workspace.organization_id, client.client_id
    )
    MCPConnection.no_workspace_objects.create(
        user=account,
        organization=workspace.organization,
        workspace=workspace,
        is_active=True,
        oauth_refresh_token_encrypted=refresh_token,
    )
    clear_workspace_context()
    response = api_client.post(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client.client_id,
            "client_secret": CLIENT_SECRET,
        },
        format="json",
        REMOTE_ADDR="203.0.113.10",
    )
    body = response.json()
    return response.status_code, body.get("error"), "access_token" in body


def _loaded_by_the_provider(user, workspace, monkeypatch, *, active):
    """Whether the OAuth provider loads the account's stored refresh token."""
    account = _account(user, active=active)
    store = LocMemCache(f"oauth-refresh-account-{uuid.uuid4().hex}", {})
    monkeypatch.setattr(oauth_provider, "cache", store)
    provider = oauth_provider.FutureAGIOAuthProvider(frontend_url="http://localhost")
    client = OAuthClientInformationFull(
        client_id="refresh-account-client",
        redirect_uris=[AnyUrl("http://localhost/callback")],
    )
    refresh_token = f"refresh-{uuid.uuid4().hex}"
    stored = oauth_provider.FutureAGIRefreshToken(
        token=refresh_token,
        client_id=client.client_id,
        scopes=[],
        user_id=str(account.pk),
        organization_id=str(workspace.organization_id),
        workspace_id=str(workspace.pk),
    )
    store.set(
        f"{oauth_provider.REFRESH_PREFIX}{refresh_token}",
        stored.model_dump(mode="json"),
        60,
    )
    loaded = async_to_sync(provider.load_refresh_token)(client, refresh_token)
    return loaded is not None


def test_token_endpoint_mints_no_access_token_for_an_inactive_account(
    api_client, user, workspace
):
    answer = _refresh_at_the_token_endpoint(api_client, user, workspace, active=False)

    assert answer == (400, "invalid_grant", False)


def test_token_endpoint_mints_an_access_token_for_an_active_account(
    api_client, user, workspace
):
    answer = _refresh_at_the_token_endpoint(api_client, user, workspace, active=True)

    assert answer == (200, None, True)


def test_provider_loads_no_refresh_token_for_an_inactive_account(
    user, workspace, monkeypatch
):
    loaded = _loaded_by_the_provider(user, workspace, monkeypatch, active=False)

    assert loaded is False


def test_provider_loads_an_active_account_refresh_token(user, workspace, monkeypatch):
    loaded = _loaded_by_the_provider(user, workspace, monkeypatch, active=True)

    assert loaded is True


def _refresh_key(refresh_token):
    """The store key of a refresh token record."""
    return f"{oauth_provider.REFRESH_PREFIX}{refresh_token}"


def _provider_over_a_store(monkeypatch):
    """A provider over an isolated store, and the client it answers."""
    store = LocMemCache(f"oauth-refresh-store-{uuid.uuid4().hex}", {})
    monkeypatch.setattr(oauth_provider, "cache", store)
    provider = oauth_provider.FutureAGIOAuthProvider(frontend_url="http://localhost")
    client = OAuthClientInformationFull(
        client_id="refresh-store-client",
        redirect_uris=[AnyUrl("http://localhost/callback")],
    )
    return SimpleNamespace(store=store, provider=provider, client=client)


def _store_refresh_token(setup, account, client_id=None):
    """Store a refresh token record for ``account`` and answer the token."""
    refresh_token = f"refresh-{uuid.uuid4().hex}"
    record = oauth_provider.FutureAGIRefreshToken(
        token=refresh_token,
        client_id=client_id or setup.client.client_id,
        scopes=[],
        user_id=str(account.pk),
        organization_id=str(account.organization_id),
    )
    setup.store.set(_refresh_key(refresh_token), record.model_dump(mode="json"), 60)
    return refresh_token


def _load(setup, refresh_token):
    """The record the provider loads for ``refresh_token``, or None."""
    load = async_to_sync(setup.provider.load_refresh_token)
    return load(setup.client, refresh_token)


def _reactivate(account):
    """Make the account active again."""
    clear_workspace_context()
    account.is_active = True
    account.save(update_fields=["is_active"])
    clear_workspace_context()


def _tokens_that_stay(setup, account, workspace):
    """Store the tokens a lookup of the account's refresh token leaves alone:
    the account's refresh token for another client, another account's refresh
    token, the account's access token and the refresh token on the account's
    connection row. Answer a check that names the ones that are gone."""
    other_account = User.objects.create_user(
        email=f"other-{uuid.uuid4().hex[:8]}@example.test",
        name="Other account",
        organization=workspace.organization,
    )
    other_client = _store_refresh_token(setup, account, client_id="another-client")
    other_account_token = _store_refresh_token(setup, other_account)
    access_token = f"access-{uuid.uuid4().hex}"
    access = oauth_provider.FutureAGIAccessToken(
        token=access_token,
        client_id=setup.client.client_id,
        scopes=[],
        user_id=str(account.pk),
        organization_id=str(account.organization_id),
    )
    access_key = f"{oauth_provider.ACCESS_PREFIX}{access_token}"
    setup.store.set(access_key, access.model_dump(mode="json"), 60)
    connection_token = generate_refresh_token(
        account.pk, workspace.organization_id, setup.client.client_id
    )
    clear_workspace_context()
    connection = MCPConnection.no_workspace_objects.create(
        user=account,
        organization=workspace.organization,
        workspace=workspace,
        is_active=True,
        oauth_refresh_token_encrypted=connection_token,
    )
    clear_workspace_context()

    def gone():
        connection.refresh_from_db()
        on_connection = connection.oauth_refresh_token_encrypted
        kept = {
            "another client's": _refresh_key(other_client) in setup.store,
            "another account's": _refresh_key(other_account_token) in setup.store,
            "access": access_key in setup.store,
            "connection's": on_connection == connection_token,
        }
        return [name for name, is_kept in kept.items() if not is_kept]

    return gone


def test_provider_removes_an_inactive_account_refresh_token_and_nothing_else(
    user, workspace, monkeypatch
):
    account = _account(user, active=False)
    setup = _provider_over_a_store(monkeypatch)
    refresh_token = _store_refresh_token(setup, account)
    others_gone = _tokens_that_stay(setup, account, workspace)

    loaded = _load(setup, refresh_token)

    assert loaded is None
    assert _refresh_key(refresh_token) not in setup.store
    assert others_gone() == []


def test_provider_refuses_the_token_after_the_account_is_active_again(
    user, workspace, monkeypatch
):
    account = _account(user, active=False)
    setup = _provider_over_a_store(monkeypatch)
    refresh_token = _store_refresh_token(setup, account)
    assert _load(setup, refresh_token) is None

    _reactivate(account)

    assert _load(setup, refresh_token) is None


def test_provider_keeps_an_active_account_refresh_token_stored(
    user, workspace, monkeypatch
):
    account = _account(user, active=True)
    setup = _provider_over_a_store(monkeypatch)
    refresh_token = _store_refresh_token(setup, account)

    loaded = _load(setup, refresh_token)

    assert loaded is not None
    assert loaded.user_id == str(account.pk)
    assert _refresh_key(refresh_token) in setup.store


def test_provider_removes_nothing_for_an_unknown_token(user, workspace, monkeypatch):
    account = _account(user, active=False)
    setup = _provider_over_a_store(monkeypatch)
    refresh_token = _store_refresh_token(setup, account)
    others_gone = _tokens_that_stay(setup, account, workspace)

    loaded = _load(setup, f"refresh-{uuid.uuid4().hex}")

    assert loaded is None
    assert _refresh_key(refresh_token) in setup.store
    assert others_gone() == []


def _fail_the_account_read(monkeypatch):
    """The account read fails with a database error."""
    failing = Mock(side_effect=DatabaseError("account store unavailable"))
    monkeypatch.setattr(User.objects, "filter", failing)


def test_provider_raises_a_failed_account_read_and_keeps_the_token(
    user, workspace, monkeypatch
):
    account = _account(user, active=True)
    setup = _provider_over_a_store(monkeypatch)
    refresh_token = _store_refresh_token(setup, account)
    _fail_the_account_read(monkeypatch)

    with pytest.raises(DatabaseError, match="account store unavailable"):
        _load(setup, refresh_token)
    assert _refresh_key(refresh_token) in setup.store


@pytest.mark.parametrize("kind", ["malformed", "unknown"])
def test_provider_loads_none_for_a_malformed_or_unknown_token(monkeypatch, kind):
    setup = _provider_over_a_store(monkeypatch)
    refresh_token = f"refresh-{uuid.uuid4().hex}"
    if kind == "malformed":
        setup.store.set(_refresh_key(refresh_token), {"token": refresh_token}, 60)
    _fail_the_account_read(monkeypatch)

    assert _load(setup, refresh_token) is None
