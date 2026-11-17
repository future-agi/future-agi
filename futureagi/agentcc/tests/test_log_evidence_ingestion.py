"""M16 request-log evidence sidecar ingestion tests."""

import copy

import pytest

from agentcc.models import AgentccAPIKey, AgentccRequestLog
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.tests.mcp_test_helpers import golden

GOLDEN_KEY_ID = "key_7f3a"


def golden_entry(**changes):
    entry = golden("log-entry-v2")
    for key, value in changes.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    return entry


@pytest.fixture
def gateway_key(organization):
    return AgentccAPIKey.objects.create(
        gateway_key_id=GOLDEN_KEY_ID,
        name="golden-key",
        organization=organization,
    )


@pytest.mark.integration
def test_log_evidence_stored(gateway_key, organization):
    entry = golden_entry()
    entry["evidence"] = {
        "budget_hold": {"hold_id": "hold-1", "enforced": True},
        "overhead": {"total_ms": 7.5},
    }
    entry["agent"]["identity"] = {"source": "runtime", "grant_id": "grant-1"}
    entry["eval_export"] = "allowed"
    entry["request_id"] = "req-evidence"
    entry["generation_id"] = "req-evidence"

    plain = golden_entry(request_id="req-no-evidence", generation_id="req-no-evidence")
    plain.pop("evidence", None)
    plain["agent"].pop("identity", None)
    plain.pop("eval_export", None)

    assert ingest_request_logs([copy.deepcopy(entry), plain]) == 2

    stored = AgentccRequestLog.no_workspace_objects.get(request_id="req-evidence")
    assert stored.evidence == {
        "budget_hold": {"hold_id": "hold-1", "enforced": True},
        "overhead": {"total_ms": 7.5},
        "identity": {"source": "runtime", "grant_id": "grant-1"},
        "eval_export": "allowed",
    }
    assert (
        AgentccRequestLog.no_workspace_objects.get(
            request_id="req-no-evidence"
        ).evidence
        is None
    )
