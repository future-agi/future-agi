"""What SIEM export and the explorer download may carry (G16-SIEM round 2).

Only the allowlisted metadata projection leaves the product, each text value
bounded by its column and accepted only when the gateway's span scalar
sanitizer would export it unchanged; the org's own privacy redactor then
applies to every value, as the gateway applies it to its span scalars. The
G16 frozen privacy corpus, the gateway's scalar shape fixtures and
shape-fitting carriers of the same synthetic credentials run through all six
emitters (webhook audit events and spans, OTLP traces and logs, explorer CSV
and NDJSON): no credential and no unapproved value comes out, while ordinary
identifiers do.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from agentcc.services import mcp_tool_call_export, siem_payloads, siem_signing
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.services.org_config_writer import write_config_version
from agentcc.tests.siem_privacy_corpus import fixtures, leaked
from agentcc.tests.siem_receiver import Receiver, admit_loopback
from agentcc.tests.test_mcp_tool_calls_api import tool_call_entry

DESTINATIONS = "/agentcc/siem/destinations/"
EXPORT = "/agentcc/mcp/tool-calls/export/"
REDACTED = "[redacted]"
ORG_ID = "00000000-0000-4000-8000-000000000001"
NOW = datetime(2026, 10, 27, 12, 1, tzinfo=UTC)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def iterator(self, chunk_size=None):
        return iter(self.rows)


def _row(value):
    row = SimpleNamespace(
        organization_id=ORG_ID, started_at=datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    )
    for column in siem_payloads.COLUMNS:
        if column in siem_payloads.INT_COLUMNS:
            setattr(row, column, 7)
        elif column in siem_payloads.BOOL_COLUMNS:
            setattr(row, column, True)
        elif column != "started_at":
            setattr(row, column, value)
    return row


def _emitters(row):
    delivery_id = uuid.UUID(int=1)
    audit = siem_payloads.envelope("audit_events", delivery_id, ORG_ID, NOW)
    audit["events"] = siem_payloads.audit_events([row])
    spans = siem_payloads.envelope("spans", delivery_id, ORG_ID, NOW)
    spans["otlp"] = siem_payloads.spans([row])
    return {
        "webhook_audit_events": siem_signing.encode(audit).decode(),
        "webhook_spans": siem_signing.encode(spans).decode(),
        "otlp_traces": siem_signing.encode(siem_payloads.spans([row])).decode(),
        "otlp_logs": siem_signing.encode(siem_payloads.logs([row], NOW)).decode(),
        "explorer_csv": "".join(mcp_tool_call_export.csv_lines(_Rows([row]))),
        "explorer_ndjson": "".join(mcp_tool_call_export.ndjson_lines(_Rows([row]))),
    }


def test_corpus_never_leaves_through_any_emitter():
    leaks = {}
    for fixture in fixtures():
        if fixture["group"] == "control":
            continue
        out = _emitters(_row(fixture["value"]))
        needles = (*fixture["needles"], fixture["value"])
        hit = sorted(name for name, text in out.items() if leaked(text, needles))
        if siem_payloads.scalar(fixture["value"]) != REDACTED:
            hit.append("scalar")
        if hit:
            # Names only: a failure message never carries a fixture's value.
            leaks[f"{fixture['group']}/{fixture['name']}"] = hit
    assert leaks == {}


def test_ordinary_identifiers_still_leave():
    for fixture in fixtures():
        if fixture["group"] != "control":
            continue
        value = fixture["value"]
        assert siem_payloads.scalar(value) == value
        exported = siem_payloads.audit_values(_row(value))
        for column in ("tool", "agent_id", "api_key_id", "budget_scope"):
            assert exported[column] == value, (column, fixture["name"])
        for text in _emitters(_row(value)).values():
            assert value in text


def test_fixed_formats_and_bounds_are_enforced():
    row = _row("researcher")
    row.session_hash = "not-a-session-hash"
    row.outcome = "Success; drop"
    row.tool_call_id = "t" * 65
    row.error_code = "-32603"
    exported = siem_payloads.audit_values(row)
    assert exported["session_hash"] == REDACTED
    assert exported["outcome"] == REDACTED
    assert exported["tool_call_id"] == REDACTED
    assert exported["error_code"] == "-32603"
    row.session_hash = "ab" * 32
    assert siem_payloads.audit_values(row)["session_hash"] == "ab" * 32


def _enable(client, body):
    created = client.post(DESTINATIONS, body, format="json")
    assert created.status_code == 201, created.content
    result = created.json()["result"]
    enabled = client.patch(
        f"{DESTINATIONS}{result['id']}/", {"enabled": True}, format="json"
    )
    assert enabled.status_code == 200, enabled.content
    return result


def _export_all(client, organization, monkeypatch, records):
    """Every request body the destinations received, and both downloads."""
    with Receiver() as receiver:
        admit_loopback(monkeypatch, receiver)
        _enable(
            client,
            {
                "name": "SOC webhook",
                "kind": "webhook",
                "url": receiver.url + "/hooks",
                "streams": ["audit_events", "spans"],
            },
        )
        _enable(
            client,
            {
                "name": "SOC collector",
                "kind": "otlp_http",
                "url": receiver.url,
                "streams": ["audit_events", "spans"],
            },
        )
        ingest_request_logs(records)
        call_command("agentcc_siem_deliver", "--limit", "1000")
        texts = {
            f"{r['path']}#{n}": r["body"].decode()
            for n, r in enumerate(receiver.requests)
        }
    assert sorted({key.split("#")[0] for key in texts}) == [
        "/hooks",
        "/v1/logs",
        "/v1/traces",
    ]
    for export_format in ("json", "csv"):
        response = client.get(EXPORT, {"export_format": export_format})
        assert response.status_code == 200, response.content
        texts[f"export-{export_format}"] = b"".join(response.streaming_content).decode()
    return texts


def _record(organization, value, n=0):
    record = tool_call_entry(
        organization,
        event_id=f"tc_p{n}_{uuid.uuid4().hex[:12]}",
        tool=value,
        upstream_tool=value,
        api_key_id=value,
        budget_scope=value,
    )
    record["agent"] = {**record["agent"], "agent_id": value, "run_id": value}
    if len(value) <= 64:
        for field in ("request_id", "server_id", "virtual_server_id", "approval_id"):
            record[field] = value
    return record


@pytest.mark.integration
def test_credential_carriers_never_leave_end_to_end(
    auth_client, organization, settings, monkeypatch
):
    settings.AGENTCC_SIEM_EXPORT_ENABLED = True
    carriers = [
        f for f in fixtures() if f["group"] == "derived" and len(f["value"]) <= 128
    ]
    records = [_record(organization, f["value"], n) for n, f in enumerate(carriers)]
    texts = _export_all(auth_client, organization, monkeypatch, records)
    leaks = {
        f["name"]: sorted(
            {
                k.split("#")[0]
                for k, text in texts.items()
                if leaked(text, (*f["needles"], f["value"]))
            }
        )
        for f in carriers
    }
    assert {name: where for name, where in leaks.items() if where} == {}


ORG_POLICIES = {
    "full": ({"enabled": True, "mode": "full"}, "researcher-redacted", False),
    "patterns-match": (
        {
            "enabled": True,
            "mode": "patterns",
            "redact_patterns": [{"name": "cust", "pattern": "CUST-[0-9]+"}],
        },
        "CUST-424242",
        False,
    ),
    "patterns-default-mode-alias": (
        {
            "enabled": True,
            "redactPatterns": [{"name": "cust", "pattern": "CUST-[0-9]+"}],
        },
        "CUST-424243",
        False,
    ),
    "posix-class-fails-closed": (
        {
            "enabled": True,
            "mode": "patterns",
            "redact_patterns": [{"name": "digits", "pattern": "[[:digit:]]{6}"}],
        },
        "CUST-424244",
        False,
    ),
    "patterns-no-match": (
        {
            "enabled": True,
            "mode": "patterns",
            "redact_patterns": [{"name": "cust", "pattern": "CUST-[0-9]+"}],
        },
        "researcher-kept",
        True,
    ),
    "disabled": ({"enabled": False, "mode": "full"}, "researcher-also-kept", True),
}


@pytest.mark.integration
@pytest.mark.parametrize("policy", sorted(ORG_POLICIES))
def test_org_privacy_redactor_applies_to_every_export(
    auth_client, organization, user, settings, monkeypatch, policy
):
    settings.AGENTCC_SIEM_EXPORT_ENABLED = True
    privacy, value, kept = ORG_POLICIES[policy]
    write_config_version(
        organization, {"privacy": privacy}, user=user, push=lambda config: True
    )
    texts = _export_all(
        auth_client, organization, monkeypatch, [_record(organization, value)]
    )
    found = sorted({k.split("#")[0] for k, text in texts.items() if value in text})
    if kept:
        assert found == [
            "/hooks",
            "/v1/logs",
            "/v1/traces",
            "export-csv",
            "export-json",
        ]
    else:
        assert found == []
        assert all(REDACTED in text for text in texts.values())
    if policy == "full":
        # Every scalar is redacted, the org id too; outcomes keep their status.
        events = [
            json.loads(text) for key, text in texts.items() if key.startswith("/hooks")
        ]
        org_id_exported = str(organization.id) in "".join(texts.values())
        assert not org_id_exported
        audit = next(e for e in events if e["stream"] == "audit_events")
        assert audit["events"][0]["tool"] == REDACTED
        assert audit["events"][0]["latency_ms"] == 412
        assert audit["events"][0]["is_error"] is False
