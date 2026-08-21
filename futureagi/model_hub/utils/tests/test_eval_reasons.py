"""Tests for the reason-column name parse in ``model_hub.utils.eval_reasons``.

Reason columns are created as ``f"{eval_name}-reason"`` (see
``get_eval_reasons``). To recover the eval name, the trailing ``-reason``
suffix must be stripped. Using ``name.split("-reason")[0]`` truncates any
eval whose own name contains ``-reason`` (e.g. ``"no-reason-check"`` ->
``"no"``), which then fails to join back to its eval-value column and drops
the eval from the explanation summary. ``eval_name_from_reason_column_name``
strips only the trailing suffix.
"""

from model_hub.utils.eval_reasons import eval_name_from_reason_column_name


def test_plain_name():
    assert eval_name_from_reason_column_name("toxicity-reason") == "toxicity"


def test_name_containing_reason_is_not_truncated():
    # Regression: split("-reason")[0] returned "no" here.
    assert (
        eval_name_from_reason_column_name("no-reason-check-reason")
        == "no-reason-check"
    )


def test_name_ending_in_reason_word():
    assert (
        eval_name_from_reason_column_name("gives-reason-reason") == "gives-reason"
    )


def test_only_trailing_suffix_stripped():
    # Multiple internal occurrences, single trailing strip.
    assert (
        eval_name_from_reason_column_name("a-reason-b-reason-c-reason")
        == "a-reason-b-reason-c"
    )
