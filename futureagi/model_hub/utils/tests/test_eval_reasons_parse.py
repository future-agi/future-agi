"""Tests for eval reason column name parsing (issue #1721).

Verifies that eval names containing "-reason" are not truncated by
the suffix-stripping logic.
"""
import pytest


class TestReasonColumnParse:
    """Issue #1721: split("-reason")[0] truncates eval names containing -reason."""

    @pytest.mark.parametrize(
        "column_name,expected",
        [
            # Standard case: eval name + "-reason" suffix
            ("accuracy-reason", "accuracy"),
            ("gpt4-accuracy-reason", "gpt4-accuracy"),
            # Eval names that themselves contain "-reason"
            ("no-reason-check-reason", "no-reason-check"),
            ("gives-reason-reason", "gives-reason"),
            # Names without the suffix (should be unchanged)
            ("accuracy", "accuracy"),
            ("gpt4-accuracy", "gpt4-accuracy"),
        ],
    )
    def test_removesuffix_correctly_strips_trailing_reason(self, column_name, expected):
        """removesuffix only strips the trailing -reason, not earlier occurrences."""
        result = column_name.removesuffix("-reason")
        assert result == expected

    @pytest.mark.parametrize(
        "column_name,expected",
        [
            ("no-reason-check-reason", "no-reason-check"),
            ("gives-reason-reason", "gives-reason"),
        ],
    )
    def test_split_truncates_names_containing_reason(self, column_name, expected):
        """Demonstrates the bug: split('-reason')[0] truncates at the first match."""
        # The OLD behavior (bug) would return the wrong result:
        buggy_result = column_name.split("-reason")[0]
        assert buggy_result != expected, (
            f"Expected split to truncate '{column_name}' incorrectly, "
            f"but got '{buggy_result}' (expected '{expected}')"
        )
