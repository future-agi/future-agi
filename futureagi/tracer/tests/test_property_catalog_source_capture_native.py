"""Bounded SQL/ownership checks; this fake transport is not live DDL evidence."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog import native_write_transport
from tracer.services.clickhouse.v2.property_catalog.source_capture import (
    DurableSourceCapture,
    SourceCaptureError,
    SourceCaptureUncertain,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_native import (
    REQUIRED_CAPTURE_COLUMNS,
    CaptureResourceBudget,
    NativeSourceCaptureBackend,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_reservations import (
    SourceCaptureBackpressure,
)
from tracer.services.clickhouse.v2.property_catalog.write_admission import WriteMember
from tracer.tests.test_property_catalog_source_capture import specification


class Schema:
    source_sha256 = "a" * 64
    capture_sha256 = "b" * 64

    def __init__(self, spec):
        self.source_database, self.source_table, self.source_uuid = (
            spec.source_database,
            "spans",
            spec.source_table_uuid,
        )
        self.target_database, self.target_table, self.target_uuid = (
            spec.capture_database,
            spec.capture_table,
            spec.capture_table_uuid,
        )
        self.required_stored_columns = tuple(sorted(REQUIRED_CAPTURE_COLUMNS))
        self.create_sql = (
            f"CREATE TABLE IF NOT EXISTS `{spec.capture_database}`.`{spec.capture_table}` "
            f"UUID '{spec.capture_table_uuid}' (id String) ENGINE = MergeTree ORDER BY id"
        )

    def verify_source(self, sql):
        if sql != "qualified-source":
            raise SourceCaptureError("source DDL changed")

    def verify_capture(self, sql):
        if sql != self.create_sql:
            raise SourceCaptureError("capture DDL changed")


class Driver:
    def __init__(self, spec, schema):
        self.database, self.user, self.server_enforced_readonly = (
            spec.source_database,
            "writer",
            False,
        )
        self.spec, self.schema = spec, schema
        self.server_uuid = spec.source_server_uuid
        self.source = (
            spec.source_server_uuid,
            spec.source_table_uuid,
            "ReplacingMergeTree",
            "qualified-source",
        )
        self.target = None
        self.captured = []
        self.source_parts = [
            ("20260901_1_1_0", "a" * 32, 2, 64),
            ("20260902_2_2_0", "b" * 32, 3, 96),
        ]
        self.missing_column = None
        self.other_tables = []
        self.queries, self.commands = [], []
        self.failure = None

    def execute_read(self, sql, params, *, timeout_ms, settings):
        self.queries.append((sql, params, timeout_ms, settings))
        assert sql.startswith(
            "SELECT toString(serverUUID()) AS capture_server_uuid, groupArray(tuple("
        )
        assert settings["readonly"] == 2 and settings["max_result_rows"] == 1
        if "FROM system.parts_columns" in sql:
            columns = sorted(
                set(self.schema.required_stored_columns) - {self.missing_column}
            )
            assert "AND rows>0" in sql
            values = [(p[0], columns) for p in self.captured if p[2]]
        elif "FROM system.parts" in sql:
            # system.parts already returns the SipHash as hex text (String).
            assert "toString(hash_of_all_files)" in sql
            assert "hex(hash_of_all_files)" not in sql
            values = (
                self.source_parts
                if params["database"] == self.database
                else self.captured
            )
        elif "FROM system.tables" in sql and "table" not in params:
            values = [(name,) for name in self.other_tables]
            if self.target:
                values.append((self.spec.capture_table,))
        elif "FROM system.tables" in sql:
            value = self.source if params["database"] == self.database else self.target
            values = [] if value is None else [value]
        else:
            raise AssertionError("unexpected metadata query")
        return (
            [(self.server_uuid, values)],
            [("capture_server_uuid", "String"), ("capture_rows", "Array(Tuple)")],
            0,
        )

    def command(self, **options):
        options["before_send"]()
        sql = options["sql"]
        self.commands.append(sql)
        if sql == self.schema.create_sql:
            self.target = (
                self.spec.source_server_uuid,
                self.spec.capture_table_uuid,
                "MergeTree",
                sql,
            )
        elif sql.startswith("ALTER TABLE"):
            assert sql == (
                f"ALTER TABLE `{self.spec.capture_database}`.`{self.spec.capture_table}` "
                f"ATTACH PARTITION ALL FROM `{self.database}`.`spans`"
            )
            self.captured = list(self.source_parts)
        elif sql.startswith("DROP TABLE"):
            assert (
                sql
                == f"DROP TABLE `{self.spec.capture_database}`.`{self.spec.capture_table}` SYNC"
            )
            self.target, self.captured = None, []
        else:
            raise AssertionError("unexpected mutation")
        if self.failure and sql.startswith(self.failure):
            raise TimeoutError("lost native response")
        return []


@pytest.fixture
def case(tmp_path, monkeypatch):
    spec = specification()
    schema = Schema(spec)
    driver = Driver(spec, schema)
    member = WriteMember(
        "source-db-id", "node", "node", spec.source_server_uuid, (), "http://node:8123"
    )
    reservations = []
    backend = NativeSourceCaptureBackend(
        driver,
        source_reader=SimpleNamespace(
            database=spec.source_database,
            user="source_reader",
            server_enforced_readonly=True,
            execute_read=driver.execute_read,
        ),
        member=member,
        spec=spec,
        schema=schema,
        budget=CaptureResourceBudget(2, 10, 1024),
        capacity_reservation=lambda s, size: reservations.append((s, size)),
    )
    monkeypatch.setattr(
        native_write_transport,
        "ordinary_once",
        lambda d, **opts: d.command(**opts),
        raising=False,
    )
    manager = DurableSourceCapture(str(tmp_path), backend, can_retire=lambda s: True)
    return SimpleNamespace(
        spec=spec,
        schema=schema,
        driver=driver,
        backend=backend,
        manager=manager,
        reservations=reservations,
    )


def test_journal_and_closed_backend_create_attach_verify_restart_and_retire(
    case, tmp_path
):
    c = case
    captured = c.manager.acquire(c.spec)
    assert (captured.parts, captured.rows, captured.bytes_on_disk) == (2, 5, 160)
    assert c.reservations == [(c.spec, 160)]
    assert len(c.driver.commands) == 2
    c.driver.source_parts.append(("20260903_3_3_0", "c" * 32, 1, 48))
    restarted = DurableSourceCapture(
        str(tmp_path), c.backend, can_retire=lambda s: True
    )
    assert restarted.acquire(c.spec) == captured
    assert len(c.driver.commands) == 2
    restarted.retire(c.spec)
    assert c.driver.target is None
    assert len(c.driver.source_parts) == 3  # No source removal or rewrite.
    assert len(c.driver.commands) == 3


@pytest.mark.parametrize("wrong", ["other-server", ""])
def test_even_empty_metadata_must_match_same_server(case, wrong):
    case.driver.server_uuid = wrong
    with pytest.raises(SourceCaptureError, match="another source member"):
        case.backend.inspect(case.spec)
    assert not case.driver.commands


def test_source_table_incarnation_change_never_creates(case):
    case.driver.source = (
        case.spec.source_server_uuid,
        "other-table",
        "ReplacingMergeTree",
        "qualified-source",
    )
    with pytest.raises(SourceCaptureError, match="incarnation"):
        case.manager.acquire(case.spec)
    assert not case.driver.commands


@pytest.mark.parametrize(
    "engine", ["Distributed", "ReplacingMergeTree", "ReplicatedMergeTree"]
)
def test_capture_must_not_replace_or_replicate_its_rows(case, engine):
    case.manager.acquire(case.spec)
    case.driver.target = (*case.driver.target[:2], engine, case.schema.create_sql)
    with pytest.raises(SourceCaptureError):
        case.backend.inspect(case.spec)
    with pytest.raises(SourceCaptureError):
        case.backend.drop_owned(case.spec)
    assert len(case.driver.commands) == 2


def test_uncertain_attach_never_retries_and_invalid_contents_remain_reclaimable(case):
    case.driver.failure = "ALTER TABLE"
    with pytest.raises(TimeoutError):
        case.manager.acquire(case.spec)
    case.driver.missing_column = "_version"
    with pytest.raises(SourceCaptureUncertain):
        case.manager.acquire(case.spec)
    with pytest.raises(SourceCaptureError, match="physically store"):
        case.backend.inspect(case.spec)
    case.manager.retire(case.spec)
    assert case.driver.target is None
    assert sum(s.startswith("ALTER TABLE") for s in case.driver.commands) == 1


@pytest.mark.parametrize("column", sorted(REQUIRED_CAPTURE_COLUMNS))
def test_every_input_column_must_be_physically_present(case, column):
    case.driver.missing_column = column
    with pytest.raises(SourceCaptureError, match="physically store"):
        case.manager.acquire(case.spec)
    assert case.manager._load(case.spec)["phase"] == "attaching"
    case.manager.retire(case.spec)
    assert case.driver.target is None


def test_same_table_name_different_uuid_never_deleted(case):
    case.manager.acquire(case.spec)
    case.driver.target = (
        case.spec.source_server_uuid,
        "foreign-uuid",
        "MergeTree",
        case.schema.create_sql,
    )
    with pytest.raises(SourceCaptureError, match="another capture incarnation"):
        case.manager.retire(case.spec)
    assert len(case.driver.commands) == 2


def test_occupied_slots_do_not_allocate_or_mutate(case):
    case.driver.other_tables = ["spans_" + "a" * 32, "spans_" + "b" * 32]
    with pytest.raises(SourceCaptureBackpressure, match="slots"):
        case.manager.acquire(case.spec)
    assert not case.driver.commands and not case.reservations


@pytest.mark.parametrize("kind", ["parts", "bytes", "unexpected"])
def test_capacity_rejection_precedes_writes(case, kind):
    if kind == "parts":
        case.driver.source_parts = [
            (f"part_{i:03}", "a" * 32, 1, 32) for i in range(11)
        ]
    elif kind == "bytes":
        case.driver.source_parts = [("part_001", "a" * 32, 1, 2048)]
    else:
        case.driver.other_tables = ["unowned"]
    with pytest.raises(
        SourceCaptureBackpressure if kind == "bytes" else SourceCaptureError
    ):
        case.manager.acquire(case.spec)
    assert not case.driver.commands and not case.reservations


@pytest.mark.parametrize(
    "tables", [["unowned", "spans_" + "a" * 32], ["spans_" + "a" * 32] * 2]
)
def test_full_namespace_does_not_hide_unknown_or_duplicate_objects_as_backpressure(
    case, tables
):
    case.driver.other_tables = tables
    with pytest.raises(SourceCaptureError) as error:
        case.manager.acquire(case.spec)
    assert not isinstance(error.value, SourceCaptureBackpressure)
    assert not case.driver.commands and not case.reservations


def test_bound_backend_cannot_be_reused_for_another_build(case):
    other = replace(case.spec, build_token="e15c2d9c-08ef-4bca-bd15-cc1d52147bea")
    with pytest.raises(SourceCaptureError, match="bound spec"):
        case.backend.create_empty_once(other, before_send=lambda: None)
    assert not case.driver.queries and not case.driver.commands


def test_source_mutable_ddl_change_is_not_a_capture_schema_match(case):
    case.driver.source = (*case.driver.source[:3], "new source schema")
    with pytest.raises(SourceCaptureError, match="source DDL"):
        case.manager.acquire(case.spec)
    assert not case.driver.commands


def test_no_metadata_query_uses_unbounded_output_or_cache(case):
    case.manager.acquire(case.spec)
    for sql, _, timeout_ms, settings in case.driver.queries:
        assert timeout_ms == 30_000
        assert "LIMIT" in sql
        assert settings["use_query_cache"] == 0
        assert settings["max_result_bytes"] == 8 << 20


def test_empty_source_part_has_no_nondeterministic_values_to_qualify(case):
    case.driver.source_parts = [("part_001", "a" * 32, 0, 64)]
    observed = case.manager.acquire(case.spec)
    assert observed.rows == 0 and observed.parts == 1
    case.manager.retire(case.spec)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_database", "other"),
        ("source_table", "other"),
        ("source_uuid", "other"),
        ("target_database", "other"),
        ("target_table", "other"),
        ("target_uuid", "other"),
    ],
)
def test_same_schema_hash_cannot_authorize_another_object_header(case, field, value):
    setattr(case.schema, field, value)
    with pytest.raises(SourceCaptureError, match="capability or schema changed"):
        case.manager.acquire(case.spec)
    assert not case.driver.queries and not case.driver.commands


def test_physical_column_check_uses_the_schema_resolved_alias_dependency(case):
    case.schema.required_stored_columns = tuple(
        sorted(REQUIRED_CAPTURE_COLUMNS - {"model"})
    ) + ("stored_model",)
    observed = case.manager.acquire(case.spec)
    assert observed.rows == 5
    case.driver.missing_column = "stored_model"
    with pytest.raises(SourceCaptureError, match="physically store"):
        case.backend.inspect(case.spec)


@pytest.mark.parametrize(
    "values", [(0, 1, 1), (3, 1, 1), (2, 10001, 1), (1, 1, True), (True, 1, 1)]
)
def test_capacity_requires_reviewed_positive_bounds(values):
    with pytest.raises(ValueError):
        CaptureResourceBudget(*values)
