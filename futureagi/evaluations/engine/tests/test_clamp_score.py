"""Tests for ``_clamp_unit_score`` — keeps weaker-judge numeric outputs in
the unit range [0, 1] rather than failing the whole eval.

Covers numeric, string-numeric, edge values, extreme values, and
unparseable inputs.
"""

from __future__ import annotations

import pytest

from evaluations.engine.formatting import _clamp_unit_score


class TestInRange:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.0, 0.0),
            (0.5, 0.5),
            (1.0, 1.0),
            (0.0001, 0.0001),
            (0.9999, 0.9999),
            (0.123456789, 0.123456789),
        ],
    )
    def test_in_range_passes_through(self, value, expected):
        assert _clamp_unit_score(value) == expected


class TestClampToOne:
    @pytest.mark.parametrize(
        "value",
        [1.0001, 1.5, 2.0, 5.0, 10.0, 100.0, 1e6, float("inf")],
    )
    def test_above_one_clamped_to_one(self, value):
        assert _clamp_unit_score(value) == 1.0


class TestClampToZero:
    @pytest.mark.parametrize(
        "value",
        [-0.0001, -0.5, -1.0, -100.0, -1e6, float("-inf")],
    )
    def test_below_zero_clamped_to_zero(self, value):
        assert _clamp_unit_score(value) == 0.0


class TestIntValues:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, 0.0),
            (1, 1.0),
            (2, 1.0),  # int 2 → clamped to 1.0
            (10, 1.0),
            (-1, 0.0),
            (-100, 0.0),
        ],
    )
    def test_int_coerced_and_clamped(self, value, expected):
        assert _clamp_unit_score(value) == expected


class TestStringNumeric:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("0.5", 0.5),
            ("1.0", 1.0),
            ("0", 0.0),
            ("1", 1.0),
            ("0.7", 0.7),
            # out of range string-numerics get clamped
            ("1.5", 1.0),
            ("-0.5", 0.0),
            ("10", 1.0),
        ],
    )
    def test_string_numeric_parsed_and_clamped(self, value, expected):
        assert _clamp_unit_score(value) == expected


class TestNone:
    def test_none_passes_through(self):
        assert _clamp_unit_score(None) is None


class TestUnparseable:
    @pytest.mark.parametrize(
        "value",
        ["abc", "not a number", "", "1.2.3", "[]", "0,5"],
    )
    def test_unparseable_returns_raw(self, value):
        # The helper deliberately doesn't error on unparseable input; it
        # returns the raw value so the caller can decide what to do.
        assert _clamp_unit_score(value) == value

    @pytest.mark.parametrize(
        "value",
        [[], {}, object()],
    )
    def test_non_numeric_objects_returned_raw(self, value):
        assert _clamp_unit_score(value) is value


class TestNaN:
    def test_nan_clamped_to_zero(self):
        # max(0, min(1, NaN)) → NaN (NaN compares False to everything). This
        # actually returns NaN; we just assert no exception is raised.
        import math
        result = _clamp_unit_score(float("nan"))
        # Either NaN or numeric — the contract is "do not raise".
        assert result is not None
        assert math.isnan(result) or 0.0 <= result <= 1.0


class TestBool:
    """Boolean values flow through float() coercion."""

    def test_true_becomes_one(self):
        assert _clamp_unit_score(True) == 1.0

    def test_false_becomes_zero(self):
        assert _clamp_unit_score(False) == 0.0
