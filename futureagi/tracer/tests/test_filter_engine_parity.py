"""Parity tests: PG FilterEngine speaks the same SPAN_ATTRIBUTE filter
vocabulary as the CH ClickHouseFilterBuilder.

Sourced from `SPAN_ATTR_ALLOWED_OPS` in `tracer/utils/constants.py`.
"""

import pytest
from django.db.models import Q

from tracer.utils.constants import SPAN_ATTR_ALLOWED_OPS
from tracer.utils.filters import ColType, FilterEngine


def _sample_value(ftype, op):
    """Reasonable value for an op × type combination."""
    if op in ("is_null", "is_not_null"):
        return None
    if ftype == "text":
        if op in ("in", "not_in"):
            return ["alpha", "beta"]
        return "hello"
    if ftype == "number":
        if op in ("between", "not_between"):
            return [10, 50]
        return 42
    if ftype == "boolean":
        return True
    raise AssertionError(f"unhandled {ftype} {op}")


def _make_span_attr_filter(ftype, op, value=None):
    if value is None and op not in ("is_null", "is_not_null"):
        value = _sample_value(ftype, op)
    return {
        "column_id": "test_attr",
        "filter_config": {
            "col_type": ColType.SPAN_ATTRIBUTE.value,
            "filter_type": ftype,
            "filter_op": op,
            "filter_value": value,
        },
    }


class TestSpanAttrParity:
    """get_filter_conditions_for_span_attributes accepts every canonical op."""

    @pytest.mark.parametrize(
        "ftype,op",
        [(t, op) for t, ops in SPAN_ATTR_ALLOWED_OPS.items() for op in ops],
    )
    def test_canonical_op_produces_non_empty_q(self, ftype, op):
        flt = _make_span_attr_filter(ftype, op)
        q = FilterEngine.get_filter_conditions_for_span_attributes([flt])
        assert isinstance(q, Q)
        # Empty Q has no children; non-empty op should produce at least one.
        assert len(q.children) > 0, f"{ftype} / {op} produced an empty Q tree"

    def test_text_in_uses_list_membership(self):
        """`in` should produce a __in lookup, not a substring filter."""
        flt = _make_span_attr_filter("text", "in", ["alpha", "beta"])
        q = FilterEngine.get_filter_conditions_for_span_attributes([flt])
        assert "__in" in str(q)
        assert "icontains" not in str(q)

    def test_number_not_between_canonical(self):
        flt = _make_span_attr_filter("number", "not_between", [10, 50])
        q = FilterEngine.get_filter_conditions_for_span_attributes([flt])
        # Should reference both bounds via __gte/__lte
        s = str(q)
        assert "__gte" in s and "__lte" in s

    def test_boolean_strict_native_only(self):
        """Boolean filter_value must be a native bool; strings get rejected."""
        flt = _make_span_attr_filter("boolean", "equals", value="true")
        q = FilterEngine.get_filter_conditions_for_span_attributes([flt])
        # Strict path returns empty Q on bad value (validator returns False).
        assert q.children == []

    def test_boolean_native_true_accepted(self):
        flt = _make_span_attr_filter("boolean", "equals", value=True)
        q = FilterEngine.get_filter_conditions_for_span_attributes([flt])
        assert len(q.children) > 0

    @pytest.mark.parametrize("ftype", list(SPAN_ATTR_ALLOWED_OPS.keys()))
    def test_is_null_produces_has_key_negation(self, ftype):
        flt = _make_span_attr_filter(ftype, "is_null")
        q = FilterEngine.get_filter_conditions_for_span_attributes([flt])
        assert "has_key" in str(q)

    @pytest.mark.parametrize("ftype", list(SPAN_ATTR_ALLOWED_OPS.keys()))
    def test_is_not_null_produces_has_key(self, ftype):
        flt = _make_span_attr_filter(ftype, "is_not_null")
        q = FilterEngine.get_filter_conditions_for_span_attributes([flt])
        assert "has_key" in str(q)


class TestLegacyOpsRejected:
    """Legacy op names must NOT be accepted by the canonical-only PG path."""

    @pytest.mark.parametrize(
        "legacy_op",
        ["is", "is_not", "equal_to", "not_equal_to", "not_in_between"],
    )
    def test_legacy_op_produces_empty_q(self, legacy_op):
        """Unknown ops fall through the operator_map lookup and contribute
        nothing to the result Q. This is the canonical-only contract."""
        ftype = "number" if legacy_op == "not_in_between" else "text"
        flt = _make_span_attr_filter(ftype, legacy_op, value="foo")
        q = FilterEngine.get_filter_conditions_for_span_attributes([flt])
        assert q.children == []


class TestSystemMetricAliases:
    @pytest.mark.parametrize(
        ("column_id", "orm_field"),
        [
            ("latency_ms", "row_avg_latency_ms"),
            ("latency", "row_avg_latency_ms"),
            ("cost", "row_avg_cost"),
            ("tokens", "total_tokens"),
            ("prompt_tokens", "avg_input_tokens"),
            ("completion_tokens", "avg_output_tokens"),
        ],
    )
    def test_pg_filter_engine_accepts_frontend_and_canonical_ids(
        self, column_id, orm_field
    ):
        q = FilterEngine.get_filter_conditions_for_system_metrics(
            [
                {
                    "column_id": column_id,
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 0,
                        "col_type": ColType.SYSTEM_METRIC.value,
                    },
                }
            ]
        )

        assert orm_field in str(q)


class TestInMemoryFilterOps:
    """In-memory `_filter_*` methods accept the canonical vocabulary."""

    def test_filter_number_not_between(self):
        engine = FilterEngine([])
        objs = [{"x": 5}, {"x": 20}, {"x": 80}, {"x": 100}]
        out = engine._filter_number(objs, "x", "not_between", [10, 50], ColType.NORMAL)
        assert {obj["x"] for obj in out} == {5, 80, 100}

    def test_filter_number_is_null(self):
        engine = FilterEngine([])
        objs = [{"x": 5}, {"x": None}, {"y": 1}]
        out = engine._filter_number(objs, "x", "is_null", None, ColType.NORMAL)
        assert len(out) == 2  # x:None and missing x

    def test_filter_number_is_not_null(self):
        engine = FilterEngine([])
        objs = [{"x": 5}, {"x": None}, {"y": 1}]
        out = engine._filter_number(objs, "x", "is_not_null", None, ColType.NORMAL)
        assert len(out) == 1  # only x:5

    def test_filter_text_in_proper_set_membership(self):
        engine = FilterEngine([])
        objs = [{"k": "alpha"}, {"k": "beta"}, {"k": "gamma"}]
        out = engine._filter_text(objs, "k", "in", ["alpha", "beta"], ColType.NORMAL)
        assert {obj["k"] for obj in out} == {"alpha", "beta"}

    def test_filter_text_not_in(self):
        engine = FilterEngine([])
        objs = [{"k": "alpha"}, {"k": "beta"}, {"k": "gamma"}]
        out = engine._filter_text(objs, "k", "not_in", ["alpha"], ColType.NORMAL)
        assert {obj["k"] for obj in out} == {"beta", "gamma"}

    def test_filter_text_is_null(self):
        engine = FilterEngine([])
        objs = [{"k": "alpha"}, {"k": ""}, {"k": None}, {}]
        out = engine._filter_text(objs, "k", "is_null", None, ColType.NORMAL)
        assert len(out) == 3  # "", None, missing

    def test_filter_boolean_native_true(self):
        engine = FilterEngine([])
        objs = [{"b": True}, {"b": False}, {"b": "true"}]
        out = engine._filter_boolean(objs, "b", True, ColType.NORMAL, "equals")
        # Strict native bool: only `True` matches; `"true"` string is ignored.
        assert out == [{"b": True}]

    def test_filter_boolean_is_null(self):
        engine = FilterEngine([])
        objs = [{"b": True}, {"b": None}, {}]
        out = engine._filter_boolean(objs, "b", None, ColType.NORMAL, "is_null")
        assert len(out) == 2  # None and missing


class TestLegacyOpsRaiseInMemory:
    """In-memory filters must reject legacy ops too."""

    def test_filter_number_rejects_not_in_between(self):
        engine = FilterEngine([])
        with pytest.raises(ValueError):
            engine._filter_number([{"x": 1}], "x", "not_in_between", [0, 10], ColType.NORMAL)

    def test_filter_text_rejects_is(self):
        engine = FilterEngine([])
        with pytest.raises(ValueError):
            engine._filter_text([{"k": "v"}], "k", "is", "v", ColType.NORMAL)
