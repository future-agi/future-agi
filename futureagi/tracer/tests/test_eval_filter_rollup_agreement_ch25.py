"""Live ClickHouse proof that eval filters return what the roll-up tile shows.

TH-8106. The Observe trace list shows a Pass/Fail eval as a roll-up tile
("2 pass / 1 fail") and its filter offers ``Passed`` / ``Failed``: a trace
matches when at least one of its completed results has that verdict. This
module runs the real count query behind the tile and the real filter compiler
behind the panel against the same fixture rows and requires them to agree:

* ``Passed`` returns exactly the traces whose tile has ``pass >= 1``;
* ``Failed`` exactly those with ``fail >= 1``;
* ``is set`` exactly those whose tile has any completed result;
* a Choices label exactly the traces whose tile counts that label.

The fixtures include the rows that used to split the two: a rerun requeued to
pending (or claimed as running) that still stores its previous verdict, a
skipped or errored work item with a verdict, a legacy ``ERROR`` output with
``error = 0``, version flips and both tombstones.

Both eval-logger tables are covered: the legacy CDC mirror (with lifecycle
``status``) and the direct-write v2 table (no ``status`` column).

The suite issues DDL and DML as an admin user, so it opts in explicitly
before it opens a socket (``FI_LIVE_CH_TESTS=1`` plus ``CH25_NATIVE_PORT``)
and works only inside a unique scratch database it drops afterwards.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from clickhouse_driver import Client

from conftest import _require_safe_ch25_test_target
from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    EvalFilterMetadata,
)
from tracer.services.clickhouse.schema import CDC_EVAL_LOGGER
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

pytestmark = pytest.mark.integration

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_USER = os.environ.get("CH25_USER", "default")
CH_PASSWORD = os.environ.get("CH25_PASSWORD", "")

LIVE_CH_TESTS_ENV_VAR = "FI_LIVE_CH_TESTS"
REFUSED_NATIVE_PORTS = frozenset({19000, 19001, 19002, 19010, *range(18230, 18233)})

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PASS_FAIL_CONFIG = "a0000000-0000-4000-8000-00000000000a"
CHOICES_CONFIG = "c0000000-0000-4000-8000-00000000000c"
OUTPUT_TYPES = {PASS_FAIL_CONFIG: "PASS_FAIL", CHOICES_CONFIG: "CHOICES"}
DECLARED_CHOICES = {CHOICES_CONFIG: ["clear", "vague"]}

V2_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "services/clickhouse/v2/schema/011_eval_logger_v2.sql"
)

BASE_TIME = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _trace(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"th-8106/{name}"))


def _live_native_port() -> int:
    if os.environ.get(LIVE_CH_TESTS_ENV_VAR) != "1":
        pytest.skip(f"live ClickHouse tests are opt-in: set {LIVE_CH_TESTS_ENV_VAR}=1")
    raw_port = os.environ.get("CH25_NATIVE_PORT", "").strip()
    if not raw_port:
        pytest.skip(
            "CH25_NATIVE_PORT is not set; this suite will not guess a "
            "ClickHouse port for a test that writes"
        )
    try:
        port = int(raw_port)
    except ValueError:
        pytest.skip(f"CH25_NATIVE_PORT={raw_port!r} is not a port number")
    if port in REFUSED_NATIVE_PORTS:
        pytest.skip(
            f"refusing to write to ClickHouse on port {port}: that port is "
            "reserved for port-forwards to shared clusters on this host"
        )
    return port


def _client(port: int, database: str) -> Client:
    return Client(
        host=CH_HOST,
        port=port,
        user=CH_USER,
        password=CH_PASSWORD,
        database=database,
        connect_timeout=3,
        settings={"optimize_on_insert": 0},
    )


@pytest.fixture(scope="module")
def ch_port() -> int:
    return _live_native_port()


@pytest.fixture(scope="module")
def ch_database(ch_port: int):
    database = f"_test_eval_rollup_{uuid.uuid4().hex}"
    _require_safe_ch25_test_target(host=CH_HOST, database=database)
    admin = _client(ch_port, "default")
    created = False
    try:
        try:
            admin.execute("SELECT 1")
        except Exception as exc:  # pragma: no cover - environment guard
            pytest.skip(f"CH 25.3 not reachable on {CH_HOST}:{ch_port} ({exc!r})")
        admin.execute(f"CREATE DATABASE {database}")
        created = True
        yield database
    finally:
        if created:
            admin.execute(f"DROP DATABASE IF EXISTS {database} SYNC")


@pytest.fixture(scope="module")
def ch(ch_port: int, ch_database: str) -> Client:
    return _client(ch_port, ch_database)


# ---------------------------------------------------------------------------
# Fixture rows. Each trace names the case it pins.
# ---------------------------------------------------------------------------


def _legacy_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    clock = iter(range(1, 10_000))

    def add(
        trace: str,
        *,
        config: str = PASS_FAIL_CONFIG,
        row_id: str | None = None,
        span: str = "span-1",
        output_bool: int | None = None,
        choices: list[str] | None = None,
        output_str: str | None = None,
        error: int = 0,
        status: str = "completed",
        deleted: int = 0,
        cdc_deleted: int = 0,
        version: int | None = None,
    ) -> str:
        row_id = row_id or str(uuid.uuid4())
        tick = next(clock)
        created = BASE_TIME + timedelta(seconds=tick)
        rows.append(
            {
                "id": row_id,
                "trace_id": _trace(trace),
                "observation_span_id": f"{trace}-{span}",
                "trace_session_id": None,
                "target_type": "span",
                "custom_eval_config_id": config,
                "output_bool": output_bool,
                "output_float": None,
                "output_str": output_str,
                "output_str_list": "[]" if choices is None else _json_list(choices),
                "error": error,
                "status": status,
                "deleted": deleted,
                "created_at": created,
                "updated_at": created,
                "_peerdb_synced_at": created,
                "_peerdb_is_deleted": cdc_deleted,
                "_peerdb_version": version if version is not None else tick,
            }
        )
        return row_id

    # Plain completed results.
    add("mixed", span="span-1", output_bool=1)
    add("mixed", span="span-2", output_bool=0)
    add("pass-only", output_bool=1)
    add("fail-only", output_bool=0)
    # Two completed attempts on one span: the tile counts both.
    add("two-attempts", output_bool=0)
    add("two-attempts", output_bool=1)
    # A rule rerun requeues the entry: status goes back to pending while the
    # previous run's verdict is still stored on the row.
    requeued = add("requeued-to-pending", output_bool=0)
    add("requeued-to-pending", row_id=requeued, output_bool=0, status="pending")
    # A worker claimed it: running, verdict still the previous run's.
    claimed = add("claimed-running", output_bool=1)
    add("claimed-running", row_id=claimed, output_bool=1, status="running")
    # One span passed; another span's entry is waiting on a rerun.
    add("pass-plus-pending", span="span-1", output_bool=1)
    waiting = add("pass-plus-pending", span="span-2", output_bool=0)
    add(
        "pass-plus-pending",
        span="span-2",
        row_id=waiting,
        output_bool=0,
        status="pending",
    )
    add("skipped-with-verdict", output_bool=0, status="skipped")
    add("status-errored-with-verdict", output_bool=1, status="errored")
    add("error-flag", error=1)
    # Legacy rows marked the failure only in output_str.
    add("legacy-error-output", output_bool=0, output_str="ERROR")
    # Rows written before the lifecycle column existed count as completed.
    add("legacy-empty-status", output_bool=0, status="")
    # Version flip: the newest version of one entry decides.
    flipped = add("version-flip", output_bool=0)
    add("version-flip", row_id=flipped, output_bool=1)
    # Tombstones: app soft delete and CDC delete marker.
    deleted = add("app-tombstone", output_bool=0)
    add("app-tombstone", row_id=deleted, output_bool=0, deleted=1)
    cdc = add("cdc-tombstone", output_bool=1)
    add("cdc-tombstone", row_id=cdc, output_bool=1, cdc_deleted=1)

    # Choices.
    add("choice-clear", config=CHOICES_CONFIG, choices=["clear"])
    add("choice-both", config=CHOICES_CONFIG, choices=["clear", "vague"])
    stale_choice = add("choice-requeued", config=CHOICES_CONFIG, choices=["clear"])
    add(
        "choice-requeued",
        config=CHOICES_CONFIG,
        row_id=stale_choice,
        choices=["clear"],
        status="pending",
    )
    add("choice-skipped", config=CHOICES_CONFIG, choices=["vague"], status="skipped")
    return rows


def _v2_rows(legacy: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same history on the direct-write v2 table.

    v2 rows carry no lifecycle column: the engine writes them with a result.
    Pending/running/skipped/errored work-item versions therefore have no v2
    shape and are dropped, as are CDC delete markers; app tombstones become
    ``is_deleted``. Where such a version superseded a completed one, the older
    completed version is what v2 keeps. So the v2 run proves the filter's
    v2 status literal and that both sides agree on plain completed, errored,
    ``ERROR``-output, version-flip and app-tombstone rows. The stale-verdict
    rerun cases are proven on the legacy table, the only one that stores them.
    """

    rows = []
    for row in legacy:
        if row["status"] in ("pending", "running", "skipped", "errored"):
            continue
        if row["_peerdb_is_deleted"]:
            continue
        rows.append(
            {
                "id": row["id"],
                "trace_id": row["trace_id"],
                "observation_span_id": row["observation_span_id"],
                "trace_session_id": None,
                "target_type": row["target_type"],
                "custom_eval_config_id": row["custom_eval_config_id"],
                "output_bool": row["output_bool"],
                "output_float": row["output_float"],
                "output_str": row["output_str"],
                "output_str_list": row["output_str_list"],
                "error": row["error"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "is_deleted": row["deleted"],
                "_version": row["_peerdb_version"],
            }
        )
    return rows


def _json_list(values: list[str]) -> str:
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"


ALL_TRACES = sorted(
    {
        "mixed",
        "pass-only",
        "fail-only",
        "two-attempts",
        "requeued-to-pending",
        "claimed-running",
        "pass-plus-pending",
        "skipped-with-verdict",
        "status-errored-with-verdict",
        "error-flag",
        "legacy-error-output",
        "legacy-empty-status",
        "version-flip",
        "app-tombstone",
        "cdc-tombstone",
        "choice-clear",
        "choice-both",
        "choice-requeued",
        "choice-skipped",
        "no-evals",
    }
)
NAME_BY_TRACE = {_trace(name): name for name in ALL_TRACES}


def _legacy_ddl() -> str:
    ddl = re.sub(
        r"ENGINE = ReplicatedReplacingMergeTree\([^)]*\)",
        "ENGINE = ReplacingMergeTree(_peerdb_version)",
        CDC_EVAL_LOGGER,
    )
    assert "Replicated" not in ddl
    return ddl


def _v2_ddl() -> str:
    # Line comments in the migration contain semicolons; drop them first.
    text = re.sub(r"--[^\n]*", "", V2_SCHEMA.read_text())
    statements = [
        statement.strip()
        for statement in text.split(";")
        if "CREATE TABLE IF NOT EXISTS tracer_eval_logger_v2" in statement
    ]
    assert len(statements) == 1
    return statements[0]


@pytest.fixture(scope="module")
def eval_tables(ch: Client):
    legacy = _legacy_rows()
    ch.execute(_legacy_ddl())
    ch.execute(
        "INSERT INTO tracer_eval_logger (" + ", ".join(legacy[0]) + ") VALUES",
        legacy,
        types_check=True,
    )
    v2 = _v2_rows(legacy)
    ch.execute(_v2_ddl())
    ch.execute(
        "INSERT INTO tracer_eval_logger_v2 (" + ", ".join(v2[0]) + ") VALUES",
        v2,
        types_check=True,
    )
    ch.execute("CREATE TABLE trace_universe (trace_id String) ENGINE = Memory")
    ch.execute(
        "INSERT INTO trace_universe (trace_id) VALUES",
        [{"trace_id": _trace(name)} for name in ALL_TRACES],
    )
    yield


# ---------------------------------------------------------------------------
# The two sides: tile (count query + pivot) and filter (compiler).
# ---------------------------------------------------------------------------


def _tiles(ch: Client) -> dict[str, dict[str, Any]]:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        eval_config_ids=[PASS_FAIL_CONFIG, CHOICES_CONFIG],
    )
    sql, params = builder.build_eval_query(
        [_trace(name) for name in ALL_TRACES], count_mode=True
    )
    rows, columns = ch.execute(sql, params, with_column_types=True)
    cells = TraceListQueryBuilderV2.pivot_eval_results(
        [list(row) for row in rows],
        [name for name, _type in columns],
        count_mode=True,
        output_types=OUTPUT_TYPES,
        declared_choices=DECLARED_CHOICES,
    )
    return {NAME_BY_TRACE[trace]: value for trace, value in cells.items()}


def _filtered(
    ch: Client, config: str, output_type: str, op: str, value: Any
) -> set[str]:
    filter_config: dict[str, Any] = {
        "col_type": ClickHouseFilterBuilder.EVAL_METRIC,
        "filter_op": op,
    }
    if value is not None:
        filter_config["filter_value"] = value
    builder = ClickHouseFilterBuilderV2(
        project_id=PROJECT_ID,
        query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE,
        score_date_scope=False,
        eval_filter_metadata={config: EvalFilterMetadata((config,), output_type)},
    )
    where, params = builder.translate(
        [{"column_id": config, "filter_config": filter_config}]
    )
    rows = ch.execute(f"SELECT trace_id FROM trace_universe WHERE {where}", params)
    return {NAME_BY_TRACE[row[0]] for row in rows}


def _tile_count(cell: Any, key: str) -> int:
    if not isinstance(cell, dict) or cell.get("error") or cell.get("status"):
        return 0
    return int(cell.get(key) or 0)


@pytest.fixture(params=["tracer_eval_logger", "tracer_eval_logger_v2"])
def eval_table(request, settings, eval_tables):
    settings.CH25_EVAL_LOGGER_TABLE = request.param
    return request.param


# ---------------------------------------------------------------------------
# Agreement.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filter_value", "tile_key"),
    [("Passed", "pass"), ("Failed", "fail")],
)
def test_pass_fail_filter_returns_the_traces_the_tile_counts(
    ch, eval_table, filter_value, tile_key
):
    tiles = _tiles(ch)
    expected = {
        name
        for name, cells in tiles.items()
        if _tile_count(cells.get(PASS_FAIL_CONFIG), tile_key) >= 1
    }
    # The fixture must actually exercise both sides.
    assert expected

    got = _filtered(ch, PASS_FAIL_CONFIG, "PASS_FAIL", "equals", [filter_value])

    assert got == expected


def test_pass_fail_presence_matches_traces_with_a_counted_result(ch, eval_table):
    tiles = _tiles(ch)
    expected = {
        name
        for name, cells in tiles.items()
        if _tile_count(cells.get(PASS_FAIL_CONFIG), "pass")
        + _tile_count(cells.get(PASS_FAIL_CONFIG), "fail")
        >= 1
    }

    got = _filtered(ch, PASS_FAIL_CONFIG, "PASS_FAIL", "is_not_null", None)

    assert got == expected


@pytest.mark.parametrize("label", ["clear", "vague"])
def test_choice_filter_returns_the_traces_the_tile_counts(ch, eval_table, label):
    tiles = _tiles(ch)
    expected = {
        name
        for name, cells in tiles.items()
        if _tile_count(cells.get(CHOICES_CONFIG), label) >= 1
    }
    assert expected

    got = _filtered(ch, CHOICES_CONFIG, "CHOICES", "equals", [label])

    assert got == expected


def test_stale_verdicts_on_unfinished_entries_match_no_verdict(
    ch, settings, eval_tables
):
    """The cases that used to diverge, asserted by name on the legacy table."""

    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    tiles = _tiles(ch)

    assert tiles["requeued-to-pending"][PASS_FAIL_CONFIG] == {"status": "pending"}
    assert tiles["claimed-running"][PASS_FAIL_CONFIG] == {"status": "running"}
    assert tiles["pass-plus-pending"][PASS_FAIL_CONFIG] == {"pass": 1, "fail": 0}
    assert tiles["legacy-error-output"][PASS_FAIL_CONFIG] == {"error": True}
    assert tiles["legacy-empty-status"][PASS_FAIL_CONFIG] == {"pass": 0, "fail": 1}

    failed = _filtered(ch, PASS_FAIL_CONFIG, "PASS_FAIL", "equals", ["Failed"])
    passed = _filtered(ch, PASS_FAIL_CONFIG, "PASS_FAIL", "equals", ["Passed"])
    for name in (
        "requeued-to-pending",
        "pass-plus-pending",
        "skipped-with-verdict",
        "legacy-error-output",
    ):
        assert name not in failed, name
    for name in ("claimed-running", "status-errored-with-verdict"):
        assert name not in passed, name
    assert "legacy-empty-status" in failed
    assert "pass-plus-pending" in passed

    choice = _filtered(ch, CHOICES_CONFIG, "CHOICES", "equals", ["clear"])
    assert "choice-requeued" not in choice
