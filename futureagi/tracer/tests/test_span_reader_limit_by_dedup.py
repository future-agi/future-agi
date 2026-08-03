"""``dedup_via_limit_by`` must reproduce ``spans FINAL`` exactly (TH-7226).

Three rules, each silent when broken: is_deleted filtered AFTER the dedup (else
deleted rows resurrect), the full sorting key as the dedup key, and every column
named (MATERIALIZED columns are absent from ``SELECT *``; the lean select stubs
heavy ones to a bare ``''``). The SQL-shape tests pin all three without CH,
mirroring ``test_span_reader_point_read_oom.py``; the last test proves the
semantics against a real ReplacingMergeTree.
"""

import uuid

import pytest

from tracer.services.clickhouse.v2.span_reader import (
    _READ_COLUMNS,
    _SORTING_KEY,
    CHSpanReader,
    _output_name,
)


class _RecordingClient:
    """Captures the SQL + settings of the last query; returns no rows."""

    def __init__(self):
        self.sql = None
        self.settings = None

    def query(self, sql, parameters=None, settings=None):
        self.sql = sql
        self.settings = settings or {}

        class _Result:
            result_rows = []

        return _Result()


def _reader_with(client) -> CHSpanReader:
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client
    return reader


def _dedup_reads():
    """(label, callable) for each annotation-path read that opts in."""
    return [
        (
            "roots_by_trace_ids",
            lambda r: r.roots_by_trace_ids(
                ["t1", "t2"], include_heavy=False, dedup_via_limit_by=True
            ),
        ),
        (
            "list_by_ids",
            lambda r: r.list_by_ids(
                ["s1", "s2"], include_heavy=True, dedup_via_limit_by=True
            ),
        ),
    ]


@pytest.mark.parametrize(
    "label,call", _dedup_reads(), ids=lambda v: getattr(v, "__name__", v)
)
def test_dedup_read_uses_limit_by_not_final(label, call):
    client = _RecordingClient()
    call(_reader_with(client))
    assert "FINAL" not in client.sql, f"{label} still uses FINAL"
    assert "LIMIT 1 BY" in client.sql
    # The FINAL-only workaround must go with FINAL: without FINAL, skip indexes
    # are on by default and the is_deleted minmax hazard does not arise.
    assert "use_skip_indexes_if_final" not in client.settings


@pytest.mark.parametrize(
    "label,call", _dedup_reads(), ids=lambda v: getattr(v, "__name__", v)
)
def test_dedup_key_is_the_full_sorting_key(label, call):
    """Rule 2. A shorter key collapses rows the engine considers distinct."""
    client = _RecordingClient()
    call(_reader_with(client))
    limit_by = client.sql.split("LIMIT 1 BY", 1)[1]
    for key_col in ("observation_type", "toStartOfHour(start_time)", "trace_id", "id"):
        assert key_col in limit_by, f"{label}: {key_col} missing from the dedup key"
    # project_id / service_name appear under private aliases: project_id is
    # exposed to callers as project_id_str, and service_name is not in the read
    # set at all, so both are re-selected raw for the key.
    assert "_dedup_project" in limit_by
    assert "_dedup_service" in limit_by


@pytest.mark.parametrize(
    "label,call", _dedup_reads(), ids=lambda v: getattr(v, "__name__", v)
)
def test_is_deleted_is_filtered_outside_the_dedup(label, call):
    """Rule 1 — the resurrection guard, and the reason for the subquery."""
    client = _RecordingClient()
    call(_reader_with(client))
    assert "is_deleted = 0" in client.sql
    inner = client.sql.split("LIMIT 1 BY", 1)[1]
    filter_pos = inner.index("is_deleted = 0")
    close_paren = inner.index(")")
    assert close_paren < filter_pos, (
        f"{label}: is_deleted is filtered INSIDE the dedup subquery — that "
        "resurrects rows whose newest version is deleted"
    )


@pytest.mark.parametrize(
    "label,call", _dedup_reads(), ids=lambda v: getattr(v, "__name__", v)
)
def test_every_column_is_named_for_the_outer_projection(label, call):
    """Rule 3. Decoding is positional, so a dropped/renamed column is silent."""
    client = _RecordingClient()
    call(_reader_with(client))
    projection = client.sql.split(" FROM (", 1)[0]
    for col in _READ_COLUMNS:
        name = _output_name(col)
        assert name in projection, f"{label}: {name} missing from the projection"
    # No bare '' stubs: the lean select's unnamed stubs cannot be projected.
    assert ", ''," not in projection


# ── the constant vs the live table ─────────────────────────────────────────────


@pytest.mark.django_db
def test_sorting_key_matches_the_live_spans_table():
    """``_SORTING_KEY`` must equal the real ``spans`` ORDER BY.

    Everything else here is self-consistent: ``_DEDUP_KEY`` is derived from the
    constant and the equivalence test builds its own table from it too, so all
    of it stays green if the production ORDER BY changes underneath — while the
    dedup silently over- or under-collapses. This is the one assertion tied to
    the actual table.
    """
    from tracer.services.clickhouse.v2 import get_reader
    from tracer.services.clickhouse.v2.span_reader import _SORTING_KEY

    with get_reader() as reader:
        rows = reader._client.query(
            "SELECT sorting_key FROM system.tables WHERE name = 'spans' "
            "AND database = currentDatabase()"
        ).result_rows
    assert rows, "spans table not found — cannot verify the dedup key"

    normalise = lambda s: ",".join(p.strip() for p in s.split(","))  # noqa: E731
    live = normalise(rows[0][0])
    assert normalise(_SORTING_KEY) == live, (
        "_SORTING_KEY has drifted from the spans ORDER BY. The LIMIT 1 BY dedup "
        "collapses on this key, so a mismatch silently returns the wrong row "
        f"version.\n  constant: {_SORTING_KEY}\n  live    : {live}"
    )


# ── semantics against a real ReplacingMergeTree ────────────────────────────────


@pytest.mark.django_db
def test_limit_by_matches_final_including_deleted_rows():
    """LIMIT 1 BY == FINAL for newest-wins AND for deletions.

    Merges stopped, one part per row — otherwise the engine collapses the
    duplicates itself and every variant agrees, proving nothing.
    """
    from tracer.services.clickhouse.v2 import get_reader

    table = f"th7226_probe_{uuid.uuid4().hex[:8]}"
    with get_reader() as reader:
        ch = reader._client
        ch.command(
            f"CREATE TABLE {table} ("
            "  project_id UUID, observation_type LowCardinality(String),"
            "  service_name LowCardinality(String), start_time DateTime64(6),"
            "  trace_id UUID, id String, name String,"
            "  is_deleted UInt8 DEFAULT 0, _version UInt64 DEFAULT 1"
            ") ENGINE = ReplacingMergeTree(_version, is_deleted) "
            "PARTITION BY toDate(start_time) "
            f"ORDER BY ({_SORTING_KEY})"
        )
        try:
            ch.command(f"SYSTEM STOP MERGES {table}")
            p = "11111111-1111-1111-1111-111111111111"
            t = "22222222-2222-2222-2222-222222222222"
            rows = [
                ("A", "A-v1", 0, 1),
                ("A", "A-v2-NEWEST", 0, 2),  # newest wins
                ("B", "B-v1", 0, 1),
                ("B", "B-v2-DELETED", 1, 2),  # must NOT resurrect as B-v1
                ("C", "C-v1-DELETED", 1, 1),
                ("C", "C-v2-ALIVE", 0, 2),  # deleted then revived
            ]
            for rid, nm, deleted, version in rows:  # one INSERT => one part
                ch.insert(
                    table,
                    [
                        [
                            p,
                            "span",
                            "svc",
                            "2026-07-01 10:00:00",
                            t,
                            rid,
                            nm,
                            deleted,
                            version,
                        ]
                    ],
                    column_names=[
                        "project_id",
                        "observation_type",
                        "service_name",
                        "start_time",
                        "trace_id",
                        "id",
                        "name",
                        "is_deleted",
                        "_version",
                    ],
                )
            parts = ch.query(
                f"SELECT count() FROM system.parts WHERE table = '{table}' AND active"
            ).result_rows[0][0]
            assert parts > 1, "parts already merged — the probe would prove nothing"

            def names(sql):
                return sorted(r[0] for r in ch.query(sql).result_rows)

            final = names(f"SELECT name FROM {table} FINAL")
            limit_by = names(
                f"SELECT name FROM (SELECT * FROM {table} "
                f"ORDER BY _version DESC LIMIT 1 BY {_SORTING_KEY}) "
                "WHERE is_deleted = 0"
            )
            assert final == ["A-v2-NEWEST", "C-v2-ALIVE"]
            assert limit_by == final, (
                f"LIMIT 1 BY diverged from FINAL: {limit_by} != {final}"
            )

            # And the mistake this guards against, so the test above cannot pass
            # for the wrong reason.
            naive = names(
                f"SELECT name FROM (SELECT * FROM {table} WHERE is_deleted = 0 "
                f"ORDER BY _version DESC LIMIT 1 BY {_SORTING_KEY})"
            )
            assert "B-v1" in naive, (
                "filtering is_deleted inside the dedup no longer resurrects a "
                "deleted row — re-check whether the outer filter is still needed"
            )
        finally:
            ch.command(f"SYSTEM START MERGES {table}")
            ch.command(f"DROP TABLE IF EXISTS {table}")
