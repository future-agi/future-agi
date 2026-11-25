"""Schema-aware health checks: declared requirements, not discovered optionality."""

from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, Mock

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

    # Schema-only doubles represent the diagnostic connection. Real integration
    # clients still exercise the production factory and its isolated transport.
    health_client = getattr(consistency, "HealthCheckClient", None)
    if health_client is not None:
        factory = health_client.from_client
        monkeypatch.setattr(
            health_client,
            "from_client",
            lambda source, **kwargs: (
                source if isinstance(source, Mock) else factory(source, **kwargs)
            ),
        )

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


PART_COLUMNS = {"database", "table", "name", "rows", "active"}
VERIFIED_COLUMNS = {
    **dict.fromkeys(CDC_TABLES, CDC_COLUMNS),
    "system.parts": PART_COLUMNS,
}


def sampled_tables(params):
    """Tables one batched lag statement covers, from its ``tableN`` parameters."""
    return [
        value
        for key, value in (params or {}).items()
        if key.startswith("table") and key[5:].isdigit()
    ]


def fake_client(columns, *, sample=None, parts=None, table_rows=None):
    """Schema/part double. Explicit inventories may omit empty tables.

    Metadata visibility is explicit in columns. A default part per CDC table
    models a stable complete inventory; a supplied sample can simulate a partial
    aggregate or invalid timestamp independently of its physical row count.
    """
    explicit_sample = sample is not None
    if sample is None:
        sample = (1, datetime.now(UTC))
    if parts is None:
        parts = [
            (table, "all_1_1_0", sample[0])
            for table, names in columns.items()
            if "_peerdb_synced_at" in names and sample[0]
        ]
    table_rows = table_rows or {}

    def execute(sql, params=None):
        if sql == "SELECT 1":
            return [(1,)]
        if "system.columns" in sql:
            return [
                (table, column) for table, names in columns.items() for column in names
            ]
        if "FROM system.parts" in sql:
            rows = [row[:3] for row in parts if row[0] in params["tables"]]
            return [
                row
                for table in params["tables"]
                for row in sorted(r for r in rows if r[0] == table)[: params["parts"]]
            ]
        if "FROM system.tables" in sql:
            return [(table, table_rows.get(table, 1)) for table in params["tables"]]
        if "max(_peerdb_synced_at)" in sql:
            # One batched statement answers ``(table, count, max)`` per table;
            # the older per-table statements answer a single unnamed row.
            tables = sampled_tables(params)
            if tables:
                result = []
                for index, table in enumerate(tables):
                    names = params.get(f"parts{index}", ())
                    count = sum(
                        row[2] for row in parts if row[0] == table and row[1] in names
                    )
                    if explicit_sample and names:
                        count = sample[0]
                    result.append((table, count, sample[1]))
                return result
            return [sample if "count()" in sql else (sample[1],)]
        if "LIMIT 0" in sql:
            return []
        if "count()" in sql:
            return [(0,)]
        raise AssertionError(f"Unexpected query: {sql}")

    client = Mock()
    client.ping.return_value = True
    client.execute.side_effect = execute
    client.execute_read.side_effect = lambda sql, params=None, **kwargs: (
        execute(sql, params),
        [],
        0,
    )
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
        checker = configure(fake_client(VERIFIED_COLUMNS))
        health = checker.get_health_status()
        assert health.status == "healthy"
        assert set(health.cdc_lag) == set(CDC_TABLES)
        assert all(0 <= lag < 60 for lag in health.cdc_lag.values())
        assert health.cdc_lag_verified == dict.fromkeys(CDC_TABLES, True)

    def test_native_cutover_retains_eval_cdc_on_native_connection(self, configure):
        legacy = fake_client({})
        native = fake_client(
            {
                **NATIVE_COLUMNS,
                "tracer_eval_logger": CDC_COLUMNS,
                "system.parts": PART_COLUMNS,
            }
        )
        checker = configure(legacy, native, retired=True)
        health = checker.get_health_status()
        assert health.status == "healthy"
        assert set(health.cdc_lag) == {"tracer_eval_logger"}
        legacy.execute_read.assert_called_once_with("SELECT 1", None, timeout_ms=ANY)
        legacy.execute.assert_not_called()
        assert not any(
            "FROM tracer_trace" in str(call) or "FROM trace_session " in str(call)
            for call in native.execute_read.call_args_list
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
        assert not any(
            "_peerdb" in str(call) for call in native.execute_read.call_args_list
        )

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
            "FROM trace_session" in str(call)
            for call in client.execute_read.call_args_list
        )

    def test_empty_metadata_does_not_disable_requirements(self, configure):
        client = fake_client({})
        health = configure(client).get_health_status()
        assert health.status == "unhealthy"
        assert health.cdc_lag == dict.fromkeys(CDC_TABLES, -1)
        assert not any(
            "max(_peerdb" in str(call) for call in client.execute_read.call_args_list
        )

    def test_metadata_failure_is_not_healthy(self, configure):
        client = fake_client({})
        read = client.execute_read.side_effect

        def unavailable(sql, *args, **kwargs):
            if "system.columns" in sql:
                raise RuntimeError("private database details")
            return read(sql, *args, **kwargs)

        client.execute_read.side_effect = unavailable
        configure(client)
        result = response()
        assert result.status_code == 200  # Existing monitoring contract.
        assert result.data["status"] == "unhealthy"
        assert "private" not in str(result.data)
        assert set(result.data) == {
            "status",
            "clickhouse_connected",
            "cdc_lag",
            "cdc_lag_verified",
            "routing",
        }

    def test_disconnected_keeps_http_contract_but_reports_unhealthy(self, configure):
        client = fake_client({})
        client.execute_read.side_effect = ConnectionError("synthetic outage")
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
            "FROM traces" in str(call) for call in native.execute_read.call_args_list
        )

    def test_split_destinations_are_both_checked_in_mixed_routing(
        self, configure, settings
    ):
        settings.CLICKHOUSE_V2 = {"QUERY_TYPES_V2_ONLY": "TRACE_LIST"}
        legacy = fake_client(VERIFIED_COLUMNS)
        native = fake_client(
            {
                **NATIVE_COLUMNS,
                "tracer_eval_logger": CDC_COLUMNS,
                "system.parts": PART_COLUMNS,
            }
        )
        checker = configure(legacy, native)
        assert checker.get_health_status().status == "healthy"
        native.execute_read.side_effect = ConnectionError("synthetic outage")
        assert checker.get_health_status().status == "unhealthy"

    @pytest.mark.parametrize(
        "sample,expected",
        [
            ((0, lambda: datetime(1970, 1, 1)), "healthy"),
            ((1, lambda: None), "unhealthy"),
            ((1, lambda: datetime.now(UTC) - timedelta(minutes=5)), "degraded"),
            ((1, lambda: datetime.now(UTC).replace(tzinfo=None)), "healthy"),
        ],
    )
    def test_empty_invalid_stale_and_naive_samples(self, configure, sample, expected):
        # Timestamps are built when the test runs, not at collection, so a long
        # suite cannot age the "fresh" samples into the stale branch.
        sample = (sample[0], sample[1]())
        checker = configure(fake_client(VERIFIED_COLUMNS, sample=sample))
        health = checker.get_health_status()
        assert health.status == expected
        if sample[0] == 0:
            assert set(health.details["checks"].values()) == {"empty"}
            assert set(health.cdc_lag.values()) == {-1}
            assert health.cdc_lag_verified == dict.fromkeys(CDC_TABLES, False)

    def test_row_counts_omit_retired_tables(self, configure, monkeypatch):
        native = fake_client(
            {
                **NATIVE_COLUMNS,
                "tracer_eval_logger": CDC_COLUMNS,
                "system.parts": PART_COLUMNS,
            }
        )
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
        assert not any(
            "FINAL" in str(call) for call in client.execute_read.call_args_list
        )

    def test_empty_destination_does_not_hide_lag_in_other_destination(
        self, configure, settings
    ):
        settings.CLICKHOUSE_V2 = {"QUERY_TYPES_V2_ONLY": "DASHBOARD"}
        legacy = fake_client(VERIFIED_COLUMNS, sample=(0, None))
        native = fake_client(
            {
                **NATIVE_COLUMNS,
                "tracer_eval_logger": CDC_COLUMNS,
                "system.parts": PART_COLUMNS,
            },
            sample=(1, datetime.now(UTC) - timedelta(minutes=5)),
        )
        checker = configure(legacy, native)
        health = checker.get_health_status()
        assert health.status == "degraded"
        assert health.cdc_lag["tracer_eval_logger"] == -1
        assert health.cdc_lag_verified["tracer_eval_logger"] is False

    def test_failed_aggregate_is_not_healthy(self, configure):
        client = fake_client(VERIFIED_COLUMNS)
        read = client.execute_read.side_effect

        def unavailable(sql, params=None, **kwargs):
            if "max(_peerdb" in sql:
                raise RuntimeError("synthetic query failure")
            return read(sql, params, **kwargs)

        client.execute_read.side_effect = unavailable
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
                    host=driver.connection.host,
                    port=driver.connection.port,
                    database=driver.connection.database,
                    user=driver.connection.user,
                    password=driver.connection.password,
                )
                for driver in (legacy, native)
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


@pytest.mark.unit
@pytest.mark.parametrize("with_verification", [False, True])
def test_health_serializer_accepts_existing_keys_without_new_field(with_verification):
    from tracer.serializers.health import ClickHouseHealthResponseSerializer

    legacy = {
        "status": "healthy",
        "clickhouse_connected": True,
        "cdc_lag": {"tracer_trace": -1.0},
        "routing": {"CH_ROUTE_TRACE_LIST": "pg"},
    }
    payload = dict(legacy)
    if with_verification:
        payload["cdc_lag_verified"] = {"tracer_trace": False}
    serializer = ClickHouseHealthResponseSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert {key: serializer.data[key] for key in legacy} == legacy
    assert serializer.fields["cdc_lag_verified"].required is False
    assert ("cdc_lag_verified" in serializer.data) is with_verification


@pytest.mark.unit
def test_health_openapi_verification_is_optional_and_old_contract_stable():
    import json
    from pathlib import Path

    from tracer.serializers.health import ClickHouseHealthErrorResponseSerializer

    root = Path(__file__).resolve().parents[3]
    contract = json.loads((root / "api_contracts/openapi/swagger.json").read_text())
    definitions = contract["definitions"]
    response_schema = definitions["ClickHouseHealthResponse"]
    assert response_schema["required"] == [
        "status",
        "clickhouse_connected",
        "cdc_lag",
        "routing",
    ]
    old_properties = response_schema["properties"]
    assert old_properties["status"]["enum"] == [
        "healthy",
        "degraded",
        "unhealthy",
        "disabled",
    ]
    assert old_properties["clickhouse_connected"]["type"] == "boolean"
    assert old_properties["cdc_lag"]["additionalProperties"] == {"type": "number"}
    assert old_properties["routing"]["type"] == "object"
    assert "-1 means unverified" in old_properties["cdc_lag"]["description"]
    for name in ("ClickHouseHealthResponse", "ClickHouseHealthErrorResponse"):
        schema = definitions[name]
        assert "cdc_lag_verified" not in schema.get("required", [])
        assert schema["properties"]["cdc_lag_verified"]["additionalProperties"] == {
            "type": "boolean"
        }
    assert (
        ClickHouseHealthErrorResponseSerializer().fields["cdc_lag_verified"].required
        is False
    )
    operation = contract["paths"]["/api/health/clickhouse/"]["get"]
    assert (
        operation["responses"]["200"]["schema"]["$ref"]
        == "#/definitions/ClickHouseHealthResponse"
    )
    assert (
        operation["responses"]["503"]["schema"]["$ref"]
        == "#/definitions/ClickHouseHealthErrorResponse"
    )


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["fresh", "empty", "disabled", "error"])
def test_health_api_verification_matches_lag_in_all_response_modes(
    configure, monkeypatch, mode
):
    from tracer.views import clickhouse_health

    checker = configure(
        fake_client(VERIFIED_COLUMNS, parts=[] if mode == "empty" else None)
    )
    if mode == "disabled":
        monkeypatch.setattr(consistency, "is_clickhouse_enabled", lambda: False)
    if mode == "error":
        monkeypatch.setattr(
            clickhouse_health,
            "ConsistencyChecker",
            Mock(side_effect=RuntimeError("synthetic")),
        )
    result = response()
    assert result.status_code == (503 if mode == "error" else 200)
    assert {"clickhouse_connected", "cdc_lag", "routing"} <= result.data.keys()
    assert "status" in result.data
    assert result.data["cdc_lag_verified"] == {
        table: lag >= 0 for table, lag in result.data["cdc_lag"].items()
    }
    if mode in ("disabled", "error"):
        assert result.data["cdc_lag_verified"] == {}
    elif mode == "empty":
        assert result.data["cdc_lag_verified"] == dict.fromkeys(CDC_TABLES, False)
        assert result.data["cdc_lag"] == dict.fromkeys(CDC_TABLES, -1)
    else:
        assert checker.get_health_status().cdc_lag_verified == dict.fromkeys(
            CDC_TABLES, True
        )
