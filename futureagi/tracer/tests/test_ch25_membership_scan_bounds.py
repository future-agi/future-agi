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

import pytest

from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
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

FINAL_STATUS_FILTER = {
    "column_id": "final_status",
    "filter_config": {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "completed",
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

    def test_candidate_trace_ids_bound_outer_and_membership_reads(self):
        candidate_ids = [
            "22222222-2222-2222-2222-222222222222",
            "33333333-3333-3333-3333-333333333333",
        ]
        builder = TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_number=0,
            page_size=10,
            filters=[DATETIME_FILTER, STR_EQ_FILTER],
            candidate_trace_ids=candidate_ids,
        )

        sql, params = builder.build()
        sub = _membership_subquery(sql)

        assert "trace_id IN %(candidate_trace_ids)s" in sub
        # The same fixed-size set also bounds the outer root lookup.
        assert sql.count("trace_id IN %(candidate_trace_ids)s") == 2
        assert "LIMIT 1 BY trace_id" in sql
        assert params["candidate_trace_ids"] == tuple(candidate_ids)


class TestTraceRootAttributeFastPath:
    @pytest.mark.parametrize(
        ("column_id", "physical_column"),
        [
            ("latency", "latency_ms"),
            ("name", "name"),
        ],
    )
    def test_root_system_metrics_apply_to_outer_root(self, column_id, physical_column):
        metric_filter = {
            "column_id": column_id,
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text" if column_id != "latency" else "number",
                "filter_op": "equals",
                "filter_value": "ERROR" if column_id != "latency" else 100,
            },
        }
        sql, _ = TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_number=0,
            page_size=10,
            filters=[DATETIME_FILTER, metric_filter],
        ).build()
        compact_sql = " ".join(sql.split())

        expected_predicate = (
            f"lower({physical_column}) ="
            if column_id == "name"
            else f"{physical_column} ="
        )
        assert expected_predicate in compact_sql
        assert "trace_id IN (SELECT trace_id FROM spans" not in compact_sql

    def test_final_status_predicates_scoped_root_row_directly(self):
        sql, params = TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_number=0,
            page_size=10,
            filters=[DATETIME_FILTER, FINAL_STATUS_FILTER],
        ).build()
        compact_sql = " ".join(sql.split())

        assert "trace_id IN (SELECT trace_id FROM spans" not in compact_sql
        assert "(parent_span_id IS NULL OR parent_span_id = '')" in compact_sql
        assert "mapContains(attrs_string, 'final_status')" in compact_sql
        assert "attrs_string['final_status']" in compact_sql
        assert "mapValues(attrs_string)" not in compact_sql
        assert "project_id = %(project_id)s" in compact_sql
        assert "start_time >= %(start_date)s" in compact_sql
        assert "start_time < %(end_date)s" in compact_sql
        assert params["project_id"] == PROJECT_ID

    def test_unverified_country_keeps_bounded_membership_fallback(self):
        country_filter = {
            "column_id": "country",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "US",
            },
        }
        sql, _ = TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_number=0,
            page_size=10,
            filters=[DATETIME_FILTER, country_filter],
        ).build()
        sub = _membership_subquery(sql)

        assert "mapContains(attrs_string, 'country')" in sub
        assert "start_time >= %(start_date)s - INTERVAL 1 DAY" in sub
        assert "start_time < %(end_date)s + INTERVAL 1 DAY" in sub

    def test_span_final_status_keeps_row_semantics_and_index_companion(self):
        sql, params = SpanListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_number=0,
            page_size=10,
            filters=[DATETIME_FILTER, FINAL_STATUS_FILTER],
        ).build()
        compact_sql = " ".join(sql.split())

        assert "trace_id IN (SELECT trace_id FROM spans" not in compact_sql
        assert "(parent_span_id IS NULL OR parent_span_id = '')" not in compact_sql
        assert "mapContains(attrs_string, 'final_status')" in compact_sql
        assert "has(arrayMap(x -> lower(x), mapValues(attrs_string))" in compact_sql
        assert "project_id = %(project_id)s" in compact_sql
        assert "start_time >= %(start_date)s" in compact_sql
        assert "start_time < %(end_date)s" in compact_sql
        assert params["project_id"] == PROJECT_ID

    def test_non_root_attribute_keeps_bounded_membership_fallback(self):
        sql, _ = TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_number=0,
            page_size=10,
            filters=[DATETIME_FILTER, STR_EQ_FILTER],
        ).build()
        sub = _membership_subquery(sql)

        assert "start_time >= %(start_date)s - INTERVAL 1 DAY" in sub
        assert "start_time < %(end_date)s + INTERVAL 1 DAY" in sub
        assert "mapContains(attrs_string, 'session_name')" in sub


class TestTimeSeriesAttrFilterScope:
    def test_attr_candidate_discovery_is_project_scoped_time_bounded_and_capped(self):
        builder = TimeSeriesQueryBuilder(
            project_id=PROJECT_ID,
            filters=[DATETIME_FILTER, SPAN_ATTR_FILTER],
            interval="day",
        )
        sql, params = builder.build()
        assert builder.query_source == "trace_candidate_plan"
        assert "FROM spans FINAL" in sql
        assert "project_id = %(project_id)s" in sql
        assert "start_time >= %(start_date)s" in sql
        assert "start_time < %(end_date)s" in sql
        assert "graph_candidate_attr_0_attr_1" in sql
        assert params["graph_candidate_attr_0_attr_1"] == 26065846.0
        assert "LIMIT %(graph_trace_candidate_limit)s" in sql
        assert "trace_id IN (" not in sql
        assert params["project_id"] == PROJECT_ID
        assert "start_date" in params
        assert params["graph_trace_candidate_limit"] == 1


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
    """ASCII text equality/IN may emit a companion predicate matching
    idx_attrs_str_values (bloom over arrayMap(x -> lower(x),
    mapValues(attrs_string))) — the lower()-wrapped comparison alone can
    never engage a skip index. The companion is implied by the real
    predicate, so result sets are unchanged. Unicode-aware substring
    predicates cannot use the ASCII-lowered ngram companion safely."""

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

    def test_contains_omits_ascii_only_ngram_companion(self):
        sql, _ = _v2_sql(_with_op("contains", "heckout"))
        assert "positionUTF8(lowerUTF8" in sql
        assert "arrayStringConcat" not in sql

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
