"""
End-to-end infrastructure tests for the MCP server.

Tests authentication, sessions, rate limiting, tool groups,
usage recording, connections, and tool listing through the
actual HTTP endpoints.
"""

import time
from unittest.mock import patch

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from mcp_server.constants import DEFAULT_TOOL_GROUPS, TOOL_GROUPS
from mcp_server.exceptions import RateLimitExceededError
from mcp_server.models.connection import MCPConnection
from mcp_server.models.session import MCPSession
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.models.usage import MCPUsageRecord

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOOL_CALL_URL = "/mcp/internal/tool-call/"
TOOL_LIST_URL = "/mcp/internal/tools/"
HEALTH_URL = "/mcp/health/"
TOOL_GROUPS_URL = "/mcp/config/tool-groups/"


def _call_tool(client, tool_name, params=None, session_id=None):
    """Convenience wrapper for calling a tool via the internal endpoint."""
    payload = {"tool_name": tool_name, "params": params or {}}
    if session_id:
        payload["session_id"] = session_id
    return client.post(TOOL_CALL_URL, payload, format="json")


# ---------------------------------------------------------------------------
# 1. Authentication E2E
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAuthenticationE2E:
    """Verify that auth is enforced on protected endpoints."""

    def test_unauthenticated_tool_call_returns_403(self):
        client = APIClient()
        response = client.post(
            TOOL_CALL_URL,
            {"tool_name": "whoami", "params": {}},
            format="json",
        )
        assert response.status_code in (401, 403)

    def test_unauthenticated_tool_list_returns_403(self):
        client = APIClient()
        response = client.get(TOOL_LIST_URL)
        assert response.status_code in (401, 403)

    def test_authenticated_tool_call_succeeds(self, auth_client):
        response = _call_tool(auth_client, "whoami")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] is True
        assert "session_id" in data

    def test_health_check_no_auth(self):
        client = APIClient()
        response = client.get(HEALTH_URL)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] is True
        assert data["result"]["healthy"] is True


# ---------------------------------------------------------------------------
# 2. Session Management E2E
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSessionManagementE2E:
    """Verify session creation, reuse, and counter tracking."""

    def test_first_call_creates_session(self, auth_client):
        response = _call_tool(auth_client, "whoami")
        assert response.status_code == 200
        data = response.json()

        session_id = data["session_id"]
        assert session_id is not None

        # Verify session exists in the database
        assert MCPSession.objects.filter(id=session_id).exists()

    def test_session_reuse(self, auth_client):
        """Passing session_id from a previous call should reuse the session."""
        resp1 = _call_tool(auth_client, "whoami")
        session_id = resp1.json()["session_id"]

        resp2 = _call_tool(auth_client, "whoami", session_id=session_id)
        assert resp2.status_code == 200
        assert resp2.json()["session_id"] == session_id

        # Only one session should exist for this connection
        conn = MCPConnection.objects.first()
        assert MCPSession.objects.filter(connection=conn).count() == 1

    def test_session_counter_increments(self, auth_client):
        """tool_call_count should increment with each tool call."""
        resp = _call_tool(auth_client, "whoami")
        session_id = resp.json()["session_id"]

        for _ in range(4):
            _call_tool(auth_client, "whoami", session_id=session_id)

        session = MCPSession.objects.get(id=session_id)
        assert session.tool_call_count == 5

    def test_error_counter_increments(self, auth_client):
        """error_count should increment when a tool call produces an error."""
        # Call a tool that does not exist -> 404 from the view, no session counter update.
        # Instead, call whoami first to get a session, then call a nonexistent tool
        # with the session_id. The view returns 404 before session handling, so we
        # need a tool that exists but returns an error result.
        # We mock the tool's run() to return an error result.
        from ai_tools.base import ToolResult
        from ai_tools.registry import registry

        tool = registry.get("whoami")
        original_run = tool.run

        def _error_run(raw_params, context):
            return ToolResult.error("forced error for testing")

        resp = _call_tool(auth_client, "whoami")
        session_id = resp.json()["session_id"]

        try:
            tool.run = _error_run
            _call_tool(auth_client, "whoami", session_id=session_id)
            _call_tool(auth_client, "whoami", session_id=session_id)
        finally:
            tool.run = original_run

        session = MCPSession.objects.get(id=session_id)
        # 1 success + 2 errors = 3 total calls, 2 errors
        assert session.tool_call_count == 3
        assert session.error_count == 2

    def test_new_session_per_connection(self, auth_client):
        """Calls without session_id should each create a new session."""
        resp1 = _call_tool(auth_client, "whoami")
        resp2 = _call_tool(auth_client, "whoami")

        sid1 = resp1.json()["session_id"]
        sid2 = resp2.json()["session_id"]
        assert sid1 != sid2
        assert MCPSession.objects.count() == 2

    def test_session_last_activity_updates(self, auth_client):
        """last_activity_at should be updated on subsequent calls."""
        resp = _call_tool(auth_client, "whoami")
        session_id = resp.json()["session_id"]

        session = MCPSession.objects.get(id=session_id)
        first_activity = session.last_activity_at

        # Make another call on the same session
        _call_tool(auth_client, "whoami", session_id=session_id)

        session.refresh_from_db()
        # last_activity_at uses auto_now so it updates on every save
        assert session.last_activity_at >= first_activity


# ---------------------------------------------------------------------------
# 3. Usage Recording E2E
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUsageRecordingE2E:
    """Verify MCPUsageRecord creation and field population."""

    def test_successful_call_records_usage(self, auth_client):
        _call_tool(auth_client, "whoami")

        records = MCPUsageRecord.objects.all()
        assert records.count() == 1
        assert records[0].response_status == "success"

    def test_error_call_records_usage(self, auth_client):
        """A tool call that returns an error result should record status='error'."""
        from ai_tools.base import ToolResult
        from ai_tools.registry import registry

        tool = registry.get("whoami")
        original_run = tool.run

        def _error_run(raw_params, context):
            return ToolResult.error("forced error for testing")

        try:
            tool.run = _error_run
            _call_tool(auth_client, "whoami")
        finally:
            tool.run = original_run

        record = MCPUsageRecord.objects.first()
        assert record is not None
        assert record.response_status == "error"

    def test_usage_records_tool_name(self, auth_client):
        _call_tool(auth_client, "whoami")

        record = MCPUsageRecord.objects.first()
        assert record is not None
        assert record.tool_name == "whoami"

    def test_usage_records_latency(self, auth_client):
        _call_tool(auth_client, "whoami")

        record = MCPUsageRecord.objects.first()
        assert record is not None
        assert record.latency_ms >= 0

    def test_multiple_calls_create_multiple_records(self, auth_client):
        resp = _call_tool(auth_client, "whoami")
        session_id = resp.json()["session_id"]

        _call_tool(auth_client, "whoami", session_id=session_id)
        _call_tool(auth_client, "whoami", session_id=session_id)

        assert MCPUsageRecord.objects.count() == 3

    def test_usage_records_request_params(self, auth_client):
        params = {"limit": 5}
        _call_tool(auth_client, "list_datasets", params=params)

        record = MCPUsageRecord.objects.first()
        assert record is not None
        assert record.tool_name == "list_datasets"
        assert record.request_params == params


# ---------------------------------------------------------------------------
# 4. Tool Groups E2E
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestToolGroupsE2E:
    """Verify tool group enable/disable behaviour."""

    def test_all_groups_enabled_by_default(self, auth_client, mcp_connection):
        config = mcp_connection.tool_config
        assert set(config.enabled_groups) == set(DEFAULT_TOOL_GROUPS)

    def test_disable_group_blocks_tool(self, auth_client, user, workspace):
        """Disabling the 'datasets' group should prevent list_datasets calls."""
        # Ensure a connection exists by making an initial call
        _call_tool(auth_client, "whoami")

        conn = MCPConnection.objects.get(user=user, workspace=workspace, deleted=False)
        config = conn.tool_config

        # Remove 'datasets' from enabled groups
        config.enabled_groups = [g for g in config.enabled_groups if g != "datasets"]
        config.save()

        response = _call_tool(auth_client, "list_datasets")
        assert response.status_code == 403
        assert "disabled" in response.json().get("error", "").lower()

    def test_enable_group_allows_tool(self, auth_client, user, workspace):
        """With only 'context' enabled, whoami should still work."""
        _call_tool(auth_client, "whoami")

        conn = MCPConnection.objects.get(user=user, workspace=workspace, deleted=False)
        config = conn.tool_config
        config.enabled_groups = ["context"]
        config.save()

        response = _call_tool(auth_client, "whoami")
        assert response.status_code == 200
        assert response.json()["status"] is True

    def test_update_groups_via_api(self, auth_client, user, workspace):
        """PUT /mcp/config/tool-groups/ should persist the group selection."""
        # First ensure connection exists
        _call_tool(auth_client, "whoami")

        subset = ["context", "evaluations"]
        response = auth_client.put(
            TOOL_GROUPS_URL,
            {"enabled_groups": subset},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] is True
        assert set(data["result"]["enabled_groups"]) == set(subset)

        # Verify in DB
        conn = MCPConnection.objects.get(user=user, workspace=workspace, deleted=False)
        config = conn.tool_config
        assert set(config.enabled_groups) == set(subset)

    def test_invalid_group_rejected(self, auth_client):
        """PUT with an invalid group name should return 400."""
        # Ensure connection exists
        _call_tool(auth_client, "whoami")

        response = auth_client.put(
            TOOL_GROUPS_URL,
            {"enabled_groups": ["context", "nonexistent_group"]},
            format="json",
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# 5. Rate Limiting E2E
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRateLimitingE2E:
    """Verify rate limiting enforcement on tool calls."""

    def test_rate_limit_allows_normal_usage(self, auth_client):
        """A few calls should all succeed without rate limiting."""
        for _ in range(3):
            response = _call_tool(auth_client, "whoami")
            assert response.status_code == 200

    def test_rate_limit_returns_429(self, auth_client):
        """When rate limit is exceeded, the endpoint should return 429 with Retry-After."""
        with patch(
            "mcp_server.views.transport.check_rate_limit",
            side_effect=RateLimitExceededError(
                "Rate limit exceeded: 20 calls/minute", retry_after=42
            ),
        ):
            response = _call_tool(auth_client, "whoami")

        assert response.status_code == 429
        assert "Retry-After" in response
        assert response["Retry-After"] == "42"
        data = response.json()
        assert data["status"] is False
        assert "rate limit" in data.get("error", "").lower()


# ---------------------------------------------------------------------------
# 6. Connection Management E2E
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConnectionManagementE2E:
    """Verify MCPConnection auto-creation and reuse."""

    def test_first_call_creates_connection(self, auth_client, user, workspace):
        assert MCPConnection.objects.count() == 0

        _call_tool(auth_client, "whoami")

        assert MCPConnection.objects.count() == 1
        conn = MCPConnection.objects.first()
        assert conn.user == user
        assert conn.workspace == workspace

    def test_connection_reused_across_calls(self, auth_client):
        _call_tool(auth_client, "whoami")
        _call_tool(auth_client, "whoami")

        assert MCPConnection.objects.count() == 1

    def test_connection_has_tool_config(self, auth_client):
        _call_tool(auth_client, "whoami")

        conn = MCPConnection.objects.first()
        assert conn is not None

        # tool_config is a OneToOneField accessed via related name
        config = MCPToolGroupConfig.objects.filter(connection=conn)
        assert config.exists()
        assert set(config.first().enabled_groups) == set(DEFAULT_TOOL_GROUPS)


# ---------------------------------------------------------------------------
# 7. Tool List E2E
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestToolListE2E:
    """Verify the tool listing endpoint."""

    def test_list_all_tools(self, auth_client):
        response = auth_client.get(TOOL_LIST_URL)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] is True

        tools = data["result"]["tools"]
        assert len(tools) > 0

        # Each tool should have these fields
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "category" in tool

    def test_tool_count_matches_registry(self, auth_client):
        from ai_tools.registry import registry

        response = auth_client.get(TOOL_LIST_URL)
        data = response.json()

        returned_count = data["result"]["total"]
        # The returned count should match the number of tools whose groups
        # are all enabled (which is all of them by default).
        all_tools = registry.list_all()
        # Some tools may belong to categories not in CATEGORY_TO_GROUP;
        # the endpoint only returns tools with a valid enabled group.
        from mcp_server.constants import CATEGORY_TO_GROUP

        expected = sum(
            1
            for t in all_tools
            if CATEGORY_TO_GROUP.get(t.category) in DEFAULT_TOOL_GROUPS
        )
        assert returned_count == expected

    def test_disabled_tools_not_in_list(self, auth_client, user, workspace):
        """Disabling a group should remove its tools from the listing."""
        # First call to create connection
        _call_tool(auth_client, "whoami")

        conn = MCPConnection.objects.get(user=user, workspace=workspace, deleted=False)
        config = conn.tool_config
        config.enabled_groups = ["context"]
        config.save()

        response = auth_client.get(TOOL_LIST_URL)
        data = response.json()
        tools = data["result"]["tools"]
        tool_names = {t["name"] for t in tools}

        # 'list_datasets' belongs to 'datasets' group, should NOT be listed
        assert "list_datasets" not in tool_names
        # 'whoami' belongs to 'context' group, should be listed
        assert "whoami" in tool_names
