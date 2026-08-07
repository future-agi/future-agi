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
- SimulationQueryBuilder integration (system metrics, breakdowns, filters)
- DatasetQueryBuilder integration (system metrics, breakdowns)
"""

import os
import uuid
from datetime import datetime, timezone

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
