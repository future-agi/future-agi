import json

import pytest

from ee.agenthub.trace_scanner.investigation import (
    Investigation,
    InvestigationLimits,
    ModelReply,
    normalize_verification,
)
from ee.agenthub.trace_scanner.investigation_types import Verification


def finding(status="violated", kind="outcome", evidence=None):
    return {
        "id": "f1",
        "kind": kind,
        "status": status,
        "summary": "Observed result differs from requested value",
        "evidence_ids": evidence if evidence is not None else ["e1"],
        "missing_facts": [],
    }


def decision(action="finish"):
    return {
        "action": action,
        "reason": "Check the returned value",
        "task": "Read value",
        "system_prompt": "Check the exact recorded value",
        "source_ids": ["e1"],
        "check": "Compare requested and returned values",
        "findings": [finding()],
        "amendments": [],
    }


def verification(status="violated"):
    return {
        "accepted": True,
        "reason": "Evidence checked",
        "findings": [finding(status)],
        "requirement_checks": [
            {"requirement": "Required result", "status": status, "evidence_ids": ["e1"]}
        ],
        "counterevidence": [],
    }


class ScriptedProvider:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.requests = []

    def __call__(self, messages, tools, max_tokens):
        self.requests.append((messages, tools, max_tokens))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return (
            reply
            if isinstance(reply, ModelReply)
            else ModelReply(json.dumps(reply), usage={"total_tokens": 10})
        )


def run(provider, **kwargs):
    return Investigation(provider, **kwargs).run(
        objective="Return the required value",
        scope="trace",
        records=[{"id": "e1", "value": {"actual": 1}}],
    )


def test_direct_finish_always_runs_verifier():
    provider = ScriptedProvider(decision(), verification())
    result = run(provider)
    assert result["outcome"]["status"] == "violated"
    assert [c["phase"] for c in result["budget"]["calls"]] == [
        "controller:0",
        "verifier",
    ]
    assert all(not tools for _, tools, _ in provider.requests)
    assert all(limit == 16384 for _, _, limit in provider.requests)
    assert "Structured output contract:" in provider.requests[0][0][0]["content"]


@pytest.mark.parametrize("call_id", ["call1", "call1::sig::opaque-signature"])
def test_child_tool_returns_to_controller_before_verifier(call_id):
    tool = {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "check_evidence",
            "arguments": json.dumps(
                {"operation": "read", "operands": [{"id": "e1", "pointer": "/actual"}]}
            ),
        },
    }
    child = {
        "assignment_challenges": [],
        "proposed_requirements": [],
        "findings": [finding()],
        "missing_facts": [],
    }
    provider = ScriptedProvider(
        decision("investigate"),
        ModelReply(None, [tool]),
        child,
        decision(),
        verification(),
    )
    result = run(provider)
    assert [c["phase"] for c in result["budget"]["calls"]] == [
        "controller:0",
        "child:1",
        "child:1",
        "controller:1",
        "verifier",
    ]
    assert provider.requests[1][1][0]["function"]["name"] == "check_evidence"
    assert not provider.requests[2][1]
    continuation = provider.requests[2][0]
    assert continuation[2]["tool_calls"][0]["id"] == call_id
    assert continuation[3]["tool_call_id"] == call_id
    assert result["evidence_receipts"][0]["value"] == 1
    returned = json.loads(provider.requests[3][0][1]["content"])
    assert returned["children"][0]["report"]["status"] == "completed"
    assert returned["force_finish"] is True


def test_child_failure_does_not_imply_success():
    provider = ScriptedProvider(
        decision("investigate"), TimeoutError(), decision(), verification("unknown")
    )
    result = run(provider)
    assert result["outcome"]["status"] == "unknown"
    assert result["investigation"]["children"][0]["report"]["status"] == "unknown"


def test_controller_cannot_exceed_child_budget():
    with pytest.raises(ValueError, match="forced finish"):
        run(
            ScriptedProvider(decision("investigate")),
            limits=InvestigationLimits(max_children=0),
        )


def test_empty_or_invalid_verifier_never_restores_controller_claim():
    empty = verification()
    empty.update(findings=[], requirement_checks=[])
    assert run(ScriptedProvider(decision(), empty))["outcome"]["status"] == "unknown"
    with pytest.raises(ValueError):
        run(ScriptedProvider(decision(), ModelReply("not json")))


@pytest.mark.parametrize("kind", ["process", "instruction", "safety"])
def test_recovered_outcome_keeps_non_outcome_findings(kind):
    verified = verification("satisfied")
    verified["findings"].append(finding(kind=kind))
    result = run(ScriptedProvider(decision(), verified))
    assert result["outcome"]["status"] == "satisfied"
    assert any(
        f["kind"] == kind and f["status"] == "violated" for f in result["findings"]
    )


def test_instruction_can_violate_final_requirement():
    verified = verification()
    verified["findings"][0]["kind"] = "instruction"
    assert (
        run(ScriptedProvider(decision(), verified))["outcome"]["status"] == "violated"
    )


def test_invalid_references_and_conflicting_checks_are_unknown():
    verified = verification()
    verified["findings"][0]["evidence_ids"] = ["invented"]
    verified["requirement_checks"].append(
        {
            "requirement": "Required result",
            "status": "satisfied",
            "evidence_ids": ["e1"],
        }
    )
    status, findings, requirements = normalize_verification(
        Verification.model_validate(verified), {"e1"}
    )
    assert status == "unknown"
    assert findings[0]["status"] == "unknown"
    assert all(r["status"] == "unknown" for r in requirements)


def test_input_budget_rejects_without_truncating_or_calling_provider():
    provider = ScriptedProvider()
    with pytest.raises(ValueError, match="not truncated"):
        run(provider, limits=InvestigationLimits(max_input_tokens=1))
    assert provider.requests == []


def test_blank_requirement_does_not_establish_success():
    verified = verification("satisfied")
    verified["requirement_checks"][0]["requirement"] = "  "
    result = run(ScriptedProvider(decision(), verified))
    assert result["outcome"]["requirements"][0]["status"] == "unknown"
    assert result["outcome"]["status"] == "unknown"
