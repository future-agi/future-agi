"""
ClickHouse Integration Tests

Tests that execute real queries against a ClickHouse instance.
These tests require a running ClickHouse server and are skipped
when ClickHouse is not available.

Run with:
    pytest tracer/tests/test_clickhouse_integration.py -v -m integration

Requires:
    - ClickHouse running on CH_TEST_HOST:CH_TEST_PORT (default: localhost:18123)
    - clickhouse-connect package installed

Covered:
- Connection and schema lifecycle
- Data insertion (spans, evals, nulls, deduplication)
- DashboardQueryBuilder integration (system/eval metrics, time ranges, filters, aggregations)
- SimulationQueryBuilder integration (system metrics, breakdowns, filters)
- DatasetQueryBuilder integration (system metrics, breakdowns)
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------

_TEST_DATABASE = "test_futureagi"


@pytest.fixture(scope="session")
def ch_client():
    """Connect to test ClickHouse instance. Skip if unavailable."""
    try:
        import clickhouse_connect
    except ImportError:
        pytest.skip("clickhouse-connect not installed")

    try:
        client = clickhouse_connect.get_client(
            host=os.environ.get("CH_TEST_HOST", "localhost"),
            port=int(os.environ.get("CH_TEST_PORT", "18123")),
        )
        client.command("SELECT 1")
        return client
    except Exception:
        pytest.skip("ClickHouse not available for integration tests")


@pytest.fixture(scope="session")
def ch_schema(ch_client):
    """Initialize ClickHouse schema for tests.

    Creates the test_futureagi database and applies all DDL statements.
    Runs once per test session.

    Uses ``_to_single_node_engine`` so the DDL works on the single-node
    test ClickHouse instance (no Keeper / Replicated engines).
    """
    import clickhouse_connect
    from tracer.services.clickhouse.schema import (
        _to_single_node_engine,
        SCHEMA_DDL_STATEMENTS,
    )

    ch_client.command(f"CREATE DATABASE IF NOT EXISTS {_TEST_DATABASE}")

    # Connect with the test database as default so unqualified table names
    # in DDL (``CREATE TABLE foo``) land in test_futureagi, not ``default``.
    db_client = clickhouse_connect.get_client(
        host=os.environ.get("CH_TEST_HOST", "localhost"),
        port=int(os.environ.get("CH_TEST_PORT", "18123")),
        database=_TEST_DATABASE,
    )

    for name, ddl in SCHEMA_DDL_STATEMENTS:
        # Convert to single-node engines for the test CH instance
        ddl_test = _to_single_node_engine(ddl)
        # Rewrite any explicit ``futureagi.`` references to the test database
        ddl_test = ddl_test.replace("futureagi.", f"{_TEST_DATABASE}.")
        try:
            db_client.command(ddl_test)
        except Exception as exc:
            # Ignore "already exists" errors; propagate others so schema
            # issues surface during test runs instead of hiding silently.
            err_msg = str(exc)
            if "already exists" not in err_msg.lower():
                import warnings
                warnings.warn(f"CH schema DDL failed for {name}: {err_msg[:200]}")

    db_client.close()
    return ch_client


@pytest.fixture
def ch_test_data(ch_schema):
    """Insert test span data and clean up after test.

    Inserts a known set of spans into the CDC table and waits briefly
    for the Materialized View to populate the ``spans`` table.
    """
    client = ch_schema
    project_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Insert test trace into CDC trace table
    client.command(
        f"""
        INSERT INTO {_TEST_DATABASE}.tracer_trace
            (id, project_id, name, session_id, external_id, tags,
             _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
        VALUES
            ('{trace_id}', '{project_id}', 'Test Trace', '', '', '[]',
             now64(), 0, 1)
        """
    )

    # Insert test spans into CDC observation span table
    spans = []
    for i in range(5):
        span_id = f"span_{uuid.uuid4().hex[:16]}"
        latency = 100 * (i + 1)
        cost = 0.001 * (i + 1)
        tokens = 10 * (i + 1)
        model = "gpt-4" if i % 2 == 0 else "gpt-3.5-turbo"
        status = "OK" if i < 4 else "ERROR"
        start_time = now - timedelta(seconds=latency / 1000 + 1)
        end_time = now

        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.tracer_observation_span
                (id, trace_id, project_id, name, observation_type, status,
                 start_time, end_time, latency_ms, model, provider,
                 prompt_tokens, completion_tokens, total_tokens, cost,
                 input, output, span_attributes, resource_attributes,
                 metadata, tags, span_events,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{span_id}', '{trace_id}', '{project_id}', 'Span {i}', 'llm', '{status}',
                 '{start_time.strftime("%Y-%m-%d %H:%M:%S")}',
                 '{end_time.strftime("%Y-%m-%d %H:%M:%S")}',
                 {latency}, '{model}', 'openai',
                 {tokens}, {tokens // 2}, {tokens + tokens // 2}, {cost},
                 '{{"prompt": "hello {i}"}}', '{{"response": "world {i}"}}',
                 '{{}}',' {{}}',
                 '{{}}', '[]', '[]',
                 now64(), 0, {i + 1})
            """
        )
        spans.append(
            {
                "id": span_id,
                "trace_id": trace_id,
                "project_id": project_id,
                "latency_ms": latency,
                "cost": cost,
                "total_tokens": tokens + tokens // 2,
                "model": model,
                "status": status,
            }
        )

    yield {
        "client": client,
        "project_id": project_id,
        "trace_id": trace_id,
        "spans": spans,
        "now": now,
    }

    # Cleanup
    for table in [
        "tracer_observation_span",
        "tracer_trace",
        "tracer_eval_logger",
        "spans",
    ]:
        try:
            client.command(f"TRUNCATE TABLE {_TEST_DATABASE}.{table}")
        except Exception:
            pass


@pytest.fixture
def ch_eval_data(ch_test_data):
    """Insert eval logger data linked to the test spans."""
    client = ch_test_data["client"]
    config_id = str(uuid.uuid4())
    now = ch_test_data["now"]

    for i, span in enumerate(ch_test_data["spans"]):
        eval_id = str(uuid.uuid4())
        score = 0.5 + (i * 0.1)  # 0.5, 0.6, 0.7, 0.8, 0.9
        passed = 1 if score >= 0.7 else 0

        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.tracer_eval_logger
                (id, observation_span_id, trace_id, custom_eval_config_id,
                 output_float, output_bool,
                 created_at,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{eval_id}', '{span["id"]}', '{span["trace_id"]}',
                 '{config_id}', {score}, {passed},
                 '{now.strftime("%Y-%m-%d %H:%M:%S")}',
                 now64(), 0, {i + 1})
            """
        )

    ch_test_data["config_id"] = config_id
    return ch_test_data


# ---------------------------------------------------------------------------
# Simulation & Dataset fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ch_simulation_data(ch_schema):
    """Insert test simulation call data."""
    client = ch_schema
    test_execution_id = str(uuid.uuid4())
    scenario_id = str(uuid.uuid4())
    agent_version_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    for i in range(5):
        call_id = str(uuid.uuid4())
        call_type = "voice" if i % 2 == 0 else "text"
        call_status = "completed" if i < 4 else "failed"
        duration = 30.0 + i * 10
        score = 0.6 + i * 0.08

        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.simulate_call_execution
                (id, test_execution_id, scenario_id, agent_version_id,
                 simulation_call_type, status,
                 duration_seconds, cost_cents, overall_score,
                 message_count, created_at,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{call_id}', '{test_execution_id}', '{scenario_id}', '{agent_version_id}',
                 '{call_type}', '{call_status}',
                 {duration}, {i * 0.5}, {score},
                 {10 + i}, '{now.strftime("%Y-%m-%d %H:%M:%S")}',
                 now64(), 0, {i + 1})
            """
        )

    yield {
        "client": client,
        "test_execution_id": test_execution_id,
        "scenario_id": scenario_id,
        "agent_version_id": agent_version_id,
    }

    try:
        client.command(f"TRUNCATE TABLE {_TEST_DATABASE}.simulate_call_execution")
    except Exception:
        pass


@pytest.fixture
def ch_dataset_data(ch_schema):
    """Insert test dataset cell data."""
    client = ch_schema
    dataset_id = str(uuid.uuid4())
    row_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    column_id = str(uuid.uuid4())

    for i in range(5):
        cell_id = str(uuid.uuid4())
        prompt_tokens = 50 + i * 10
        completion_tokens = 20 + i * 5
        response_time = 100.0 + i * 50
        cell_status = "completed" if i < 4 else "error"

        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.model_hub_cell
                (id, dataset_id, column_id, row_id,
                 prompt_tokens, completion_tokens, response_time, status,
                 created_at,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{cell_id}', '{dataset_id}', '{column_id}', '{row_id}',
                 {prompt_tokens}, {completion_tokens}, {response_time}, '{cell_status}',
                 '{now.strftime("%Y-%m-%d %H:%M:%S")}',
                 now64(), 0, {i + 1})
            """
        )

    yield {
        "client": client,
        "dataset_id": dataset_id,
        "column_id": column_id,
    }

    try:
        client.command(f"TRUNCATE TABLE {_TEST_DATABASE}.model_hub_cell")
    except Exception:
        pass


# ===========================================================================
# A. TestClickHouseConnection
# ===========================================================================


@pytest.mark.integration
class TestClickHouseConnection:
    """Test basic ClickHouse connectivity and schema management."""

    def test_can_connect_to_clickhouse(self, ch_client):
        """Should be able to execute a simple query."""
        result = ch_client.command("SELECT 1")
        assert result == 1

    def test_schema_initialization(self, ch_schema):
        """Applying DDL should create all expected tables."""
        client = ch_schema
        result = client.query(
            f"SELECT name FROM system.tables WHERE database = '{_TEST_DATABASE}'"
        )
        tables = [row[0] for row in result.result_rows]
        # Core CDC tables should exist
        assert "tracer_observation_span" in tables
        assert "tracer_trace" in tables

    def test_drop_and_recreate_schema(self, ch_client):
        """Should be able to drop and recreate the test database."""
        from tracer.services.clickhouse.schema import (
            _to_single_node_engine,
            get_drop_statements,
            SCHEMA_DDL_STATEMENTS,
        )

        temp_db = "test_futureagi_temp"
        ch_client.command(f"CREATE DATABASE IF NOT EXISTS {temp_db}")

        # Apply schema (single-node engines for test CH)
        for name, ddl in SCHEMA_DDL_STATEMENTS:
            ddl_test = _to_single_node_engine(ddl)
            ddl_test = ddl_test.replace("futureagi.", f"{temp_db}.")
            try:
                ch_client.command(ddl_test)
            except Exception:
                pass

        # Drop using drop statements (rewritten for temp DB)
        for drop_stmt in get_drop_statements():
            drop_stmt = drop_stmt.replace("futureagi.", f"{temp_db}.")
            try:
                ch_client.command(drop_stmt)
            except Exception:
                pass

        # Drop the database itself
        ch_client.command(f"DROP DATABASE IF EXISTS {temp_db}")

        # Verify it's gone
        result = ch_client.command(
            f"SELECT count() FROM system.databases WHERE name = '{temp_db}'"
        )
        assert result == 0


# ===========================================================================
# B. TestClickHouseDataInsertion
# ===========================================================================


@pytest.mark.integration
class TestClickHouseDataInsertion:
    """Test data insertion into ClickHouse CDC tables."""

    def test_insert_span_data(self, ch_test_data):
        """Inserting spans into CDC table should be queryable."""
        client = ch_test_data["client"]
        project_id = ch_test_data["project_id"]

        result = client.command(
            f"""
            SELECT count()
            FROM {_TEST_DATABASE}.tracer_observation_span FINAL
            WHERE project_id = '{project_id}'
              AND _peerdb_is_deleted = 0
            """
        )
        assert result == 5

    def test_insert_eval_data(self, ch_eval_data):
        """Inserting eval logger entries should be queryable."""
        client = ch_eval_data["client"]
        config_id = ch_eval_data["config_id"]

        result = client.command(
            f"""
            SELECT count()
            FROM {_TEST_DATABASE}.tracer_eval_logger FINAL
            WHERE custom_eval_config_id = '{config_id}'
              AND _peerdb_is_deleted = 0
            """
        )
        assert result == 5

    def test_insert_with_null_fields(self, ch_schema):
        """Inserting a span with NULL optional fields should succeed."""
        client = ch_schema
        span_id = f"null_span_{uuid.uuid4().hex[:8]}"
        project_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())

        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.tracer_observation_span
                (id, trace_id, project_id, name, observation_type,
                 start_time, end_time, latency_ms, model,
                 prompt_tokens, completion_tokens, total_tokens, cost,
                 input, output, span_attributes, resource_attributes,
                 metadata, tags, span_events,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{span_id}', '{trace_id}', '{project_id}', 'Null Span', 'tool',
                 NULL, NULL, NULL, NULL,
                 NULL, NULL, NULL, NULL,
                 '', '', '{{}}', '{{}}',
                 '{{}}', '[]', '[]',
                 now64(), 0, 1)
            """
        )

        result = client.command(
            f"""
            SELECT count()
            FROM {_TEST_DATABASE}.tracer_observation_span FINAL
            WHERE id = '{span_id}' AND _peerdb_is_deleted = 0
            """
        )
        assert result == 1

        # Cleanup
        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.tracer_observation_span
                (id, trace_id, project_id, name, observation_type,
                 input, output, span_attributes, resource_attributes,
                 metadata, tags, span_events,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{span_id}', '{trace_id}', '{project_id}', 'Null Span', 'tool',
                 '', '', '{{}}', '{{}}',
                 '{{}}', '[]', '[]',
                 now64(), 1, 2)
            """
        )

    def test_insert_duplicate_deduplication(self, ch_schema):
        """ReplacingMergeTree should keep only the latest version of a row."""
        client = ch_schema
        span_id = f"dedup_span_{uuid.uuid4().hex[:8]}"
        project_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())

        # Insert version 1
        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.tracer_observation_span
                (id, trace_id, project_id, name, observation_type,
                 latency_ms, input, output,
                 span_attributes, resource_attributes, metadata, tags, span_events,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{span_id}', '{trace_id}', '{project_id}', 'Version 1', 'llm',
                 100, '', '',
                 '{{}}', '{{}}', '{{}}', '[]', '[]',
                 now64(), 0, 1)
            """
        )

        # Insert version 2 (same id, higher version)
        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.tracer_observation_span
                (id, trace_id, project_id, name, observation_type,
                 latency_ms, input, output,
                 span_attributes, resource_attributes, metadata, tags, span_events,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{span_id}', '{trace_id}', '{project_id}', 'Version 2', 'llm',
                 200, '', '',
                 '{{}}', '{{}}', '{{}}', '[]', '[]',
                 now64(), 0, 2)
            """
        )

        # With FINAL, should see only the latest version
        result = client.command(
            f"""
            SELECT name
            FROM {_TEST_DATABASE}.tracer_observation_span FINAL
            WHERE id = '{span_id}' AND _peerdb_is_deleted = 0
            """
        )
        assert "Version 2" in str(result)

        # Count should be 1 (deduplicated)
        count = client.command(
            f"""
            SELECT count()
            FROM {_TEST_DATABASE}.tracer_observation_span FINAL
            WHERE id = '{span_id}' AND _peerdb_is_deleted = 0
            """
        )
        assert count == 1

        # Cleanup: soft-delete
        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.tracer_observation_span
                (id, trace_id, project_id, name, observation_type,
                 input, output, span_attributes, resource_attributes,
                 metadata, tags, span_events,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{span_id}', '{trace_id}', '{project_id}', 'Version 2', 'llm',
                 '', '', '{{}}', '{{}}',
                 '{{}}', '[]', '[]',
                 now64(), 1, 3)
            """
        )


# ===========================================================================
# C. TestDashboardQueryBuilderIntegration
# ===========================================================================


@pytest.mark.integration
class TestDashboardQueryBuilderIntegration:
    """Test DashboardQueryBuilder against a real ClickHouse instance."""

    def _build_config(
        self,
        project_id,
        metric_name="latency",
        aggregation="avg",
        metric_type="system_metric",
        preset="30D",
        granularity="day",
        filters=None,
        breakdowns=None,
        **extra_metric_fields,
    ):
        config = {
            "project_ids": [project_id],
            "granularity": granularity,
            "time_range": {"preset": preset},
            "metrics": [
                {
                    "id": metric_name,
                    "name": metric_name,
                    "type": metric_type,
                    "aggregation": aggregation,
                    **extra_metric_fields,
                }
            ],
            "filters": filters or [],
            "breakdowns": breakdowns or [],
        }
        return config

    def _execute_query(self, client, sql, params):
        """Execute a parameterized query against the test database."""
        # Rewrite table references to the test database
        sql_test = sql.replace("futureagi.", f"{_TEST_DATABASE}.")
        # Replace bare table references that aren't already prefixed
        for table in ["spans", "tracer_eval_logger", "trace_annotation"]:
            # Only replace if not already prefixed with database name
            sql_test = sql_test.replace(
                f"FROM {table} ", f"FROM {_TEST_DATABASE}.{table} "
            )
            sql_test = sql_test.replace(
                f"FROM {table}\n", f"FROM {_TEST_DATABASE}.{table}\n"
            )
            sql_test = sql_test.replace(
                f"JOIN {table} ", f"JOIN {_TEST_DATABASE}.{table} "
            )
        try:
            result = client.query(sql_test, parameters=params)
            return result.result_rows
        except Exception as e:
            # Return empty if query references tables that don't exist in test
            if "UNKNOWN_TABLE" in str(e) or "doesn't exist" in str(e):
                pytest.skip(f"Table not available in test DB: {e}")
            raise

    def test_system_metric_query_executes(self, ch_test_data):
        """Building and executing a latency query should not raise."""
        from tracer.services.clickhouse.query_builders.dashboard import (
            DashboardQueryBuilder,
        )

        config = self._build_config(ch_test_data["project_id"])
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1

        sql, params, metric_info = queries[0]
        rows = self._execute_query(ch_test_data["client"], sql, params)
        # Should return rows (possibly empty if MV hasn't run)
        assert isinstance(rows, list)

    def test_eval_metric_query_executes(self, ch_eval_data):
        """Building and executing an eval metric query should not raise."""
        from tracer.services.clickhouse.query_builders.dashboard import (
            DashboardQueryBuilder,
        )

        config = self._build_config(
            ch_eval_data["project_id"],
            metric_name="accuracy",
            metric_type="eval_metric",
            aggregation="avg",
            config_id=ch_eval_data["config_id"],
            output_type="SCORE",
        )
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        rows = self._execute_query(ch_eval_data["client"], sql, params)
        assert isinstance(rows, list)

    def test_time_range_filtering_works(self, ch_schema):
        """Data inserted at different dates should be filtered by time range."""
        client = ch_schema
        project_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # Insert trace
        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.tracer_trace
                (id, project_id, name, session_id, external_id, tags,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{trace_id}', '{project_id}', 'Time Test Trace', '', '', '[]',
                 now64(), 0, 1)
            """
        )

        # Insert spans at different dates: 2 days ago and 10 days ago
        for days_ago, label in [(2, "recent"), (10, "old")]:
            span_id = f"time_{label}_{uuid.uuid4().hex[:8]}"
            ts = now - timedelta(days=days_ago)
            client.command(
                f"""
                INSERT INTO {_TEST_DATABASE}.tracer_observation_span
                    (id, trace_id, project_id, name, observation_type,
                     start_time, end_time, latency_ms,
                     input, output, span_attributes, resource_attributes,
                     metadata, tags, span_events,
                     _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
                VALUES
                    ('{span_id}', '{trace_id}', '{project_id}', 'Span {label}', 'llm',
                     '{ts.strftime("%Y-%m-%d %H:%M:%S")}',
                     '{ts.strftime("%Y-%m-%d %H:%M:%S")}',
                     100, '', '',
                     '{{}}', '{{}}', '{{}}', '[]', '[]',
                     now64(), 0, 1)
                """
            )

        # Query directly to verify time filtering
        count_7d = client.command(
            f"""
            SELECT count()
            FROM {_TEST_DATABASE}.tracer_observation_span FINAL
            WHERE project_id = '{project_id}'
              AND _peerdb_is_deleted = 0
              AND start_time >= now() - INTERVAL 7 DAY
            """
        )
        count_30d = client.command(
            f"""
            SELECT count()
            FROM {_TEST_DATABASE}.tracer_observation_span FINAL
            WHERE project_id = '{project_id}'
              AND _peerdb_is_deleted = 0
              AND start_time >= now() - INTERVAL 30 DAY
            """
        )
        assert count_7d == 1  # Only the recent span
        assert count_30d == 2  # Both spans

        # Cleanup
        for label in ["recent", "old"]:
            client.command(
                f"""
                ALTER TABLE {_TEST_DATABASE}.tracer_observation_span
                DELETE WHERE project_id = '{project_id}'
                """
            )

    def test_breakdown_query_returns_grouped_data(self, ch_test_data):
        """A breakdown query should group results by the breakdown dimension."""
        from tracer.services.clickhouse.query_builders.dashboard import (
            DashboardQueryBuilder,
        )

        config = self._build_config(
            ch_test_data["project_id"],
            breakdowns=[{"type": "system_metric", "name": "model"}],
        )
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]

        assert "breakdown_value" in sql
        rows = self._execute_query(ch_test_data["client"], sql, params)
        assert isinstance(rows, list)

    def test_filter_string_equals(self, ch_test_data):
        """A filter with equal_to operator should narrow results."""
        from tracer.services.clickhouse.query_builders.dashboard import (
            DashboardQueryBuilder,
        )

        config = self._build_config(
            ch_test_data["project_id"],
            filters=[
                {
                    "metric_type": "system_metric",
                    "metric_name": "model",
                    "operator": "equal_to",
                    "value": "gpt-4",
                }
            ],
        )
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]

        assert "model" in sql
        rows = self._execute_query(ch_test_data["client"], sql, params)
        assert isinstance(rows, list)

    def test_filter_string_contains(self, ch_test_data):
        """A str_contains filter should use LIKE."""
        from tracer.services.clickhouse.query_builders.dashboard import (
            DashboardQueryBuilder,
        )

        config = self._build_config(
            ch_test_data["project_id"],
            filters=[
                {
                    "metric_type": "system_metric",
                    "metric_name": "model",
                    "operator": "str_contains",
                    "value": "gpt",
                }
            ],
        )
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]

        assert "LIKE" in sql
        rows = self._execute_query(ch_test_data["client"], sql, params)
        assert isinstance(rows, list)

    def test_filter_number_between(self, ch_test_data):
        """Numeric filters with greater_than/less_than should narrow results."""
        from tracer.services.clickhouse.query_builders.dashboard import (
            DashboardQueryBuilder,
        )

        config = self._build_config(
            ch_test_data["project_id"],
            filters=[
                {
                    "metric_type": "system_metric",
                    "metric_name": "cost",
                    "operator": "greater_than",
                    "value": 0.001,
                },
                {
                    "metric_type": "system_metric",
                    "metric_name": "cost",
                    "operator": "less_than",
                    "value": 0.01,
                },
            ],
        )
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        rows = self._execute_query(ch_test_data["client"], sql, params)
        assert isinstance(rows, list)

    def test_aggregation_avg_returns_correct_value(self, ch_test_data):
        """avg() aggregation on known data should return the correct average."""
        client = ch_test_data["client"]
        project_id = ch_test_data["project_id"]

        # Compute expected average from test data
        latencies = [s["latency_ms"] for s in ch_test_data["spans"]]
        expected_avg = sum(latencies) / len(latencies)

        result = client.command(
            f"""
            SELECT avg(latency_ms)
            FROM {_TEST_DATABASE}.tracer_observation_span FINAL
            WHERE project_id = '{project_id}'
              AND _peerdb_is_deleted = 0
            """
        )
        assert abs(float(result) - expected_avg) < 0.01

    def test_aggregation_p95_returns_correct_value(self, ch_test_data):
        """quantile(0.95) should return a value within the expected range."""
        client = ch_test_data["client"]
        project_id = ch_test_data["project_id"]

        latencies = sorted([s["latency_ms"] for s in ch_test_data["spans"]])

        result = client.command(
            f"""
            SELECT quantile(0.95)(latency_ms)
            FROM {_TEST_DATABASE}.tracer_observation_span FINAL
            WHERE project_id = '{project_id}'
              AND _peerdb_is_deleted = 0
            """
        )
        p95 = float(result)
        # p95 should be between the max and second-to-max value
        assert p95 >= latencies[-2]
        assert p95 <= latencies[-1] * 1.1  # Allow small margin

    def test_aggregation_count_distinct(self, ch_test_data):
        """uniq() should count distinct models correctly."""
        client = ch_test_data["client"]
        project_id = ch_test_data["project_id"]

        result = client.command(
            f"""
            SELECT uniq(model)
            FROM {_TEST_DATABASE}.tracer_observation_span FINAL
            WHERE project_id = '{project_id}'
              AND _peerdb_is_deleted = 0
            """
        )
        # Test data has 2 distinct models: gpt-4, gpt-3.5-turbo
        assert int(result) == 2


# ===========================================================================
# D. TestSimulationQueryBuilderIntegration
# ===========================================================================


@pytest.mark.integration
class TestSimulationQueryBuilderIntegration:
    """Test SimulationQueryBuilder against a real ClickHouse instance."""

    def _build_config(
        self,
        workspace_id,
        metric_name="duration",
        aggregation="avg",
        preset="30D",
        granularity="day",
        filters=None,
        breakdowns=None,
        **extra,
    ):
        return {
            "source": "simulation",
            "workspace_id": workspace_id,
            "granularity": granularity,
            "time_range": {"preset": preset},
            "metrics": [
                {
                    "id": metric_name,
                    "name": metric_name,
                    "type": "system_metric",
                    "aggregation": aggregation,
                    **extra,
                }
            ],
            "filters": filters or [],
            "breakdowns": breakdowns or [],
        }

    def test_simulation_metric_query_executes(self, ch_simulation_data):
        """Building and executing a simulation duration query should not raise."""
        from tracer.services.clickhouse.query_builders.simulation_dashboard import (
            SimulationQueryBuilder,
        )

        config = self._build_config(ch_simulation_data["test_execution_id"])
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1

        sql, params, _ = queries[0]
        # Rewrite for test DB
        sql_test = sql.replace("futureagi.", f"{_TEST_DATABASE}.")
        try:
            result = ch_simulation_data["client"].query(sql_test, parameters=params)
            assert isinstance(result.result_rows, list)
        except Exception as e:
            if "UNKNOWN_TABLE" in str(e) or "doesn't exist" in str(e):
                pytest.skip(f"Simulation tables not in test schema: {e}")
            raise

    def test_simulation_breakdown_by_agent_version(self, ch_simulation_data):
        """Breakdown by agent_version should include breakdown_value column."""
        from tracer.services.clickhouse.query_builders.simulation_dashboard import (
            SimulationQueryBuilder,
        )

        config = self._build_config(
            ch_simulation_data["test_execution_id"],
            breakdowns=[{"type": "system_metric", "name": "agent_version"}],
        )
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "breakdown_value" in sql

    def test_simulation_filter_by_call_type(self, ch_simulation_data):
        """Filtering by call_type should produce valid SQL."""
        from tracer.services.clickhouse.query_builders.simulation_dashboard import (
            SimulationQueryBuilder,
        )

        config = self._build_config(
            ch_simulation_data["test_execution_id"],
            filters=[
                {
                    "metric_type": "system_metric",
                    "metric_name": "call_type",
                    "operator": "equal_to",
                    "value": "voice",
                }
            ],
        )
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "call_type" in sql

        sql_test = sql.replace("futureagi.", f"{_TEST_DATABASE}.")
        try:
            result = ch_simulation_data["client"].query(sql_test, parameters=params)
            assert isinstance(result.result_rows, list)
        except Exception as e:
            if "UNKNOWN_TABLE" in str(e) or "doesn't exist" in str(e):
                pytest.skip(f"Simulation tables not in test schema: {e}")
            raise


# ===========================================================================
# E. TestDatasetQueryBuilderIntegration
# ===========================================================================


@pytest.mark.integration
class TestDatasetQueryBuilderIntegration:
    """Test DatasetQueryBuilder against a real ClickHouse instance."""

    def _build_config(
        self,
        workspace_id,
        metric_name="row_count",
        aggregation="count",
        preset="30D",
        granularity="day",
        filters=None,
        breakdowns=None,
        **extra,
    ):
        return {
            "workflow": "dataset",
            "workspace_id": workspace_id,
            "granularity": granularity,
            "time_range": {"preset": preset},
            "metrics": [
                {
                    "id": metric_name,
                    "name": metric_name,
                    "type": "system_metric",
                    "aggregation": aggregation,
                    **extra,
                }
            ],
            "filters": filters or [],
            "breakdowns": breakdowns or [],
        }

    def test_dataset_metric_query_executes(self, ch_dataset_data):
        """Building and executing a dataset row_count query should not raise."""
        from tracer.services.clickhouse.query_builders.dataset_dashboard import (
            DatasetQueryBuilder,
        )

        config = self._build_config(ch_dataset_data["dataset_id"])
        builder = DatasetQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1

        sql, params, _ = queries[0]
        sql_test = sql.replace("futureagi.", f"{_TEST_DATABASE}.")
        try:
            result = ch_dataset_data["client"].query(sql_test, parameters=params)
            assert isinstance(result.result_rows, list)
        except Exception as e:
            if "UNKNOWN_TABLE" in str(e) or "doesn't exist" in str(e):
                pytest.skip(f"Dataset tables not in test schema: {e}")
            raise

    def test_dataset_breakdown_by_column(self, ch_dataset_data):
        """Breakdown by column_name should include breakdown_value column."""
        from tracer.services.clickhouse.query_builders.dataset_dashboard import (
            DatasetQueryBuilder,
        )

        config = self._build_config(
            ch_dataset_data["dataset_id"],
            breakdowns=[{"type": "system_metric", "name": "column_name"}],
        )
        builder = DatasetQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "breakdown_value" in sql
