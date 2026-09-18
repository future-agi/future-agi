"""Pins the Feed's normalized investigation evidence projection."""

from types import SimpleNamespace

from tracer.queries.feed import _investigation_reel


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
