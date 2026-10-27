"""Results are persisted only once the verifier has ruled on every claim.

verify_issues fails closed, which is correct when the verifier read the trace and
refused a claim. It is wrong when the call never happened: every flagged trace
would be written with has_issues=False, and because the already-scanned
anti-join treats any row as terminal — FAILED included — that verdict is
permanent and the trace is never looked at again. A gateway outage would
silently and irreversibly empty the feed.

The Vertex token going stale within an hour of a passing smoke test is the
observed version of this, so these pin the deferral rather than the fail-closed
default.
"""
from types import SimpleNamespace
from unittest.mock import patch

from ee.agenthub.trace_scanner import verify as V


def _issue(brief="agent fabricated the totals"):
    return SimpleNamespace(brief=brief, category="Unsupported Claim", confidence="H")


TRACE = {
    "trace_id": "t-1",
    "spans": [
        {
            "span_name": "root",
            "span_attributes": {
                "input.value": "what were the totals?",
                "output.value": "The totals came to $412.90.",
            },
            "child_spans": [],
        }
    ],
}


class TestSilenceIsNotARefusal:
    def test_transport_error_counts_as_unanswered(self):
        with patch.object(V, "_call", side_effect=RuntimeError("gateway down")):
            kept, rejected = V.verify_issues(TRACE, [_issue()])
        assert kept == []
        assert rejected[0]["reason"] in V.UNANSWERED_REASONS

    def test_unparseable_response_counts_as_unanswered(self):
        with patch.object(V, "_call", return_value="not json at all"):
            _kept, rejected = V.verify_issues(TRACE, [_issue()])
        assert rejected[0]["reason"] in V.UNANSWERED_REASONS

    def test_omitted_verdict_counts_as_unanswered(self):
        """The call succeeded but said nothing about this claim, so the trace is
        not fully ruled on and must not be persisted on the strength of its
        siblings."""
        resp = (
            '{"verdicts": [{"i": 1, "verdict": "CONFIRM", '
            '"quote": "The totals came to $412.90.", "why": "x"}]}'
        )
        with patch.object(V, "_call", return_value=resp):
            kept, rejected = V.verify_issues(TRACE, [_issue(), _issue("second claim")])
        assert len(kept) == 1, "the answered claim should still be confirmed"
        assert rejected[0]["reason"] == "no_verdict_returned"
        assert rejected[0]["reason"] in V.UNANSWERED_REASONS

    def test_a_real_refutation_is_answered(self):
        """Otherwise the deferral would swallow the verifier actually working."""
        resp = '{"verdicts": [{"i": 1, "verdict": "REFUTE", "quote": "", "why": "x"}]}'
        with patch.object(V, "_call", return_value=resp):
            _kept, rejected = V.verify_issues(TRACE, [_issue()])
        assert rejected[0]["reason"] == "refuted"
        assert rejected[0]["reason"] not in V.UNANSWERED_REASONS

    def test_unverifiable_citation_is_answered(self):
        resp = (
            '{"verdicts": [{"i": 1, "verdict": "CONFIRM", '
            '"quote": "a sentence never in the trace", "why": "x"}]}'
        )
        with patch.object(V, "_call", return_value=resp):
            _kept, rejected = V.verify_issues(TRACE, [_issue()])
        assert rejected[0]["reason"] == "citation_unverifiable"
        assert rejected[0]["reason"] not in V.UNANSWERED_REASONS


class TestNothingIsPersistedForADeferredTrace:
    def test_retryable_results_are_not_written(self):
        from ee.agenthub.trace_scanner.scanner import ScanResult
        from tracer.queries.trace_scanner import write_scan_results

        deferred = ScanResult(
            trace_id="11111111-1111-1111-1111-111111111111",
            has_issues=False,
            error="scanner_verify_incomplete",
            retryable=True,
        )
        # No DB row is created, so the write count stays zero and the trace
        # remains invisible to filter_already_scanned.
        assert write_scan_results([deferred], "p-1", "v8") == 0

    def test_retryable_defaults_off_so_normal_results_still_write(self):
        from ee.agenthub.trace_scanner.scanner import ScanResult

        assert ScanResult(trace_id="t", has_issues=False).retryable is False


class TestClaimNumberingCannotSilentlyDeferEverything:
    """The listing is 1-based; a model that answers 0-based must not cost a trace.

    Under the persist rule one unmatched index defers the WHOLE trace, so a
    numbering habit would send every flagged trace back to the sweep forever —
    the feed thinning with nothing in the logs but `no_verdict_returned`.
    """

    def test_a_fully_zero_based_answer_is_accepted(self):
        resp = (
            '{"verdicts": ['
            '{"i": 0, "verdict": "CONFIRM", "quote": "The totals came to $412.90.", "why": "x"},'
            '{"i": 1, "verdict": "REFUTE", "quote": "", "why": "x"}]}'
        )
        with patch.object(V, "_call", return_value=resp):
            kept, rejected = V.verify_issues(TRACE, [_issue(), _issue("second claim")])
        assert len(kept) == 1, "a 0-based answer was thrown away"
        assert rejected[0]["reason"] == "refuted"
        assert rejected[0]["reason"] not in V.UNANSWERED_REASONS

    def test_a_partial_answer_is_still_unanswered(self):
        """Only an exact {0..N-1} set is a numbering shift; anything else is a
        genuinely missing verdict and must still defer."""
        resp = (
            '{"verdicts": [{"i": 0, "verdict": "CONFIRM", '
            '"quote": "The totals came to $412.90.", "why": "x"}]}'
        )
        with patch.object(V, "_call", return_value=resp):
            _kept, rejected = V.verify_issues(TRACE, [_issue(), _issue("second claim")])
        assert any(r["reason"] == "no_verdict_returned" for r in rejected)
