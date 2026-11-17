"""Settings > MCP Server shows one URL; the MCP server must advertise the same.

The page reads ``mcp_url`` from GET /mcp/config/. An IDE that connects to it
gets a 401 whose ``WWW-Authenticate`` names ``resource_metadata``; the
protected-resource document then names the resource and the authorization
server. All of them must share one base, or discovery fails (critic-teamv2#7:
the page said :8100 while the 401 said :8000).
"""

import re

import httpx
import pytest
from django.test import override_settings

from mcp_server import mcp_app
from mcp_server.public_url import mcp_public_base_url, mcp_public_url

pytestmark = pytest.mark.django_db(transaction=True)

RESOURCE_METADATA = re.compile(r'resource_metadata="([^"]+)"')


@pytest.fixture
def no_env_base(monkeypatch):
    monkeypatch.delenv("MCP_SERVER_BASE_URL", raising=False)


async def _unauthenticated_initialize():
    mcp_app.get_mcp_streamable_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mcp_app.mcp_streamable_with_auth),
        base_url="http://localhost",
    ) as client:
        return await client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )


class TestMCPPublicBaseUrl:
    @override_settings(MCP_SERVER_HOST=None, BASE_URL="http://localhost:8100/")
    def test_defaults_to_the_public_api_url(self, no_env_base):
        assert mcp_public_base_url() == "http://localhost:8100"
        assert mcp_public_url() == "http://localhost:8100/mcp"

    @override_settings(MCP_SERVER_HOST="https://mcp.example.test", BASE_URL="http://x")
    def test_mcp_server_host_wins_over_base_url(self, no_env_base):
        assert mcp_public_url() == "https://mcp.example.test/mcp"

    @override_settings(MCP_SERVER_HOST="https://mcp.example.test", BASE_URL="http://x")
    def test_the_environment_wins_over_settings(self, monkeypatch):
        monkeypatch.setenv("MCP_SERVER_BASE_URL", "https://env.example.test/")
        assert mcp_public_url() == "https://env.example.test/mcp"

    @override_settings(MCP_SERVER_HOST=None, BASE_URL="")
    def test_unknown_when_nothing_is_set(self, no_env_base):
        assert mcp_public_base_url() is None
        assert mcp_public_url() is None


class TestPageAndServerAgree:
    @override_settings(MCP_SERVER_HOST=None, BASE_URL="http://localhost:8100")
    def test_config_url_matches_the_401_resource_metadata(
        self, auth_client, no_env_base
    ):
        from asgiref.sync import async_to_sync

        page_url = auth_client.get("/mcp/config/").data["result"]["mcp_url"]
        assert page_url == "http://localhost:8100/mcp"

        response = async_to_sync(_unauthenticated_initialize)()
        assert response.status_code == 401
        metadata = RESOURCE_METADATA.search(response.headers["WWW-Authenticate"])
        assert metadata, response.headers["WWW-Authenticate"]
        assert (
            metadata.group(1)
            == "http://localhost:8100/.well-known/oauth-protected-resource/mcp"
        )
        # The resource the metadata describes is the URL the page shows.
        assert metadata.group(1).split("/.well-known/")[0] + "/mcp" == page_url

    @override_settings(MCP_SERVER_HOST=None, BASE_URL="http://localhost:8100")
    def test_protected_resource_document_names_the_page_url(
        self, monkeypatch, no_env_base
    ):
        from asgiref.sync import async_to_sync

        monkeypatch.setattr(mcp_app, "_oauth_app", None)
        app = mcp_app.get_mcp_oauth_app()

        async def fetch():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost"
            ) as client:
                return await client.get("/.well-known/oauth-protected-resource/mcp")

        response = async_to_sync(fetch)()
        assert response.status_code == 200
        body = response.json()
        assert body["resource"] == "http://localhost:8100/mcp"
        assert [s.rstrip("/") for s in body["authorization_servers"]] == [
            "http://localhost:8100"
        ]
        monkeypatch.setattr(mcp_app, "_oauth_app", None)

    @override_settings(MCP_SERVER_HOST=None, BASE_URL="")
    def test_config_reports_no_url_when_unknown(self, auth_client, no_env_base):
        result = auth_client.get("/mcp/config/").data["result"]
        assert result["mcp_url"] is None

    @override_settings(MCP_SERVER_HOST=None, BASE_URL="http://localhost:8000")
    def test_self_hosted_default_names_no_futureagi_host(
        self, auth_client, no_env_base
    ):
        page_url = auth_client.get("/mcp/config/").data["result"]["mcp_url"]
        assert "futureagi.com" not in page_url
