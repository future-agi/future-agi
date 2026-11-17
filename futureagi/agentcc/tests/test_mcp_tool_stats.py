"""Per-tool MCP stats on the Tools list: from the org's own tool-call log.

Each tool the mcp-tools read lists carries ``stats``: its calls of the last 30
days in ``AgentccToolCall`` (what Logs > Tool calls reads), never the
gateway's in-memory counters. A call counts for a tool by its server and
upstream name, so a call made through a virtual server's alias counts too.
"""

import datetime
import uuid

import pytest
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import Organization
from agentcc.models import AgentccToolCall
from agentcc.tests.test_mcp_admin_api import V1_STATUS, faked_gateway, seed_mcp

GATEWAY = "/agentcc/gateways/default"

NO_CALLS = {
    "call_count": 0,
    "error_count": 0,
    "avg_latency_ms": None,
    "error_rate": 0,
    "last_called": None,
    "window_days": 30,
}

TOOLS = {
    "tools": [
        {"name": "alpha_echo", "server_id": "alpha", "upstream_name": "echo"},
        {"name": "alpha_search", "server_id": "alpha", "upstream_name": "search"},
    ]
}


@pytest.fixture
def fake_gateway():
    with faked_gateway() as gateway:
        gateway.on("GET", "/-/mcp/status", body=V1_STATUS)
        gateway.on("GET", "/-/mcp/tools", body=TOOLS)
        yield gateway


@pytest.fixture
def org(user):
    seed_mcp(user.organization, ["alpha", "beta"])
    return user.organization


def call(organization, ago, outcome="success", latency_ms=10, **fields):
    """One stored tool call of alpha/echo, ``ago`` before now."""
    event_id = uuid.uuid4().hex
    values = {
        "server_id": "alpha",
        "tool": "alpha_echo",
        "upstream_tool": "echo",
        "started_at": timezone.now() - ago if ago is not None else None,
        "outcome": outcome,
        "latency_ms": latency_ms,
        **fields,
    }
    return AgentccToolCall.objects.create(
        organization=organization,
        event_id=event_id,
        tool_call_id=event_id,
        **values,
    )


def stats_by_name(auth_client):
    response = auth_client.get(f"{GATEWAY}/mcp-tools/")
    assert response.status_code == 200, response.json()
    return {tool["name"]: tool["stats"] for tool in response.json()["result"]}


@pytest.mark.integration
@pytest.mark.api
class TestMCPToolStats:
    def test_counts_calls_errors_latency_and_last_called(
        self, auth_client, org, fake_gateway
    ):
        hour = datetime.timedelta(hours=1)
        call(org, 9 * hour, "success", 10)
        # Through a virtual server that exposes the tool under another name.
        call(org, 8 * hour, "success", 30, tool="team_echo_alias")
        call(org, 7 * hour, "is_error", 20)
        call(org, 6 * hour, "error", None)
        call(org, 5 * hour, "timeout", 40)
        newest = call(org, 4 * hour, "cancelled", 50)
        # Refused by the org's own policy: never reached the tool.
        call(org, 1 * hour, "denied", 5)
        # No start time: outside every window.
        call(org, None, "error", 70)
        # The same upstream name on another server is another tool.
        call(org, 2 * hour, "error", 90, server_id="beta", tool="beta_echo")

        stats = stats_by_name(auth_client)

        assert stats["alpha_echo"]["call_count"] == 6
        assert stats["alpha_echo"]["error_count"] == 3
        # The call with no latency is left out of the average.
        assert stats["alpha_echo"]["avg_latency_ms"] == 30.0
        assert stats["alpha_echo"]["error_rate"] == 0.5
        assert parse_datetime(stats["alpha_echo"]["last_called"]) == newest.started_at
        assert stats["alpha_echo"]["window_days"] == 30
        assert stats["alpha_search"] == NO_CALLS

    def test_window_is_the_last_30_days(self, auth_client, org, fake_gateway):
        window = datetime.timedelta(days=30)
        minute = datetime.timedelta(minutes=1)
        inside = call(org, window - minute, "error", 10)
        call(org, window + minute, "success", 90)

        stats = stats_by_name(auth_client)["alpha_echo"]

        assert stats["call_count"] == 1
        assert stats["error_count"] == 1
        assert stats["avg_latency_ms"] == 10.0
        assert stats["error_rate"] == 1.0
        assert parse_datetime(stats["last_called"]) == inside.started_at

    def test_another_orgs_calls_never_count(self, auth_client, org, fake_gateway):
        other = Organization.objects.create(name="Other MCP Stats Org")
        seed_mcp(other, ["alpha"])
        hour = datetime.timedelta(hours=1)
        for outcome in ("success", "error", "timeout"):
            call(other, hour, outcome, 500)
        call(other, hour, "success", 500, upstream_tool="search")
        mine = call(org, 3 * hour, "success", 20)

        stats = stats_by_name(auth_client)

        assert stats["alpha_echo"] == {
            "call_count": 1,
            "error_count": 0,
            "avg_latency_ms": 20.0,
            "error_rate": 0,
            "last_called": stats["alpha_echo"]["last_called"],
            "window_days": 30,
        }
        assert parse_datetime(stats["alpha_echo"]["last_called"]) == mine.started_at
        assert stats["alpha_search"] == NO_CALLS

    def test_gateway_counters_are_never_shown(self, auth_client, org, fake_gateway):
        # The gateway's in-memory counters reset on restart and are not kept
        # per organization: a value it sends is replaced.
        counted = {
            "call_count": 99,
            "error_count": 7,
            "avg_latency_ms": 12.5,
            "error_rate": 0.07,
            "last_called": "2026-11-01T00:00:00Z",
        }
        fake_gateway.on(
            "GET",
            "/-/mcp/tools",
            body={"tools": [{**tool, "stats": counted} for tool in TOOLS["tools"]]},
        )

        stats = stats_by_name(auth_client)

        assert stats == {"alpha_echo": NO_CALLS, "alpha_search": NO_CALLS}
