"""A refreshed MCP OAuth access token never carries more scope than the user
consented to (RFC 6749 section 6). The scope is the consented scope narrowed
to the connection's currently enabled tool groups, and it is carried across
refresh-token rotation. Also covers parsing of the refresh-token lifetime
setting."""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.cache import cache

from mcp_server.models.connection import MCPConnection
from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.models.oauth_code import MCPOAuthCode
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.oauth_utils import (
    decrypt_oauth_token,
    generate_refresh_token,
    hash_client_secret,
)
from tfc.middleware.workspace_context import set_workspace_context

REDIRECT = "https://example.com/callback"
SECRET = "synthetic-secret"
TOOL_GROUPS_URL = "/mcp/config/tool-groups/"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _relaxed_throttles(settings):
    # These tests exercise token scope, not throttling.
    settings.MCP_OAUTH_TOKEN_IP_THROTTLE_RATE = "1000/min"
    settings.MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE = "1000/min"
    settings.MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT = 1000


def _client(client_id):
    return MCPOAuthClient.objects.filter(
        client_id=client_id
    ).first() or MCPOAuthClient.objects.create(
        client_id=client_id,
        client_secret_hash=hash_client_secret(SECRET),
        name=client_id,
        redirect_uris=[REDIRECT],
    )


def _code(user, workspace, client, value, scope):
    set_workspace_context(
        workspace=workspace, organization=user.organization, user=user
    )
    return MCPOAuthCode.objects.create(
        code=value,
        client=client,
        user=user,
        organization=user.organization,
        workspace=workspace,
        redirect_uri=REDIRECT,
        scope=scope,
    )


def _post(payload):
    from rest_framework.test import APIClient

    return APIClient().post(
        "/mcp/oauth/token/", payload, format="json", REMOTE_ADDR="198.51.100.40"
    )


def _authorize(user, workspace, scope, client_id="scope-client", code="scope-code"):
    _code(user, workspace, _client(client_id), code, scope)
    response = _post(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": SECRET,
            "redirect_uri": REDIRECT,
        }
    )
    assert response.status_code == 200, response.data
    return response.data


def _refresh(token, client_id="scope-client"):
    response = _post(
        {
            "grant_type": "refresh_token",
            "refresh_token": token,
            "client_id": client_id,
            "client_secret": SECRET,
        }
    )
    assert response.status_code == 200, response.data
    return response.data


def _granted(token_response):
    """Scope as both the response and the signed access token state it."""
    claim = decrypt_oauth_token(token_response["access_token"])["scope"]
    listed = [s for s in token_response["scope"].split(",") if s]
    assert listed == list(claim)
    return sorted(claim)


def _set_groups(auth_client, groups):
    response = auth_client.put(
        TOOL_GROUPS_URL, {"enabled_groups": groups}, format="json"
    )
    assert response.status_code == 200, response.data


def _connection(user, workspace):
    return MCPConnection.no_workspace_objects.get(
        user=user, workspace=workspace, deleted=False
    )


@pytest.mark.django_db
class TestRefreshKeepsConsentedScope:
    def test_enabling_more_tool_groups_does_not_widen_a_refreshed_token(
        self, user, workspace, auth_client
    ):
        tokens = _authorize(user, workspace, ["context"])
        assert _granted(tokens) == ["context"]

        # The user (or an admin) later enables more groups for the connection.
        _set_groups(auth_client, ["context", "datasets", "gateway"])

        first = _refresh(tokens["refresh_token"])
        assert _granted(first) == ["context"]
        # Carried across rotation: the successor is bounded the same way.
        second = _refresh(first["refresh_token"])
        assert _granted(second) == ["context"]

    def test_another_clients_wider_consent_does_not_widen_this_clients_refresh(
        self, user, workspace
    ):
        narrow = _authorize(user, workspace, ["context"], "client-a", "code-a")
        # A second client on the same connection is consented more groups,
        # which rewrites the connection's enabled groups.
        wide = _authorize(
            user, workspace, ["context", "datasets"], "client-b", "code-b"
        )

        assert _granted(_refresh(narrow["refresh_token"], "client-a")) == [
            "context"
        ]
        assert _granted(_refresh(wide["refresh_token"], "client-b")) == [
            "context",
            "datasets",
        ]

    def test_disabled_groups_are_dropped_and_come_back_only_up_to_consent(
        self, user, workspace, auth_client
    ):
        tokens = _authorize(user, workspace, ["context", "datasets"])

        _set_groups(auth_client, ["context"])
        narrowed = _refresh(tokens["refresh_token"])
        assert _granted(narrowed) == ["context"]

        # Re-enabling restores consented groups only.
        _set_groups(auth_client, ["context", "datasets", "gateway"])
        restored = _refresh(narrowed["refresh_token"])
        assert _granted(restored) == ["context", "datasets"]

    def test_unchanged_groups_refresh_to_the_full_consented_scope(
        self, user, workspace
    ):
        tokens = _authorize(user, workspace, ["context", "evaluations"])

        refreshed = _refresh(tokens["refresh_token"])

        assert _granted(refreshed) == ["context", "evaluations"]
        assert refreshed["refresh_token"] != tokens["refresh_token"]


@pytest.mark.django_db
class TestLegacyRefreshTokens:
    """Refresh tokens issued before the consented scope was recorded."""

    def _legacy_token(self, user, workspace, client_id, groups):
        set_workspace_context(
            workspace=workspace, organization=user.organization, user=user
        )
        connection = MCPConnection.no_workspace_objects.create(
            user=user,
            organization=user.organization,
            workspace=workspace,
            is_active=True,
        )
        token = generate_refresh_token(
            user_id=user.id, org_id=user.organization.id, client_id=client_id
        )
        connection.oauth_refresh_token_encrypted = token
        connection.save(update_fields=["oauth_refresh_token_encrypted"])
        MCPToolGroupConfig.no_workspace_objects.create(
            connection=connection, enabled_groups=groups
        )
        return token

    def test_legacy_token_is_bounded_by_the_authorization_that_issued_it(
        self, user, workspace, auth_client
    ):
        client = _client("legacy-client")
        code = _code(user, workspace, client, "legacy-code", ["context"])
        code.used = True
        code.save(update_fields=["used"])
        token = self._legacy_token(
            user, workspace, "legacy-client", ["context", "datasets", "gateway"]
        )

        first = _refresh(token, "legacy-client")
        assert _granted(first) == ["context"]
        assert _granted(_refresh(first["refresh_token"], "legacy-client")) == [
            "context"
        ]

    def test_legacy_token_without_a_recorded_consent_is_frozen_at_first_refresh(
        self, user, workspace, auth_client
    ):
        _client("legacy-client")
        token = self._legacy_token(user, workspace, "legacy-client", ["context"])

        first = _refresh(token, "legacy-client")
        assert _granted(first) == ["context"]

        _set_groups(auth_client, ["context", "datasets"])
        assert _granted(_refresh(first["refresh_token"], "legacy-client")) == [
            "context"
        ]


# ── MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS parsing ─────────────────────────────

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_TTL = 30 * 24 * 3600


def _settings_ttl(raw):
    env = {**os.environ, "MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS": raw}
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import tfc.settings.settings as s;"
            "print(s.MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS)",
        ],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    "raw", ["thirty-days", "1.5", "0", "-60", "1000000000000", "9" * 30]
)
def test_malformed_refresh_ttl_falls_back_to_the_default(raw):
    result = _settings_ttl(raw)

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == str(_DEFAULT_TTL)
    assert "MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS" in result.stderr


@pytest.mark.parametrize(
    "raw,expected",
    [("7200", 7200), (" 3600 ", 3600), ("1", 1), ("315360000", 315360000)],
)
def test_valid_refresh_ttl_is_used(raw, expected):
    result = _settings_ttl(raw)

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == str(expected)


def test_refresh_ttl_parser_logs_the_fallback(caplog):
    from tfc.settings import settings as settings_module

    with caplog.at_level(logging.WARNING):
        value = settings_module._mcp_oauth_refresh_token_ttl("not-a-number")

    assert value == _DEFAULT_TTL
    assert "MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS" in caplog.text
    assert settings_module._mcp_oauth_refresh_token_ttl(None) == _DEFAULT_TTL
    assert settings_module._mcp_oauth_refresh_token_ttl("  ") == _DEFAULT_TTL
