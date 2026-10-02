"""``columns`` projection on the two span reads the eval FK resolver uses.

The resolver read the whole 44-column ``CHSpan`` to consume ``id`` + ``trace_id``,
and under FINAL the wide-column buffers cost 4.7–8.25 GiB per attempt — CH code
241 against the eval engine's memory cap, five retries, task dead. ``columns``
narrows the SELECT, which is where that memory goes.

Three things make it safe, each silent when broken: the SELECT really is
narrowed (SQL-shape, no CH), a name the reader does not recognise never reaches
the SQL, and the projected values are the ones the ``CHSpan`` read returns for
the same rows (against a real CH).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from tracer.services.clickhouse.v2.span_reader import _SELECT_SQL, CHSpanReader

# Spans a caller would plausibly project: the two the FK resolver needs, a plain
# scalar, and the two that are SQL expressions behind a ``_str`` alias.
_PROJECTED = ["id", "trace_id", "name", "latency_ms", "project_id", "org_id"]


class _RecordingClient:
    """Captures the SQL of the last query; returns no rows."""

    def __init__(self):
        self.sql = None

    def query(self, sql, parameters=None, settings=None):
        self.sql = sql

        class _Result:
            result_rows = []

        return _Result()


def _reader_with(client) -> CHSpanReader:
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client
    return reader


def _projection_reads():
    """(label, callable) for each read that takes ``columns``."""
    return [
        (
            "list_root_spans_by_trace_ids",
            lambda r, cols: r.list_root_spans_by_trace_ids(["t1"], columns=cols),
        ),
        ("list_by_ids", lambda r, cols: r.list_by_ids(["s1"], columns=cols)),
    ]


def _select_clause(sql: str) -> str:
    return sql.split(" FROM ", 1)[0]


@pytest.mark.parametrize(
    "label,call", _projection_reads(), ids=lambda v: getattr(v, "__name__", v)
)
def test_projection_selects_only_the_named_columns(label, call):
    client = _RecordingClient()
    call(_reader_with(client), ["id", "trace_id"])
    assert _select_clause(client.sql) == "SELECT id, trace_id", label


@pytest.mark.parametrize(
    "label,call", _projection_reads(), ids=lambda v: getattr(v, "__name__", v)
)
def test_default_mode_selects_the_full_read_set(label, call):
    client = _RecordingClient()
    call(_reader_with(client), None)
    assert _select_clause(client.sql) == f"SELECT {_SELECT_SQL}", label


@pytest.mark.parametrize(
    "label,call", _projection_reads(), ids=lambda v: getattr(v, "__name__", v)
)
@pytest.mark.parametrize(
    "bad", [["trace_id", "not_a_column"], []], ids=["unknown", "empty"]
)
def test_unrecognised_column_raises_before_any_sql_runs(label, call, bad):
    """The allowlist is also the injection guard — caller strings are never
    interpolated, so a name that misses it must not produce a query at all."""
    client = _RecordingClient()
    with pytest.raises(ValueError):
        call(_reader_with(client), bad)
    assert client.sql is None, label


def test_root_span_projection_requires_the_trace_id_key():
    client = _RecordingClient()
    with pytest.raises(ValueError, match="trace_id"):
        _reader_with(client).list_root_spans_by_trace_ids(["t1"], columns=["id"])
    assert client.sql is None


# ── the projected values against a real ClickHouse ─────────────────────────────


@pytest.fixture(scope="module")
def seeded():
    """Reader + two root spans in their own project; SKIPs when CH is down."""
    import clickhouse_connect

    from tracer.services.clickhouse.v2 import get_reader, get_v2_config
    from tracer.tests._ch_seed import seed_ch_spans

    cfg = get_v2_config()
    try:
        client = clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
        )
        client.command("SELECT 1")
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"CH 25.3 (v2) not reachable ({exc!r}); integration test")

    project_id, org_id = str(uuid.uuid4()), str(uuid.uuid4())
    start = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    spans = [
        {
            "id": f"span-{uuid.uuid4().hex[:12]}",
            "trace_id": str(uuid.uuid4()),
            "project_id": project_id,
            "org_id": org_id,
            "parent_span_id": "",
            "name": f"root-{n}",
            "observation_type": "llm",
            "status": "OK",
            "start_time": start,
            "end_time": start,
            "latency_ms": 100 + n,
            "created_at": start,
        }
        for n in range(2)
    ]
    seed_ch_spans(spans, client=client)
    with get_reader() as reader:
        yield reader, project_id, spans


@pytest.mark.integration
def test_root_span_projection_returns_the_chspan_values(seeded):
    reader, project_id, spans = seeded
    trace_ids = [s["trace_id"] for s in spans]
    default = reader.list_root_spans_by_trace_ids(trace_ids, project_id=project_id)
    projected = reader.list_root_spans_by_trace_ids(
        trace_ids, project_id=project_id, columns=_PROJECTED
    )
    assert set(projected) == set(trace_ids)
    for trace_id, row in projected.items():
        assert row == {c: getattr(default[trace_id], c) for c in _PROJECTED}


@pytest.mark.integration
def test_list_by_ids_projection_returns_the_chspan_values(seeded):
    reader, project_id, spans = seeded
    span_ids = [s["id"] for s in spans]
    default = {s.id: s for s in reader.list_by_ids(span_ids, project_id=project_id)}
    projected = reader.list_by_ids(span_ids, project_id=project_id, columns=_PROJECTED)
    assert {r["id"] for r in projected} == set(span_ids)
    for row in projected:
        assert row == {c: getattr(default[row["id"]], c) for c in _PROJECTED}
