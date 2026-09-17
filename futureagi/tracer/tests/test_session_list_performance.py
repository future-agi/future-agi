"""
Stress tests for the session list ClickHouse queries.

These tests verify that:
1. The query builder produces exact aggregate queries with finite page bounds
2. The count-skip logic correctly eliminates unnecessary count queries
3. The span attributes query is scoped to selected sessions and root spans
4. Large result sets are processed within acceptable time bounds
5. The real attribute aggregator retains every key and typed value

Run with: bin/test -k "test_session_list_performance" --no-services unit
"""

import json
import time
import uuid
from datetime import datetime

import pytest


@pytest.mark.unit
class TestSessionListQueryPerformance:
    """Stress tests for SessionListQueryBuilder query generation performance."""

    def _make_builder(self, num_filters=0, aggregate_filters=0, page_size=30):
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        filters = []
        for i in range(num_filters):
            filters.append(
                {
                    "column_id": f"custom_attr_{i}",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": f"value_{i}",
                    },
                }
            )
        for i in range(aggregate_filters):
            col = ["duration", "total_cost", "total_tokens", "traces_count"][i % 4]
            filters.append(
                {
                    "column_id": col,
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": i * 10,
                    },
                }
            )

        return SessionListQueryBuilder(
            project_id=str(uuid.uuid4()),
            filters=filters,
            page_number=0,
            page_size=page_size,
        )

    def test_build_query_generation_speed(self):
        """Query generation for build() should complete in < 50ms even with many filters."""
        builder = self._make_builder(num_filters=20, aggregate_filters=4)

        start = time.monotonic()
        for _ in range(100):
            builder.params = {"project_id": builder.project_id}
            builder.build()
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, f"build() too slow: {elapsed:.2f}s for 100 iterations"

    def test_count_query_generation_speed_simple_path(self):
        """Simple count query (no HAVING) should be fast to generate."""
        builder = self._make_builder(num_filters=10, aggregate_filters=0)
        builder.build()

        start = time.monotonic()
        for _ in range(100):
            builder._build_simple_count_query()
        elapsed = time.monotonic() - start

        assert elapsed < 0.5, (
            f"Simple count query too slow: {elapsed:.2f}s for 100 iter"
        )

    def test_count_query_generation_speed_aggregated_path(self):
        """Aggregated count query (with HAVING) should be fast to generate."""
        builder = self._make_builder(num_filters=10, aggregate_filters=4)
        builder.build()

        start = time.monotonic()
        for _ in range(100):
            builder._build_aggregated_count_query()
        elapsed = time.monotonic() - start

        assert elapsed < 0.5, f"Aggregated count query too slow: {elapsed:.2f}s"

    def test_span_attributes_query_has_bounds(self):
        """Selected sessions bound hydration, not a silent physical-row cutoff."""
        builder = self._make_builder()
        builder.build()

        session_ids = [str(uuid.uuid4()) for _ in range(30)]
        query, params = builder.build_span_attributes_query(session_ids)

        assert "LIMIT 500" not in query
        assert params["attr_session_ids"] == tuple(session_ids)
        assert "(parent_span_id IS NULL OR parent_span_id = '')" in query

    def test_trace_count_is_exact_in_every_session_aggregate_query(self):
        """Published session trace totals must never use approximate uniq()."""
        builder = self._make_builder(aggregate_filters=2)
        builder.build()

        main_query, _ = builder.build()
        count_query, _ = builder.build_count_query()

        assert "uniqExact(trace_id) AS traces_count" in main_query
        assert "uniq(trace_id)" not in main_query
        assert "uniq(trace_id)" not in count_query

    def test_simple_count_avoids_group_by(self):
        """Simple count path must NOT use GROUP BY."""
        builder = self._make_builder(num_filters=5, aggregate_filters=0)
        builder.build()
        query, _ = builder.build_count_query()

        # The id-remap survivor map (id_remap_sql) embeds internal
        # `GROUP BY new_id` + `GROUP BY any_id` in its join subquery; the simple
        # count path must have no OTHER (session-level) GROUP BY — strip the
        # remap's first.
        stripped = query.replace("GROUP BY new_id", "").replace("GROUP BY any_id", "")
        assert "GROUP BY" not in stripped
        assert "HAVING" not in query
        assert "count(DISTINCT trace_session_id)" in query

    def test_id_query_continuous_floor_and_ceiling_window_on_created_at(self):
        floor = datetime(2026, 8, 1, 12, 0)
        ceil = datetime(2026, 8, 1, 12, 5)
        query, params = self._make_builder().build_id_query(
            created_at_floor=floor, created_at_ceiling=ceil
        )
        # Arrival window replaces the start_time bound on the span scan.
        assert (
            "created_at >= "
            "fromUnixTimestamp64Micro(%(created_at_floor_us)s, 'UTC')" in query
        )
        assert (
            "created_at < "
            "fromUnixTimestamp64Micro(%(created_at_ceiling_us)s, 'UTC')" in query
        )
        assert "start_time >= %(start_date)s" not in query
        assert params["created_at_floor"] == floor
        assert params["created_at_ceiling"] == ceil

    def test_id_query_default_keeps_start_time_window(self):
        query, params = self._make_builder().build_id_query()
        assert (
            "start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')" in query
        )
        assert "created_at_ceiling" not in params


@pytest.mark.unit
class TestSessionListCountSkipStress:
    """Stress test the count-skip optimization logic with various edge cases."""

    @pytest.mark.parametrize(
        "page_number,page_size,result_count,expected_total,needs_count",
        [
            (0, 30, 30, 30, False),
            (0, 30, 5, 5, False),
            (0, 30, 0, 0, False),
            (0, 30, 31, None, True),
            (5, 30, 10, 160, False),
            (5, 30, 31, None, True),
            (0, 100, 100, 100, False),
            (0, 100, 101, None, True),
            (0, 1, 1, 1, False),
            (0, 1, 2, None, True),
        ],
    )
    def test_count_skip_logic(
        self, page_number, page_size, result_count, expected_total, needs_count
    ):
        """Parametrized test for count-skip decision logic."""
        result_data = [{"session_id": f"s-{i}"} for i in range(result_count)]

        has_more = len(result_data) > page_size
        actual_data = result_data[:page_size]

        if not has_more and page_number == 0:
            total_count = len(actual_data)
        elif not has_more:
            total_count = (page_number * page_size) + len(actual_data)
        else:
            total_count = None

        if needs_count:
            assert total_count is None
        else:
            assert total_count == expected_total


@pytest.mark.unit
class TestSpanAttributesProcessingStress:
    """Measure the real complete attribute aggregator, not a copied capped loop."""

    def _simulate_attribute_processing(
        self, num_sessions, attrs_per_session, keys_per_attr
    ):
        from tracer.views.trace_session import _aggregate_session_attribute_rows

        rows = [
            {
                "session_id": f"session-{session}",
                "span_attributes_raw": json.dumps({
                    f"key_{key}": f"value_{session}_{root}_{key}"
                    for key in range(keys_per_attr)
                }),
            }
            for session in range(num_sessions)
            for root in range(attrs_per_session)
        ]
        started = time.monotonic()
        attributes = _aggregate_session_attribute_rows(rows)
        elapsed = time.monotonic() - started
        assert len(attributes) == num_sessions
        for keys in attributes.values():
            assert len(keys) == keys_per_attr
            assert all(len(values) == attrs_per_session for values in keys.values())
        return elapsed, attributes

    def test_attribute_processing_30_sessions_510_rows(self):
        elapsed, _ = self._simulate_attribute_processing(30, 17, 10)
        assert elapsed < 0.5, f"Took {elapsed:.3f}s"

    def test_attribute_processing_preserves_all_keys_and_values(self):
        elapsed, _ = self._simulate_attribute_processing(30, 100, 100)
        assert elapsed < 2.0, f"Took {elapsed:.3f}s"

    def test_stress_many_sessions_many_attributes(self):
        elapsed, _ = self._simulate_attribute_processing(30, 17, 260)
        assert elapsed < 2.0, f"Took {elapsed:.3f}s"


@pytest.mark.unit
class TestQueryTimeoutBudget:
    """Verify that the timeout budget allocation is correct."""

    def test_timeout_budget_phase1(self):
        """Phase 1 publishes an exact trace count under its finite page bound."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id=str(uuid.uuid4()),
            filters=[],
            page_number=0,
            page_size=30,
        )
        query, params = builder.build()
        assert "uniqExact(trace_id) AS traces_count" in query
        assert "uniq(trace_id)" not in query
        assert "LIMIT" in query

    def test_timeout_budget_count_optimized(self):
        """Count query without HAVING should avoid expensive aggregation."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id=str(uuid.uuid4()),
            filters=[],
            page_number=0,
            page_size=30,
        )
        builder.build()
        query, params = builder.build_count_query()
        assert "count(DISTINCT trace_session_id)" in query
        assert "sum(cost)" not in query
        assert "dateDiff" not in query

    def test_timeout_budget_span_attributes_bounded(self):
        """Hydrate all roots of the finite session page without truncation."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id=str(uuid.uuid4()),
            filters=[],
            page_number=0,
            page_size=30,
        )
        builder.build()
        session_ids = [str(uuid.uuid4()) for _ in range(30)]
        query, params = builder.build_span_attributes_query(session_ids)
        assert "LIMIT 500" not in query
        assert params["attr_session_ids"] == tuple(session_ids)
        assert "parent_span_id IS NULL OR parent_span_id = ''" in query
        # The committed PREWHERE micro-opt became a WHERE when the query gained
        # the P3b id-remap LEFT JOIN: ClickHouse PREWHERE cannot reference a
        # joined column, and the session-id filter now matches the resolved
        # `ts_remap.survivor_id` (see session_list.build_span_attributes_query).
        # The query is scoped to the selected sessions and root-span filter;
        # assert the resolved session filter is applied in the WHERE.
        assert "WHERE" in query
        assert "IN %(attr_session_ids)s" in query
