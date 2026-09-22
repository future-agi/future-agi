"""Pins the Feed's normalized investigation evidence projection."""

from types import SimpleNamespace
from unittest.mock import patch

from tracer.queries.feed import _finding_span_context, _investigation_reel


class _RelatedRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


def test_omega_report_without_key_moments_shows_evidence_receipts():
    report = SimpleNamespace(
        key_moments=_RelatedRows([]),
        evidence_receipts=_RelatedRows(
            [
                SimpleNamespace(
                    deleted=False,
                    excerpt="The requested item was not delivered",
                    span_id="span-1",
                ),
                SimpleNamespace(deleted=True, excerpt="Hidden", span_id="span-2"),
            ]
        ),
    )

    reel = _investigation_reel(report)

    assert len(reel) == 1
    assert reel[0]["label"] == "RECEIPT"
    assert reel[0]["span"] == "span-1"
    assert reel[0]["raw"] == "The requested item was not delivered"


def test_omega_reel_uses_finding_and_supported_attribution_only():
    excerpt = '{"name":"ChatAnthropic","input.value":"prompt"}'
    receipt = SimpleNamespace(excerpt=excerpt, span_id="span-1", evidence_id="ev-1")
    finding = SimpleNamespace(
        statement="The assistant failed to deliver the requested item",
        attributions=_RelatedRows([
            SimpleNamespace(deleted=False, role="decisive", status="supported", span_id="span-1"),
            SimpleNamespace(deleted=False, role="symptom", status="supported", span_id="span-1"),
            SimpleNamespace(deleted=False, role="origin", status="unknown", span_id="span-2"),
        ]),
    )
    report = SimpleNamespace(
        key_moments=_RelatedRows([SimpleNamespace(
            deleted=False, kevinified="Unrelated", verbatim="Unrelated",
            role="origin", span_id="other", status="ok", is_failure=False,
        )]),
        evidence_receipts=_RelatedRows(
            [SimpleNamespace(deleted=False, excerpt="Unrelated", span_id="other")]
        ),
    )

    reel = _investigation_reel(
        report, selected_findings=[(finding, {"decisive": [receipt]})],
        span_context={"span-1": {
            "name": "Recorded operation", "attrs_string": {
                "input.value": "false", "output.value": "0"
            },
        }},
    )

    assert [step["label"] for step in reel] == ["FINDING", "DECISIVE"]
    assert reel[0]["text"] == finding.statement
    assert reel[1]["text"] == "Recorded operation"
    assert reel[1]["raw"] == excerpt
    assert reel[1]["evidence_id"] == "ev-1"
    assert reel[1]["status"] == "neutral"
    assert reel[1]["input_preview"] == "false"
    assert reel[1]["output_preview"] == "0"
    assert reel[1]["io_source"] == "recorded_span"
    assert not any("Unrelated" in str(step) for step in reel)


def test_omega_attribution_without_receipt_is_not_treated_as_false():
    finding = SimpleNamespace(
        statement="A failed handoff", attributions=_RelatedRows([
            SimpleNamespace(deleted=False, role="origin", status="supported", span_id="span-1")
        ]),
    )
    reel = _investigation_reel(None, selected_findings=[(finding, {})])
    assert [step["label"] for step in reel] == ["FINDING", "ORIGIN"]
    assert reel[1]["span"] == "span-1"
    assert reel[1]["raw"] is None
    assert reel[1]["status"] == "neutral"


def test_recorded_span_read_is_project_scoped_and_rejects_other_trace():
    finding = SimpleNamespace(
        statement="Failure", attributions=_RelatedRows([
            SimpleNamespace(deleted=False, role="decisive", status="supported", span_id="span-1")
        ]),
    )

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def list_by_ids(self, ids, **kwargs):
            assert ids == ["span-1"]
            assert kwargs["project_id"] == "project-1"
            assert kwargs["include_heavy"] is False
            assert "attrs_string" in kwargs["columns"]
            return [
                {"id": "span-1", "trace_id": "trace-1", "attrs_string": {}},
                {"id": "span-1", "trace_id": "other-trace", "attrs_string": {}},
            ]

    with patch("tracer.queries.feed.get_reader", return_value=Reader()):
        context = _finding_span_context({"trace-1": [(finding, {})]}, "project-1")
    assert list(context) == [("trace-1", "span-1")]


def test_legacy_key_moments_take_precedence_over_receipts():
    report = SimpleNamespace(
        key_moments=_RelatedRows(
            [
                SimpleNamespace(
                    deleted=False,
                    kevinified="No answer sent",
                    verbatim="No answer sent",
                    role="decisive",
                    span_id="span-1",
                    status="error",
                    is_failure=True,
                )
            ]
        ),
        evidence_receipts=_RelatedRows([]),
    )

    reel = _investigation_reel(report)

    assert len(reel) == 1
    assert reel[0]["raw"] == "No answer sent"


def test_selected_issue_receipts_do_not_show_report_wide_moments_or_receipts():
    selected = SimpleNamespace(
        deleted=False, excerpt="Linked to selected finding", span_id="selected"
    )
    report = SimpleNamespace(
        key_moments=_RelatedRows(
            [
                SimpleNamespace(
                    deleted=False,
                    kevinified="Unrelated moment",
                    verbatim="Unrelated moment",
                    role="decisive",
                    span_id="other",
                    status="error",
                    is_failure=True,
                )
            ]
        ),
        evidence_receipts=_RelatedRows(
            [SimpleNamespace(deleted=False, excerpt="Unrelated", span_id="other")]
        ),
    )

    assert [
        step["raw"]
        for step in _investigation_reel(report, selected_receipts=[selected])
    ] == ["Linked to selected finding"]
    assert _investigation_reel(report, selected_receipts=[]) == []
