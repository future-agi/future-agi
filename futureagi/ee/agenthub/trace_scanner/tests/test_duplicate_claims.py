"""One mistake must reach the feed as one claim.

The V8 prompt asks for an issue per FAILED dimension, so a single event that
trips two dimensions is emitted twice by construction. Measured on a production
corpus: 10 of 60 flagged traces produced two claims, 9 of them describing
literally the same event — and because the two claims carry different
categories, category-partitioned assignment cannot merge them at clustering time
and merge_duplicate_clusters cannot merge them afterwards. One cash-arithmetic
bug reached the feed as four claims across three separate entries.

Nothing downstream can repair a per-trace duplicate, which is why this is fixed
where the evidence quotes are still in hand.
"""
import pytest

from ee.agenthub.trace_scanner.scanner import (
    TraceScanner,
    _collapse_same_event,
    _same_event,
)

QUOTE = "total_value already includes the cash balance of $8,400"
SEEN = (
    "the tool returned total_value already includes the cash balance of $8,400 "
    "and the agent replied your portfolio totals $78,033 after adding cash"
)


class TestSameEventDetection:
    def test_identical_quotes_are_one_event(self):
        assert _same_event(QUOTE, QUOTE)

    def test_a_contained_quote_is_the_same_event(self):
        """Two dimensions clip the same sentence at different ends."""
        assert _same_event(QUOTE, "already includes the cash balance of $8,400")

    def test_unrelated_quotes_are_not_merged(self):
        assert not _same_event(QUOTE, "the agent never asked which account to use")

    def test_empty_evidence_never_merges(self):
        """Absence claims carry no quote; merging on nothing would merge everything."""
        assert not _same_event("", QUOTE)
        assert not _same_event(QUOTE, None)

    def test_merging_is_conservative_on_partial_overlap(self):
        """A shared phrase is not a shared event — a false merge hides a real bug."""
        assert not _same_event(
            "the agent reported the portfolio totals $78,033",
            "the agent reported the account was opened in 2019",
        )


class TestCollapse:
    @staticmethod
    def _dims(**kw):
        return {k: {"evidence": v, "verdict": "FAIL"} for k, v in kw.items()}

    def test_two_dimensions_on_one_event_become_one(self):
        dims = self._dims(grounding=QUOTE, tools=QUOTE)
        assert _collapse_same_event({"grounding", "tools"}, dims) == {"tools"}

    def test_the_mechanism_survives_not_the_symptom(self):
        """A tool that failed EXPLAINS the fabrication that followed."""
        dims = self._dims(goal=QUOTE, tools=QUOTE, grounding=QUOTE)
        assert _collapse_same_event({"goal", "tools", "grounding"}, dims) == {"tools"}

    def test_distinct_events_both_survive(self):
        dims = self._dims(
            grounding=QUOTE,
            instruction="the system prompt forbids using the customer's first name",
        )
        assert _collapse_same_event({"grounding", "instruction"}, dims) == {
            "grounding",
            "instruction",
        }

    def test_a_single_failure_is_untouched(self):
        dims = self._dims(grounding=QUOTE)
        assert _collapse_same_event({"grounding"}, dims) == {"grounding"}

    def test_nothing_failed_stays_nothing(self):
        assert _collapse_same_event(set(), {}) == set()


class TestCollapseThroughTheGate:
    """End to end: the duplicate must be gone from `issues`, not merely from the
    dimension set."""

    @staticmethod
    def _parsed():
        return {
            "dimensions": {
                "grounding": {"evidence": QUOTE, "verdict": "FAIL"},
                "tools": {"evidence": QUOTE, "verdict": "FAIL"},
            },
            "issues": [
                {"dim": "grounding", "cat": "Unsupported Claim",
                 "brief": "double-counted cash in the portfolio total", "conf": "H"},
                {"dim": "tools", "cat": "Tool Output Misinterpretation",
                 "brief": "added cash to a total that already included cash", "conf": "H"},
            ],
        }

    def test_one_issue_reaches_the_feed(self):
        out = TraceScanner._v8_to_trace_output(self._parsed(), SEEN)
        assert len(out["issues"]) == 1, "the same event was surfaced twice"

    def test_the_surviving_issue_names_the_mechanism(self):
        out = TraceScanner._v8_to_trace_output(self._parsed(), SEEN)
        assert out["issues"][0]["cat"] == "Tool Output Misinterpretation"

    def test_an_ungrounded_dimension_cannot_win_the_merge(self):
        """Collapse runs after gating, so a dimension whose quote was invented is
        already gone and cannot become the surviving claim."""
        parsed = self._parsed()
        parsed["dimensions"]["tools"]["evidence"] = "a sentence that is not in the trace"
        out = TraceScanner._v8_to_trace_output(parsed, SEEN)
        assert len(out["issues"]) == 1
        assert out["issues"][0]["cat"] == "Unsupported Claim"


@pytest.mark.parametrize("dim", ["tools", "grounding", "instruction", "goal", "completion"])
def test_every_dimension_has_a_specificity_rank(dim):
    from ee.agenthub.trace_scanner.scanner import _DIMENSION_SPECIFICITY

    assert dim in _DIMENSION_SPECIFICITY, "an unranked dimension sorts last by accident"
