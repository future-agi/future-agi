"""Live ClickHouse 25.3 proof of the OSS ``native:040_trajectory_store`` upgrade.

Each test owns a private database. A "pre-040" layout is what the previous OSS
release's native bootstrap leaves: every packaged native object except
trajectory_items, and spans without the six 040 pointer columns. Witness rows
are written into it, background merges are stopped, and the upgrade then runs
through ``bootstrap_native`` exactly as ``oss_cdc_install --phase native
--apply`` drives it (database-bound clickhouse-connect client, SELECT-only
reads, every write recorded).

What this proves on a real server, which the metadata doubles cannot:
- the planned statements are exactly the packaged 040 CREATE then the ALTER;
- the ALTER is metadata-only: active parts are byte-for-byte the same parts and
  no mutation is queued;
- ClickHouse reports the six columns, codec included, exactly as packaged, and
  retained rows read the constant defaults (``payload_encoding = 'inline'``);
- the previous release's strict spans inspector accepts the upgraded layout;
- a second run, a fresh install and a resumed partial ALTER converge with no
  further statements, and a wrong 040 column type is refused before any write.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from conftest import _ch_test_owned_database, _open_ch_test_http_client
from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_native_bootstrap as native

pytestmark = pytest.mark.integration

PROJECT_ID = "22222222-2222-2222-2222-222222222222"
TRACE_ID = "33333333-3333-3333-3333-333333333333"
UPGRADE = "native:040_trajectory_store"
# Independent copy of 040_trajectory_items.sql's spans columns, as
# system.columns reports them (type, default_kind, default_expression, codec).
EXPECTED_COLUMNS = {
    "input_item_hashes": ("Array(String)", "DEFAULT", "[]", "CODEC(ZSTD(1))"),
    "output_item_hashes": ("Array(String)", "DEFAULT", "[]", "CODEC(ZSTD(1))"),
    "tool_def_hashes": ("Array(String)", "DEFAULT", "[]", "CODEC(ZSTD(1))"),
    "item_scope_id": ("String", "DEFAULT", "''", "CODEC(ZSTD(1))"),
    "payload_encoding": (
        "LowCardinality(String)",
        "DEFAULT",
        "'inline'",
        "CODEC(ZSTD(1))",
    ),
    "payload_envelope": ("String", "DEFAULT", "''", "CODEC(ZSTD(1))"),
}


class Recorder:
    """The installer's client shape: SELECT-only reads, recorded writes."""

    def __init__(self, raw):
        self.raw = raw
        self.commands: list[str] = []

    def query(self, statement, parameters=None, settings=None):
        assert statement.lstrip().startswith("SELECT ")
        return self.raw.query(statement, parameters=parameters, settings=settings)

    def command(self, statement):
        self.commands.append(statement)
        return self.raw.command(statement)


@contextmanager
def _database(prefix):
    with _ch_test_owned_database(prefix) as database:
        raw = _open_ch_test_http_client(database=database)
        try:
            assert raw.query("SELECT version()").result_rows[0][0].startswith("25.3.")
            yield database, raw
        finally:
            raw.close()


def _pre040_definitions(database):
    """The previous release's native objects, in its CREATE order."""
    definitions = dict(native.native_definitions(database))
    del definitions["trajectory_items"]
    head, entries, tail = native._table_parts(definitions["spans"])
    kept = [e for e in entries if core._tokens(e)[0] not in EXPECTED_COLUMNS]
    assert len(kept) == len(entries) - len(EXPECTED_COLUMNS)
    definitions["spans"] = head + "(\n" + ",\n".join(kept) + "\n)" + tail
    return definitions


def _install_pre040(database, raw, *, rows=True):
    for sql in _pre040_definitions(database).values():
        raw.command(sql)
    if rows:
        raw.command(
            f"INSERT INTO {database}.traces (id, project_id, name, created_at) "
            f"VALUES ('{TRACE_ID}', '{PROJECT_ID}', 'witness', now64(6))"
        )
        # Two days, so two partitions and at least two active parts.
        raw.command(
            f"INSERT INTO {database}.spans "
            "(project_id, observation_type, start_time, trace_id, id, name, input, output) "
            f"VALUES ('{PROJECT_ID}', 'llm', '2026-11-01 00:00:00', '{TRACE_ID}', "
            "'span-1', 'n', 'input one', 'output one'), "
            f"('{PROJECT_ID}', 'llm', '2026-11-02 00:00:00', '{TRACE_ID}', "
            "'span-2', 'n', 'input two', 'output two')"
        )
    raw.command(f"SYSTEM STOP MERGES {database}.spans")


def _columns(raw, database):
    rows = raw.query(
        "SELECT name, type, default_kind, default_expression, compression_codec "
        "FROM system.columns WHERE database = %(db)s AND table = 'spans'",
        parameters={"db": database},
    ).result_rows
    return {name: tuple(shape) for name, *shape in rows}


def _parts(raw, database):
    return raw.query(
        "SELECT name, rows, bytes_on_disk, modification_time FROM system.parts "
        "WHERE database = %(db)s AND table = 'spans' AND active ORDER BY name",
        parameters={"db": database},
    ).result_rows


def _mutations(raw, database):
    return raw.query(
        "SELECT count() FROM system.mutations WHERE database = %(db)s",
        parameters={"db": database},
    ).result_rows[0][0]


def _tables(raw, database):
    return {
        row[0]
        for row in raw.query(
            "SELECT name FROM system.tables WHERE database = %(db)s",
            parameters={"db": database},
        ).result_rows
    }


def _previous_release_accepts(raw, database):
    """Run the unchanged strict spans inspector with the pre-040 contract."""
    (row,) = raw.query(
        native._TABLES_SQL,
        parameters={"database": database, "names": ("spans",)},
    ).result_rows
    columns = {
        name: (data_type, kind, expression)
        for name, (data_type, kind, expression, _) in _columns(raw, database).items()
    }
    core._check_table(
        "spans",
        tuple(row[1:]),
        columns,
        {},
        _pre040_definitions(database)["spans"],
        database=database,
        native=True,
    )


def test_pre040_layout_with_rows_upgrades_metadata_only():
    with _database("test_traj_oss_upgrade_") as (database, raw):
        _install_pre040(database, raw)
        assert not set(EXPECTED_COLUMNS) & set(_columns(raw, database))
        _previous_release_accepts(raw, database)
        parts, mutations = _parts(raw, database), _mutations(raw, database)
        assert len(parts) >= 2

        client = Recorder(raw)
        assert native.inspect_native(client, database=database) == (UPGRADE,)
        assert client.commands == []
        assert native.bootstrap_native(client, database=database) == (UPGRADE,)
        upgrade = native.native_upgrade_statements(database)
        assert client.commands == [upgrade["trajectory_items"], upgrade["spans"]]

        columns = _columns(raw, database)
        assert {c: columns[c] for c in EXPECTED_COLUMNS} == EXPECTED_COLUMNS
        assert "trajectory_items" in _tables(raw, database)
        # Metadata-only: the same active parts, no queued or finished mutation.
        assert _parts(raw, database) == parts
        assert _mutations(raw, database) == mutations
        witnesses = raw.query(
            f"SELECT id, input, output, payload_encoding, input_item_hashes, "
            "output_item_hashes, tool_def_hashes, item_scope_id, payload_envelope "
            f"FROM {database}.spans FINAL ORDER BY id"
        ).result_rows
        assert [tuple(r) for r in witnesses] == [
            ("span-1", "input one", "output one", "inline", [], [], [], "", ""),
            ("span-2", "input two", "output two", "inline", [], [], [], "", ""),
        ]
        assert raw.query(
            f"SELECT count() FROM {database}.trajectory_items"
        ).result_rows == [(0,)]
        # A downgrade to the previous release still passes its inspector.
        _previous_release_accepts(raw, database)

        second = Recorder(raw)
        assert native.inspect_native(second, database=database) == ()
        assert native.bootstrap_native(second, database=database) == ()
        assert second.commands == []


def test_fresh_install_includes_040():
    with _database("test_traj_oss_fresh_") as (database, raw):
        client = Recorder(raw)
        created = native.bootstrap_native(client, database=database)
        assert "trajectory_items" in created and UPGRADE not in created
        assert not any(sql.startswith("ALTER") for sql in client.commands)
        columns = _columns(raw, database)
        assert {c: columns[c] for c in EXPECTED_COLUMNS} == EXPECTED_COLUMNS
        again = Recorder(raw)
        assert native.bootstrap_native(again, database=database) == ()
        assert again.commands == []


def test_partial_exact_subset_resumes():
    with _database("test_traj_oss_partial_") as (database, raw):
        _install_pre040(database, raw)
        upgrade = native.native_upgrade_statements(database)
        # An interrupted upgrade: the table and the first two columns exist.
        raw.command(upgrade["trajectory_items"])
        raw.command(",\n".join(upgrade["spans"].split(",\n")[:2]) + ";")
        parts, mutations = _parts(raw, database), _mutations(raw, database)

        client = Recorder(raw)
        assert native.inspect_native(client, database=database) == (UPGRADE,)
        assert native.bootstrap_native(client, database=database) == (UPGRADE,)
        assert client.commands == [upgrade["spans"]]
        columns = _columns(raw, database)
        assert {c: columns[c] for c in EXPECTED_COLUMNS} == EXPECTED_COLUMNS
        assert _parts(raw, database) == parts
        assert _mutations(raw, database) == mutations
        assert native.inspect_native(Recorder(raw), database=database) == ()


def test_wrong_040_type_refused_before_any_write():
    with _database("test_traj_oss_wrongtype_") as (database, raw):
        _install_pre040(database, raw)
        raw.command(
            f"ALTER TABLE {database}.spans "
            "ADD COLUMN payload_encoding String DEFAULT 'inline'"
        )
        before = _tables(raw, database)
        client = Recorder(raw)
        with pytest.raises(native.NativeBootstrapError, match="spans: incompatible"):
            native.bootstrap_native(client, database=database)
        assert client.commands == []
        assert _tables(raw, database) == before
