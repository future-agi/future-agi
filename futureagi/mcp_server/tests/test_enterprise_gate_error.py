"""MCP reports an Enterprise gate with its stable code (TH-8084).

A generated tool that hits a refused organization, workspace or member
creation gets ENTERPRISE_FEATURE_REQUIRED with the enterprise_gate block as
details; never a rate-limit error. Other HTTP errors keep HTTP_<status>.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from mcp_server import mcp_app, usage_helpers
from mcp_server.api_executor import APIExecutionError

GATE = {
    "feature": "members",
    "edition": "community",
    "limit": 3,
    "current": 3,
    "requested": 1,
    "license_state": "missing",
    "contact": "sales@futureagi.com",
    "activation_route": "/dashboard/settings/ee-licenses",
}


@pytest.fixture
def call_tool(monkeypatch):
    org = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(
        mcp_app,
        "_current_context",
        lambda: SimpleNamespace(organization=org, user=None, workspace=None),
    )
    monkeypatch.setattr(
        mcp_app, "_enabled_tools_for_context", lambda ctx: (None, {"whoami"})
    )
    monkeypatch.setattr(
        "mcp_server.rate_limiter.enforce_commercial_rate_limit", lambda o: None
    )
    monkeypatch.setattr(usage_helpers, "get_or_create_session", lambda *a, **k: None)
    monkeypatch.setattr(usage_helpers, "record_usage", lambda **k: None)
    monkeypatch.setattr(usage_helpers, "update_session_counters", lambda *a, **k: None)

    def _call(error: APIExecutionError):
        async def _raise(*args, **kwargs):
            raise error

        monkeypatch.setattr(mcp_app.executor, "execute", _raise)
        return asyncio.run(mcp_app.call_generated_tool("whoami", {}))

    return _call


def test_enterprise_gate_keeps_its_stable_code(call_tool):
    """AC-08: MCP clients get ENTERPRISE_FEATURE_REQUIRED and the gate block."""
    body = {
        "status": False,
        "code": "ENTERPRISE_FEATURE_REQUIRED",
        "message": "Community includes up to 3 organization members.",
        "upgrade_required": True,
        "enterprise_gate": GATE,
    }
    result = call_tool(APIExecutionError(body["message"], status_code=402, data=body))
    error = result.structuredContent["error"]
    assert result.isError is True
    assert error["code"] == "ENTERPRISE_FEATURE_REQUIRED"
    assert error["details"] == GATE
    assert error["code"] != "RATE_LIMITED"


def test_other_http_errors_are_unchanged(call_tool):
    """AC-13: non-gate errors keep HTTP_<status>."""
    result = call_tool(
        APIExecutionError("nope", status_code=402, data={"code": "PAYMENT_REQUIRED"})
    )
    assert result.structuredContent["error"]["code"] == "HTTP_402"
