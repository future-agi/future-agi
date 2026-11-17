"""M16 MCP tool-call webhook ingestion tests."""

import copy
import json
from unittest.mock import patch

import pytest

from agentcc.models import AgentccAPIKey, AgentccRequestLog, AgentccToolCall
from agentcc.models.request_outcome import AgentccRequestOutcome
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.tests.mcp_test_helpers import golden

GOLDEN_KEY_ID = "key_7f3a"


def tool_call_entry(organization, **changes):
    record = golden("mcp-tool-call-v1")
    record["org_id"] = str(organization.id)
    record.update(changes)
    return record


def request_entry(request_id, key_id=GOLDEN_KEY_ID, **changes):
    return {
        "request_id": request_id,
        "timestamp": "2026-10-16T09:10:12Z",
        "model": "gpt-4o",
        "provider": "openai",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "status_code": 200,
        "latency_ms": 100,
        "auth_key_id": key_id,
        **changes,
    }


def outcome_entry(organization, event_id="outcome-1"):
    return {
        "kind": "outcome",
        "event_id": event_id,
        "org_id": str(organization.id),
        "generation_id": "req-mixed",
        "outcome": "ok",
        "source": "feedback",
        "ts_ms": 1792056612419,
    }


@pytest.fixture
def gateway_key(organization):
    return AgentccAPIKey.objects.create(
        gateway_key_id=GOLDEN_KEY_ID,
        name="golden-key",
        organization=organization,
    )


@pytest.mark.integration
class TestMCPToolCallIngestion:
    def test_golden_record_fills_columns(self, gateway_key, organization):
        assert ingest_request_logs([tool_call_entry(organization)]) == 0

        row = AgentccToolCall.objects.get()
        assert row.organization_id == organization.id
        assert row.event_id == "tc_01M4ZF0A9KQ2V7W3X5Y8Z1B4C6"
        assert row.tool_call_id == row.event_id
        assert (row.server_id, row.tool, row.upstream_tool) == (
            "fagi.web",
            "fagi.web_search",
            "search",
        )
        assert (row.billing_mode, row.credential_type, row.credential_source) == (
            "managed",
            "managed",
            "managed",
        )
        assert (
            row.upstream_cost_micros,
            row.billed_cost_micros,
            row.markup_micros,
        ) == (
            5000,
            5250,
            250,
        )
        assert row.agent_run_id == "run_R2"
        assert row.content_mode == "full"
        assert row.arguments == {"query": "gateway billing reconciliation"}
        assert row.result["content"][0]["type"] == "text"
        assert row.evidence == {
            "identity": tool_call_entry(organization)["identity"],
            "account_label": "work",
            "policy": {
                "rule": "policy:read-only-web",
                "action": "allow",
                "shadow": False,
            },
            "flow": tool_call_entry(organization)["flow"],
            "shaped": {
                "original_bytes": 48211,
                "returned_bytes": 5120,
                "spilled": True,
            },
        }

    def test_long_oauth_client_id_is_preserved(self, gateway_key, organization):
        client_id = "https://example.test/client.json?" + ("a" * 600)
        record = tool_call_entry(
            organization,
            event_id="tc-long-client-id",
            tool_call_id="tc-long-client-id",
            client_id=client_id,
        )

        assert ingest_request_logs([record]) == 0
        assert (
            AgentccToolCall.objects.get(event_id="tc-long-client-id").client_id
            == client_id
        )

    @pytest.mark.parametrize(
        "poison",
        [
            {"arguments": {"q": "x\x00y"}},
            {"result": {"content": [{"type": "text", "text": "bin\x00ary"}]}},
            {"error_message": "upstream said \x00"},
            {"tool": "probe\x00tool"},
        ],
        ids=["arguments", "result", "error_message", "tool"],
    )
    def test_one_org_nul_does_not_rollback_another_org(
        self, gateway_key, organization, poison
    ):
        other = type(organization).objects.create(name="Poison Org")
        clean = tool_call_entry(
            organization, event_id="tc-clean-org", tool_call_id="tc-clean-org"
        )
        poisoned = tool_call_entry(
            other, event_id="tc-poison-org", tool_call_id="tc-poison-org", **poison
        )

        assert ingest_request_logs([request_entry("req-poison"), clean, poisoned]) == 1
        assert AgentccToolCall.objects.filter(organization=organization).count() == 1
        assert AgentccToolCall.objects.filter(organization=other).count() == 1

        row = AgentccToolCall.objects.get(event_id="tc-poison-org")
        serialized = json.dumps(
            {
                "arguments": row.arguments,
                "result": row.result,
                "error_message": row.error_message,
                "tool": row.tool,
            },
            ensure_ascii=False,
        )
        assert "\x00" not in serialized

        assert ingest_request_logs([request_entry("req-poison"), clean, poisoned]) == 0
        assert AgentccToolCall.objects.count() == 2

    def test_gateway_content_shape_is_preserved(self, gateway_key, organization):
        prefix_len = 16 * 1024 - len(
            json.dumps(
                {"truncated": True, "original_bytes": 50_000, "prefix": ""},
                separators=(",", ":"),
            ).encode()
        )
        gateway_truncated = {
            "truncated": True,
            "original_bytes": 50_000,
            "prefix": "a" * prefix_len,
        }
        record = tool_call_entry(
            organization,
            event_id="tc-gateway-content",
            tool_call_id="tc-gateway-content",
            arguments=gateway_truncated,
            result=gateway_truncated,
        )

        assert ingest_request_logs([record]) == 0
        row = AgentccToolCall.objects.get(event_id="tc-gateway-content")
        assert row.arguments == gateway_truncated
        assert row.result == gateway_truncated

    def test_lone_surrogates_are_replaced(self, gateway_key, organization):
        record = tool_call_entry(
            organization,
            event_id="tc-surrogate",
            tool_call_id="tc-surrogate",
            arguments={"query": "bad" + chr(0xD800) + "\x00"},
        )

        assert ingest_request_logs([record]) == 0
        assert AgentccToolCall.objects.get(event_id="tc-surrogate").arguments == {
            "query": "bad��"
        }

    def test_deep_nested_content_does_not_abort_other_organizations(self, organization):
        other = type(organization).objects.create(name="Deep Content Org")
        third = type(organization).objects.create(name="After Deep Content Org")

        deep = {}
        cursor = deep
        for _ in range(5000):
            cursor["nested"] = {}
            cursor = cursor["nested"]

        records = [
            tool_call_entry(
                organization,
                event_id="tc-deep-before",
                tool_call_id="tc-deep-before",
            ),
            tool_call_entry(
                other,
                event_id="tc-deep-5000",
                tool_call_id="tc-deep-5000",
                arguments=deep,
            ),
            tool_call_entry(
                third,
                event_id="tc-deep-after",
                tool_call_id="tc-deep-after",
            ),
        ]

        # This traverses the real mixed-batch ingestion path.  A recursive
        # sanitizer raises before the later organization's row is considered.
        assert ingest_request_logs(records) == 0
        assert set(AgentccToolCall.objects.values_list("event_id", flat=True)) == {
            "tc-deep-before",
            "tc-deep-5000",
            "tc-deep-after",
        }

        stored = AgentccToolCall.objects.get(event_id="tc-deep-5000").arguments
        for _ in range(5000):
            stored = stored["nested"]
        assert stored == {}

    def test_evidence_and_integer_columns_are_fitted(self, gateway_key, organization):
        record = tool_call_entry(
            organization,
            event_id="tc-fit-columns",
            tool_call_id="tc-fit-columns",
            account_label="a" * 65,
            latency_ms=2**31,
            upstream_latency_ms=2**31,
            markup_bps=2**31,
        )

        assert ingest_request_logs([record]) == 0
        row = AgentccToolCall.objects.get(event_id="tc-fit-columns")
        assert row.evidence["account_label"] == "a" * 65
        assert row.latency_ms is None
        assert row.upstream_latency_ms is None
        assert row.markup_bps == 0

    def test_tool_call_evidence_stored(self, gateway_key, organization):
        with_evidence = tool_call_entry(
            organization,
            event_id="tc-with-evidence",
            tool_call_id="tc-with-evidence",
            identity={"source": "runtime"},
            account_label="work",
            policy={"rule": "allow", "action": "allow", "shadow": False},
            flow={"decision": "allow"},
            shaped={"original_bytes": 10, "returned_bytes": 5, "spilled": False},
        )
        without_evidence = tool_call_entry(
            organization,
            event_id="tc-without-evidence",
            tool_call_id="tc-without-evidence",
        )
        for field in ("identity", "account_label", "policy", "flow", "shaped"):
            without_evidence.pop(field, None)

        assert ingest_request_logs([with_evidence, without_evidence]) == 0

        assert AgentccToolCall.objects.get(event_id="tc-with-evidence").evidence == {
            "identity": {"source": "runtime"},
            "account_label": "work",
            "policy": {"rule": "allow", "action": "allow", "shadow": False},
            "flow": {"decision": "allow"},
            "shaped": {"original_bytes": 10, "returned_bytes": 5, "spilled": False},
        }
        assert (
            AgentccToolCall.objects.get(event_id="tc-without-evidence").evidence is None
        )

    def test_replayed_batch_no_duplicates_no_usage_event(
        self, gateway_key, organization
    ):
        batch = [tool_call_entry(organization)]
        with patch("ee.usage.services.emitter.emit") as emit:
            assert ingest_request_logs(copy.deepcopy(batch)) == 0
            assert ingest_request_logs(copy.deepcopy(batch)) == 0

        assert AgentccToolCall.objects.count() == 1
        emit.assert_not_called()

    def test_duplicate_event_ids_in_one_batch_store_once(
        self, gateway_key, organization
    ):
        first = tool_call_entry(
            organization,
            event_id="tc-batch-duplicate",
            tool_call_id="tc-batch-duplicate",
        )
        second = copy.deepcopy(first)
        second["tool"] = "different-tool"

        assert ingest_request_logs([first, second]) == 0

        row = AgentccToolCall.objects.get(event_id="tc-batch-duplicate")
        assert row.tool == first["tool"]
        assert AgentccToolCall.objects.count() == 1

    def test_missing_event_id_rejected(self, gateway_key, organization):
        missing = tool_call_entry(organization)
        missing.pop("event_id")
        blank = tool_call_entry(organization, event_id="")
        missing_org = tool_call_entry(organization, event_id="tc-no-org")
        missing_org.pop("org_id")

        assert ingest_request_logs([missing, blank, missing_org]) == 0
        assert AgentccToolCall.objects.count() == 0

    def test_mixed_batch_logs_outcomes_tool_calls(self, gateway_key, organization):
        tool = tool_call_entry(
            organization, event_id="tc-mixed", tool_call_id="tc-mixed"
        )
        with patch("ee.usage.services.emitter.emit") as emit:
            assert (
                ingest_request_logs(
                    [request_entry("req-mixed"), outcome_entry(organization), tool]
                )
                == 1
            )

        assert AgentccRequestLog.no_workspace_objects.filter(
            request_id="req-mixed"
        ).exists()
        assert AgentccRequestOutcome.objects.filter(event_id="outcome-1").exists()
        assert AgentccToolCall.objects.filter(event_id="tc-mixed").exists()
        emit.assert_called_once()
        assert emit.call_args.args[0].event_type == "gateway_request"
