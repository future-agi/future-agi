"""Live ClickHouse 25.3 proof of the trajectory read state (RD-3).

Each database here is private and probed on the reader's own client, so the
state follows that database's schema:

- before 040 the state is NOT_READY and reads emit exactly the dev SQL (true
  included: it still probes and refuses);
- with 040 and items, the state is READY whatever the flag says, and items_v1
  rows hydrate to the dedup-off bytes of the Go contract fixtures;
- false is honoured only while ``trajectory_items`` is empty;
- a partial 040 (three of six columns) is NOT_READY;
- the kill switch and a failing probe never serve a preview as content.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import override_settings

from conftest import (
    _ch_test_apply_v2_schema,
    _ch_test_native_client,
    _ch_test_owned_database,
    _open_ch_test_http_client,
)
from tracer.services.clickhouse.v2 import (
    apply_schema,
    span_reader,
    trace_detail_reads,
    trajectory_hydration,
)
from tracer.services.clickhouse.v2.trajectory_hydration import (
    CONTENT_INLINE,
    CONTENT_MISSING,
    TRAJECTORY_SPAN_COLUMNS,
    TrajectoryHydrationUnavailable,
    TrajectoryReadState,
    analytics_read_state_probe,
    client_read_state_probe,
    content_unavailable_sides,
    trajectory_read_state,
)
from tracer.tests.trajectory_read_state_support import SchemaProbe

pytestmark = pytest.mark.integration

SCHEMA_DIR = Path(apply_schema.__file__).parent / "schema"
CONTRACT_DIR = Path(__file__).parent / "fixtures" / "trajectory_contract"


def _jsonl(name: str) -> list[dict]:
    with (CONTRACT_DIR / name).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


PAYLOADS = _jsonl("payloads.jsonl")
PROJECT_ID = PAYLOADS[0]["project_id"]
EXTRACTED = [p for p in PAYLOADS if p["input_extracted"] or p["output_extracted"]]
_STORED = {row["id"]: row["input"] for row in _jsonl("spans.jsonl")}
# A case whose stored input preview is shorter than the content.
LONG = next(
    p
    for p in EXTRACTED
    if p["input_extracted"] and len(_STORED[p["span_id"]]) < len(p["input"])
)


def _ci_requires_clickhouse() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


@pytest.fixture(autouse=True)
def _fresh_state_cache():
    trajectory_hydration.reset_read_state_cache()
    yield
    trajectory_hydration.reset_read_state_cache()


class _NativeAnalytics:
    """The analytics executor shape trace detail reads use, on a native
    client, probing its own database."""

    def __init__(self, client, database):
        self._client = client
        self.trajectory_read_probe = analytics_read_state_probe(
            self, endpoint=f"native:{database}"
        )

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        rows, columns = self._client.execute(
            query,
            params,
            settings={**settings, "max_execution_time": max(timeout_ms / 1000, 5.0)},
            with_column_types=True,
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows], complete=True
        )


def _schema_files(*, with_040: bool) -> list[str]:
    return [
        p.name
        for p in sorted(SCHEMA_DIR.glob("*.sql"))
        if not p.name.startswith("_")
        and (with_040 or p.name != "040_trajectory_items.sql")
    ]


@pytest.fixture(scope="module")
def ch():
    """``open(schema, items=...)`` -> (database, http, native) for private
    databases, all dropped at module end."""
    with ExitStack() as stack:

        def open_database(schema: str, *, items: bool = False):
            try:
                database = stack.enter_context(
                    _ch_test_owned_database("test_traj_guard_")
                )
                _ch_test_apply_v2_schema(
                    database, files=_schema_files(with_040=schema == "040")
                )
                http = _open_ch_test_http_client(database=database)
                stack.callback(http.close)
                native = stack.enter_context(_ch_test_native_client(database=database))
            except pytest.skip.Exception as exc:
                if _ci_requires_clickhouse():
                    pytest.fail(
                        f"the read-state tests must run in CI: {exc}", pytrace=False
                    )
                raise
            if schema == "partial":
                http.command(
                    "ALTER TABLE spans "
                    "ADD COLUMN IF NOT EXISTS input_item_hashes Array(String) DEFAULT [], "
                    "ADD COLUMN IF NOT EXISTS output_item_hashes Array(String) DEFAULT [], "
                    "ADD COLUMN IF NOT EXISTS tool_def_hashes Array(String) DEFAULT []"
                )
            if schema == "040":
                tables = (
                    [("trajectory_items", "items.jsonl"), ("spans", "spans.jsonl")]
                    if items
                    else []
                )
                for table, name in tables:
                    http.raw_insert(
                        table,
                        insert_block=(CONTRACT_DIR / name).read_bytes(),
                        fmt="JSONEachRow",
                    )
            assert native.execute("SELECT version()")[0][0].startswith("25.3.")
            return database, http, native

        cache: dict[tuple[str, bool], tuple] = {}

        def get(schema: str, *, items: bool = False):
            key = (schema, items)
            if key not in cache:
                cache[key] = open_database(schema, items=items)
            return cache[key]

        yield get


def _reader(http, database) -> span_reader.CHSpanReader:
    reader = span_reader.CHSpanReader.__new__(span_reader.CHSpanReader)
    reader._client = http
    reader._read_state_probe = client_read_state_probe(http, endpoint=database)
    return reader


def _dev_select(include_heavy: bool) -> str:
    return span_reader._SELECT_SQL if include_heavy else span_reader._LEAN_SELECT_SQL


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_pre040_state_not_ready_and_sql_identical_to_dev(ch):
    database, http, native = ch("pre040")
    reader = _reader(http, database)
    assert trajectory_read_state(reader) is TrajectoryReadState.NOT_READY

    sent: list[str] = []

    class _Recording:
        def query(self, sql, parameters=None, settings=None):
            sent.append(sql)
            return http.query(sql, parameters=parameters, settings=settings)

        def close(self):
            return None

    reader._client = _Recording()
    # Reads run on the database without the columns: an unknown identifier
    # would raise here.
    assert reader.list_by_trace(str(uuid.uuid4()), project_id=PROJECT_ID) == []
    assert reader.get("missing", project_id=PROJECT_ID) is None
    reads = [s for s in sent if "system." not in s]
    assert reads, "the reader issued its span reads"
    for sql in reads:
        for column in (*TRAJECTORY_SPAN_COLUMNS, "trajectory_items"):
            assert column not in sql, sql
    # The SELECT list is dev's, byte for byte.
    assert any(_dev_select(True) in sql for sql in reads)

    analytics = _NativeAnalytics(native, database)
    builder = trace_detail_reads.TraceDetailReadBuilder(
        project_ids=[PROJECT_ID], trace_id="t"
    )
    ident = trace_detail_reads.PhysicalSpanIdentity(
        project_id=PROJECT_ID,
        trace_id="t",
        span_id="s",
        start_time=datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
    )
    state = trajectory_read_state(analytics)
    assert state is TrajectoryReadState.NOT_READY
    sql, params = builder.build_content_query([ident], state=state)
    native.execute(sql, params)  # executes without the 040 columns


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=True)
def test_flag_true_pre040_does_not_reference_missing_columns(ch):
    database, http, _native = ch("pre040")
    reader = _reader(http, database)
    assert trajectory_read_state(reader) is TrajectoryReadState.NOT_READY
    assert reader.list_by_trace(str(uuid.uuid4()), project_id=PROJECT_ID) == []
    with trajectory_hydration.read_state_snapshot(reader):
        assert span_reader._active_select_sql(True) == _dev_select(True)


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_040_applied_flag_unset_hydrates_items_v1_rows_byte_exact(ch):
    database, http, native = ch("040", items=True)
    reader = _reader(http, database)
    assert trajectory_read_state(reader) is TrajectoryReadState.READY
    for case in PAYLOADS:
        spans = reader.list_by_trace(case["trace_id"], project_id=PROJECT_ID)
        assert len(spans) == 1, case["case"]
        span = spans[0]
        assert span.input.encode("utf-8") == case["input"].encode("utf-8")
        assert span.output.encode("utf-8") == case["output"].encode("utf-8")
        assert content_unavailable_sides(span) == []

    analytics = _NativeAnalytics(native, database)
    for case in EXTRACTED:
        detail = trace_detail_reads.read_trace_detail(
            analytics=analytics,
            project_ids=[PROJECT_ID],
            trace_id=case["trace_id"],
            include_annotations=False,
        )
        assert detail.spans[0]["input"] == case["input"], case["case"]
        assert detail.spans[0]["output"] == case["output"], case["case"]


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=False)
def test_flag_false_after_items_exist_still_hydrates_never_preview(ch):
    database, http, _native = ch("040", items=True)
    reader = _reader(http, database)
    assert trajectory_read_state(reader) is TrajectoryReadState.READY
    for case in EXTRACTED:
        (span,) = reader.list_by_trace(case["trace_id"], project_id=PROJECT_ID)
        assert span.input == case["input"], case["case"]
        assert span.output == case["output"], case["case"]


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=False)
def test_flag_false_with_empty_trajectory_items_projects_nothing(ch):
    database, http, _native = ch("040", items=False)
    reader = _reader(http, database)
    assert trajectory_read_state(reader) is TrajectoryReadState.NOT_READY
    sent: list[str] = []

    class _Recording:
        def query(self, sql, parameters=None, settings=None):
            sent.append(sql)
            return http.query(sql, parameters=parameters, settings=settings)

        def close(self):
            return None

    reader._client = _Recording()
    reader.list_by_trace(str(uuid.uuid4()), project_id=PROJECT_ID)
    for sql in (s for s in sent if "system." not in s):
        for column in TRAJECTORY_SPAN_COLUMNS:
            assert column not in sql
    # Unset (auto) on the same database is READY: false is the only reason.
    trajectory_hydration.reset_read_state_cache()
    with override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None):
        assert trajectory_read_state(_reader(http, database)) is (
            TrajectoryReadState.READY
        )


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_partial_040_columns_is_not_ready(ch):
    database, http, _native = ch("partial")
    reader = _reader(http, database)
    assert trajectory_read_state(reader) is TrajectoryReadState.NOT_READY
    # The reader stays on dev's SQL, so it still runs on the partial schema.
    assert reader.list_by_trace(str(uuid.uuid4()), project_id=PROJECT_ID) == []


@override_settings(
    FI_TRAJECTORY_STORE_READ_ENABLED=None, FI_TRAJECTORY_HYDRATION_KILL=True
)
def test_kill_switch_returns_unavailable_never_preview(ch):
    from tracer.services.clickhouse.v2.eval_loader import (
        SpanContentUnavailable,
        _construct_from_chspan,
    )

    database, http, native = ch("040", items=True)
    reader = _reader(http, database)
    assert trajectory_read_state(reader) is TrajectoryReadState.KILLED
    sent: list[str] = []

    class _Recording:
        def query(self, sql, parameters=None, settings=None):
            sent.append(sql)
            return http.query(sql, parameters=parameters, settings=settings)

        def close(self):
            return None

    reader._client = _Recording()
    for case in EXTRACTED:
        span = reader.get(case["span_id"], project_id=PROJECT_ID)
        expected = [
            side
            for side, flag in (
                ("input", case["input_extracted"]),
                ("output", case["output_extracted"]),
            )
            if flag
        ]
        assert content_unavailable_sides(span) == expected, case["case"]
        assert span.content_state == tuple(
            CONTENT_MISSING if s in expected else CONTENT_INLINE
            for s in ("input", "output")
        )
        with pytest.raises(SpanContentUnavailable):
            _construct_from_chspan(span)
    assert not any("FROM trajectory_items" in s for s in sent), "nothing fetched"

    analytics = _NativeAnalytics(native, database)
    detail = trace_detail_reads.read_trace_detail(
        analytics=analytics,
        project_ids=[PROJECT_ID],
        trace_id=EXTRACTED[0]["trace_id"],
        include_annotations=False,
    )
    assert content_unavailable_sides(detail.spans[0])


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_probe_error_raises_unavailable_not_preview(ch):
    database, http, _native = ch("040", items=True)
    reader = _reader(http, database)
    reader._read_state_probe = SchemaProbe(
        error=ConnectionError("system.columns unreachable"),
        endpoint=f"broken:{database}",
    ).probe
    case = EXTRACTED[0]
    with pytest.raises(TrajectoryHydrationUnavailable) as exc:
        reader.list_by_trace(case["trace_id"], project_id=PROJECT_ID)
    assert exc.value.code == "read_state_probe_failed"
    with pytest.raises(TrajectoryHydrationUnavailable):
        reader.get(case["span_id"], project_id=PROJECT_ID)


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_state_flip_mid_request_is_consistent(ch):
    """The probe flips between the content SQL and its hydration; the read
    still uses the one state it resolved first."""
    database, _http, native = ch("040", items=True)
    analytics = _NativeAnalytics(native, database)
    real = analytics.trajectory_read_probe
    calls = {"n": 0}

    def flipping(sql, params, names):
        calls["n"] += 1
        if calls["n"] <= 2:  # the first probe sees no 040 schema
            return []
        return real.run(sql, params, names)

    analytics.trajectory_read_probe = trajectory_hydration.ReadStateProbe(
        f"flip:{database}", flipping
    )
    sent: list[str] = []
    inner = analytics.execute_ch_query

    def recording(query, params, *, timeout_ms, settings):
        sent.append(query)
        # Every later probe would answer READY.
        trajectory_hydration.reset_read_state_cache()
        return inner(query, params, timeout_ms=timeout_ms, settings=settings)

    analytics.execute_ch_query = recording
    case = LONG
    detail = trace_detail_reads.read_trace_detail(
        analytics=analytics,
        project_ids=[PROJECT_ID],
        trace_id=case["trace_id"],
        include_annotations=False,
    )
    # Resolved once (two probe statements), as NOT_READY: no pointer column
    # in the content SQL and no hydration query, though the schema "flipped".
    assert calls["n"] == 2
    assert not any("FROM trajectory_items" in s for s in sent)
    assert not any("payload_envelope" in s for s in sent)
    assert detail.spans[0]["input"] != case["input"]
