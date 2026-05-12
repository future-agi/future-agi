"""
Regression tests for the ClickHouse trace-listing query in
``TraceSessionViewSet._retrieve_clickhouse``.

The original query had ``min(start_time) AS start_time`` AND
``ORDER BY min(start_time) ASC``. ClickHouse rejects that combination
(``ILLEGAL_AGGREGATION``: aggregate function found inside another
aggregate) because the alias collides with the column name. The
exception was swallowed by the try/except wrapper, silently dropping
the request to the PG fallback path -- which only orders by
``Trace.created_at`` and falls back to non-deterministic heap order
when every trace in a session shares the same ``created_at`` (which
the SDK stamps on bulk-ingested runs).

These tests pin:
  1. The CH query parses + executes without raising.
  2. Rows come back ordered by ``min(start_time)`` ASC.
  3. The aliased ``trace_min_start_time`` field is populated and
     downstream code can read it without falling back to ``None``.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# Cycle-breaker -- same rationale as test_eval_task_runtime.
import model_hub.tasks  # noqa: F401, E402


_TEST_DATABASE = "test_trace_session_ch_query"


@pytest.fixture(scope="module")
def ch_client():
    """Connect to test ClickHouse via clickhouse-driver (the same client
    the app uses). Skip when unavailable."""
    try:
        from clickhouse_driver import Client as CHDriver
    except ImportError:
        pytest.skip("clickhouse-driver not installed")

    try:
        client = CHDriver(
            host=os.environ.get("CH_HOST", "localhost"),
            port=int(os.environ.get("CH_PORT", "19000")),
            user=os.environ.get("CH_USERNAME", "default"),
            password=os.environ.get("CH_PASSWORD", ""),
        )
        client.execute("SELECT 1")
        return client
    except Exception:
        pytest.skip("ClickHouse not available for integration tests")


@pytest.fixture(scope="module")
def ch_spans_table(ch_client):
    """Create a stripped-down ``spans`` table in an isolated test DB.

    Only the columns the trace-listing query touches are declared --
    enough to exercise the alias collision without dragging the full
    production schema (Maps, MVs, dictionaries) into the test setup.
    """
    ch_client.execute(f"CREATE DATABASE IF NOT EXISTS {_TEST_DATABASE}")
    ch_client.execute(f"DROP TABLE IF EXISTS {_TEST_DATABASE}.spans")
    ch_client.execute(
        f"""
        CREATE TABLE {_TEST_DATABASE}.spans (
            trace_id String,
            project_id UUID,
            trace_session_id Nullable(UUID),
            parent_span_id Nullable(String),
            input String DEFAULT '',
            output String DEFAULT '',
            latency_ms Nullable(Int32),
            cost Nullable(Float64),
            start_time Nullable(DateTime64(3)),
            total_tokens Nullable(Int32),
            prompt_tokens Nullable(Int32),
            completion_tokens Nullable(Int32),
            _peerdb_is_deleted UInt8 DEFAULT 0
        ) ENGINE = MergeTree()
        ORDER BY (project_id, trace_id)
        """
    )
    yield ch_client
    ch_client.execute(f"DROP DATABASE IF EXISTS {_TEST_DATABASE}")


def _trace_listing_query(database: str) -> str:
    """The query the API runs, parameterised on the test database name.

    Mirrors ``trace_query`` in
    ``tracer/views/trace_session.py::_retrieve_clickhouse`` -- copied
    instead of imported because the view inlines the SQL. If the view
    changes the alias name back to ``start_time`` (or otherwise
    re-introduces the collision), this test must fail; updating the
    copy here without re-running against CH would defeat the point.
    """
    return f"""
        SELECT
            toString(trace_id) AS trace_id,
            any(input) AS input,
            any(output) AS output,
            min(CASE WHEN parent_span_id IS NULL OR parent_span_id = '' THEN latency_ms ELSE NULL END) AS root_latency_ms,
            round(sum(cost), 6) AS total_cost,
            min(start_time) AS trace_min_start_time,
            sum(total_tokens) AS total_tokens,
            sum(prompt_tokens) AS input_tokens,
            sum(completion_tokens) AS output_tokens
        FROM {database}.spans
        WHERE project_id = %(project_id)s
          AND trace_session_id = %(session_id)s
          AND _peerdb_is_deleted = 0
        GROUP BY trace_id
        ORDER BY trace_min_start_time ASC
        LIMIT %(limit)s
        OFFSET %(offset)s
    """


@pytest.mark.integration
class TestTraceListingCHQuery:
    """End-to-end validation of the listing query against test ClickHouse."""

    def _exec(self, client, query, params):
        rows, columns = client.execute(query, params, with_column_types=True)
        col_names = [c[0] for c in columns]
        return [dict(zip(col_names, row)) for row in rows]

    def test_query_parses_without_alias_collision(self, ch_spans_table):
        """The original bug raised ILLEGAL_AGGREGATION at parse/analysis
        time -- empty data is enough to catch a regression."""
        client = ch_spans_table
        project_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())

        # Should not raise -- empty result is fine.
        rows = self._exec(
            client,
            _trace_listing_query(_TEST_DATABASE),
            {
                "project_id": project_id,
                "session_id": session_id,
                "limit": 10,
                "offset": 0,
            },
        )
        assert rows == []

    def test_orders_by_min_start_time_ascending(self, ch_spans_table):
        """Two traces in the same session with different ``start_time``s
        come back chronologically -- the trace whose earliest span
        starts first is row 0, regardless of trace_id alphabetical
        order."""
        client = ch_spans_table
        project_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        base = datetime(2026, 5, 11, 8, 52, 9, tzinfo=timezone.utc)

        # Pick trace ids so the alphabetically-first one starts LATER.
        trace_late_alpha = "00000000-0000-0000-0000-000000000001"
        trace_early_late_alpha = "ffffffff-ffff-ffff-ffff-ffffffffffff"

        columns = (
            "trace_id, project_id, trace_session_id, parent_span_id, "
            "input, output, latency_ms, cost, start_time, "
            "total_tokens, prompt_tokens, completion_tokens, _peerdb_is_deleted"
        )
        client.execute(
            f"INSERT INTO {_TEST_DATABASE}.spans ({columns}) VALUES",
            [
                # Alphabetically-first trace, starts LATER.
                {
                    "trace_id": trace_late_alpha,
                    "project_id": project_id,
                    "trace_session_id": session_id,
                    "parent_span_id": None,
                    "input": "input-late",
                    "output": "output-late",
                    "latency_ms": 100,
                    "cost": 0.001,
                    "start_time": base + timedelta(seconds=5),
                    "total_tokens": 10,
                    "prompt_tokens": 5,
                    "completion_tokens": 5,
                    "_peerdb_is_deleted": 0,
                },
                # Alphabetically-last trace, starts EARLIER.
                {
                    "trace_id": trace_early_late_alpha,
                    "project_id": project_id,
                    "trace_session_id": session_id,
                    "parent_span_id": None,
                    "input": "input-early",
                    "output": "output-early",
                    "latency_ms": 50,
                    "cost": 0.002,
                    "start_time": base + timedelta(seconds=1),
                    "total_tokens": 20,
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "_peerdb_is_deleted": 0,
                },
            ],
        )

        rows = self._exec(
            client,
            _trace_listing_query(_TEST_DATABASE),
            {
                "project_id": project_id,
                "session_id": session_id,
                "limit": 10,
                "offset": 0,
            },
        )

        assert len(rows) == 2
        # Earliest start wins -- not alphabetic id.
        assert rows[0]["trace_id"] == trace_early_late_alpha
        assert rows[1]["trace_id"] == trace_late_alpha
        # The aliased column is populated (this was null in the PG
        # fallback that the original bug forced the API onto).
        assert rows[0]["trace_min_start_time"] is not None
        assert rows[0]["trace_min_start_time"] < rows[1]["trace_min_start_time"]

    def test_min_start_time_picks_earliest_span_in_trace(self, ch_spans_table):
        """``min(start_time)`` aggregates over ALL spans in a trace, so
        a child span that starts before its parent (rare but possible
        with skewed clocks) still drives the trace's ordering."""
        client = ch_spans_table
        project_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        base = datetime(2026, 5, 11, 8, 52, 9, tzinfo=timezone.utc)
        trace_id = str(uuid.uuid4())

        columns = (
            "trace_id, project_id, trace_session_id, parent_span_id, "
            "input, output, latency_ms, cost, start_time, "
            "total_tokens, prompt_tokens, completion_tokens, _peerdb_is_deleted"
        )
        client.execute(
            f"INSERT INTO {_TEST_DATABASE}.spans ({columns}) VALUES",
            [
                {
                    "trace_id": trace_id,
                    "project_id": project_id,
                    "trace_session_id": session_id,
                    "parent_span_id": None,
                    "input": "",
                    "output": "",
                    "latency_ms": 100,
                    "cost": 0.001,
                    "start_time": base + timedelta(seconds=5),
                    "total_tokens": 10,
                    "prompt_tokens": 5,
                    "completion_tokens": 5,
                    "_peerdb_is_deleted": 0,
                },
                {
                    "trace_id": trace_id,
                    "project_id": project_id,
                    "trace_session_id": session_id,
                    "parent_span_id": "root_span",
                    "input": "",
                    "output": "",
                    "latency_ms": 50,
                    "cost": 0.001,
                    "start_time": base + timedelta(seconds=1),
                    "total_tokens": 10,
                    "prompt_tokens": 5,
                    "completion_tokens": 5,
                    "_peerdb_is_deleted": 0,
                },
            ],
        )

        rows = self._exec(
            client,
            _trace_listing_query(_TEST_DATABASE),
            {
                "project_id": project_id,
                "session_id": session_id,
                "limit": 10,
                "offset": 0,
            },
        )

        assert len(rows) == 1
        # min(start_time) = t+1 (child wins), not t+5 (root). The
        # clickhouse-driver returns DateTime64 as naive datetimes; the
        # value is in UTC but tzinfo is dropped, so compare via the
        # naive form of our base instant.
        assert rows[0]["trace_min_start_time"] == (
            base + timedelta(seconds=1)
        ).replace(tzinfo=None)
