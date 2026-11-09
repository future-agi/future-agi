"""Schema-aware health checks: declared requirements, not discovered optionality."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from rest_framework.test import APIRequestFactory

from tracer.services.clickhouse import consistency, schema
from tracer.services.clickhouse.v2 import query_service
from tracer.views.clickhouse_health import ClickHouseHealthView

CDC_TABLES = ("tracer_trace", "trace_session", "tracer_eval_logger")
CDC_COLUMNS = {"project_id", "created_at", "_peerdb_is_deleted", "_peerdb_synced_at"}
NATIVE_COLUMNS = {
    "traces": {"id", "project_id", "is_deleted", "_version"},
    "spans": {"id", "project_id", "is_deleted", "_version"},
    "trace_sessions": {"trace_session_id", "project_id", "is_deleted", "version"},
}
NATIVE_EVAL_COLUMNS = {
    "id",
    "trace_id",
    "observation_span_id",
    "trace_session_id",
    "custom_eval_config_id",
    "is_deleted",
    "_version",
}


@pytest.fixture
def configure(monkeypatch, settings):
    settings.CLICKHOUSE_V2 = {}
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    monkeypatch.setattr(consistency, "is_clickhouse_enabled", lambda: True)

    def apply(legacy, native=None, *, retired=False):
        monkeypatch.setattr(schema, "_DROP_LEGACY_CDC_CHAIN", retired)
        monkeypatch.setattr(consistency, "get_clickhouse_client", lambda: legacy)
        # Patch the owner as well as the imported binding so these tests also
        # run against the old checker for the fails-on-base regression proof.
        monkeypatch.setattr(query_service, "get_v2_query_client", lambda: native)
        monkeypatch.setattr(
            consistency, "get_v2_query_client", lambda: native, raising=False
        )
        return consistency.ConsistencyChecker()

    return apply


def fake_client(columns, *, sample=None):
    if sample is None:
        sample = (1, datetime.now(UTC))

    def execute(sql, params=None):
        if "system.columns" in sql:
            return [
                (table, column) for table, names in columns.items() for column in names
            ]
        if "max(_peerdb_synced_at)" in sql:
            # Match both old and new aggregate query shapes.
            return [sample if "count()" in sql else (sample[1],)]
        if "LIMIT 0" in sql:
            return []
        if "count()" in sql:
            return [(0,)]
        raise AssertionError(f"Unexpected query: {sql}")

    client = Mock()
    client.ping.return_value = True
    client.execute.side_effect = execute
    return client


def response():
    return ClickHouseHealthView.as_view()(
        APIRequestFactory().get("/api/health/clickhouse/")
    )


@pytest.mark.unit
class TestSchemaAwareHealth:
    def test_disabled_does_not_probe(self, configure, monkeypatch):
        client = fake_client({})
        checker = configure(client)
        monkeypatch.setattr(consistency, "is_clickhouse_enabled", lambda: False)
        assert checker.get_health_status().status == "disabled"
        client.ping.assert_not_called()
        client.execute.assert_not_called()

    def test_complete_cdc_schema(self, configure):
        checker = configure(fake_client(dict.fromkeys(CDC_TABLES, CDC_COLUMNS)))
        health = checker.get_health_status()
        assert health.status == "healthy"
        assert set(health.cdc_lag) == set(CDC_TABLES)
        assert all(0 <= lag < 60 for lag in health.cdc_lag.values())

    def test_native_cutover_retains_eval_cdc_on_native_connection(self, configure):
        legacy = fake_client({})
        native = fake_client({**NATIVE_COLUMNS, "tracer_eval_logger": CDC_COLUMNS})
        checker = configure(legacy, native, retired=True)
        health = checker.get_health_status()
        assert health.status == "healthy"
        assert set(health.cdc_lag) == {"tracer_eval_logger"}
        legacy.ping.assert_not_called()
        legacy.execute.assert_not_called()
        assert not any(
            "FROM tracer_trace" in str(call) or "FROM trace_session " in str(call)
            for call in native.execute.call_args_list
        )

    def test_native_eval_has_no_cdc_probe(self, configure, settings):
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
        native = fake_client(
            {**NATIVE_COLUMNS, "tracer_eval_logger_v2": NATIVE_EVAL_COLUMNS}
        )
        checker = configure(fake_client({}), native, retired=True)
        health = checker.get_health_status()
        assert health.status == "healthy"
        assert health.cdc_lag == {}
        assert not any("_peerdb" in str(call) for call in native.execute.call_args_list)

    @pytest.mark.parametrize(
        "columns,reason",
        [({}, "missing_required_table"), ({"project_id"}, "missing_required_columns")],
    )
    def test_required_cdc_schema_missing_is_not_healthy(
        self, configure, columns, reason
    ):
        inventory = dict.fromkeys(CDC_TABLES, CDC_COLUMNS)
        inventory["trace_session"] = columns
        client = fake_client(inventory)
        checker = configure(client)
        health = checker.get_health_status()
        assert health.status == "unhealthy"
        assert health.cdc_lag["trace_session"] == -1
        assert health.details["checks"]["legacy.trace_session"] == reason
        assert not any(
            "FROM trace_session" in str(call) for call in client.execute.call_args_list
        )

    def test_empty_metadata_does_not_disable_requirements(self, configure):
        client = fake_client({})
        health = configure(client).get_health_status()
        assert health.status == "unhealthy"
        assert health.cdc_lag == dict.fromkeys(CDC_TABLES, -1)
        assert not any(
            "max(_peerdb" in str(call) for call in client.execute.call_args_list
        )

    def test_metadata_failure_is_not_healthy(self, configure):
        client = fake_client({})
        client.execute.side_effect = RuntimeError("private database details")
        configure(client)
        result = response()
        assert result.status_code == 200  # Existing monitoring contract.
        assert result.data["status"] == "unhealthy"
        assert "private" not in str(result.data)
        assert set(result.data) == {
            "status",
            "clickhouse_connected",
            "cdc_lag",
            "routing",
        }

    def test_disconnected_keeps_http_contract_but_reports_unhealthy(self, configure):
        client = fake_client({})
        client.ping.return_value = False
        configure(client)
        result = response()
        assert result.status_code == 200
        assert result.data["status"] == "unhealthy"
        assert result.data["clickhouse_connected"] is False
        client.execute.assert_not_called()

    def test_required_native_schema_missing(self, configure):
        native = fake_client({"tracer_eval_logger": CDC_COLUMNS})
        health = configure(fake_client({}), native, retired=True).get_health_status()
        assert health.status == "unhealthy"
        assert health.details["checks"]["native.traces"] == "missing_required_table"
        assert not any(
            "FROM traces" in str(call) for call in native.execute.call_args_list
        )

    def test_split_destinations_are_both_checked_in_mixed_routing(
        self, configure, settings
    ):
        settings.CLICKHOUSE_V2 = {"QUERY_TYPES_V2_ONLY": "TRACE_LIST"}
        legacy = fake_client(dict.fromkeys(CDC_TABLES, CDC_COLUMNS))
        native = fake_client({**NATIVE_COLUMNS, "tracer_eval_logger": CDC_COLUMNS})
        checker = configure(legacy, native)
        assert checker.get_health_status().status == "healthy"
        native.ping.return_value = False
        assert checker.get_health_status().status == "unhealthy"

    @pytest.mark.parametrize(
        "sample,expected",
        [
            ((0, datetime(1970, 1, 1)), "healthy"),
            ((1, None), "unhealthy"),
            ((1, datetime.now(UTC) - timedelta(minutes=5)), "degraded"),
            ((1, datetime.utcnow()), "healthy"),
        ],
    )
    def test_empty_invalid_stale_and_naive_samples(self, configure, sample, expected):
        checker = configure(
            fake_client(dict.fromkeys(CDC_TABLES, CDC_COLUMNS), sample=sample)
        )
        health = checker.get_health_status()
        assert health.status == expected
        if sample[0] == 0:
            assert set(health.details["checks"].values()) == {"empty"}
            assert set(health.cdc_lag.values()) == {-1}

    def test_row_counts_omit_retired_tables(self, configure, monkeypatch):
        native = fake_client({**NATIVE_COLUMNS, "tracer_eval_logger": CDC_COLUMNS})
        checker = configure(fake_client({}), native, retired=True)
        pg = Mock()
        pg.cursor.return_value.__enter__ = Mock(
            return_value=Mock(fetchone=Mock(return_value=(0,)))
        )
        pg.cursor.return_value.__exit__ = Mock(return_value=False)
        monkeypatch.setattr(consistency, "pg_connection", pg)
        results = checker.check_row_counts(
            "synthetic-project", datetime(2026, 1, 1), datetime(2026, 1, 2)
        )
        assert [result.table for result in results] == ["tracer_eval_logger"]
        assert results[0].is_consistent

    def test_row_counts_do_not_query_missing_schema(self, configure, monkeypatch):
        client = fake_client({})
        checker = configure(client)
        pg = Mock()
        monkeypatch.setattr(consistency, "pg_connection", pg)
        results = checker.check_row_counts(
            "synthetic-project", datetime(2026, 1, 1), datetime(2026, 1, 2)
        )
        assert len(results) == 3
        assert not any(result.is_consistent for result in results)
        pg.cursor.assert_not_called()
        assert not any("FINAL" in str(call) for call in client.execute.call_args_list)

    def test_empty_destination_does_not_hide_lag_in_other_destination(
        self, configure, settings
    ):
        settings.CLICKHOUSE_V2 = {"QUERY_TYPES_V2_ONLY": "DASHBOARD"}
        legacy = fake_client(dict.fromkeys(CDC_TABLES, CDC_COLUMNS), sample=(0, None))
        native = fake_client(
            {**NATIVE_COLUMNS, "tracer_eval_logger": CDC_COLUMNS},
            sample=(1, datetime.now(UTC) - timedelta(minutes=5)),
        )
        checker = configure(legacy, native)
        assert checker.get_health_status().status == "degraded"

    def test_failed_aggregate_is_not_healthy(self, configure):
        client = fake_client(dict.fromkeys(CDC_TABLES, CDC_COLUMNS))
        execute = client.execute.side_effect

        def unavailable(sql, params=None):
            if "max(_peerdb" in sql:
                raise RuntimeError("synthetic query failure")
            return execute(sql, params)

        client.execute.side_effect = unavailable
        assert configure(client).get_health_status().status == "unhealthy"


@pytest.fixture
def real_clients(configure):
    from conftest import _ch_test_native_client, _ch_test_owned_database
    from tracer.services.clickhouse.client import ClickHouseClient

    with (
        _ch_test_owned_database("test_health_legacy_") as legacy_db,
        _ch_test_owned_database("test_health_native_") as native_db,
    ):
        with (
            _ch_test_native_client(database=legacy_db) as legacy,
            _ch_test_native_client(database=native_db) as native,
        ):
            clients = [
                ClickHouseClient(
                    host="localhost",
                    port=legacy.connection.port,
                    database=db,
                    user="default",
                    password="",
                )
                for db in (legacy_db, native_db)
            ]
            try:
                yield legacy, native, clients
            finally:
                for client in clients:
                    client.close()


def create_cdc(client, table, *, synced=True):
    sync_column = ", _peerdb_synced_at DateTime64(6, 'UTC')" if synced else ""
    client.execute(
        f"CREATE TABLE {table} (id UUID, project_id UUID, created_at DateTime, "
        f"_peerdb_is_deleted UInt8, _peerdb_version Int64{sync_column}) "
        "ENGINE = ReplacingMergeTree(_peerdb_version) ORDER BY id"
    )


def create_native(client):
    # Use the packaged native CREATEs, not a renamed CDC table.
    from pathlib import Path

    from tracer.services.clickhouse.v2 import apply_schema

    folder = Path(apply_schema.__file__).parent / "schema"
    for filename, table in (
        ("002_spans_v2.sql", "spans"),
        ("011_eval_logger_v2.sql", "tracer_eval_logger_v2"),
        ("015_traces_and_trace_dict.sql", "traces"),
        ("018_trace_sessions.sql", "trace_sessions"),
    ):
        sql = (folder / filename).read_text()
        ddl = next(
            statement
            for statement in apply_schema.split_statements(sql)
            if statement.startswith(f"CREATE TABLE IF NOT EXISTS {table}\n")
        )
        client.execute(ddl)


@pytest.mark.integration
class TestRealClickHouseHealth:
    @pytest.mark.parametrize(
        "column,column_type",
        [
            ("trace_id", "Nullable(UUID)"),
            ("observation_span_id", "Nullable(String)"),
            ("trace_session_id", "Nullable(UUID)"),
        ],
    )
    def test_native_eval_packaged_schema_and_missing_column_recovery(
        self, configure, real_clients, settings, caplog, column, column_type
    ):
        _, native, clients = real_clients
        create_native(native)
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
        checker = configure(*clients, retired=True)
        columns = {
            row[0] for row in native.execute("DESCRIBE TABLE tracer_eval_logger_v2")
        }
        assert "project_id" not in columns
        assert NATIVE_EVAL_COLUMNS <= columns
        health = checker.get_health_status()
        assert health.status == "healthy"
        assert health.details["checks"]["native.tracer_eval_logger_v2"] == "ready"
        assert health.cdc_lag == {}
        assert response().data["status"] == "healthy"

        # Remove a real target column (and its dependent skip index), not a
        # fabricated project_id. No data probe should run on this broken schema.
        native.execute(f"ALTER TABLE tracer_eval_logger_v2 DROP INDEX idx_{column}")
        native.execute(f"ALTER TABLE tracer_eval_logger_v2 DROP COLUMN {column}")
        health = checker.get_health_status()
        assert health.status == "unhealthy"
        assert (
            health.details["checks"]["native.tracer_eval_logger_v2"]
            == "missing_required_columns"
        )
        assert response().data["status"] == "unhealthy"

        native.execute(
            f"ALTER TABLE tracer_eval_logger_v2 ADD COLUMN {column} {column_type}"
        )
        assert checker.get_health_status().status == "healthy"
        assert response().data["status"] == "healthy"
        assert "UNKNOWN_TABLE" not in caplog.text
        assert "UNKNOWN_IDENTIFIER" not in caplog.text

    def test_retired_trace_tables_are_not_queried_and_missing_eval_is_unhealthy(
        self, configure, real_clients, caplog
    ):
        legacy, native, clients = real_clients
        create_native(native)
        create_cdc(native, "tracer_eval_logger")
        checker = configure(*clients, retired=True)
        health = checker.get_health_status()
        assert health.status == "healthy"
        assert health.cdc_lag == {"tracer_eval_logger": -1}
        assert "UNKNOWN_TABLE" not in caplog.text
        native.execute("DROP TABLE tracer_eval_logger")
        health = checker.get_health_status()
        assert health.status == "unhealthy"
        assert (
            health.details["checks"]["native.tracer_eval_logger"]
            == "missing_required_table"
        )
        assert "UNKNOWN_TABLE" not in caplog.text
        create_cdc(native, "tracer_eval_logger")
        assert checker.get_health_status().status == "healthy"

    def test_cdc_missing_column_and_recovery(self, configure, real_clients):
        legacy, _, clients = real_clients
        for table in CDC_TABLES:
            create_cdc(legacy, table, synced=table != "trace_session")
        checker = configure(*clients)
        health = checker.get_health_status()
        assert health.status == "unhealthy"
        assert (
            health.details["checks"]["legacy.trace_session"]
            == "missing_required_columns"
        )
        legacy.execute(
            "ALTER TABLE trace_session ADD COLUMN _peerdb_synced_at DateTime64(6, 'UTC')"
        )
        assert checker.get_health_status().status == "healthy"
        legacy.execute(
            "INSERT INTO trace_session (id, project_id, _peerdb_synced_at) VALUES",
            [
                (
                    "00000000-0000-0000-0000-000000000001",
                    "00000000-0000-0000-0000-000000000002",
                    datetime.now(UTC),
                )
            ],
        )
        health = checker.get_health_status()
        assert health.status == "healthy"
        assert 0 <= health.cdc_lag["trace_session"] < 60
