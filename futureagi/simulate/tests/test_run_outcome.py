"""A run's verdict and pass rate: the one rule every run and summary payload reports."""

from __future__ import annotations

import pytest

from simulate.services.run_outcome import (
    OutcomeCounts,
    outcome_fields,
    pass_rate,
    run_outcome,
)


def _counts(passed=0, failed=0, errored=0, skipped=0, no_score=0) -> OutcomeCounts:
    return {
        "passed": passed,
        "failed": failed,
        "errored": errored,
        "skipped": skipped,
        "no_score": no_score,
    }


@pytest.mark.parametrize(
    ("status", "counts", "verdict"),
    [
        # Not ended: running, whatever its calls say.
        ("running", _counts(failed=3), "running"),
        ("pending", _counts(), "running"),
        ("evaluating", _counts(passed=2), "running"),
        ("cancelling", _counts(passed=2), "running"),
        # Stopped: cancelled, not a failure.
        ("cancelled", _counts(failed=1), "cancelled"),
        # Completed with only unscored calls: no score.
        ("completed", _counts(no_score=2), "no_score"),
        # A passed call scores the run.
        ("completed", _counts(passed=1, no_score=2), "passed"),
        ("completed", _counts(passed=3), "passed"),
        # An empty completed run passes, as the runs list has always read it.
        ("completed", _counts(), "passed"),
        # Anything failed, errored or skipped fails the run.
        ("completed", _counts(passed=3, failed=1), "failed"),
        ("completed", _counts(passed=3, errored=1), "failed"),
        ("completed", _counts(skipped=1, no_score=2), "failed"),
        # A run that ended failed is a failure even with nothing failed.
        ("failed", _counts(passed=2), "failed"),
        # The lifecycle is read case-insensitively: the runs list titles it.
        ("Completed", _counts(no_score=1), "no_score"),
    ],
)
def test_the_verdict(status, counts, verdict):
    assert run_outcome(status=status, counts=counts)["outcome_status"] == verdict


@pytest.mark.parametrize(
    ("passed", "base", "rate"),
    [
        (0, 0, None),
        (3, 3, 100),
        (0, 4, 0),
        (2, 3, 67),
        # Half rounds up, as the clients rounded it.
        (1, 8, 13),
        (57, 200, 29),
    ],
)
def test_the_pass_rate_rounds_half_up(passed, base, rate):
    assert pass_rate(passed=passed, base=base) == rate


def test_errored_and_skipped_calls_count_against_the_rate_and_unscored_ones_do_not():
    outcome = run_outcome(
        status="completed",
        counts=_counts(passed=2, failed=1, errored=1, skipped=1, no_score=5),
    )

    assert (outcome["pass_rate"], outcome["scored"]) == (40, 5)


def _tally(by_status, no_score_by_status=None):
    return {"by_status": by_status, "no_score_by_status": no_score_by_status or {}}


def test_a_run_with_errored_calls_keeps_the_dev_counts_and_rate():
    # 8 passed and 2 errored: outcome_failed counts the errored calls, and the rate is 80%, as before.
    fields = outcome_fields(
        status="completed",
        receipts=_tally({"passed": 8, "errored": 2}),
        calls=None,
    )

    assert fields == {
        "outcome_passed": 8,
        "outcome_failed": 2,
        "outcome_errored": 2,
        "outcome_skipped": 0,
        "outcome_no_score": 0,
        "outcome_status": "failed",
        "pass_rate": 80,
        "scored": 10,
    }


def test_a_passed_receipt_whose_call_has_no_score_is_counted_but_not_scored():
    fields = outcome_fields(
        status="completed",
        receipts=_tally({"passed": 3}, {"passed": 3}),
        calls=None,
    )

    assert (
        fields["outcome_passed"],
        fields["outcome_no_score"],
        fields["outcome_status"],
        fields["pass_rate"],
        fields["scored"],
    ) == (3, 3, "no_score", None, 0)


def test_the_runs_list_judges_a_run_without_receipts_by_its_calls():
    fields = outcome_fields(
        status="completed", receipts=None, calls={"completed": 3, "failed": 1}
    )

    assert fields == {
        "outcome_passed": None,
        "outcome_failed": None,
        "outcome_errored": None,
        "outcome_skipped": None,
        "outcome_no_score": None,
        "outcome_status": "failed",
        "pass_rate": 75,
        "scored": 4,
    }


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "running"])
def test_a_summary_row_without_receipts_has_no_verdict_and_no_rate(status):
    fields = outcome_fields(status=status, receipts=None, calls=None)

    assert (fields["outcome_status"], fields["pass_rate"], fields["scored"]) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("calls", "receipts", "no_score_receipts"),
    [
        ({"passed": 2}, {"passed": 2}, {}),
        ({"passed": 1, "error": 1}, {"passed": 1, "errored": 1}, {}),
        ({"passed": 1, "inconclusive": 1}, {"passed": 1, "skipped": 1}, {}),
        ({"no_score": 2}, {"passed": 2}, {"passed": 2}),
        ({"passed": 1, "failed": 1}, {"passed": 1, "failed": 1}, {}),
    ],
)
def test_the_analytics_and_runs_list_mappers_agree_on_a_run(
    calls, receipts, no_score_receipts
):
    from simulate.services.run_outcome import call_outcome_counts, receipt_counts

    from_calls = run_outcome(status="completed", counts=call_outcome_counts(calls))
    from_receipts = run_outcome(
        status="completed",
        counts=receipt_counts(
            {"by_status": receipts, "no_score_by_status": no_score_receipts}
        ),
    )

    assert from_calls == from_receipts
