import json

import pytest

from ee.agenthub.trace_scanner.investigation import ModelReply
from ee.agenthub.trace_scanner.scanner import TraceScanner


@pytest.mark.parametrize(
    "status,has_issues", [("violated", True), ("unknown", False), ("satisfied", False)]
)
def test_requirement_only_outcome_reaches_feed(monkeypatch, status, has_issues):
    from ee.agenthub.trace_scanner.investigation import Investigation

    report = {
        "outcome": {
            "status": status,
            "requirements": [
                {
                    "requirement": "Delivery must reach the requested recipient",
                    "status": status,
                    "evidence_ids": ["event:0"],
                }
            ],
        },
        "findings": [],
    }
    monkeypatch.setattr(Investigation, "run", lambda self, **kwargs: report)
    result = TraceScanner().scan_batch(
        [
            {
                "trace_id": "trace",
                "spans": [
                    {
                        "span_id": "send",
                        "span_attributes": {"output.value": "Sent to Bob"},
                    }
                ],
            }
        ]
    )[0]
    assert result.error is None
    assert result.outcome == status
    assert result.has_issues is has_issues
    assert result.investigation is report
    if has_issues:
        assert (
            result.issues[0].brief
            == report["outcome"]["requirements"][0]["requirement"]
        )
        assert result.key_moments[0].span == "send"
    else:
        assert not result.issues


def test_scanner_preserves_unknown_and_usage_across_batches():
    class Provider:
        total_cost_usd = 0
        token_usage = {"total_tokens": 0}

        def __call__(self, messages, tools, max_tokens):
            self.total_cost_usd += 0.01
            self.token_usage["total_tokens"] += 10
            stage = json.loads(messages[1]["content"])["stage"]
            if stage == "controller":
                body = {
                    "action": "finish",
                    "reason": "Missing final observation",
                    "task": "",
                    "system_prompt": "",
                    "source_ids": [],
                    "check": "",
                    "findings": [],
                    "amendments": [],
                }
            else:
                body = {
                    "accepted": False,
                    "reason": "Missing final observation",
                    "findings": [],
                    "requirement_checks": [],
                    "counterevidence": [],
                }
            return ModelReply(json.dumps(body))

    scanner = TraceScanner()
    scanner._investigation_provider = Provider()
    trace = {
        "trace_id": "trace",
        "spans": [
            {
                "span_id": "root",
                "span_attributes": {"input.value": "Persist change"},
                "child_spans": [],
            }
        ],
    }
    for _ in range(2):
        result = scanner.scan_batch([trace])[0]
        assert result.outcome == "unknown"
        assert not result.has_issues
        assert not result.retryable
        assert result.scan_version == "v2-adaptive-1"
        assert result.investigation["outcome"]["status"] == "unknown"
    assert scanner.total_cost_usd == 0.04
    assert scanner.token_usage["total_tokens"] == 40


def test_records_preserve_long_values_and_parentage_without_mutation():
    value = "x" * 500_000
    trace = {
        "spans": [
            {
                "span_id": "root",
                "span_attributes": {"input.value": value},
                "child_spans": [
                    {
                        "span_id": "child",
                        "span_attributes": {"output.value": False},
                        "child_spans": [],
                    }
                ],
            }
        ]
    }
    records = TraceScanner._investigation_records(trace)
    assert records[0]["value"]["span_attributes"]["input.value"] == value
    assert records[1]["value"]["parent_span_id"] == "root"
    assert records[1]["value"]["span_attributes"]["output.value"] is False
    assert "child_spans" not in records[0]["value"]
    assert "parent_span_id" not in trace["spans"][0]["child_spans"][0]


def test_finding_breadcrumb_uses_cited_source_without_static_taxonomy(monkeypatch):
    from ee.agenthub.trace_scanner.investigation import Investigation

    monkeypatch.setattr(
        Investigation,
        "run",
        lambda self, **kwargs: {
            "outcome": {"status": "violated"},
            "findings": [
                {
                    "status": "violated",
                    "summary": "Recipient differs from request",
                    "evidence_ids": ["event:1"],
                }
            ],
        },
    )
    trace = {
        "trace_id": "trace",
        "spans": [
            {
                "span_id": "root",
                "span_attributes": {},
                "child_spans": [
                    {
                        "span_id": "send",
                        "span_attributes": {
                            "input.value": "Send to Alice",
                            "output.value": "Sent to Bob",
                            "span.kind": "TOOL",
                        },
                        "child_spans": [],
                    },
                ],
            }
        ],
    }
    result = TraceScanner().scan_batch([trace])[0]
    assert result.has_issues
    assert result.issues[0].category == ""
    assert result.issues[0].group == ""
    assert result.key_moments[0].span == "send"
    assert json.loads(result.key_moments[0].verbatim)["output.value"] == "Sent to Bob"
    assert not result.key_moments[
        0
    ].is_failure  # citation alone is not step localization
