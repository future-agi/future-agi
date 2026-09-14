"""``is_null``/``is_not_null`` on ``has_eval``/``has_annotation`` must be a 400."""

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)

# Presence-only columns, compiled for the trace list (v1) and span list (v2).
pytestmark = [
    pytest.mark.unit,
    pytest.mark.parametrize(
        "builder_cls", [ClickHouseFilterBuilder, ClickHouseFilterBuilderV2]
    ),
    pytest.mark.parametrize("column_id", ["has_eval", "has_annotation"]),
]


def _translate(builder_cls, column_id, filter_op):
    config = {"filter_type": "boolean", "filter_op": filter_op, "filter_value": True}
    return builder_cls(
        project_id="p1", candidate_ids_param="candidate_trace_ids"
    ).translate([{"column_id": column_id, "filter_config": config}])


@pytest.mark.parametrize("filter_op", ["is_null", "is_not_null"])
def test_null_operator_raises_invalid_filter_shape(builder_cls, column_id, filter_op):
    with pytest.raises(UnsupportedFilterShapeError, match="only the equals operation"):
        _translate(builder_cls, column_id, filter_op)


def test_equals_still_compiles_a_membership_predicate(builder_cls, column_id):
    where, _ = _translate(builder_cls, column_id, "equals")
    assert "trace_id IN" in where
    assert "trace_id NOT IN" not in where
