"""``session_trace_ids`` narrows its FINAL read without changing the answer.

The shipped read filtered spans by the remap-resolved session id, which no
index can serve, so every lookup scanned all of the project's spans. The read
now first finds the traces holding a span with any raw id that can resolve to
the session. These tests manufacture straddlers, a consolidation group, a
foreign project, a span that moved session in a later version and a deleted
span, and check the new read against the expected trace sets and against a
frozen copy of the shipped statement.

Runs against the configured ClickHouse (``CH25_*``); data lives under a fresh
project id and is deleted afterwards.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

START = datetime(2025, 1, 1, tzinfo=UTC)


def _client():
    from tracer.services.clickhouse.v2 import get_v2_config

    cfg = get_v2_config()
    try:
        import clickhouse_connect

        client = clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
            send_receive_timeout=15,
        )
        client.query("SELECT 1")
    except Exception:
        pytest.skip("ClickHouse not reachable for the session_trace_ids test")
    return client


def _ids(n):
    return [str(uuid.uuid4()) for _ in range(n)]


def _span(client, project_id, session_id, trace_id, span_id=None, **extra):
    """One span version; each call is its own insert, so its own part."""
    cols = {
        "project_id": project_id,
        "observation_type": "agent",
        "start_time": START,
        "trace_id": trace_id,
        "id": span_id or str(uuid.uuid4()),
        "name": "prefilter-span",
        "trace_session_id": session_id,
        "_version": 1,
        "is_deleted": 0,
        **extra,
    }
    client.command(
        f"INSERT INTO spans ({', '.join(cols)}) "
        f"VALUES ({', '.join(f'%({c})s' for c in cols)})",
        parameters=cols,
    )


def _remap(client, old_id, new_id):
    client.command(
        "INSERT INTO trace_session_id_remap (old_id, new_id, version) "
        "VALUES (%(o)s, %(n)s, %(v)s)",
        parameters={"o": old_id, "n": new_id, "v": START},
    )


def _shipped(reader, project_id, session_id):
    """The statement before the narrowing, kept here as the reference."""
    resolved = reader._resolve_session_ids_to_canonical([session_id])[session_id]
    join, pred = reader._session_filter_remap()
    rows = reader._client.query(
        f"SELECT DISTINCT toString(spans.trace_id) FROM spans FINAL {join} "
        f"WHERE spans.project_id = %(p)s AND {pred}",
        parameters={"p": project_id, "sid": resolved},
    ).result_rows
    return {str(r[0]) for r in rows}


@pytest.fixture()
def world():
    client = _client()
    project, other = _ids(2)
    plain, moved_to = _ids(2)
    strad_old, strad_new = _ids(2)
    group_a, group_b, group_new = _ids(3)
    t = dict(
        zip(
            ["plain", "plain2", "old", "new", "a", "b", "g", "other", "moved", "gone"],
            _ids(10),
            strict=True,
        )
    )
    remaps = [(strad_old, strad_new), (group_a, group_new), (group_b, group_new)]
    try:
        _span(client, project, plain, t["plain"])
        _span(client, project, plain, t["plain2"])
        _span(client, project, moved_to, t["plain2"])
        _span(client, project, strad_old, t["old"])
        _span(client, project, strad_new, t["new"])
        _span(client, project, group_a, t["a"])
        _span(client, project, group_b, t["b"])
        _span(client, project, group_new, t["g"])
        for old_id, new_id in remaps:
            _remap(client, old_id, new_id)
        # The same session id in another project never leaks in.
        _span(client, other, plain, t["other"])
        # A span that moved to another session in its latest version, written
        # as a separate part so its older version sits in other granules.
        moved = str(uuid.uuid4())
        _span(client, project, plain, t["moved"], span_id=moved)
        _span(client, project, moved_to, t["moved"], span_id=moved, _version=2)
        # A span deleted in its latest version.
        gone = str(uuid.uuid4())
        _span(client, project, plain, t["gone"], span_id=gone)
        _span(client, project, plain, t["gone"], span_id=gone, _version=2, is_deleted=1)
        yield {
            "project": project,
            "plain": plain,
            "moved_to": moved_to,
            "strad": (strad_old, strad_new),
            "group": (group_a, group_b, group_new),
            "t": t,
        }
    finally:
        client.command(
            "ALTER TABLE spans DELETE WHERE project_id IN (%(p)s, %(o)s) "
            "SETTINGS mutations_sync=2",
            parameters={"p": project, "o": other},
        )
        client.command(
            "ALTER TABLE trace_session_id_remap DELETE WHERE old_id IN %(ids)s "
            "SETTINGS mutations_sync=2",
            parameters={"ids": tuple(old for old, _ in remaps)},
        )


@pytest.mark.integration
def test_session_trace_ids_matches_the_shipped_read(world):
    from tracer.services.clickhouse.v2 import get_reader

    t = world["t"]
    strad_old, strad_new = world["strad"]
    group_a, group_b, group_new = world["group"]
    expected = {
        world["plain"]: {t["plain"], t["plain2"]},
        world["moved_to"]: {t["plain2"], t["moved"]},
        strad_old: {t["old"], t["new"]},
        strad_new: {t["old"], t["new"]},
        group_a: {t["a"], t["b"], t["g"]},
        group_b: {t["a"], t["b"], t["g"]},
        group_new: {t["a"], t["b"], t["g"]},
        str(uuid.uuid4()): set(),
    }
    with get_reader() as reader:
        for session_id, traces in expected.items():
            got = set(reader.session_trace_ids(world["project"], session_id))
            assert got == traces, session_id
            assert got == _shipped(reader, world["project"], session_id)
