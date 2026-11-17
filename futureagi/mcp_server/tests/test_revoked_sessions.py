"""Regression coverage for explicit session revocation."""

from unittest.mock import Mock

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from mcp_server.models.session import MCPSession
from mcp_server.models.usage import MCPUsageRecord
from mcp_server.usage_helpers import get_or_create_session
from mcp_server.views import transport

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("transport_name", ["stdio", "streamable_http"])
def test_explicit_revoked_session_is_denied_without_replacement(
    mcp_session, transport_name
):
    mcp_session.status = "revoked"
    mcp_session.ended_at = timezone.now()
    mcp_session.transport = transport_name
    mcp_session.save(update_fields=["status", "ended_at", "transport"])
    # Even when a reusable session exists, an explicit revoked ID must fail.
    MCPSession.objects.create(
        connection=mcp_session.connection,
        user=mcp_session.user,
        organization=mcp_session.organization,
        workspace=mcp_session.workspace,
        transport=transport_name,
    )
    before = list(MCPSession.objects.order_by("id").values())

    with pytest.raises(PermissionDenied) as denied:
        get_or_create_session(
            mcp_session.connection,
            session_id=mcp_session.id,
            transport=transport_name,
        )

    assert denied.value.get_codes() == "session_revoked"
    assert list(MCPSession.objects.order_by("id").values()) == before


@pytest.mark.parametrize("session_status", ["active", "disconnected"])
def test_explicit_non_revoked_session_remains_reusable(mcp_session, session_status):
    mcp_session.status = session_status
    mcp_session.save(update_fields=["status"])
    count = MCPSession.objects.count()

    session = get_or_create_session(
        mcp_session.connection, session_id=mcp_session.id
    )

    mcp_session.refresh_from_db()
    assert session.pk == mcp_session.pk
    assert mcp_session.status == "active"
    assert MCPSession.objects.count() == count


def test_tool_call_rejects_same_session_after_revoke(auth_client, monkeypatch):
    execute = Mock(return_value={"ok": True})
    monkeypatch.setattr(transport.executor, "execute_sync", execute)
    monkeypatch.setattr(transport, "check_rate_limit", Mock())
    monkeypatch.setattr(transport, "get_rate_limit_tier", Mock(return_value="test"))

    first = auth_client.post(
        "/mcp/internal/tool-call/",
        {"tool_name": "whoami", "params": {}},
        format="json",
    )
    assert first.status_code == 200
    execute.assert_called_once()
    session_id = first.data["session_id"]

    revoked = auth_client.delete(f"/mcp/sessions/{session_id}/")
    assert revoked.status_code == 200
    assert revoked.data["result"]["message"] == "Session revoked"
    session = MCPSession.objects.get(pk=session_id)
    assert session.status == "revoked"
    assert session.ended_at is not None
    assert session.tool_call_count == 1
    before = list(MCPSession.objects.order_by("id").values())
    usage_count = MCPUsageRecord.objects.count()
    assert usage_count == 1
    execute.reset_mock()

    for _ in range(2):
        response = auth_client.post(
            "/mcp/internal/tool-call/",
            {"tool_name": "whoami", "params": {}, "session_id": session_id},
            format="json",
        )
        assert response.status_code == 403
        assert "session_id" not in response.data
        execute.assert_not_called()
        assert list(MCPSession.objects.order_by("id").values()) == before
        assert MCPUsageRecord.objects.count() == usage_count
