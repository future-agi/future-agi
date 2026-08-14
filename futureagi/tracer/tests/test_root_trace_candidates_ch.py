"""Real-ClickHouse integration test for ``CHSpanReader.root_trace_candidates``.

The scan sweep's candidate query is the one piece the DB-free sweep tests
(``test_scan_sweep.py``) can't cover: the actual SQL — root-only
(``parent_span_id = ''``), the ``is_deleted`` filter, the half-open
``created_at`` window, the partition-pruning ``start_time`` floor, and the
``GROUP BY`` dedup to ``min(created_at)``. This drives the real reader against a
stripped-down ``spans`` table so the SQL can't drift from a copied string.

Skips when ClickHouse is unavailable (so it's inert in a CH-less CI lane and
runs where CH is present).
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tracer.services.clickhouse.v2.span_reader import CHSpanReader

_TEST_DB = "test_root_trace_candidates"
_COLS = ["trace_id", "project_id", "parent_span_id", "is_deleted", "start_time", "created_at"]
_UTC = timezone.utc


@pytest.fixture(scope="module")
def reader():
    """A real CHSpanReader pointed at an isolated test DB; skip if CH is down."""
    import clickhouse_connect

    # Mirror conftest: the dev .env's `clickhouse` hostname doesn't resolve
    # outside Docker, so force the test sidecar at localhost.
    host = os.environ.get("CH25_HOST") or os.environ.get("CH_HOST") or "localhost"
    if host == "clickhouse":
        host = "localhost"
    port = int(os.environ.get("CH25_HTTP_PORT") or os.environ.get("CH_HTTP_PORT") or 18123)
    try:
        admin = clickhouse_connect.get_client(host=host, port=port, username="default", password="")
        admin.query("SELECT 1")
    except Exception:
        pytest.skip("ClickHouse not available for integration tests")

    admin.command(f"CREATE DATABASE IF NOT EXISTS {_TEST_DB}")
    admin.command(f"DROP TABLE IF EXISTS {_TEST_DB}.spans")
    admin.command(
        f"""
        CREATE TABLE {_TEST_DB}.spans (
            trace_id       String,
            project_id     UUID,
            parent_span_id String,
            is_deleted     UInt8,
            start_time     DateTime64(6, 'UTC'),
            created_at     DateTime64(6, 'UTC')
        ) ENGINE = MergeTree() ORDER BY (project_id, trace_id)
        """
    )
    rdr = CHSpanReader(host=host, port=port, database=_TEST_DB)
    rdr._admin = admin  # for inserts in tests
    yield rdr
    rdr.close()
    admin.command(f"DROP DATABASE IF EXISTS {_TEST_DB}")


def _insert(reader, rows):
    reader._admin.insert(f"{_TEST_DB}.spans", rows, column_names=_COLS)


def _row(pid, tid, *, ca, st=None, parent="", deleted=0):
    return [tid, pid, parent, deleted, st or ca, ca]


class TestRootTraceCandidates:
    def test_returns_only_in_window_undeleted_roots(self, reader):
        pid = uuid.uuid4()
        now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=_UTC)
        lower, upper = now - timedelta(hours=1), now
        _insert(
            reader,
            [
                _row(pid, "R_in", ca=now - timedelta(minutes=30)),  # kept
                _row(pid, "R_late", ca=now + timedelta(minutes=10)),  # > upper
                _row(pid, "R_early", ca=lower - timedelta(minutes=10)),  # < lower
                _row(pid, "R_deleted", ca=now - timedelta(minutes=20), deleted=1),
            ],
        )
        got = dict(reader.root_trace_candidates(str(pid), lower, upper))
        assert set(got) == {"R_in"}
        assert got["R_in"] == now - timedelta(minutes=30)

    def test_root_filter_ignores_child_spans(self, reader):
        # The root carries the candidate's created_at; a child with an EARLIER
        # created_at must not leak in (it would shift min() if parent_span_id=''
        # were dropped).
        pid = uuid.uuid4()
        now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=_UTC)
        lower, upper = now - timedelta(hours=1), now
        _insert(
            reader,
            [
                _row(pid, "T", ca=now - timedelta(minutes=30)),  # root
                _row(pid, "T", ca=now - timedelta(minutes=40), parent="some-parent"),  # child, earlier
            ],
        )
        got = dict(reader.root_trace_candidates(str(pid), lower, upper))
        assert got == {"T": now - timedelta(minutes=30)}

    def test_start_time_floor_excludes_late_export(self, reader):
        # created_at is in-window, but start_time predates (lower - 7d): a
        # >7d-late export is intentionally beyond the partition-pruning floor.
        pid = uuid.uuid4()
        now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=_UTC)
        lower, upper = now - timedelta(hours=1), now
        _insert(
            reader,
            [
                _row(pid, "fresh", ca=now - timedelta(minutes=10), st=now - timedelta(minutes=10)),
                _row(pid, "stale", ca=now - timedelta(minutes=10), st=lower - timedelta(days=8)),
            ],
        )
        got = dict(reader.root_trace_candidates(str(pid), lower, upper))
        assert set(got) == {"fresh"}

    def test_dedupes_to_min_created_at(self, reader):
        # Two root rows for one trace (e.g. a re-export) collapse to one
        # candidate at the earliest created_at — no FINAL needed.
        pid = uuid.uuid4()
        now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=_UTC)
        lower, upper = now - timedelta(hours=1), now
        first = now - timedelta(minutes=20)
        _insert(
            reader,
            [
                _row(pid, "D", ca=first),
                _row(pid, "D", ca=now - timedelta(minutes=15), st=now - timedelta(minutes=14)),
            ],
        )
        got = reader.root_trace_candidates(str(pid), lower, upper)
        assert got == [("D", first)]
