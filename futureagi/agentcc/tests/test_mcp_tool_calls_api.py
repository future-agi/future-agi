"""M16 MCP tool-call API tests."""

import json
from pathlib import Path

import pytest

from agentcc.models import AgentccAPIKey, AgentccToolCall
from agentcc.services.log_ingestion import ingest_request_logs

EXAMPLES_DIR = (
    Path(__file__).resolve().parents[3] / "api_contracts" / "gateway" / "examples"
)
GOLDEN_KEY_ID = "key_7f3a"


def tool_call_entry(organization, **changes):
    record = json.loads((EXAMPLES_DIR / "mcp-tool-call-v1.json").read_text())
    record["org_id"] = str(organization.id)
    record.update(changes)
    return record


@pytest.fixture
def gateway_key(organization):
    return AgentccAPIKey.objects.create(
        gateway_key_id=GOLDEN_KEY_ID,
        name="golden-key",
        organization=organization,
    )


def ingest(organization, **changes):
    return ingest_request_logs([tool_call_entry(organization, **changes)])


@pytest.mark.integration
class TestMCPToolCallAPI:
    def test_filters_run_server_outcome_billing_mode(
        self, auth_client, gateway_key, organization
    ):
        ingest(
            organization,
            event_id="tc-run-managed",
            tool_call_id="tc-run-managed",
            api_key_id="api-key-a",
            agent={"run_id": "run-filter", "agent_id": "agent-a"},
            server_id="server-a",
            tool="tool-a",
            outcome="success",
            billing_mode="managed",
        )
        ingest(
            organization,
            event_id="tc-run-byok",
            tool_call_id="tc-run-byok",
            api_key_id="api-key-b",
            agent={"run_id": "run-filter", "agent_id": "agent-b"},
            server_id="server-b",
            tool="tool-b",
            outcome="denied",
            billing_mode="byok",
        )
        ingest(
            organization,
            event_id="tc-other",
            tool_call_id="tc-other",
            api_key_id="api-key-c",
            agent={"run_id": "run-other", "agent_id": "agent-c"},
            server_id="server-c",
            tool="tool-c",
            outcome="error",
            billing_mode="managed",
        )

        def event_ids(**params):
            response = auth_client.get("/agentcc/mcp/tool-calls/", params)
            assert response.status_code == 200, response.json()
            return {row["event_id"] for row in response.json()["results"]}

        assert event_ids(run_id="run-filter") == {"tc-run-managed", "tc-run-byok"}
        assert event_ids(server_id="server-a") == {"tc-run-managed"}
        assert event_ids(outcome="denied") == {"tc-run-byok"}
        assert event_ids(billing_mode="byok") == {"tc-run-byok"}
        assert event_ids(billing_mode="managed") == {"tc-run-managed", "tc-other"}
        assert event_ids(agent_id="agent-c") == {"tc-other"}
        assert event_ids(api_key_id="api-key-b") == {"tc-run-byok"}
        assert event_ids(tool="tool-a") == {"tc-run-managed"}

        for invalid in ("free", "managed,free", "byok,managed", ""):
            response = auth_client.get(
                "/agentcc/mcp/tool-calls/", {"billing_mode": invalid}
            )
            assert response.status_code == 400

    def test_time_filters_accept_iso_datetimes(
        self, auth_client, gateway_key, organization
    ):
        ingest(
            organization,
            event_id="tc-before",
            tool_call_id="tc-before",
            started_at="2026-10-16T09:10:11Z",
        )
        ingest(
            organization,
            event_id="tc-time",
            tool_call_id="tc-time",
            started_at="2026-10-16T09:10:12Z",
        )
        ingest(
            organization,
            event_id="tc-after",
            tool_call_id="tc-after",
            started_at="2026-10-16T09:10:14Z",
        )

        def event_ids(**params):
            response = auth_client.get("/agentcc/mcp/tool-calls/", params)
            assert response.status_code == 200, response.json()
            return {row["event_id"] for row in response.json()["results"]}

        assert event_ids(**{"from": "2026-10-16T09:10:12"}) == {
            "tc-time",
            "tc-after",
        }
        assert event_ids(**{"to": "2026-10-16T09:10:12Z"}) == {
            "tc-before",
            "tc-time",
        }
        assert event_ids(
            **{"from": "2026-10-16T09:10:12", "to": "2026-10-16T09:10:13Z"}
        ) == {"tc-time"}

        invalid = auth_client.get(
            "/agentcc/mcp/tool-calls/", {"from": "not-a-datetime"}
        )
        assert invalid.status_code == 400

    def test_other_org_not_visible(self, auth_client, gateway_key, organization, db):
        ingest(organization, event_id="tc-own", tool_call_id="tc-own")
        other = type(organization).objects.create(name="Other Tool Org")
        ingest(other, event_id="tc-other-org", tool_call_id="tc-other-org")

        response = auth_client.get("/agentcc/mcp/tool-calls/")
        assert response.status_code == 200
        assert {row["event_id"] for row in response.json()["results"]} == {"tc-own"}

    def test_detail_omits_absent_content(self, auth_client, gateway_key, organization):
        ingest(
            organization,
            event_id="tc-metadata",
            tool_call_id="tc-metadata",
            content_mode="metadata",
        )
        row = AgentccToolCall.objects.get(event_id="tc-metadata")

        response = auth_client.get(f"/agentcc/mcp/tool-calls/{row.pk}/")
        assert response.status_code == 200, response.json()
        data = response.json()["result"]
        assert data["event_id"] == "tc-metadata"
        assert "arguments" not in data
        assert "result" not in data

    def test_detail_other_org_not_visible(self, auth_client, gateway_key, organization):
        other = type(organization).objects.create(name="Other Detail Org")
        ingest(other, event_id="tc-other-detail", tool_call_id="tc-other-detail")
        row = AgentccToolCall.objects.get(event_id="tc-other-detail")

        response = auth_client.get(f"/agentcc/mcp/tool-calls/{row.pk}/")

        assert response.status_code == 404

    def test_cursor_pagination_handles_missing_started_at(
        self, auth_client, gateway_key, organization
    ):
        for index in range(2):
            record = tool_call_entry(
                organization,
                event_id=f"tc-null-{index}",
                tool_call_id=f"tc-null-{index}",
            )
            record.pop("started_at")
            ingest_request_logs([record])
        for index in range(2):
            ingest(
                organization,
                event_id=f"tc-dated-{index}",
                tool_call_id=f"tc-dated-{index}",
            )

        url, params, seen = "/agentcc/mcp/tool-calls/", {"limit": 1}, []
        while url:
            response = auth_client.get(url, params)
            assert response.status_code == 200, response.json()
            body = response.json()
            seen.extend(row["event_id"] for row in body["results"])
            url, params = body["next"], None

        assert set(seen) == {
            "tc-null-0",
            "tc-null-1",
            "tc-dated-0",
            "tc-dated-1",
        }
