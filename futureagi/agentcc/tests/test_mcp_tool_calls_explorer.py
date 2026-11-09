"""The advanced tool-call explorer's API (G16-SIEM, M37 increment).

The list takes latency bounds next to its server, tool, key, outcome and time
filters. Each row says how the call went through the gateway: its attempts,
its approval (read from the caller's own organization only) and which limit,
if any, stopped it. An export streams the filtered view as CSV or NDJSON with
the same closed, metadata-only columns as SIEM export, audited, whatever
content the org stored or consented to capture.
"""

import csv
import io
import json
import uuid

import pytest
from django.utils import timezone

from accounts.models.audit_log import AuditLog
from agentcc.models import AgentccMCPApproval
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.services.org_config_writer import write_config_version
from agentcc.tests.test_mcp_credentials import _org_with_owner
from agentcc.tests.test_mcp_tool_calls_api import tool_call_entry

pytestmark = pytest.mark.integration

LIST = "/agentcc/mcp/tool-calls/"
EXPORT = "/agentcc/mcp/tool-calls/export/"
CLOSED_COLUMNS = [
    "event_id",
    "tool_call_id",
    "request_id",
    "started_at",
    "api_key_id",
    "principal_kind",
    "virtual_server_id",
    "server_id",
    "tool",
    "upstream_tool",
    "tool_revision",
    "via",
    "protocol_version",
    "session_hash",
    "agent_id",
    "agent_run_id",
    "agent_step",
    "outcome",
    "is_error",
    "error_class",
    "error_origin",
    "error_code",
    "approval_id",
    "latency_ms",
    "upstream_latency_ms",
    "gateway_overhead_ms",
    "bytes_in",
    "bytes_out",
    "billing_mode",
    "credential_source",
    "upstream_cost_micros",
    "billed_cost_micros",
    "markup_micros",
    "dispatched",
    "budget_scope",
    "content_mode",
]


def ingest(organization, **changes):
    event_id = changes.pop("event_id", f"tc_{uuid.uuid4().hex[:20]}")
    record = tool_call_entry(
        organization, event_id=event_id, tool_call_id=event_id, **changes
    )
    ingest_request_logs([record])
    return event_id


def listed(client, **params):
    response = client.get(LIST, params)
    assert response.status_code == 200, response.content
    return {row["event_id"]: row for row in response.json()["results"]}


def test_latency_bounds_filter_the_list(auth_client, organization):
    fast = ingest(organization, latency_ms=50)
    medium = ingest(organization, latency_ms=500)
    slow = ingest(organization, latency_ms=5000)
    assert set(listed(auth_client, min_latency_ms=100)) == {medium, slow}
    assert set(listed(auth_client, max_latency_ms=1000)) == {fast, medium}
    assert set(listed(auth_client, min_latency_ms=100, max_latency_ms=1000)) == {medium}
    for bad in ({"min_latency_ms": "-1"}, {"max_latency_ms": "fast"}):
        assert auth_client.get(LIST, bad).status_code == 400


def test_rows_carry_attempts_approval_and_limit_outcomes(auth_client, organization):
    other_org, _ = _org_with_owner("Explorer other org")
    now = timezone.now()
    AgentccMCPApproval.objects.create(
        approval_id="appr_mine",
        organization=organization,
        virtual_server_id="vs_x",
        tool="fagi.web_search",
        args_hash="0" * 64,
        status="approved",
        requested_at=now,
        decided_at=now,
    )
    AgentccMCPApproval.objects.create(
        approval_id="appr_foreign",
        organization=other_org,
        virtual_server_id="vs_x",
        tool="fagi.web_search",
        args_hash="0" * 64,
        status="approved",
        requested_at=now,
    )
    ok = ingest(organization, approval_id="appr_mine")
    budget = ingest(
        organization,
        outcome="denied",
        dispatched=False,
        error_class="budget",
        error_origin="gateway",
        error_code="tool_budget_exhausted",
        approval_id="appr_foreign",
        upstream_latency_ms=0,
    )
    rows = listed(auth_client)

    outcomes = rows[ok]["outcomes"]
    assert [a["step"] for a in outcomes["attempts"]] == ["gateway", "upstream"]
    assert outcomes["attempts"][0]["result"] == "passed"
    assert outcomes["attempts"][1] == {
        "step": "upstream",
        "result": "ok",
        "latency_ms": 389,
        "credential_source": "managed",
        "fallback_from": "",
    }
    assert outcomes["approval"]["approval_id"] == "appr_mine"
    assert outcomes["approval"]["status"] == "approved"
    assert outcomes["approval"]["decided_at"] is not None
    assert outcomes["limits"] == {
        "stopped_by": None,
        "code": "",
        "budget_scope": "run_R1",
    }

    refused = rows[budget]["outcomes"]
    assert refused["attempts"] == [
        {"step": "gateway", "result": "denied", "code": "tool_budget_exhausted"}
    ]
    assert refused["approval"] == {"approval_id": "appr_foreign", "status": None}
    assert refused["limits"]["stopped_by"] == "budget"
    assert refused["limits"]["code"] == "tool_budget_exhausted"


def test_csv_export_streams_the_filtered_view_with_closed_columns(
    auth_client, organization, user
):
    write_config_version(
        organization,
        {"privacy": {"trace_content": True}},
        user=user,
        push=lambda config: True,
    )
    canary = f"canary-{uuid.uuid4().hex}"
    kept = ingest(
        organization,
        server_id="export-a",
        error_message=canary,
        arguments={"q": canary},
        result={"text": canary},
    )
    ingest(organization, server_id="export-b")
    response = auth_client.get(
        EXPORT, {"export_format": "csv", "server_id": "export-a"}
    )
    assert response.status_code == 200, response.content
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment" in response["Content-Disposition"]
    text = b"".join(response.streaming_content).decode()
    assert canary not in text
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == CLOSED_COLUMNS
    assert len(rows) == 2 and rows[1][0] == kept
    [entry] = AuditLog.objects.filter(
        organization=organization, action="agentcc.mcp_tool_calls_exported"
    )
    assert entry.actor_id == user.id
    assert entry.metadata["row_count"] == 1
    assert entry.metadata["format"] == "csv"
    assert entry.metadata["filters"] == {"server_id": "export-a"}


def test_json_export_is_ndjson_without_content(auth_client, organization):
    first = ingest(organization, arguments={"q": "kept-out"}, result={"r": "kept-out"})
    response = auth_client.get(EXPORT, {"export_format": "json"})
    assert response.status_code == 200
    lines = b"".join(response.streaming_content).decode().strip().splitlines()
    [row] = [json.loads(line) for line in lines]
    assert list(row) == CLOSED_COLUMNS
    assert row["event_id"] == first and row["latency_ms"] == 412
    assert row["dispatched"] is True
    assert "kept-out" not in json.dumps(row)


def test_export_neutralizes_spreadsheet_formulas(auth_client, organization):
    ingest(organization, server_id="-cmd", agent={"run_id": "run_1"})
    text = b"".join(
        auth_client.get(EXPORT, {"export_format": "csv"}).streaming_content
    ).decode()
    row = list(csv.reader(io.StringIO(text)))[1]
    assert row[CLOSED_COLUMNS.index("server_id")] == "'-cmd"


def test_export_is_scoped_and_refuses_an_empty_view(auth_client, organization):
    other_org, _ = _org_with_owner("Export other org")
    ingest(other_org, server_id="foreign")
    response = auth_client.get(EXPORT, {"export_format": "csv"})
    assert response.status_code == 400
    assert response.json()["code"] == "export_empty"
    ingest(organization, server_id="mine")
    text = b"".join(
        auth_client.get(EXPORT, {"export_format": "csv"}).streaming_content
    ).decode()
    assert "foreign" not in text and "mine" in text
    assert auth_client.get(EXPORT, {"export_format": "xml"}).status_code == 400
