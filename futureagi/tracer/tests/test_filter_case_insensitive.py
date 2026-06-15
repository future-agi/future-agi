"""TH-4993 — text-type filters must match case-insensitively.

Covers both SYSTEM_METRIC text columns (trace_name, name, model, provider)
and SPAN_ATTRIBUTE filter_type="text". Non-text types (number, boolean,
UUIDs) and non-CI columns (e.g. span_id) must stay literal.
"""

import pytest

from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
)


def _builder(mode: str = "span") -> ClickHouseFilterBuilder:
    return ClickHouseFilterBuilder(table="spans", query_mode=mode)


def _sm(col_id, op, value, ftype="text"):
    return {
        "column_id": col_id,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": ftype,
            "filter_op": op,
            "filter_value": value,
        },
    }


def _attr(key, op, value, ftype="text"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": ftype,
            "filter_op": op,
            "filter_value": value,
        },
    }


@pytest.mark.unit
class TestSystemMetricTextCaseInsensitive:
    """trace_name / name / model / provider — TH-4993 ticket scope."""

    @pytest.mark.parametrize("col", ["trace_name", "name", "model", "provider"])
    def test_contains_uses_ilike(self, col):
        where, params = _builder().translate([_sm(col, "contains", "FooBar")])
        assert f"{col} ILIKE" in where
        assert params["col_1"] == "%FooBar%"

    @pytest.mark.parametrize("col", ["trace_name", "name", "model", "provider"])
    def test_not_contains_uses_not_ilike(self, col):
        where, params = _builder().translate([_sm(col, "not_contains", "Foo")])
        assert f"{col} NOT ILIKE" in where
        assert params["col_1"] == "%Foo%"

    @pytest.mark.parametrize("col", ["trace_name", "name", "model", "provider"])
    def test_starts_with_uses_ilike(self, col):
        where, _ = _builder().translate([_sm(col, "starts_with", "Foo")])
        assert f"{col} ILIKE" in where

    @pytest.mark.parametrize("col", ["trace_name", "name", "model", "provider"])
    def test_ends_with_uses_ilike(self, col):
        where, _ = _builder().translate([_sm(col, "ends_with", "Bar")])
        assert f"{col} ILIKE" in where

    @pytest.mark.parametrize("col", ["trace_name", "name", "model", "provider"])
    def test_equals_lowers_both_sides(self, col):
        where, params = _builder().translate([_sm(col, "equals", "FooBar")])
        assert f"lower({col}) =" in where
        assert params["col_1"] == "foobar"

    @pytest.mark.parametrize("col", ["trace_name", "name", "model", "provider"])
    def test_not_equals_lowers_both_sides(self, col):
        where, params = _builder().translate([_sm(col, "not_equals", "FooBar")])
        assert f"lower({col}) !=" in where
        assert params["col_1"] == "foobar"

    @pytest.mark.parametrize("col", ["trace_name", "name", "model", "provider"])
    def test_in_lowers_values(self, col):
        where, params = _builder().translate(
            [_sm(col, "in", ["GPT-4", "Claude"])]
        )
        assert f"lower({col}) IN" in where
        assert params["col_1"] == ("gpt-4", "claude")

    @pytest.mark.parametrize("col", ["trace_name", "name", "model", "provider"])
    def test_not_in_lowers_values(self, col):
        where, params = _builder().translate(
            [_sm(col, "not_in", ["GPT-4", "Claude"])]
        )
        assert f"lower({col}) NOT IN" in where
        assert params["col_1"] == ("gpt-4", "claude")

    def test_status_remains_case_insensitive(self):
        """status was already CI before TH-4993; behavior must be preserved."""
        where, params = _builder().translate([_sm("status", "equals", "ERROR")])
        assert "lower(status) =" in where
        assert params["col_1"] == "error"

    def test_trace_mode_wraps_in_subquery_with_ilike(self):
        where, params = _builder("trace").translate(
            [_sm("trace_name", "contains", "Foo")]
        )
        # Trace-list mode wraps the predicate in `trace_id IN (SELECT ...)`,
        # the ILIKE belongs inside that wrap.
        assert "trace_id IN (SELECT trace_id FROM spans" in where
        assert "trace_name ILIKE" in where

    def test_span_id_not_case_folded(self):
        """UUID-shaped columns must not be lowercased."""
        where, _ = _builder().translate([_sm("span_id", "equals", "ABC")])
        assert "lower(" not in where


@pytest.mark.unit
class TestSpanAttributeTextCaseInsensitive:
    """SPAN_ATTRIBUTE filter_type='text' — TH-4993 (broad scope)."""

    def test_equals_lowers_both_sides(self):
        where, params = _builder().translate([_attr("mykey", "equals", "Hello")])
        assert "lower(span_attr_str['mykey']) =" in where
        assert params["attr_1"] == "hello"

    def test_not_equals_lowers_both_sides(self):
        where, params = _builder().translate(
            [_attr("mykey", "not_equals", "Hello")]
        )
        assert "lower(span_attr_str['mykey']) !=" in where
        assert params["attr_1"] == "hello"

    def test_in_lowers_values(self):
        where, params = _builder().translate(
            [_attr("mykey", "in", ["Hello", "World"])]
        )
        assert "lower(span_attr_str['mykey']) IN" in where
        assert params["attr_1"] == ("hello", "world")

    def test_not_in_lowers_values(self):
        where, params = _builder().translate(
            [_attr("mykey", "not_in", ["Hello", "World"])]
        )
        assert "lower(span_attr_str['mykey']) NOT IN" in where
        assert params["attr_1"] == ("hello", "world")

    def test_contains_uses_ilike(self):
        where, params = _builder().translate(
            [_attr("mykey", "contains", "Hello")]
        )
        assert "span_attr_str['mykey'] ILIKE" in where
        assert params["attr_1"] == "%Hello%"

    def test_not_contains_uses_not_ilike(self):
        where, params = _builder().translate(
            [_attr("mykey", "not_contains", "Hello")]
        )
        assert "span_attr_str['mykey'] NOT ILIKE" in where
        assert params["attr_1"] == "%Hello%"

    def test_starts_with_uses_ilike(self):
        where, _ = _builder().translate(
            [_attr("mykey", "starts_with", "Hel")]
        )
        assert "span_attr_str['mykey'] ILIKE" in where

    def test_ends_with_uses_ilike(self):
        where, _ = _builder().translate([_attr("mykey", "ends_with", "lo")])
        assert "span_attr_str['mykey'] ILIKE" in where

    def test_exists_predicate_still_present(self):
        """Case-insensitive predicate must still guard with mapContains."""
        where, _ = _builder().translate([_attr("mykey", "equals", "X")])
        assert "mapContains(span_attr_str, 'mykey')" in where

    def test_number_attr_not_case_folded(self):
        where, _ = _builder().translate(
            [_attr("mykey", "equals", 5, ftype="number")]
        )
        assert "lower(" not in where
        assert "span_attr_num['mykey'] =" in where

    def test_boolean_attr_not_case_folded(self):
        where, _ = _builder().translate(
            [_attr("mykey", "equals", True, ftype="boolean")]
        )
        assert "lower(" not in where
        assert "span_attr_bool['mykey'] =" in where
