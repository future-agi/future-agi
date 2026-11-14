import uuid

import pytest
from django.db import IntegrityError

from mcp_server.models.connection import MCPConnection
from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.models.session import MCPSession
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.models.usage import MCPUsageRecord
from tfc.middleware.workspace_context import set_workspace_context


class TestMCPConnection:
    def test_create_connection(self, user, workspace):
        set_workspace_context(
            workspace=workspace, organization=user.organization, user=user
        )

        conn = MCPConnection(
            user=user,
            organization=user.organization,
            workspace=workspace,
        )
        conn.save()

        assert conn.id is not None
        assert conn.connection_mode == "remote"
        assert conn.is_active is True

    def test_unique_per_user_workspace(self, user, workspace):
        set_workspace_context(
            workspace=workspace, organization=user.organization, user=user
        )

        MCPConnection(
            user=user,
            organization=user.organization,
            workspace=workspace,
        ).save()

        with pytest.raises(IntegrityError):
            MCPConnection(
                user=user,
                organization=user.organization,
                workspace=workspace,
            ).save()

    def test_soft_delete(self, mcp_connection):
        mcp_connection.delete()
        assert mcp_connection.deleted is True
        assert MCPConnection.objects.filter(id=mcp_connection.id).count() == 0
        assert MCPConnection.all_objects.filter(id=mcp_connection.id).count() == 1


class TestMCPToolGroupConfig:
    def test_default_groups(self, mcp_connection):
        config = mcp_connection.tool_config
        assert "context" in config.enabled_groups
        assert "evaluations" in config.enabled_groups
        assert "datasets" in config.enabled_groups
        assert "observability" in config.enabled_groups

    def test_update_groups(self, mcp_connection):
        config = mcp_connection.tool_config
        config.enabled_groups = ["context"]
        config.save()
        config.refresh_from_db()
        assert config.enabled_groups == ["context"]

    def test_update_groups_to_empty_list(self, mcp_connection):
        config = mcp_connection.tool_config
        config.enabled_groups = []
        config.save()
        config.refresh_from_db()
        assert config.enabled_groups == []


class TestMCPSession:
    def test_create_session(self, mcp_session):
        assert mcp_session.status == "active"
        assert mcp_session.transport == "stdio"
        assert mcp_session.tool_call_count == 0

    def test_session_counters(self, mcp_session):
        mcp_session.tool_call_count = 5
        mcp_session.error_count = 1
        mcp_session.save()
        mcp_session.refresh_from_db()
        assert mcp_session.tool_call_count == 5
        assert mcp_session.error_count == 1


class TestMCPUsageRecord:
    def test_create_usage_record(self, mcp_session):
        record = MCPUsageRecord.objects.create(
            session=mcp_session,
            organization=mcp_session.organization,
            workspace=mcp_session.workspace,
            user=mcp_session.user,
            tool_name="whoami",
            tool_group="context",
            response_status="success",
            latency_ms=42,
        )

        assert record.id is not None
        assert record.tool_name == "whoami"
        assert record.latency_ms == 42


class TestMCPOAuthClient:
    def test_create_oauth_client(self, db):
        client = MCPOAuthClient.objects.create(
            client_id="test-client-id",
            client_secret_hash="hashed-secret",
            name="Test Client",
            redirect_uris=["http://localhost:3000/callback"],
        )

        assert client.is_active is True
        assert client.redirect_uris == ["http://localhost:3000/callback"]
