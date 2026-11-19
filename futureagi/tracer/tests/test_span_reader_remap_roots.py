"""``roots_by_session_ids`` and ``roots_by_end_user_ids`` read through the
id remaps: a straddler's old- and new-id roots and a consolidated group's roots
come back under the id asked for, a root that moved to another session or user
in a later version is read as it is now, and a deleted root is gone. Each case
is also checked against a full scan of the project through the global survivor
map, and ids no remap touches against the statements the readers replaced.

Runs against the configured ClickHouse (``CH25_*``); data lives under fresh
ids and is deleted afterwards.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from tracer.tests.test_session_trace_ids_prefilter import START, _client, _ids

_ROOT = "(spans.parent_span_id IS NULL OR spans.parent_span_id = '')"


def _root(client, project_id, trace_id, *, session=None, user=None, **extra):
    """One root span version; each call is its own insert, so its own part."""
    cols = {
        "project_id": project_id,
        "observation_type": "agent",
        "start_time": START,
        "trace_id": trace_id,
        "id": str(uuid.uuid4()),
        "name": "remap-root",
        "trace_session_id": session,
        "end_user_id": user,
        "_version": 1,
        "is_deleted": 0,
        **extra,
    }
    client.command(
        f"INSERT INTO spans ({', '.join(cols)}) "
        f"VALUES ({', '.join(f'%({c})s' for c in cols)})",
        parameters=cols,
    )
    return cols["id"]


def _remap(client, table, old_id, new_id):
    client.command(
        f"INSERT INTO {table} (old_id, new_id, version) VALUES (%(o)s, %(n)s, %(v)s)",
        parameters={"o": old_id, "n": new_id, "v": START},
    )


def _full_scan(reader, column, remap_table, project_id, ids):
    """``{(id asked, trace)}`` for every root whose ``column`` resolves to an
    asked id's survivor, read by scanning the whole project through the global
    survivor map (skip indexes off under FINAL, the server default)."""
    from tracer.services.clickhouse.v2.id_remap_sql import (
        remap_left_join,
        resolved_id_expr,
        survivor_map_subquery,
    )

    mapped = dict(
        reader._client.query(
            "SELECT toString(any_id), toString(survivor_id) "
            f"FROM ({survivor_map_subquery(remap_table)}) WHERE any_id IN %(ids)s",
            parameters={"ids": tuple(ids)},
        ).result_rows
    )
    survivor = {i: mapped.get(i.lower(), i.lower()) for i in ids}
    rows = reader._client.query(
        f"SELECT toString(spans.trace_id), "
        f"toString({resolved_id_expr(f'spans.{column}', 'm')}) AS r FROM spans FINAL "
        f"{remap_left_join(f'spans.{column}', remap_table, 'm')} "
        f"WHERE spans.project_id = %(p)s AND {_ROOT} AND r IN %(s)s",
        parameters={"p": project_id, "s": tuple(set(survivor.values()))},
    ).result_rows
    return {(i, t) for t, r in rows for i in ids if survivor[i] == r}


def _shipped(reader, column, project_id, ids):
    """The statement the reader replaced, kept as the reference for ids no
    remap touches."""
    rows = reader._client.query(
        f"SELECT toString(trace_id), toString({column}) FROM spans FINAL "
        f"WHERE project_id = %(p)s AND {column} IN %(ids)s "
        "AND (parent_span_id IS NULL OR parent_span_id = '')",
        parameters={"p": project_id, "ids": tuple(ids)},
        settings={"use_skip_indexes_if_final": 1},
    ).result_rows
    return {(r, t) for t, r in rows}


def _got(spans, column):
    return {(getattr(s, column), s.trace_id) for s in spans}


@pytest.fixture()
def world():
    client = _client()
    project, other = _ids(2)
    plain, moved_to, clean = _ids(3)
    s_old, s_new = _ids(2)
    g_a, g_b, g_new = _ids(3)
    u_plain, u_moved_to, u_clean, u_shift = _ids(4)
    u_old, u_new = _ids(2)
    ug_a, ug_b, ug_new = _ids(3)
    names = [
        "plain",
        "plain2",
        "clean",
        "old",
        "new",
        "a",
        "b",
        "g",
        "ua",
        "ub",
        "ug",
        "other",
        "moved",
        "gone",
        "late",
        "shift",
        "clean_gone",
    ]
    t = dict(zip(names, _ids(len(names)), strict=True))
    session_remaps = [(s_old, s_new), (g_a, g_new), (g_b, g_new)]
    user_remaps = [(u_old, u_new), (ug_a, ug_new), (ug_b, ug_new)]
    try:
        _root(client, project, t["plain"], session=plain, user=u_plain)
        _root(client, project, t["plain2"], session=plain, user=u_plain)
        _root(client, project, t["clean"], session=clean, user=u_clean)
        # A child span is not a root.
        _root(
            client,
            project,
            t["plain"],
            session=plain,
            user=u_plain,
            parent_span_id="parent",
        )
        _root(client, project, t["old"], session=s_old, user=u_old)
        _root(client, project, t["new"], session=s_new, user=u_new)
        _root(client, project, t["a"], session=g_a)
        _root(client, project, t["b"], session=g_b)
        _root(client, project, t["g"], session=g_new)
        _root(client, project, t["ua"], user=ug_a)
        _root(client, project, t["ub"], user=ug_b)
        _root(client, project, t["ug"], user=ug_new)
        for old_id, new_id in session_remaps:
            _remap(client, "trace_session_id_remap", old_id, new_id)
        for old_id, new_id in user_remaps:
            _remap(client, "end_user_id_remap", old_id, new_id)
        # The same ids in another project never leak in.
        _root(client, other, t["other"], session=plain, user=u_plain)
        # A root that moved to another session and user in a later version.
        moved = str(uuid.uuid4())
        _root(client, project, t["moved"], session=plain, user=u_plain, id=moved)
        _root(
            client,
            project,
            t["moved"],
            session=moved_to,
            user=u_moved_to,
            id=moved,
            _version=2,
        )
        # A root deleted in its latest version.
        gone = str(uuid.uuid4())
        _root(client, project, t["gone"], session=plain, user=u_plain, id=gone)
        _root(
            client,
            project,
            t["gone"],
            session=plain,
            user=u_plain,
            id=gone,
            _version=2,
            is_deleted=1,
        )
        # The same in a session and user no remap touches, where the reads
        # are compared with the statements they replaced: FINAL on the
        # ReplacingMergeTree(_version, is_deleted) table dropped a deleted
        # root too, so the dedup's is_deleted = 0 changes nothing.
        clean_gone = str(uuid.uuid4())
        _root(
            client, project, t["clean_gone"], session=clean, user=u_clean, id=clean_gone
        )
        _root(
            client,
            project,
            t["clean_gone"],
            session=clean,
            user=u_clean,
            id=clean_gone,
            _version=2,
            is_deleted=1,
        )
        # A later root, outside a user read's window.
        _root(
            client,
            project,
            t["late"],
            session=plain,
            user=u_plain,
            start_time=START + timedelta(days=3),
        )
        # A root whose start time moved later within its hour: the same row,
        # so only its latest start time counts against a window.
        shift = str(uuid.uuid4())
        _root(client, project, t["shift"], user=u_shift, id=shift)
        _root(
            client,
            project,
            t["shift"],
            user=u_shift,
            id=shift,
            _version=2,
            start_time=START + timedelta(minutes=50),
        )
        yield {
            "project": project,
            "plain": plain,
            "moved_to": moved_to,
            "clean": clean,
            "s": (s_old, s_new),
            "g": (g_a, g_b, g_new),
            "u": (u_plain, u_moved_to, u_clean, u_old, u_new),
            "u_shift": u_shift,
            "ug": (ug_a, ug_b, ug_new),
            "t": t,
        }
    finally:
        client.command(
            "ALTER TABLE spans DELETE WHERE project_id IN (%(p)s, %(o)s) "
            "SETTINGS mutations_sync=2",
            parameters={"p": project, "o": other},
        )
        for table, remaps in (
            ("trace_session_id_remap", session_remaps),
            ("end_user_id_remap", user_remaps),
        ):
            client.command(
                f"ALTER TABLE {table} DELETE WHERE old_id IN %(ids)s "
                "SETTINGS mutations_sync=2",
                parameters={"ids": tuple(old for old, _ in remaps)},
            )


@pytest.mark.integration
def test_session_roots_read_through_the_remap(world):
    from tracer.services.clickhouse.v2 import get_reader

    t, project = world["t"], world["project"]
    s_old, s_new = world["s"]
    g_a, g_b, g_new = world["g"]
    _, _, _, u_old, _ = world["u"]
    group = {t["a"], t["b"], t["g"]}
    expected = {
        world["plain"]: {t["plain"], t["plain2"], t["late"]},
        world["moved_to"]: {t["moved"]},
        world["clean"]: {t["clean"]},
        s_old: {t["old"], t["new"]},
        s_new: {t["old"], t["new"]},
        g_a: group,
        g_b: group,
        g_new: group,
        str(uuid.uuid4()): set(),
    }
    with get_reader() as reader:
        for session, traces in expected.items():
            spans = reader.roots_by_session_ids([session], project_id=project)
            got = _got(spans, "trace_session_id")
            assert got == {(session, tr) for tr in traces}, session
            assert got == _full_scan(
                reader, "trace_session_id", "trace_session_id_remap", project, [session]
            )
        # All at once: each id asked for gets its own roots, a group's ids
        # each get the group's, and each root's user is its survivor.
        asked = list(expected)
        spans = reader.roots_by_session_ids(asked, project_id=project)
        got = _got(spans, "trace_session_id")
        assert got == {(s, tr) for s, traces in expected.items() for tr in traces}
        assert got == _full_scan(
            reader, "trace_session_id", "trace_session_id_remap", project, asked
        )
        assert [s.trace_session_id for s in spans] == sorted(
            s.trace_session_id for s in spans
        )
        users = {
            s.trace_id: s.end_user_id for s in spans if s.trace_session_id == s_new
        }
        assert users == {t["old"]: u_old, t["new"]: u_old}
        # Ids no remap touches read as the replaced statement did, which
        # left out the deleted root as well.
        clean = [world["clean"]]
        assert _got(
            reader.roots_by_session_ids(clean, project_id=project), "trace_session_id"
        ) == _shipped(reader, "trace_session_id", project, clean)
        assert _shipped(reader, "trace_session_id", project, clean) == {
            (world["clean"], t["clean"])
        }
        # An id in another case reads the same roots, under the id as given.
        upper = s_new.upper()
        spans = reader.roots_by_session_ids([upper], project_id=project)
        assert _got(spans, "trace_session_id") == {
            (upper, t["old"]),
            (upper, t["new"]),
        }


@pytest.mark.integration
def test_user_roots_read_through_the_remap(world):
    from tracer.services.clickhouse.v2 import get_reader

    t, project = world["t"], world["project"]
    u_plain, u_moved_to, u_clean, u_old, u_new = world["u"]
    ug_a, ug_b, ug_new = world["ug"]
    s_old, _ = world["s"]
    group = {t["ua"], t["ub"], t["ug"]}
    expected = {
        u_plain: {t["plain"], t["plain2"], t["late"]},
        u_moved_to: {t["moved"]},
        u_clean: {t["clean"]},
        u_old: {t["old"], t["new"]},
        u_new: {t["old"], t["new"]},
        ug_a: group,
        ug_b: group,
        ug_new: group,
    }
    with get_reader() as reader:
        for user, traces in expected.items():
            spans = reader.roots_by_end_user_ids([user], project_id=project)
            got = _got(spans, "end_user_id")
            assert got == {(user, tr) for tr in traces}, user
            assert got == _full_scan(
                reader, "end_user_id", "end_user_id_remap", project, [user]
            )
        asked = list(expected)
        got = _got(
            reader.roots_by_end_user_ids(asked, project_id=project), "end_user_id"
        )
        assert got == {(u, tr) for u, traces in expected.items() for tr in traces}
        assert _got(
            reader.roots_by_end_user_ids([u_clean], project_id=project), "end_user_id"
        ) == _shipped(reader, "end_user_id", project, [u_clean])
        assert _shipped(reader, "end_user_id", project, [u_clean]) == {
            (u_clean, t["clean"])
        }
        # The window bounds both reads, on either side.
        early = reader.roots_by_end_user_ids(
            [u_plain], project_id=project, until=START + timedelta(days=1)
        )
        assert _got(early, "end_user_id") == {
            (u_plain, t["plain"]),
            (u_plain, t["plain2"]),
        }
        late = reader.roots_by_end_user_ids(
            [u_plain], project_id=project, since=START + timedelta(days=2)
        )
        assert _got(late, "end_user_id") == {(u_plain, t["late"])}
        # A bound inside a root's hour is checked against its latest version.
        u_shift, half = world["u_shift"], START + timedelta(minutes=30)
        for bounds, traces in (
            ({}, {t["shift"]}),
            ({"until": half}, set()),
            ({"since": half}, {t["shift"]}),
        ):
            spans = reader.roots_by_end_user_ids(
                [u_shift], project_id=project, **bounds
            )
            assert {s.trace_id for s in spans} == traces, bounds
        # The straddler user's sessions read as one: both roots carry the
        # session's survivor.
        straddler = reader.roots_by_end_user_ids([u_new], project_id=project)
        assert {s.trace_session_id for s in straddler} == {s_old}


@pytest.mark.integration
def test_batched_reads_match_one_read(world, monkeypatch):
    """Split into one id per statement, the reads return the same roots: a
    group's aliases spread over batches, and a root whose old and new ids land
    in different batches is read once."""
    from tracer.services.clickhouse.v2 import get_reader, span_reader

    project = world["project"]
    sessions = [
        world["plain"],
        world["moved_to"],
        *world["s"],
        *world["g"],
    ]
    users = [*world["u"], *world["ug"]]
    with get_reader() as reader:
        whole_sessions = reader.roots_by_session_ids(sessions, project_id=project)
        whole_users = reader.roots_by_end_user_ids(users, project_id=project)
        monkeypatch.setattr(span_reader, "_REMAP_ID_BATCH", 1)
        assert reader.roots_by_session_ids(sessions, project_id=project) == (
            whole_sessions
        )
        assert reader.roots_by_end_user_ids(users, project_id=project) == whole_users
    moved = [s for s in whole_sessions if s.trace_id == world["t"]["moved"]]
    assert [s.trace_session_id for s in moved] == [world["moved_to"]]


def test_session_aliases_expand_only_the_survivors_group():
    """The whole-table window map can pass the read memory ceiling; the alias
    read expands only the group the survivor belongs to."""
    from types import SimpleNamespace

    from tracer.services.clickhouse.v2.span_reader import CHSpanReader

    seen = {}

    class Client:
        def query(self, sql, parameters=None, settings=None):
            seen.update(sql=sql, parameters=parameters)
            return SimpleNamespace(result_rows=[("b",), ("a",)])

    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = Client()
    assert reader._session_alias_ids("a") == ["a", "b"]
    assert "OVER (PARTITION BY" not in seen["sql"]
    assert "old_id IN %(alias_candidates)s" in seen["sql"]
    assert seen["parameters"]["alias_candidates"] == ("a",)
