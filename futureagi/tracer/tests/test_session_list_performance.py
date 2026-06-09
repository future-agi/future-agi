"""
Stress tests for the session list ClickHouse queries.

These tests verify that:
1. The query builder produces optimized queries (no uniqExact, proper LIMIT)
2. The count-skip logic correctly eliminates unnecessary count queries
3. The span attributes query is bounded (root spans + LIMIT)
4. Large result sets are processed within acceptable time bounds
5. The attribute key cap prevents pathological memory usage

Run with: bin/test -k "test_session_list_performance" --no-services unit
"""

import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List
from unittest import mock

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

        assert (
            elapsed < 0.5
        ), f"Simple count query too slow: {elapsed:.2f}s for 100 iter"

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
        """Span attributes query must have LIMIT to prevent unbounded scans."""
        builder = self._make_builder()
        builder.build()

        session_ids = [str(uuid.uuid4()) for _ in range(30)]
        query, params = builder.build_span_attributes_query(session_ids)

        assert "LIMIT 500" in query
        assert "(parent_span_id IS NULL OR parent_span_id = '')" in query

    def test_no_uniqExact_in_any_query(self):
        """No query path should use expensive uniqExact."""
        builder = self._make_builder(aggregate_filters=2)
        builder.build()

        main_query, _ = builder.build()
        count_query, _ = builder.build_count_query()

        assert "uniqExact" not in main_query
        assert "uniqExact" not in count_query

    def test_simple_count_avoids_group_by(self):
        """Simple count path must NOT use GROUP BY."""
        builder = self._make_builder(num_filters=5, aggregate_filters=0)
        builder.build()
        query, _ = builder.build_count_query()

        assert "GROUP BY" not in query
        assert "HAVING" not in query
        assert "count(DISTINCT trace_session_id)" in query


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
    """Stress test the span attribute parsing and key-cap logic."""

    def _simulate_attribute_processing(
        self, num_sessions, attrs_per_session, keys_per_attr
    ):
        """Simulate the attribute processing loop from _list_sessions_clickhouse."""
        from tracer.views.trace_session import _json_loads

        _SKIP_ATTR_PREFIXES = (
            "raw.",
            "llm.input_messages",
            "llm.output_messages",
            "input.value",
            "output.value",
        )
        _MAX_ATTR_KEYS_PER_SESSION = 50

        attr_rows = []
        for s_idx in range(num_sessions):
            sid = f"session-{s_idx}"
            for a_idx in range(attrs_per_session):
                attrs = {
                    f"key_{k}": f"val_{s_idx}_{a_idx}_{k}" for k in range(keys_per_attr)
                }
                attr_rows.append(
                    {
                        "session_id": sid,
                        "span_attributes_raw": json.dumps(attrs),
                        "span_attr_str": {},
                        "span_attr_num": {},
                    }
                )

        aggregated_attrs: Dict[str, Dict] = {}
        start = time.monotonic()

        for attr_row in attr_rows:
            sid = str(attr_row.get("session_id", ""))
            if (
                sid in aggregated_attrs
                and len(aggregated_attrs[sid]) >= _MAX_ATTR_KEYS_PER_SESSION
            ):
                continue
            raw = attr_row.get("span_attributes_raw", "{}")
            try:
                attrs = (
                    _json_loads(raw) if isinstance(raw, str) and raw else (raw or {})
                )
            except (json.JSONDecodeError, ValueError, TypeError):
                attrs = {}
            if sid not in aggregated_attrs:
                aggregated_attrs[sid] = {}
            for key, value in attrs.items():
                if len(aggregated_attrs[sid]) >= _MAX_ATTR_KEYS_PER_SESSION:
                    break
                if key.startswith(_SKIP_ATTR_PREFIXES):
                    continue
                if isinstance(value, str) and len(value) > 500:
                    continue
                if key not in aggregated_attrs[sid]:
                    aggregated_attrs[sid][key] = set()
                if isinstance(value, (str, int, float, bool)):
                    aggregated_attrs[sid][key].add(value)

        elapsed = time.monotonic() - start
        return elapsed, aggregated_attrs

    def test_attribute_processing_30_sessions_500_rows(self):
        """Process 500 attribute rows for 30 sessions in < 500ms."""
        elapsed, attrs = self._simulate_attribute_processing(
            num_sessions=30, attrs_per_session=17, keys_per_attr=10
        )
        assert elapsed < 0.5, f"Took {elapsed:.3f}s (limit: 0.5s)"
        for sid, keys in attrs.items():
            assert len(keys) <= 50

    def test_attribute_processing_key_cap_effective(self):
        """Key cap should prevent pathological memory usage with many unique keys."""
        elapsed, attrs = self._simulate_attribute_processing(
            num_sessions=30, attrs_per_session=100, keys_per_attr=100
        )
        for sid, keys in attrs.items():
            assert len(keys) <= 50
        assert elapsed < 2.0, f"Took {elapsed:.3f}s (limit: 2.0s)"

    def test_stress_many_sessions_many_attributes(self):
        """Stress test: 30 sessions with 500 attribute rows."""
        from tracer.views.trace_session import _json_loads

        _MAX_ATTR_KEYS_PER_SESSION = 50
        _SKIP_ATTR_PREFIXES = (
            "raw.",
            "llm.input_messages",
            "llm.output_messages",
            "input.value",
            "output.value",
        )

        session_ids = [str(uuid.uuid4()) for _ in range(30)]
        attr_data = []
        for i in range(500):
            sid = session_ids[i % 30]
            attrs = {f"attr_{k}": f"value_{i}_{k}" for k in range(20)}
            attr_data.append(
                {
                    "session_id": sid,
                    "span_attributes_raw": json.dumps(attrs),
                }
            )

        start = time.monotonic()
        aggregated_attrs: Dict[str, Dict] = {}

        for attr_row in attr_data:
            sid = str(attr_row["session_id"])
            if (
                sid in aggregated_attrs
                and len(aggregated_attrs[sid]) >= _MAX_ATTR_KEYS_PER_SESSION
            ):
                continue
            raw = attr_row["span_attributes_raw"]
            try:
                attrs = _json_loads(raw) if raw else {}
            except (json.JSONDecodeError, ValueError, TypeError):
                attrs = {}
            if sid not in aggregated_attrs:
                aggregated_attrs[sid] = {}
            for key, value in attrs.items():
                if len(aggregated_attrs[sid]) >= _MAX_ATTR_KEYS_PER_SESSION:
                    break
                if key.startswith(_SKIP_ATTR_PREFIXES):
                    continue
                if isinstance(value, str) and len(value) > 500:
                    continue
                if key not in aggregated_attrs[sid]:
                    aggregated_attrs[sid][key] = set()
                if isinstance(value, (str, int, float, bool)):
                    aggregated_attrs[sid][key].add(value)

        elapsed = time.monotonic() - start
        assert elapsed < 0.5, f"Stress test took {elapsed:.3f}s (limit: 0.5s)"
        for sid in session_ids:
            if sid in aggregated_attrs:
                assert len(aggregated_attrs[sid]) <= 50


@pytest.mark.unit
class TestQueryTimeoutBudget:
    """Verify that the timeout budget allocation is correct."""

    def test_timeout_budget_phase1(self):
        """Phase 1 main aggregation should use uniq (fast) not uniqExact."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id=str(uuid.uuid4()),
            filters=[],
            page_number=0,
            page_size=30,
        )
        query, params = builder.build()
        assert "uniq(trace_id)" in query
        assert "uniqExact" not in query
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
        """Span attributes query should be bounded by LIMIT and root-span filter."""
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
        assert "LIMIT 500" in query
        assert "parent_span_id IS NULL OR parent_span_id = ''" in query
        assert "PREWHERE" in query


@pytest.mark.unit
class TestEndUserSubquery:
    """Verify the session-scoped subquery used to filter by end_user_id.

    ``end_user_id`` is set on the child span carrying the OTel ``user.id``
    attribute, not on root spans. The session list query restricts to root
    spans, so a direct ``end_user_id IN (...)`` would miss matches. The
    builder hoists the filter into a session-scoped subquery:
    ``trace_session_id IN (SELECT DISTINCT trace_session_id FROM spans
    WHERE project_id ... AND end_user_id IN (...))``.
    """

    @staticmethod
    def _build_with_end_user(
        *,
        end_user_ids,
        project_id=None,
        project_ids=None,
        extra_filters=None,
    ):
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        filters = list(extra_filters or [])
        if end_user_ids is not None:
            filters.append(
                {
                    "column_id": "end_user_id",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": end_user_ids,
                    },
                }
            )
        return SessionListQueryBuilder(
            project_id=project_id,
            project_ids=project_ids,
            filters=filters,
            page_number=0,
            page_size=30,
        )

    def test_subquery_emitted_single_project(self):
        """Single-project mode emits a session-scoped subquery on end_user_id."""
        ids = [str(uuid.uuid4()) for _ in range(3)]
        builder = self._build_with_end_user(
            end_user_ids=ids, project_id=str(uuid.uuid4())
        )
        query, params = builder.build()

        assert "trace_session_id IN (" in query
        assert "SELECT DISTINCT trace_session_id FROM spans" in query
        assert "end_user_id IN %(_eu_ids)s" in query
        assert "project_id = %(project_id)s" in query
        assert params.get("_eu_ids") == tuple(ids)

    def test_subquery_emitted_org_scope(self):
        """Org-scoped mode uses project_id IN (...) inside the subquery."""
        ids = [str(uuid.uuid4()) for _ in range(2)]
        project_ids = [str(uuid.uuid4()) for _ in range(4)]
        builder = self._build_with_end_user(end_user_ids=ids, project_ids=project_ids)
        query, params = builder.build()

        assert "trace_session_id IN (" in query
        # Org-scope: the subquery's own project filter must use IN (...)
        sub_start = query.index("SELECT DISTINCT trace_session_id")
        subquery_body = query[sub_start:]
        assert "project_id IN %(project_ids)s" in subquery_body
        assert params.get("_eu_ids") == tuple(ids)
        assert params.get("project_ids") == tuple(project_ids)

    def test_no_subquery_when_filter_absent(self):
        """Regression guard: absent filter must NOT emit the subquery clause."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id=str(uuid.uuid4()),
            filters=[],
            page_number=0,
            page_size=30,
        )
        query, params = builder.build()

        assert "trace_session_id IN (" not in query
        assert "_eu_ids" not in params

    def test_end_user_id_not_in_root_span_where(self):
        """end_user_id must NOT appear as a direct root-span WHERE column.

        The whole point of the subquery is to avoid filtering on the root
        span's (always-NULL) end_user_id. Make sure the filter builder
        didn't also splice it into the outer WHERE.
        """
        ids = [str(uuid.uuid4())]
        builder = self._build_with_end_user(
            end_user_ids=ids, project_id=str(uuid.uuid4())
        )
        query, params = builder.build()

        # Find the start of the subquery. The text BEFORE that point is
        # the outer root-span WHERE and must not reference end_user_id.
        sub_start = query.index("SELECT DISTINCT trace_session_id")
        outer_before_subquery = query[:sub_start]
        assert "end_user_id" not in outer_before_subquery, (
            "end_user_id leaked into outer root-span WHERE — "
            "it must only appear inside the session-scoped subquery"
        )

        # Also walk past the subquery's matching `)` and verify there's
        # nothing more downstream — the trailing part of the query
        # (GROUP BY / ORDER BY / LIMIT) must not mention end_user_id.
        depth = 0
        sub_end = None
        for i in range(sub_start, len(query)):
            ch = query[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    sub_end = i
                    break
        # We started inside the parenthesised subquery (depth begins at
        # 1 by the time we hit `SELECT`), so finding depth == -1 means
        # we hit the close of the subquery from outside.
        # Use a simpler fallback: find the last %(_eu_ids)s reference
        # and look only past it.
        eu_pos = query.rfind("%(_eu_ids)s")
        # Everything after `IS NOT NULL)` closing the subquery should
        # not contain end_user_id.
        after_subquery = query[query.index(")", eu_pos) :]
        assert "end_user_id" not in after_subquery, (
            "end_user_id leaked into trailing query body — "
            f"context: ...{after_subquery[:200]}"
        )

    def test_subquery_present_in_simple_count(self):
        """Simple count query (no HAVING) must also wrap the subquery."""
        ids = [str(uuid.uuid4())]
        builder = self._build_with_end_user(
            end_user_ids=ids, project_id=str(uuid.uuid4())
        )
        builder.build()
        query, params = builder.build_count_query()

        assert "count(DISTINCT trace_session_id)" in query
        assert "trace_session_id IN (" in query
        assert "end_user_id IN %(_eu_ids)s" in query
        assert params.get("_eu_ids") == tuple(ids)

    def test_subquery_present_in_aggregated_count(self):
        """Aggregated count (HAVING path) must also wrap the subquery."""
        ids = [str(uuid.uuid4())]
        # Add a HAVING-targeting filter to force the aggregated count path
        having_filter = {
            "column_id": "duration",
            "filter_config": {
                "filter_op": "greater_than",
                "filter_value": 60,
            },
        }
        builder = self._build_with_end_user(
            end_user_ids=ids,
            project_id=str(uuid.uuid4()),
            extra_filters=[having_filter],
        )
        builder.build()
        query, params = builder.build_count_query()

        assert "count() AS total FROM (" in query  # aggregated path
        assert "trace_session_id IN (" in query
        assert "end_user_id IN %(_eu_ids)s" in query
        assert "HAVING" in query
        assert params.get("_eu_ids") == tuple(ids)

    def test_extract_handles_list_value(self):
        """_extract_end_user_ids returns a list when filter_value is a list."""
        ids = [str(uuid.uuid4()) for _ in range(3)]
        builder = self._build_with_end_user(
            end_user_ids=ids, project_id=str(uuid.uuid4())
        )
        result = builder._extract_end_user_ids()
        assert result == ids

    def test_extract_handles_scalar_value(self):
        """_extract_end_user_ids wraps a scalar filter_value in a single-element list."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        single = str(uuid.uuid4())
        builder = SessionListQueryBuilder(
            project_id=str(uuid.uuid4()),
            filters=[
                {
                    "column_id": "end_user_id",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": single,
                    },
                }
            ],
            page_number=0,
            page_size=30,
        )
        result = builder._extract_end_user_ids()
        assert result == [single]

    def test_extract_returns_none_when_absent(self):
        """_extract_end_user_ids returns None when no end_user_id filter exists."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id=str(uuid.uuid4()),
            filters=[
                {
                    "column_id": "model",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4o",
                    },
                }
            ],
            page_number=0,
            page_size=30,
        )
        assert builder._extract_end_user_ids() is None

    def test_extract_ignores_empty_list_entries(self):
        """Empty / falsy values in the list are dropped."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        valid = str(uuid.uuid4())
        builder = SessionListQueryBuilder(
            project_id=str(uuid.uuid4()),
            filters=[
                {
                    "column_id": "end_user_id",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": [valid, "", None],
                    },
                }
            ],
            page_number=0,
            page_size=30,
        )
        result = builder._extract_end_user_ids()
        assert result == [valid]

    def test_subquery_perf_with_large_id_list(self):
        """Generating a subquery with 1000 end_user_id values stays fast."""
        ids = [str(uuid.uuid4()) for _ in range(1000)]
        builder = self._build_with_end_user(
            end_user_ids=ids, project_id=str(uuid.uuid4())
        )

        start = time.monotonic()
        for _ in range(50):
            builder.params = {"project_id": builder.project_id}
            builder.build()
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"build() with 1k IDs too slow: {elapsed:.3f}s / 50 iters"

    def test_camelcase_filter_keys_supported(self):
        """Frontend camelCase keys (columnId/filterConfig) are also recognized."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        ids = [str(uuid.uuid4())]
        builder = SessionListQueryBuilder(
            project_id=str(uuid.uuid4()),
            filters=[
                {
                    "columnId": "end_user_id",
                    "filterConfig": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ids,
                    },
                }
            ],
            page_number=0,
            page_size=30,
        )
        query, params = builder.build()
        assert "trace_session_id IN (" in query
        assert params.get("_eu_ids") == tuple(ids)
