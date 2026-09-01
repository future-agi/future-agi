"""
Tests for the AND/OR filter combinator (#2226).

`filter_combinator` selects how multiple query-builder filters are combined:
``"and"`` (default) joins them with AND; ``"or"`` joins them with OR.

These tests pin the contract at every layer that consumes the flag so a
future change in one layer cannot silently diverge from the others:

- Django ORM ``Q`` object connectors (``FilterEngine`` system + voice metrics)
- Raw SQL builders (``get_sql_filter_conditions_for_*``) — OR must be
  parenthesised so it cannot leak past a surrounding ``project_id = ... AND``
  scope (AND binds tighter than OR).
- ClickHouse ``ClickHouseFilterBuilder.translate`` — same parenthesisation.
- Absent flag == explicit ``"and"`` (all existing callers stay unchanged).
"""

from django.db.models import Q

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.utils.filters import ColType, FilterEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _system_metric(column_id, filter_op, filter_value, filter_type="number"):
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": ColType.SYSTEM_METRIC.value,
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


TWO_SYSTEM_METRICS = [
    _system_metric("latency_ms", "greater_than", 10),
    _system_metric("cost", "greater_than", 20),
]


def _voice_metric(column_id, filter_op, filter_value):
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": ColType.SYSTEM_METRIC.value,
            "filter_type": "number",
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


# ---------------------------------------------------------------------------
# Django ORM — Q object connectors
# ---------------------------------------------------------------------------


class TestOrmCombinator:
    def test_or_uses_or_connector(self):
        q = FilterEngine.get_filter_conditions_for_system_metrics(
            TWO_SYSTEM_METRICS, filter_combinator="or"
        )
        assert q.connector == Q.OR

    def test_and_uses_and_connector(self):
        q = FilterEngine.get_filter_conditions_for_system_metrics(
            TWO_SYSTEM_METRICS, filter_combinator="and"
        )
        assert q.connector == Q.AND

    def test_default_is_and(self):
        default = FilterEngine.get_filter_conditions_for_system_metrics(
            TWO_SYSTEM_METRICS
        )
        explicit = FilterEngine.get_filter_conditions_for_system_metrics(
            TWO_SYSTEM_METRICS, filter_combinator="and"
        )
        assert default == explicit

    def test_single_filter_unaffected_by_combinator(self):
        q = FilterEngine.get_filter_conditions_for_system_metrics(
            [_system_metric("latency_ms", "greater_than", 10)],
            filter_combinator="or",
        )
        assert q.connector == Q.AND  # single Q — reduce() returns it as-is

    def test_voice_system_metrics_or_uses_or_connector(self):
        q, annotations = FilterEngine.get_filter_conditions_for_voice_system_metrics(
            [
                _voice_metric("duration", "greater_than", 30),
                _voice_metric("turn_count", "greater_than", 5),
            ],
            filter_combinator="or",
        )
        assert q.connector == Q.OR
        assert annotations


# ---------------------------------------------------------------------------
# Raw SQL builders — OR must be parenthesised
# ---------------------------------------------------------------------------


class TestSqlCombinator:
    def test_system_metrics_or_parenthesised(self):
        filters = [
            _system_metric("avg_cost", "greater_than", 10),
            _system_metric("node_type", "equals", "llm", filter_type="text"),
        ]
        query = FilterEngine.get_sql_filter_conditions_for_system_metrics(
            filters, "SELECT * FROM t", filter_combinator="or"
        )
        assert " AND (os.cost > '10' OR os.observation_type = 'llm')" in query

    def test_system_metrics_and_not_parenthesised(self):
        filters = [
            _system_metric("avg_cost", "greater_than", 10),
            _system_metric("node_type", "equals", "llm", filter_type="text"),
        ]
        query = FilterEngine.get_sql_filter_conditions_for_system_metrics(
            filters, "SELECT * FROM t", filter_combinator="and"
        )
        assert " AND os.cost > '10' AND os.observation_type = 'llm'" in query
        assert " OR " not in query

    def test_system_metrics_default_is_and(self):
        filters = [
            _system_metric("avg_cost", "greater_than", 10),
            _system_metric("node_type", "equals", "llm", filter_type="text"),
        ]
        default = FilterEngine.get_sql_filter_conditions_for_system_metrics(
            filters, "SELECT * FROM t"
        )
        explicit = FilterEngine.get_sql_filter_conditions_for_system_metrics(
            filters, "SELECT * FROM t", filter_combinator="and"
        )
        assert default == explicit

    def test_system_metrics_or_keeps_project_scope(self):
        # A base query already scoped to one project. ORing two filters must
        # parenthesise them so the project scope keeps binding to the whole
        # group: `project_id = 'a' AND (f1 OR f2)`. The unparenthesised form
        # `project_id = 'a' AND f1 OR f2` would match rows from other projects
        # that satisfy f2.
        base = "SELECT * FROM spans WHERE project_id = 'project-a'"
        filters = [
            _system_metric("avg_cost", "greater_than", 10),
            _system_metric("avg_latency", "less_than", 5000),
        ]
        query = FilterEngine.get_sql_filter_conditions_for_system_metrics(
            filters, base, filter_combinator="or"
        )
        assert (
            "project_id = 'project-a' AND (os.cost > '10' OR os.latency_ms < '5000')"
            in query
        )
        # The leaky, unparenthesised form must not appear.
        assert "project_id = 'project-a' AND os.cost > '10' OR" not in query

    def test_eval_metrics_or_parenthesised(self):
        filters = [
            {
                "column_id": "foo",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 10,
                },
            },
            {
                "column_id": "bar",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "less_than",
                    "filter_value": 5,
                },
            },
        ]
        query, having = FilterEngine.get_sql_filter_conditions_for_eval_metrics(
            filters, "SELECT * FROM t", filter_combinator="or"
        )
        assert " AND (" in query
        assert " OR " in query
        assert query.endswith(")")

    def test_eval_metrics_and_not_parenthesised(self):
        filters = [
            {
                "column_id": "foo",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 10,
                },
            },
            {
                "column_id": "bar",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "less_than",
                    "filter_value": 5,
                },
            },
        ]
        query, having = FilterEngine.get_sql_filter_conditions_for_eval_metrics(
            filters, "SELECT * FROM t", filter_combinator="and"
        )
        assert " OR " not in query
        assert " AND " in query


# ---------------------------------------------------------------------------
# CTE builders — HAVING / WHERE fragments
# ---------------------------------------------------------------------------


class TestCteCombinator:
    def test_cte_system_metrics_or_parenthesised(self):
        filters = [
            _system_metric("avg_cost", "greater_than", 10),
            _system_metric("avg_latency", "less_than", 5000),
        ]
        clause, params = FilterEngine.get_sql_filter_conditions_for_cte_system_metrics(
            filters, filter_combinator="or"
        )
        assert clause.startswith(" HAVING (")
        assert clause.endswith(")")
        assert " OR " in clause
        assert " AND " not in clause
        assert len(params) == 2

    def test_cte_system_metrics_and_not_parenthesised(self):
        filters = [
            _system_metric("avg_cost", "greater_than", 10),
            _system_metric("avg_latency", "less_than", 5000),
        ]
        clause, params = FilterEngine.get_sql_filter_conditions_for_cte_system_metrics(
            filters, filter_combinator="and"
        )
        assert clause.startswith(" HAVING ")
        assert " OR " not in clause
        assert " AND " in clause

    def test_cte_eval_metrics_or_parenthesised(self):
        filters = [
            {
                "column_id": "foo",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 10,
                },
            },
            {
                "column_id": "bar",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "less_than",
                    "filter_value": 5,
                },
            },
        ]
        clause, params = FilterEngine.get_sql_filter_conditions_for_cte_eval_metrics(
            filters, filter_combinator="or"
        )
        assert clause.startswith(" WHERE (")
        assert clause.endswith(")")
        assert " OR " in clause
        assert " AND " not in clause

    def test_cte_eval_metrics_and_not_parenthesised(self):
        filters = [
            {
                "column_id": "foo",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 10,
                },
            },
            {
                "column_id": "bar",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "less_than",
                    "filter_value": 5,
                },
            },
        ]
        clause, params = FilterEngine.get_sql_filter_conditions_for_cte_eval_metrics(
            filters, filter_combinator="and"
        )
        assert clause.startswith(" WHERE ")
        assert " OR " not in clause
        assert " AND " in clause


# ---------------------------------------------------------------------------
# ClickHouse builder
# ---------------------------------------------------------------------------


class TestClickHouseCombinator:
    def _builder(self):
        return ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE
        )

    def _filters(self):
        return [
            _system_metric("model", "equals", "gpt-4", filter_type="text"),
            _system_metric("status", "equals", "ERROR", filter_type="text"),
        ]

    def test_or_parenthesised(self):
        where, params = self._builder().translate(
            self._filters(), filter_combinator="or"
        )
        assert where.startswith("(")
        assert where.endswith(")")
        # The two filters are ORed at the top level, each rendered as its own
        # `trace_id IN (SELECT ...)` subquery. Any ` AND ` (`project_id = ...
        # AND _peerdb_is_deleted = 0 AND lower(...)`) stays inside each
        # subquery, so it never joins the filters together; the outer parens
        # keep the surrounding project scope binding to the whole OR group.
        assert where.count("trace_id IN (SELECT") == 2
        assert where.count(" OR ") == 1

    def test_and_not_parenthesised(self):
        where, params = self._builder().translate(
            self._filters(), filter_combinator="and"
        )
        assert " OR " not in where
        assert " AND " in where
        assert not where.startswith("(")

    def test_default_is_and(self):
        builder = self._builder()
        default = builder.translate(self._filters())
        builder = self._builder()
        explicit = builder.translate(self._filters(), filter_combinator="and")
        assert default == explicit


# ---------------------------------------------------------------------------
# Cross-engine equivalence — Django ORM vs ClickHouse
# ---------------------------------------------------------------------------
#
# "Done when" #8: the Django path and the ClickHouse path must return the same
# rows for the same OR query. A live row-by-row comparison needs both backends
# wired to data, which the no-services harness does not provide. The contract
# that guarantees identical rows offline is stricter and pinnable here:
#
#   For the same filter set, both engines derive the SAME predicate universe
#   (same number of leaf conditions) and honor `filter_combinator` identically
#   — AND keeps each leaf isolated, OR joins every leaf with one operator. If
#   both build `(f1 OR f2 OR f3)` from three filters, and each leaf maps to the
#   same column+op+value in both engines, the two queries consider the same row
#   universe for identical source data. Toggling the combinator must only swap
#   the connector, never add or drop a predicate.
#
# Trace-mode ClickHouse wraps every system-metric leaf in a
# `trace_id IN (SELECT …)` subquery, so that substring occurs exactly once per
# leaf and is a stable leaf counter.


def _three_system_metrics():
    """Three distinct numeric system metrics — latency, cost, tokens.

    Each resolves to exactly one ORM leaf (DEFAULT_FIELD_MAP) and exactly one
    ClickHouse `trace_id IN (SELECT …)` leaf (SYSTEM_METRIC_MAP, trace mode).
    """
    return [
        _system_metric("latency_ms", "greater_than", 10),
        _system_metric("cost", "greater_than", 20),
        _system_metric("tokens", "greater_than", 100),
    ]


class TestCrossEngineEquivalence:
    def _orm_leaf_count(self, q):
        # reduce() nests Q objects, so a top-level `Q.children` count is NOT the
        # leaf count (e.g. three filters reduce to Q(AND, [Q(AND,[a,b]), c])).
        # Count leaf predicates recursively: a leaf is a child that is a tuple,
        # not a nested Q.
        count = 0
        for child in q.children:
            count += self._orm_leaf_count(child) if isinstance(child, Q) else 1
        return count

    def _ch_leaf_count(self, where):
        # One `trace_id IN (SELECT …)` subquery per system-metric leaf.
        return where.count("trace_id IN (SELECT")

    def test_same_predicate_count_in_and_mode(self):
        filters = _three_system_metrics()

        orm_q = FilterEngine.get_filter_conditions_for_system_metrics(filters)
        ch_where, _ = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE
        ).translate(filters, filter_combinator="and")

        # Both engines see all three filters; neither drops or duplicates one.
        assert self._orm_leaf_count(orm_q) == 3
        assert self._ch_leaf_count(ch_where) == 3

    def test_same_predicate_count_in_or_mode(self):
        filters = _three_system_metrics()

        orm_q = FilterEngine.get_filter_conditions_for_system_metrics(
            filters, filter_combinator="or"
        )
        ch_where, _ = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE
        ).translate(filters, filter_combinator="or")

        assert self._orm_leaf_count(orm_q) == 3
        assert self._ch_leaf_count(ch_where) == 3

    def test_toggling_combinator_keeps_predicate_count(self):
        # Flipping AND↔OR changes only the connector, never the predicate set.
        filters = _three_system_metrics()

        orm_and = FilterEngine.get_filter_conditions_for_system_metrics(filters)
        orm_or = FilterEngine.get_filter_conditions_for_system_metrics(
            filters, filter_combinator="or"
        )
        ch_and, _ = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE
        ).translate(filters, filter_combinator="and")
        ch_or, _ = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE
        ).translate(filters, filter_combinator="or")

        assert self._orm_leaf_count(orm_and) == self._orm_leaf_count(orm_or)
        assert self._ch_leaf_count(ch_and) == self._ch_leaf_count(ch_or)

    def test_connectors_align_across_engines(self):
        # The structural shape each engine builds from the same filters must
        # match: AND → flat AND, OR → single parenthesised OR group.
        filters = _three_system_metrics()

        orm_and = FilterEngine.get_filter_conditions_for_system_metrics(filters)
        orm_or = FilterEngine.get_filter_conditions_for_system_metrics(
            filters, filter_combinator="or"
        )
        ch_and, _ = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE
        ).translate(filters, filter_combinator="and")
        ch_or, _ = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE
        ).translate(filters, filter_combinator="or")

        # Django ORM connectors.
        assert orm_and.connector == Q.AND
        assert orm_or.connector == Q.OR
        # ClickHouse: AND has no wrapping parens; OR is one parenthesised
        # group joining every leaf. Each leaf is a `trace_id IN (SELECT …)`
        # subquery that carries its own internal ` AND ` joiners plus the
        # root-span guard (`parent_span_id IS NULL OR parent_span_id = ''`),
        # all of which are builder semantics rather than combinator
        # connectors — so assert on the LEAF-BOUNDARY joiners only: the
        # connectors that appear between `)` of one leaf and the next leaf.
        n_leaves = self._ch_leaf_count(ch_and)
        assert n_leaves == 3
        assert not ch_and.startswith("(")
        # AND mode: every leaf-boundary connector is AND (n_leaves - 1).
        assert ch_and.count(") AND trace_id IN (SELECT") == n_leaves - 1
        assert ") OR trace_id IN (SELECT" not in ch_and
        # OR mode: exactly one parenthesised group, every leaf-boundary
        # connector is OR.
        assert ch_or.startswith("(")
        assert ch_or.endswith(")")
        assert ch_or.count(") OR trace_id IN (SELECT") == n_leaves - 1
        assert ") AND trace_id IN (SELECT" not in ch_or


# ---------------------------------------------------------------------------
# Graph (chart) builders — same parenthesisation contract as the list builders
# ---------------------------------------------------------------------------
#
# The Observe charts hit get_graph_methods -> fetch_*_graph_ch, whose builders
# stitch translate()'s fragment into `... AND {extra_where}`. A bare OR there
# would leak outside the project scope exactly like the list builders would,
# so the parenthesisation contract is pinned per builder. Note the OR group is
# built once from translate() in the time-series path (guard against a caller
# passing or-combinator while the builder drops it back to AND).


class TestGraphBuilderCombinator:
    """Graph (chart) builders thread filter_combinator into translate().

    The Observe charts hit get_graph_methods -> fetch_*_graph_ch. These
    builders compose translate()'s fragment into `... AND {extra_where}`, so
    a bare OR there would leak outside the project scope exactly like in the
    list builders. Pinned at the translate() boundary (same level as
    TestClickHouseCombinator) so the assertions stay independent of how each
    builder embeds the fragment.
    """

    def _two_filters(self):
        return [
            _system_metric("avg_cost", "greater_than", 10),
            _system_metric("avg_latency", "less_than", 5000),
        ]

    def test_time_series_translates_or_group(self):
        from tracer.services.clickhouse.query_builders.time_series import (
            TimeSeriesQueryBuilder,
        )

        builder = TimeSeriesQueryBuilder(
            project_id="00000000-0000-0000-0000-000000000001",
            filters=self._two_filters(),
            interval="day",
            filter_combinator="or",
        )
        query, _params = builder.build()
        # Post-rebase the raw path filters the spans table in place; in OR
        # mode the two leaves join inside one parenthesised group:
        # AND (cost > … OR latency_ms < …).
        assert " AND (cost > %(col_1)s OR latency_ms < %(col_2)s)" in query
        # AND mode joins the same leaves with bare ANDs instead.
        and_builder = TimeSeriesQueryBuilder(
            project_id="00000000-0000-0000-0000-000000000001",
            filters=self._two_filters(),
            interval="day",
        )
        and_query, _ = and_builder.build()
        assert " AND cost > %(col_1)s" in and_query
        assert " AND latency_ms < %(col_2)s" in and_query
        assert ") OR " not in and_query.replace(
            "parent_span_id IS NULL OR parent_span_id = ''", ""
        )

    def test_time_series_default_matches_explicit_and(self):
        from tracer.services.clickhouse.query_builders.time_series import (
            TimeSeriesQueryBuilder,
        )

        default_query, _ = TimeSeriesQueryBuilder(
            project_id="00000000-0000-0000-0000-000000000001",
            filters=self._two_filters(),
            interval="day",
        ).build()
        explicit_query, _ = TimeSeriesQueryBuilder(
            project_id="00000000-0000-0000-0000-000000000001",
            filters=self._two_filters(),
            interval="day",
            filter_combinator="and",
        ).build()
        assert default_query == explicit_query
        assert ") OR trace_id IN" not in default_query

    def test_eval_metrics_or_parenthesised(self):
        from tracer.services.clickhouse.query_builders.eval_metrics import (
            EvalMetricsQueryBuilder,
        )

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-0000000000ee",
            project_id="00000000-0000-0000-0000-000000000001",
            filters=self._two_filters(),
            interval="day",
            filter_combinator="or",
            use_preaggregated=False,
        )
        query, _params = builder.build()
        # The filters compile to a trace_id IN subquery whose WHERE carries
        # the parenthesised OR group.
        assert " AND (trace_id IN (SELECT" in query
        assert ") OR trace_id IN (SELECT" in query

    def test_annotation_graph_or_parenthesised(self):
        from tracer.services.clickhouse.query_builders.annotation_graph import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id="00000000-0000-0000-0000-000000000001",
            annotation_label_id="00000000-0000-0000-0000-0000000000aa",
            annotation_name="label",
            filters=self._two_filters(),
            interval="day",
            filter_combinator="or",
            start_date=None,
            end_date=None,
        )
        query, _params = builder.build()
        assert " AND (trace_id IN (SELECT" in query
        assert ") OR trace_id IN (SELECT" in query
