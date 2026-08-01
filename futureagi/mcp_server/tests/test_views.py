import uuid

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from mcp_server.models.connection import MCPConnection
from mcp_server.models.session import MCPSession
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.models.usage import MCPUsageRecord


AUTH_REQUIRED_STATUS_CODES = (401, 403)


class TestMCPHealthView:
    def test_health_unauthenticated(self, db):
        client = APIClient()
        response = client.get("/mcp/health/")
        assert response.status_code == 200
        assert response.data["status"] is True
        assert response.data["result"]["healthy"] is True


class TestMCPToolCallView:
    def test_tool_call_whoami(self, auth_client, user, workspace):
        response = auth_client.post(
            "/mcp/internal/tool-call/",
            {"tool_name": "whoami", "params": {}},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["status"] is True
        assert user.email in response.data["result"]["content"]

    def test_tool_call_missing_name(self, auth_client):
        response = auth_client.post(
            "/mcp/internal/tool-call/",
            {"params": {}},
            format="json",
        )
        assert response.status_code == 400

    def test_tool_call_nonexistent_tool(self, auth_client):
        response = auth_client.post(
            "/mcp/internal/tool-call/",
            {"tool_name": "nonexistent_tool", "params": {}},
            format="json",
        )
        assert response.status_code == 404

    def test_tool_call_creates_session(self, auth_client, user, workspace):
        response = auth_client.post(
            "/mcp/internal/tool-call/",
            {"tool_name": "whoami", "params": {}},
            format="json",
        )
        assert response.status_code == 200
        assert "session_id" in response.data

        session_id = response.data["session_id"]
        session = MCPSession.objects.get(id=session_id)
        assert session.tool_call_count == 1

    def test_tool_call_reuses_session(self, auth_client, user, workspace):
        # First call
        resp1 = auth_client.post(
            "/mcp/internal/tool-call/",
            {"tool_name": "whoami", "params": {}},
            format="json",
        )
        session_id = resp1.data["session_id"]

        # Second call with same session
        resp2 = auth_client.post(
            "/mcp/internal/tool-call/",
            {"tool_name": "whoami", "params": {}, "session_id": session_id},
            format="json",
        )
        assert resp2.data["session_id"] == session_id

        session = MCPSession.objects.get(id=session_id)
        assert session.tool_call_count == 2

    def test_tool_call_records_usage(self, auth_client, user, workspace):
        auth_client.post(
            "/mcp/internal/tool-call/",
            {"tool_name": "whoami", "params": {}},
            format="json",
        )

        records = MCPUsageRecord.objects.filter(tool_name="whoami")
        assert records.count() == 1
        assert records.first().response_status == "success"

    def test_tool_call_list_datasets(self, auth_client):
        response = auth_client.post(
            "/mcp/internal/tool-call/",
            {"tool_name": "list_datasets", "params": {}},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["status"] is True
        assert "Datasets" in response.data["result"]["content"]

    def test_tool_call_unauthenticated(self, db):
        client = APIClient()
        response = client.post(
            "/mcp/internal/tool-call/",
            {"tool_name": "whoami", "params": {}},
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES


class TestMCPToolListView:
    def test_list_tools(self, auth_client):
        response = auth_client.get("/mcp/internal/tools/")
        assert response.status_code == 200
        assert response.data["status"] is True
        assert response.data["result"]["total"] > 0

        tool_names = [t["name"] for t in response.data["result"]["tools"]]
        assert "whoami" in tool_names

    def test_list_tools_unauthenticated(self, db):
        client = APIClient()
        response = client.get("/mcp/internal/tools/")
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES


class TestMCPConfigView:
    def test_get_config_creates_default(self, auth_client, user, workspace):
        response = auth_client.get("/mcp/config/")
        assert response.status_code == 200
        assert response.data["status"] is True
        assert "tool_config" in response.data["result"]

    def test_update_config(self, auth_client, user, workspace):
        # Get first to create default
        auth_client.get("/mcp/config/")

        response = auth_client.put(
            "/mcp/config/",
            {"connection_mode": "stdio", "is_active": True},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["result"]["connection_mode"] == "stdio"


class TestMCPToolGroupsView:
    def test_get_default_groups(self, auth_client):
        response = auth_client.get("/mcp/config/tool-groups/")
        assert response.status_code == 200
        assert "context" in response.data["result"]["enabled_groups"]

    def test_update_groups(self, auth_client, user, workspace):
        response = auth_client.put(
            "/mcp/config/tool-groups/",
            {"enabled_groups": ["context", "datasets"]},
            format="json",
        )
        assert response.status_code == 200
        assert set(response.data["result"]["enabled_groups"]) == {"context", "datasets"}

    def test_invalid_group(self, auth_client):
        response = auth_client.put(
            "/mcp/config/tool-groups/",
            {"enabled_groups": ["invalid_group"]},
            format="json",
        )
        assert response.status_code == 400


class TestMCPSessionListView:
    def test_list_sessions_empty(self, auth_client):
        response = auth_client.get("/mcp/sessions/")
        assert response.status_code == 200
        assert response.data["status"] is True

    def test_list_sessions_with_data(self, auth_client, mcp_session):
        response = auth_client.get("/mcp/sessions/")
        assert response.status_code == 200
        assert len(response.data["result"]) >= 1

    def test_list_sessions_status_filter(self, auth_client, mcp_session):
        response = auth_client.get("/mcp/sessions/?status=active")
        assert response.status_code == 200
        assert response.data["status"] is True
        assert len(response.data["result"]) >= 1
        assert all(row["status"] == "active" for row in response.data["result"])

    def test_revoke_session(self, auth_client, mcp_session):
        response = auth_client.delete(f"/mcp/sessions/{mcp_session.id}/")
        assert response.status_code == 200

        mcp_session.refresh_from_db()
        assert mcp_session.status == "revoked"


class TestMCPAnalyticsViews:
    def test_analytics_summary(self, auth_client):
        response = auth_client.get("/mcp/analytics/summary/")
        assert response.status_code == 200
        assert "total_calls" in response.data["result"]

    def test_analytics_tools(self, auth_client):
        response = auth_client.get("/mcp/analytics/tools/")
        assert response.status_code == 200

    def test_analytics_timeline(self, auth_client):
        response = auth_client.get("/mcp/analytics/timeline/")
        assert response.status_code == 200
