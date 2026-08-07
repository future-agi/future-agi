"""
Regression pins for the whale-tenant attribute-filter timeout (2026-07-24).

A SPAN_ATTRIBUTE filter (`customer_id = 26065846`) on the trace list made
every query 400 with CH Code 159: the `trace_id IN (SELECT … FROM spans …)`
membership subquery scanned the project's ENTIRE span history (157M+ rows,
19+ GiB of attrs maps) inside the 10s execution budget.

Two distinct emitters were at fault:

1. The v2 filter compiler bounded the subquery on ``created_at`` — correct
   for the v1 table (partitioned by ``toYYYYMM(created_at)``) but a no-op on
   the CH25 table, which is partitioned by ``toDate(start_time)`` with
   ``toStartOfHour(start_time)`` in the primary key and no index at all on
   ``created_at``.

2. ``TimeSeriesQueryBuilder`` built its filter compiler with no project and
   no date scope, so the graph query's subquery was a full cross-tenant
   table scan (``WHERE 1 = 1 AND … mapContains(attrs_number, …)``).
"""

from __future__ import annotations

from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

PROJECT_ID = "11111111-1111-1111-1111-111111111111"

DATETIME_FILTER = {
    "column_id": "created_at",
    "filter_config": {
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": ["2026-06-24T17:23:59.000Z", "2026-07-24T18:30:00.000Z"],
    },
}

SPAN_ATTR_FILTER = {
    "column_id": "customer_id",
    "filter_config": {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "number",
        "filter_op": "equals",
        "filter_value": 26065846,
    },
}


def _membership_subquery(sql: str) -> str:
    """Return the balanced text of the ``trace_id IN (...)`` subquery only,
    so negative assertions can't accidentally match outer WHERE clauses."""
    marker = "trace_id IN ("
    assert marker in sql, f"no membership subquery emitted:\n{sql}"
    start = sql.index(marker) + len(marker)
    depth = 1
    for i in range(start, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[start:i]
    raise AssertionError(f"unbalanced membership subquery:\n{sql}")


class TestV2MembershipSubqueryBounds:
    def test_span_attr_membership_bounds_on_start_time(self):
        builder = TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_number=0,
            page_size=10,
            filters=[DATETIME_FILTER, SPAN_ATTR_FILTER],
            sort_params=[],
            eval_config_ids=[],
            annotation_label_ids=[],
        )
        sql, params = builder.build()
        sub = _membership_subquery(sql)
        assert "start_time >= %(start_date)s - INTERVAL 1 DAY" in sub
        assert "start_time < %(end_date)s + INTERVAL 1 DAY" in sub
        assert "created_at >=" not in sub
        assert "start_date" in params
        assert "end_date" in params

    def test_system_metric_membership_bounds_on_start_time(self):
        builder = TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_number=0,
            page_size=10,
            filters=[
                DATETIME_FILTER,
                {
                    "column_id": "model",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4o",
                    },
                },
            ],
            sort_params=[],
            eval_config_ids=[],
            annotation_label_ids=[],
        )
        sql, _ = builder.build()
        sub = _membership_subquery(sql)
        assert "start_time >= %(start_date)s - INTERVAL 1 DAY" in sub
        assert "start_time < %(end_date)s + INTERVAL 1 DAY" in sub
        assert "created_at >=" not in sub


class TestTimeSeriesAttrFilterScope:
    def test_attr_subquery_is_project_scoped_and_time_bounded(self):
        builder = TimeSeriesQueryBuilder(
            project_id=PROJECT_ID,
            filters=[DATETIME_FILTER, SPAN_ATTR_FILTER],
            interval="day",
        )
        sql, params = builder.build()
        sub = _membership_subquery(sql)
        assert "project_id = %(project_id)s" in sub
        assert "start_time >= %(start_date)s - INTERVAL 1 DAY" in sub
        assert "start_time < %(end_date)s + INTERVAL 1 DAY" in sub
        assert "1 = 1" not in sub
        assert params["project_id"] == PROJECT_ID
        assert "start_date" in params


STR_EQ_FILTER = {
    "column_id": "session_name",
    "filter_config": {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "Checkout Flow",
    },
}


def _v2_sql(attr_filter):
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_number=0,
        page_size=10,
        filters=[DATETIME_FILTER, attr_filter],
        sort_params=[],
        eval_config_ids=[],
        annotation_label_ids=[],
    )
    return builder.build()


def _with_op(op, value):
    f = {
        "column_id": "session_name",
        "filter_config": dict(STR_EQ_FILTER["filter_config"]),
    }
    f["filter_config"]["filter_op"] = op
    f["filter_config"]["filter_value"] = value
    return f


class TestLoweredStringValueCompanion:
    """Text equality/IN must emit a companion predicate matching
    idx_attrs_str_values (bloom over arrayMap(x -> lower(x),
    mapValues(attrs_string))) — the lower()-wrapped comparison alone can
    never engage a skip index. The companion is implied by the real
    predicate, so result sets are unchanged."""

    def test_equals_emits_lowered_has_companion(self):
        sql, params = _v2_sql(STR_EQ_FILTER)
        assert "has(arrayMap(x -> lower(x), mapValues(attrs_string))" in sql
        # the companion constant is lowercased like the equality constant
        assert "checkout flow" in [v for v in params.values() if isinstance(v, str)]

    def test_in_emits_lowered_hasany_companion(self):
        sql, params = _v2_sql(_with_op("in", ["Checkout Flow", "ONBOARDING"]))
        assert "hasAny(arrayMap(x -> lower(x), mapValues(attrs_string)), [" in sql
        flat = [v for v in params.values() if isinstance(v, str)]
        assert "checkout flow" in flat
        assert "onboarding" in flat

    def test_not_equals_has_no_companion(self):
        # a has() companion on a negation would invert semantics — must
        # never be emitted
        sql, _ = _v2_sql(_with_op("not_equals", "Checkout Flow"))
        assert "arrayMap(x -> lower(x)" not in sql

    def test_contains_has_no_companion(self):
        # plain bloom does exact membership; substring ops get no companion
        sql, _ = _v2_sql(_with_op("contains", "heckout"))
        assert "arrayMap(x -> lower(x)" not in sql

    def test_number_equality_unchanged(self):
        sql, _ = _v2_sql(SPAN_ATTR_FILTER)
        assert "arrayMap(x -> lower(x)" not in sql

    def test_v1_builder_emits_no_companion(self):
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        fb = ClickHouseFilterBuilder(table="spans", project_id=PROJECT_ID)
        sql, _ = fb.translate([STR_EQ_FILTER])
        assert "arrayMap" not in sql
