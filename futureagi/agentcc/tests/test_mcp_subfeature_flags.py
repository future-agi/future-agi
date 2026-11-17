"""Deployment gates hide a sub-feature before authentication or any I/O."""

from unittest.mock import patch

import pytest
from django.test import Client, override_settings

from agentcc.services import connector_catalog as catalog
from tfc.utils.api_errors import build_error_envelope

pytestmark = pytest.mark.django_db

FLAGS = {
    "AGENTCC_MCP_CLIENT_OAUTH_ENABLED": False,
    "AGENTCC_MCP_K2_APPS_ENABLED": False,
}


@pytest.mark.parametrize(
    "path",
    [
        "/agentcc/mcp/connect/",
        "/agentcc/mcp/connect/crm/start/",
        "/agentcc/mcp/connect/callback/",
        "/agentcc/mcp/connect/client.json",
        "/agentcc/internal/mcp/upstream-token/",
        "/agentcc/connectors/oauth-clients/",
        # datadog also has key providers, so only the upstream switch hides these.
        "/agentcc/connectors/apps/datadog/oauth/",
        "/agentcc/connectors/apps/datadog/oauth/discovery/",
        "/agentcc/mcp-oauth/authorize",
        "/agentcc/mcp-oauth/token",
        "/agentcc/mcp-oauth/register",
        "/agentcc/mcp-oauth/revoke",
        "/agentcc/mcp-oauth/jwks.json",
        "/agentcc/mcp-oauth/consents/",
        "/agentcc/mcp-oauth/consent/example/",
        "/agentcc/mcp-oauth/.well-known/openid-configuration",
        "/.well-known/oauth-authorization-server/agentcc/mcp-oauth",
        "/.well-known/openid-configuration/agentcc/mcp-oauth",
    ],
)
@pytest.mark.parametrize("method", ["get", "post", "options"])
def test_off_routes_are_not_found_before_auth(path, method, monkeypatch):
    monkeypatch.setenv("AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED", "false")
    with override_settings(**FLAGS):
        response = getattr(Client(), method)(path)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


@pytest.mark.parametrize(
    "app", ["gmail", "google-calendar", "microsoft-outlook", "microsoft-teams"]
)
def test_k2_detail_and_add_hidden_before_auth(app, monkeypatch):
    monkeypatch.setenv("AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED", "true")
    with override_settings(**FLAGS):
        for path in [
            f"/agentcc/connectors/catalog/{app}/",
            f"/agentcc/connectors/apps/{app}/add/",
        ]:
            response = Client().get(path)
            assert response.status_code == 404
            assert response.json()["code"] == "not_found"


def test_an_off_route_answers_as_the_catalog_answers_a_hidden_app(monkeypatch):
    """The switch middleware's pre-auth 404 is the common error envelope, the
    same body the catalog view gives for an app it hides (code not_found, "Not
    found."), so a caller cannot tell which of the two answered."""
    monkeypatch.setenv("AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED", "false")
    with override_settings(**FLAGS):
        response = Client().get("/agentcc/mcp/connect/crm/start/")
    assert response.status_code == 404
    assert response.json() == build_error_envelope(
        "Not found.", status_code=404, code="not_found"
    )
    assert response["Cache-Control"] == "no-store"


def test_core_routes_remain_mounted(monkeypatch):
    monkeypatch.setenv("AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED", "false")
    with override_settings(**FLAGS):
        for path in ["/agentcc/mcp/credentials/", "/agentcc/connectors/catalog/"]:
            assert Client().get(path).status_code != 404


def test_existing_k2_app_is_coming_soon_when_off(monkeypatch):
    monkeypatch.setenv("AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED", "false")
    stored = {
        "gmail": [
            {"server_id": "gmail", "provider": "futureagi-rest", "connected": False}
        ]
    }
    with (
        override_settings(**FLAGS),
        patch.object(catalog, "_org_connectors", return_value=stored),
    ):
        _, apps = catalog.catalog_for_org(None, None)
    rows = {app["id"]: app for app in apps}
    assert "gmail" in rows
    gmail = rows["gmail"]
    assert gmail["status"] == "coming_soon"
    assert gmail["providers"] == []
    assert gmail["default_provider"] is None
    # The stored server stays hidden while its sign-in flags are off.
    assert gmail["servers"] == []


@pytest.mark.parametrize(
    "setting,path",
    [
        ("AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED", "/agentcc/mcp/connect/client.json"),
        (
            "AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED",
            "/agentcc/connectors/apps/datadog/oauth/",
        ),
        ("AGENTCC_MCP_CLIENT_OAUTH_ENABLED", "/agentcc/mcp-oauth/token"),
    ],
)
def test_switches_are_independent(setting, path, monkeypatch):
    monkeypatch.setenv(
        "AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED",
        str(setting == "AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED").lower(),
    )
    with override_settings(**{**FLAGS, **({setting: True} if setting in FLAGS else {})}):
        # Auth/validation/configuration may refuse, but the route is not hidden.
        assert Client().get(path).status_code != 404
