"""The verifier must be unable to keep a claim it cannot point at.

Precision target for what reaches the UI is >90%. Deterministic gates remove the
provably-unfounded claims; the residue is judgement, and judgement needs a reader
of the raw trace. But a reader that can assert without citing is just a second
opinion with the same failure mode as the first, so every CONFIRM carries a quote
that is checked in code before the claim survives.

Everything here pins the fail-closed behaviour: any way the verifier can fail to
establish a claim must end with that claim not being shown.
"""

from types import SimpleNamespace
from unittest.mock import patch

from ee.agenthub.trace_scanner import verify as V


def _issue(brief="agent fabricated the timing figures", cat="Tool Output Misinterpretation"):
    return SimpleNamespace(brief=brief, category=cat, confidence="H")


# Shaped exactly as `TraceData.to_dict()` emits it — `span_name`, not `name`.
# An invented shape here passes while production silently extracts nothing,
# which is how the missing span names went unnoticed.
TRACE = {
    "trace_id": "t-1",
    "spans": [
        {
            "span_name": "root",
            "span_attributes": {
                "input.value": "how long did the export take?",
                "output.value": "The export took ~80 seconds.",
            },
            "child_spans": [
                {
                    "span_name": "generate_insight",
                    "span_attributes": {"output.value": "total 80.27 seconds"},
                    "child_spans": [],
                }
            ],
        }
    ],
}


def _resp(verdict, quote, i=1):
    return '{"verdicts": [{"i": %d, "verdict": "%s", "quote": "%s", "why": "x"}]}' % (
        i, verdict, quote,
    )


class TestVerifierFailsClosed:
    def test_confirm_with_a_real_quote_keeps_the_claim(self):
        with patch.object(V, "_call", return_value=_resp("CONFIRM", "total 80.27 seconds")):
            kept, rejected = V.verify_issues(TRACE, [_issue()])
        assert len(kept) == 1 and not rejected

    def test_confirm_with_an_invented_quote_is_discarded(self):
        """The verifier asserting without evidence must not rescue a claim."""
        with patch.object(
            V, "_call", return_value=_resp("CONFIRM", "the agent said 45 minutes")
        ):
            kept, rejected = V.verify_issues(TRACE, [_issue()])
        assert kept == []
        assert rejected[0]["reason"] == "citation_unverifiable"

    def test_refute_drops_the_claim(self):
        with patch.object(V, "_call", return_value=_resp("REFUTE", "")):
            kept, rejected = V.verify_issues(TRACE, [_issue()])
        assert kept == [] and rejected[0]["reason"] == "refuted"

    def test_unparseable_response_drops_everything(self):
        with patch.object(V, "_call", return_value="I think it's probably fine?"):
            kept, rejected = V.verify_issues(TRACE, [_issue()])
        assert kept == [] and rejected[0]["reason"] == "verifier_no_verdicts"

    def test_verifier_exception_drops_everything(self):
        with patch.object(V, "_call", side_effect=RuntimeError("gateway down")):
            kept, rejected = V.verify_issues(TRACE, [_issue()])
        assert kept == [] and rejected[0]["reason"] == "verifier_error"

    def test_missing_verdict_for_a_claim_drops_that_claim(self):
        """Two claims sent, one verdict returned — the unanswered one dies."""
        with patch.object(V, "_call", return_value=_resp("CONFIRM", "total 80.27 seconds", 1)):
            kept, rejected = V.verify_issues(TRACE, [_issue(), _issue("second claim")])
        assert len(kept) == 1
        assert rejected[0]["reason"] == "no_verdict_returned"

    def test_trace_with_no_content_rejects_rather_than_passes(self):
        kept, rejected = V.verify_issues({"trace_id": "t", "spans": []}, [_issue()])
        assert kept == [] and rejected[0]["reason"] == "no_raw_trace"

    def test_no_issues_means_no_verifier_call(self):
        with patch.object(V, "_call", side_effect=AssertionError("should not be called")):
            assert V.verify_issues(TRACE, []) == ([], [])


class TestVerifierReadsRawSpans:
    def test_nested_span_content_reaches_the_verifier(self):
        """It must see the tool output, not the scanner's compressed view — that
        compression is where several measured false positives were created."""
        text = V._raw_trace_text(TRACE)
        assert "total 80.27 seconds" in text
        assert "how long did the export take?" in text
        assert "generate_insight" in text

    def test_span_names_survive_extraction(self):
        """Reading the wrong key labels every span "span" and still produces
        plausible-looking text, so assert the names themselves arrive."""
        text = V._raw_trace_text(TRACE)
        assert "[root]" in text and "[generate_insight]" in text
        assert "[span]" not in text

    def test_oversized_trace_is_marked_as_scanner_truncated(self):
        from ee.agenthub.trace_scanner.compress import SCANNER_TRUNCATION_MARK

        big = {
            "trace_id": "t",
            "spans": [{"name": "n", "span_attributes": {"output.value": "x" * 500000},
                       "child_spans": []}],
        }
        assert SCANNER_TRUNCATION_MARK in V._raw_trace_text(big)


class TestScannerMomentsAreGivenAsCoordinates:
    """The verifier reads the raw trace, but it should not have to find the
    failure unaided: at p90 that trace is ~93,000 characters and it is told to
    refute whatever it cannot establish. Handing it the scanner's own key
    moments — which already carry is_failure — is what turns "somewhere in here"
    into "start at this line"."""

    @staticmethod
    def _km(verbatim, is_failure=False, span="llm"):
        return SimpleNamespace(verbatim=verbatim, is_failure=is_failure, span=span)

    def test_failure_moment_is_marked_for_the_verifier(self):
        text = V._moments_text([
            self._km("total 80.27 seconds"),
            self._km("The export took ~80 seconds.", is_failure=True),
        ])
        assert "total 80.27 seconds" in text
        assert "scanner marked this the failure" in text
        # Only the failure moment carries the marker.
        assert text.count("scanner marked this the failure") == 1

    def test_moments_reach_the_prompt(self):
        captured = {}

        def _spy(messages):
            captured["user"] = messages[1]["content"]
            return _resp("CONFIRM", "total 80.27 seconds")

        with patch.object(V, "_call", side_effect=_spy):
            V.verify_issues(TRACE, [_issue()], [self._km("total 80.27 seconds", True)])
        assert "WHERE THE SCANNER SAYS IT HAPPENED" in captured["user"]
        assert "total 80.27 seconds" in captured["user"]

    def test_no_moments_leaves_the_prompt_unchanged(self):
        captured = {}

        def _spy(messages):
            captured["user"] = messages[1]["content"]
            return _resp("CONFIRM", "total 80.27 seconds")

        with patch.object(V, "_call", side_effect=_spy):
            V.verify_issues(TRACE, [_issue()])
        assert "WHERE THE SCANNER SAYS IT HAPPENED" not in captured["user"]

    def test_empty_verbatim_moments_are_skipped(self):
        """recover_verbatim returns "" when it cannot find the quote, and an
        empty pointer would just be noise."""
        assert V._moments_text([self._km(""), self._km("   ")]) == ""
