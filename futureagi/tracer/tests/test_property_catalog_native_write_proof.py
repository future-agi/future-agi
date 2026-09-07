"""Native proof unit tests; all network observations are fake and bounded."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import native_write_proof as subject
from tracer.services.clickhouse.v2.property_catalog import write_admission
from tracer.tests.test_property_catalog_write_admission import (
    DATABASE,
    Probe,
    admit,
    installation,
)


def row_for(table):
    values = dict.fromkeys(subject._COLUMNS[table], "ordinary")
    values.update(organization_id=str(UUID(int=1)), workspace_id=str(UUID(int=2)))
    for field, value in (
        ("catalog_epoch", 1),
        ("projection_version", 1),
        ("catalog_revision", 2),
        ("build_token", str(UUID(int=3))),
        ("target_catalog_revision", 2),
        ("target_build_token", str(UUID(int=3))),
        ("action", "activate"),
        ("first_seen", datetime(2026, 9, 1, microsecond=123456, tzinfo=UTC)),
        ("last_seen", datetime(2026, 9, 2, microsecond=654321, tzinfo=UTC)),
        ("deleted_at", None),
    ):
        if field in values:
            values[field] = value
    return values


@pytest.mark.parametrize("table", tuple(subject._COLUMNS))
def test_all_seven_tables_use_exact_columns_and_tenant_prefix(table):
    row = row_for(table)
    row["organization_id"] = UUID(int=1)
    sql, params = subject.coverage_query(
        DATABASE, table, [row], subject._COLUMNS[table]
    )
    assert sql.startswith("SELECT toUInt8(")
    assert f"FROM `{DATABASE}`.`{table}` WHERE" in sql
    assert "`organization_id` = %(scope_organization_id)s" in sql
    assert "`workspace_id` = %(scope_workspace_id)s" in sql
    assert params["scope_organization_id"] == str(UUID(int=1))
    assert "ordinary" not in sql and " FINAL" not in sql
    if table != "property_catalog_activation_control_events":
        assert params["scope_catalog_revision"] == 2
        assert params["scope_build_token"] == str(UUID(int=3))


def test_datetime64_microseconds_nulls_and_escaped_values_are_preserved():
    table = "property_definition_catalog"
    row = row_for(table)
    row["name"] = "quo'te\\雪\n"
    sql, params = subject.coverage_query(
        DATABASE, table, [row], subject._COLUMNS[table]
    )
    assert params["r0_first_seen"] == "2026-09-01 00:00:00.123456"
    assert params["r0_last_seen"] == "2026-09-02 00:00:00.654321"
    assert "isNull(`deleted_at`)" in sql and "r0_deleted_at" not in params
    assert params["r0_name"] == row["name"] and row["name"] not in sql


def test_merged_value_coverage_checks_identity_conflict_and_min_max_not_count_equality():
    table = "span_attribute_value_catalog"
    sql, _ = subject.coverage_query(
        DATABASE, table, [row_for(table)], subject._COLUMNS[table]
    )
    assert " AND NOT (`value_json`" in sql
    assert "minIf(first_seen," in sql and "<= %(r0_first_seen)s" in sql
    assert "maxIf(last_seen," in sql and ">= %(r0_last_seen)s" in sql
    assert "count() = 1" not in sql and "anyLast" not in sql


@pytest.mark.parametrize("bad", [True, 1.5, {}, b"bytes", -(1 << 63) - 1, 1 << 64])
def test_unknown_types_cannot_be_coerced_into_proof(bad):
    with pytest.raises(subject.NativeWriteProofError):
        subject._parameter(bad)


@pytest.mark.parametrize(
    "bad",
    [datetime(2026, 9, 1), datetime(2026, 9, 1, tzinfo=timezone(timedelta(hours=1)))],
)
def test_unqualified_times_cannot_be_assumed_utc(bad):
    with pytest.raises(subject.NativeWriteProofError):
        subject._parameter(bad)


def make_proof(tmp_path, *, replicas=2, result=None, environment="development"):
    probe = Probe(replicas)
    identity = installation(environment)
    admission = admit(tmp_path, probe, identity=identity)
    calls = []

    def driver(name):
        def read(sql, params, *, timeout_ms, settings):
            assert sql.startswith("SELECT")
            assert settings["readonly"] == 2 and 0 < timeout_ms <= 30_000
            calls.append((name, sql, params))
            if result is None:
                n = sql.count(" AS r")
                rows = [{f"r{i}": 1 for i in range(n)}]
            else:
                rows = result(name, sql, params)
            keys = tuple(rows[0]) if rows else ()
            return (
                [tuple(row[k] for k in keys) for row in rows],
                [(k, "") for k in keys],
                {},
            )

        return SimpleNamespace(database=DATABASE, execute_read=read)

    connections = tuple(replace(c, driver=driver(c.name)) for c in probe.connections)
    proof = subject.NativeWriteProof(
        directory=tmp_path,
        identity=identity,
        admission=admission,
        connections=connections,
    )
    return proof, calls


def test_covers_every_chunk_on_every_member(tmp_path):
    proof, calls = make_proof(tmp_path)
    table = "property_definition_catalog"
    proof.cover(
        table, [row_for(table)] * 33, columns=subject._COLUMNS[table], timeout_ms=5000
    )
    assert [name for name, _, _ in calls] == [
        "replica1",
        "replica1",
        "replica2",
        "replica2",
    ]


@pytest.mark.parametrize("explicit_resolver", [False, True])
def test_reattest_retains_distinct_production_member_routes(
    tmp_path, monkeypatch, explicit_resolver
):
    probe = Probe(3)
    identity = installation("production")
    mapped = {
        connection.name: f"https://127.0.0.1:{49000 + index}"
        for index, connection in enumerate(probe.connections)
    }
    http_routes, resolver_calls = [], []

    def resolve(connection, _discovered):
        resolver_calls.append(connection.name)
        return mapped[connection.name]

    def http_read(connection, route, sql, timeout_ms, limit):
        assert route.origin == mapped[connection.name]
        assert sql == write_admission._INVENTORY_SQL and limit == 8
        assert 0 < timeout_ms <= 5000
        http_routes.append((connection.name, route.origin))
        return deepcopy(probe.rows[connection.name])

    probe.http = http_read
    admission = admit(
        tmp_path, probe, identity=identity, route_resolver=resolve, timeout_ms=5000
    )
    before = (tmp_path / write_admission.WRITE_ADMISSION_FILENAME).read_bytes()
    http_routes.clear()
    resolver_calls.clear()

    def producer(*args, **kwargs):
        return write_admission.reattest_catalog_writes(
            *args, **kwargs, http_read=http_read
        )

    monkeypatch.setattr(subject, "reattest_catalog_writes", producer)
    proof = subject.NativeWriteProof(
        directory=tmp_path,
        identity=identity,
        admission=admission,
        connections=probe.connections,
        route_resolver=resolve if explicit_resolver else None,
    )
    proof.attest(timeout_ms=5000)
    assert set(http_routes) == set(mapped.items())
    assert resolver_calls == (list(mapped) if explicit_resolver else [])
    assert (tmp_path / write_admission.WRITE_ADMISSION_FILENAME).read_bytes() == before


def test_agreed_state_read_returns_native_types_from_all_members(tmp_path):
    timestamp = datetime(2026, 9, 6, microsecond=123456, tzinfo=UTC)
    proof, calls = make_proof(
        tmp_path, result=lambda *_: [{"created_at": timestamp, "revision": 3}]
    )
    rows = proof.agreed_read(
        f"SELECT created_at, revision FROM `{DATABASE}`.`property_catalog_activations`",
        {},
        timeout_ms=5000,
    )
    assert rows == ({"created_at": timestamp, "revision": 3},)
    assert [name for name, _, _ in calls] == ["replica1", "replica2"]


def test_agreement_rejects_a_stale_serving_member(tmp_path):
    proof, _ = make_proof(
        tmp_path, result=lambda name, *_: [{"revision": 2 if name == "replica1" else 3}]
    )
    with pytest.raises(subject.NativeWriteProofError, match="disagrees"):
        proof.agreed_read(
            f"SELECT revision FROM `{DATABASE}`.`property_catalog_activations`",
            {},
            timeout_ms=5000,
        )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT * FROM `source`.`spans`",
        f"SELECT * FROM `{DATABASE}`.`unreviewed`",
        "INSERT INTO `source`.`spans` VALUES (1)",
    ],
)
def test_agreement_is_not_a_source_or_write_escape(tmp_path, sql):
    proof, calls = make_proof(tmp_path)
    with pytest.raises((subject.NativeWriteProofError, RuntimeError)):
        proof.agreed_read(sql, {}, timeout_ms=1000)
    assert not calls


@pytest.mark.parametrize("bad", [0, True, "1", None])
def test_missing_one_replica_or_wrong_typed_flag_is_not_complete(tmp_path, bad):
    proof, calls = make_proof(
        tmp_path, result=lambda name, *_: [{"r0": bad if name == "replica2" else 1}]
    )
    table = "property_definition_catalog"
    with pytest.raises(subject.NativeWriteProofError, match="coverage absent"):
        proof.cover(
            table, [row_for(table)], columns=subject._COLUMNS[table], timeout_ms=5000
        )
    assert len(calls) == 2


def test_foreign_workspace_in_later_chunk_fails_before_any_read(tmp_path):
    proof, calls = make_proof(tmp_path)
    table = "property_definition_catalog"
    rows = [row_for(table) for _ in range(33)]
    rows[-1]["workspace_id"] = str(UUID(int=100))
    with pytest.raises(subject.NativeWriteProofError, match="mix"):
        proof.cover(table, rows, columns=subject._COLUMNS[table], timeout_ms=5000)
    assert not calls


def attempt():
    table = "property_definition_catalog"
    columns = subject._COLUMNS[table]
    return {
        "member": "replica1",
        "query_id": str(UUID(int=33)),
        "user": "writer",
        "table": table,
        "columns": columns,
        "sql": (
            f"INSERT INTO {DATABASE}.{table} ({', '.join(columns)}) "
            "SETTINGS async_insert=0, insert_quorum=2, insert_quorum_parallel=1 VALUES"
        ),
        "parameters_sha256": "a" * 64,
        "row_count": 1,
    }


def completed():
    a = attempt()
    return {
        "query_id": a["query_id"],
        "type": "QueryFinish",
        "exception_code": 0,
        "current_database": DATABASE,
        "user": a["user"],
        "query": a["sql"],
        "log_comment": a["parameters_sha256"],
        "written_rows": 1,
        "async_insert": "0",
        "insert_quorum": "2",
        "insert_quorum_parallel": "1",
    }


def test_original_query_positive_completion_is_bound_to_statement_user_rows_and_quorum(
    tmp_path,
):
    proof, calls = make_proof(tmp_path, result=lambda *_: [completed()])
    assert proof.settled(attempt(), timeout_ms=5000) is True
    assert len(calls) == 1 and calls[0][2] == {"query_id": attempt()["query_id"]}


@pytest.mark.parametrize("field", tuple(completed()))
def test_changed_original_statement_evidence_never_settles(tmp_path, field):
    row = completed()
    row[field] = 2 if type(row[field]) is int else "wrong"
    proof, _ = make_proof(tmp_path, result=lambda *_: [row])
    assert proof.settled(attempt(), timeout_ms=5000) is False


@pytest.mark.parametrize("rows", [[], [completed(), completed()]])
def test_missing_or_duplicate_completion_does_not_authorize_retry(tmp_path, rows):
    proof, calls = make_proof(tmp_path, result=lambda *_: rows)
    assert proof.settled(attempt(), timeout_ms=5000) is False
    assert len(calls) == 1


def test_missing_changed_setting_entries_require_exact_completed_pinned_sql(tmp_path):
    row = completed()
    row.update(async_insert="", insert_quorum="", insert_quorum_parallel="")
    proof, _ = make_proof(tmp_path, result=lambda *_: [row])
    assert proof.settled(attempt(), timeout_ms=1000) is True
    with pytest.raises(subject.NativeWriteProofError, match="exact SQL pins"):
        proof.settled(
            {**attempt(), "sql": "INSERT INTO catalog.table VALUES"}, timeout_ms=1000
        )
