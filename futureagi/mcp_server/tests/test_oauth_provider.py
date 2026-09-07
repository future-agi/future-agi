"""Coverage for FutureAGIOAuthProvider — the OAuth 2.1 provider behind the MCP
token endpoint.

These are characterization tests: they pin what the provider *currently does*,
not what the OAuth 2.1 spec says it ought to. That distinction matters because
the provider implements only part of the flow — the MCP SDK's own handlers sit
in front of it and enforce the rest. Specifically, the SDK (not this module)
verifies the PKCE challenge, validates the redirect URI, authenticates the
client, and narrows requested scopes against the refresh token's scopes
(mcp/server/auth/handlers/token.py). Asserting those here would pin behaviour
this module does not own.

What the provider does own, and what is pinned below:
  * client registration round-trip through the cache
  * authorization-code loading: client binding, expiry, single use
  * code -> token exchange and the metadata it persists
  * refresh-token loading, client binding, and rotation on exchange
  * revocation clearing the cache entry

Storage is Django's cache; the test settings use LocMemCache, so nothing here
needs Redis, a database, or the Docker stack.
"""

import time

import pytest
from django.core.cache import cache
from mcp.shared.auth import OAuthClientInformationFull

from mcp_server.oauth_provider import (
    ACCESS_PREFIX,
    CLIENT_PREFIX,
    CODE_PREFIX,
    REFRESH_PREFIX,
    FutureAGIAccessToken,
    FutureAGIAuthorizationCode,
    FutureAGIOAuthProvider,
    FutureAGIRefreshToken,
)

CLIENT_ID = "client-abc"
OTHER_CLIENT_ID = "client-xyz"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def provider():
    return FutureAGIOAuthProvider(frontend_url="https://app.example.com")


@pytest.fixture
def client():
    return OAuthClientInformationFull(
        client_id=CLIENT_ID,
        redirect_uris=["https://app.example.com/callback"],
    )


@pytest.fixture
def other_client():
    return OAuthClientInformationFull(
        client_id=OTHER_CLIENT_ID,
        redirect_uris=["https://evil.example.com/callback"],
    )


def _code(code="code-1", client_id=CLIENT_ID, expires_in=600, scopes=None):
    return FutureAGIAuthorizationCode(
        code=code,
        scopes=scopes if scopes is not None else ["read"],
        expires_at=time.time() + expires_in,
        client_id=client_id,
        code_challenge="challenge-value",
        redirect_uri="https://app.example.com/callback",
        redirect_uri_provided_explicitly=True,
        user_id="user-1",
        organization_id="org-1",
        workspace_id="ws-1",
    )


def _store_code(code_obj):
    cache.set(f"{CODE_PREFIX}{code_obj.code}", code_obj.model_dump(mode="json"), 600)


def _refresh(token="refresh-1", client_id=CLIENT_ID, scopes=None):
    return FutureAGIRefreshToken(
        token=token,
        client_id=client_id,
        scopes=scopes if scopes is not None else ["read", "write"],
        user_id="user-1",
        organization_id="org-1",
        workspace_id="ws-1",
    )


def _store_refresh(rt):
    cache.set(f"{REFRESH_PREFIX}{rt.token}", rt.model_dump(mode="json"), 600)


class TestProviderInit:
    def test_frontend_url_trailing_slash_is_stripped(self):
        """Guards the double-slash redirect path the constructor comment calls out."""
        p = FutureAGIOAuthProvider(frontend_url="https://dev.futureagi.com/")
        assert p.frontend_url == "https://dev.futureagi.com"

    def test_frontend_url_without_trailing_slash_is_unchanged(self):
        p = FutureAGIOAuthProvider(frontend_url="https://dev.futureagi.com")
        assert p.frontend_url == "https://dev.futureagi.com"

    def test_falls_back_to_frontend_url_env(self, monkeypatch):
        monkeypatch.setenv("FRONTEND_URL", "https://from-env.example.com/")
        assert FutureAGIOAuthProvider().frontend_url == "https://from-env.example.com"


class TestClientRegistration:
    async def test_register_then_get_round_trip(self, provider, client):
        await provider.register_client(client)
        loaded = await provider.get_client(CLIENT_ID)

        assert loaded is not None
        assert loaded.client_id == CLIENT_ID

    async def test_register_persists_under_the_client_prefix(self, provider, client):
        await provider.register_client(client)
        assert cache.get(f"{CLIENT_PREFIX}{CLIENT_ID}") is not None

    async def test_get_unknown_client_returns_none(self, provider):
        assert await provider.get_client("no-such-client") is None

    async def test_get_client_returns_none_on_corrupt_cache_entry(self, provider):
        """A malformed entry must not propagate a validation error to the caller."""
        cache.set(f"{CLIENT_PREFIX}{CLIENT_ID}", {"not": "a client"}, 600)
        assert await provider.get_client(CLIENT_ID) is None

    async def test_register_without_client_id_raises(self, provider):
        bad = OAuthClientInformationFull(
            client_id="placeholder", redirect_uris=["https://app.example.com/cb"]
        )
        bad.client_id = ""
        with pytest.raises(ValueError):
            await provider.register_client(bad)


class TestLoadAuthorizationCode:
    async def test_valid_code_loads(self, provider, client):
        _store_code(_code())
        loaded = await provider.load_authorization_code(client, "code-1")

        assert loaded is not None
        assert loaded.user_id == "user-1"
        assert loaded.organization_id == "org-1"

    async def test_unknown_code_returns_none(self, provider, client):
        assert await provider.load_authorization_code(client, "nope") is None

    async def test_code_issued_to_another_client_is_rejected(
        self, provider, other_client
    ):
        """Code substitution guard: the code is bound to its issuing client."""
        _store_code(_code(client_id=CLIENT_ID))
        assert await provider.load_authorization_code(other_client, "code-1") is None

    async def test_expired_code_is_rejected(self, provider, client):
        _store_code(_code(expires_in=-1))
        assert await provider.load_authorization_code(client, "code-1") is None

    async def test_corrupt_code_entry_returns_none(self, provider, client):
        cache.set(f"{CODE_PREFIX}code-1", {"garbage": True}, 600)
        assert await provider.load_authorization_code(client, "code-1") is None


class TestExchangeAuthorizationCode:
    async def test_returns_access_and_refresh_tokens(self, provider, client):
        code = _code()
        _store_code(code)

        token = await provider.exchange_authorization_code(client, code)

        assert token.access_token
        assert token.refresh_token
        assert token.token_type == "Bearer"
        assert token.expires_in == 3600

    async def test_authorization_code_is_single_use(self, provider, client):
        """The code is deleted on exchange, so a replay finds nothing."""
        code = _code()
        _store_code(code)

        await provider.exchange_authorization_code(client, code)

        assert cache.get(f"{CODE_PREFIX}{code.code}") is None
        assert await provider.load_authorization_code(client, code.code) is None

    async def test_access_and_refresh_metadata_are_persisted(self, provider, client):
        code = _code()
        _store_code(code)

        token = await provider.exchange_authorization_code(client, code)

        access = cache.get(f"{ACCESS_PREFIX}{token.access_token}")
        refresh = cache.get(f"{REFRESH_PREFIX}{token.refresh_token}")
        assert access is not None and refresh is not None
        assert access["user_id"] == "user-1"
        assert access["organization_id"] == "org-1"
        assert refresh["client_id"] == CLIENT_ID

    async def test_scopes_are_returned_space_delimited(self, provider, client):
        code = _code(scopes=["read", "write"])
        _store_code(code)

        token = await provider.exchange_authorization_code(client, code)
        assert token.scope == "read write"

    async def test_scope_is_none_when_the_code_carried_no_scopes(
        self, provider, client
    ):
        code = _code(scopes=[])
        _store_code(code)

        token = await provider.exchange_authorization_code(client, code)
        assert token.scope is None

    async def test_user_context_propagates_into_the_access_token(
        self, provider, client
    ):
        code = _code()
        _store_code(code)

        token = await provider.exchange_authorization_code(client, code)
        loaded = await provider.load_access_token(token.access_token)

        assert loaded is not None
        assert loaded.user_id == "user-1"
        assert loaded.organization_id == "org-1"
        assert loaded.workspace_id == "ws-1"


class TestLoadRefreshToken:
    async def test_valid_refresh_token_loads(self, provider, client):
        _store_refresh(_refresh())
        loaded = await provider.load_refresh_token(client, "refresh-1")

        assert loaded is not None
        assert loaded.user_id == "user-1"

    async def test_unknown_refresh_token_returns_none(self, provider, client):
        assert await provider.load_refresh_token(client, "nope") is None

    async def test_refresh_token_issued_to_another_client_is_rejected(
        self, provider, other_client
    ):
        _store_refresh(_refresh(client_id=CLIENT_ID))
        assert await provider.load_refresh_token(other_client, "refresh-1") is None

    async def test_corrupt_refresh_entry_returns_none(self, provider, client):
        cache.set(f"{REFRESH_PREFIX}refresh-1", {"garbage": True}, 600)
        assert await provider.load_refresh_token(client, "refresh-1") is None


class TestExchangeRefreshToken:
    async def test_old_refresh_token_is_rotated_out(self, provider, client):
        rt = _refresh()
        _store_refresh(rt)

        token = await provider.exchange_refresh_token(client, rt, ["read"])

        assert cache.get(f"{REFRESH_PREFIX}{rt.token}") is None
        assert await provider.load_refresh_token(client, rt.token) is None
        assert token.refresh_token != rt.token

    async def test_new_refresh_token_is_usable(self, provider, client):
        rt = _refresh()
        _store_refresh(rt)

        token = await provider.exchange_refresh_token(client, rt, ["read"])
        loaded = await provider.load_refresh_token(client, token.refresh_token)

        assert loaded is not None
        assert loaded.user_id == "user-1"

    async def test_requested_scopes_are_used_when_supplied(self, provider, client):
        rt = _refresh(scopes=["read", "write"])
        _store_refresh(rt)

        token = await provider.exchange_refresh_token(client, rt, ["read"])
        assert token.scope == "read"

    async def test_falls_back_to_the_refresh_tokens_scopes_when_none_requested(
        self, provider, client
    ):
        """An empty request inherits the grant's scopes rather than dropping them.

        Narrowing is enforced upstream by the SDK's token handler, which rejects
        any requested scope absent from the refresh token before reaching here.
        """
        rt = _refresh(scopes=["read", "write"])
        _store_refresh(rt)

        token = await provider.exchange_refresh_token(client, rt, [])
        assert token.scope == "read write"

    async def test_new_access_token_metadata_is_persisted(self, provider, client):
        rt = _refresh()
        _store_refresh(rt)

        token = await provider.exchange_refresh_token(client, rt, ["read"])

        access = cache.get(f"{ACCESS_PREFIX}{token.access_token}")
        assert access is not None
        assert access["user_id"] == "user-1"
        assert access["client_id"] == CLIENT_ID


class TestLoadAccessToken:
    async def test_expired_cached_access_token_is_rejected_and_evicted(self, provider):
        stale = FutureAGIAccessToken(
            token="stale-token",
            client_id=CLIENT_ID,
            scopes=["read"],
            expires_at=int(time.time()) - 10,
            user_id="user-1",
            organization_id="org-1",
        )
        cache.set(f"{ACCESS_PREFIX}stale-token", stale.model_dump(mode="json"), 600)

        assert await provider.load_access_token("stale-token") is None
        assert cache.get(f"{ACCESS_PREFIX}stale-token") is None

    async def test_unknown_and_undecryptable_token_returns_none(self, provider):
        assert await provider.load_access_token("not-a-real-token") is None


class TestRevokeToken:
    async def test_revoking_an_access_token_clears_its_cache_entry(self, provider):
        access = FutureAGIAccessToken(
            token="access-1",
            client_id=CLIENT_ID,
            scopes=["read"],
            expires_at=int(time.time()) + 600,
            user_id="user-1",
            organization_id="org-1",
        )
        cache.set(f"{ACCESS_PREFIX}access-1", access.model_dump(mode="json"), 600)

        await provider.revoke_token(access)

        assert cache.get(f"{ACCESS_PREFIX}access-1") is None

    async def test_revoking_a_refresh_token_makes_it_unloadable(self, provider, client):
        rt = _refresh()
        _store_refresh(rt)

        await provider.revoke_token(rt)

        assert cache.get(f"{REFRESH_PREFIX}{rt.token}") is None
        assert await provider.load_refresh_token(client, rt.token) is None
