"""What Instant eval shows for a session reads through the id remap: a
straddler's and a consolidated group's spans count for every session id asked
for that resolves to the group, so neither its model nor its turns, duration,
first message or errors are split, and its user is the survivor.
Each case is also checked against a scan of the project through the global
survivor map.

Runs against the configured ClickHouse (``CH25_*``); data lives under fresh
ids and is deleted afterwards.
"""

from __future__ import annotations

import uuid
from datetime import UTC, timedelta

import pytest

from tracer.services import instant_eval_rows as rows
from tracer.tests.test_session_trace_ids_prefilter import START, _client, _ids
from tracer.tests.test_span_reader_remap_roots import _remap, _root


def _llm(client, project, trace, session, model, minutes, **extra):
    """An LLM call under ``trace``'s root, ``minutes`` after the start."""
    _root(
        client,
        project,
        trace,
        session=session,
        parent_span_id="root",
        observation_type="llm",
        model=model,
        start_time=START + timedelta(minutes=minutes),
        **extra,
    )


def _facets_reference(reader, project_id, ids):
    """``{id asked: facets}`` for each session through the global survivor
    map, scanning the whole project."""
    from tracer.services.clickhouse.v2.id_remap_sql import (
        remap_left_join,
        resolved_id_expr,
        survivor_map_subquery,
    )

    mapped = dict(
        reader._client.query(
            "SELECT toString(any_id), toString(survivor_id) "
            f"FROM ({survivor_map_subquery('trace_session_id_remap')}) "
            "WHERE any_id IN %(ids)s",
            parameters={"ids": tuple(ids)},
        ).result_rows
    )
    survivor = {i: mapped.get(i.lower(), i.lower()) for i in ids}
    resolved = resolved_id_expr("spans.trace_session_id", "m")
    root = "spans.parent_span_id = ''"
    rows = reader._client.query(
        f"SELECT toString({resolved}) AS r, min(spans.start_time), "
        f"uniqExactIf(spans.trace_id, {root}), "
        "dateDiff('millisecond', min(spans.start_time), "
        "max(coalesce(spans.end_time, spans.start_time))), "
        "max(spans.status = 'ERROR'), "
        "argMinIf(spans.model, spans.start_time, spans.model != '') FROM spans "
        f"{remap_left_join('spans.trace_session_id', 'trace_session_id_remap', 'm')} "
        "WHERE spans.project_id = %(p)s AND spans.is_deleted = 0 AND r IN %(s)s "
        "GROUP BY r",
        parameters={"p": project_id, "s": tuple(set(survivor.values()))},
    ).result_rows
    by_survivor = {
        r: {
            "time": t.replace(tzinfo=UTC) if t.tzinfo is None else t,
            "turns": turns or None,
            "duration_ms": dur,
            "error": bool(err),
            "model": model,
        }
        for r, t, turns, dur, err, model in rows
    }
    return {i: by_survivor[survivor[i]] for i in ids if survivor[i] in by_survivor}


@pytest.fixture()
def facets_world():
    client = _client()
    project, other = _ids(2)
    plain, s_old, s_new, g_a, g_b, g_new = _ids(6)
    u_old, u_new, u_plain = _ids(3)
    names = ["p1", "p2", "a", "b", "c", "d", "o", "x", "y", "z"]
    t = dict(zip(names, _ids(len(names)), strict=True))
    session_remaps = [(s_old, s_new), (g_a, g_new), (g_b, g_new)]
    user_remaps = [(u_old, u_new)]

    def turn(trace, session, user, minutes, seconds, text, **extra):
        _root(
            client,
            project,
            trace,
            session=session,
            user=user,
            start_time=START + timedelta(minutes=minutes),
            end_time=START + timedelta(minutes=minutes, seconds=seconds),
            input=text,
            **extra,
        )

    try:
        turn(t["p1"], plain, u_plain, 0, 5, "plain one")
        turn(t["p2"], plain, u_plain, 3, 5, "plain two", model="m-plain")
        # A straddler: its old id holds the middle turn, its new id the
        # first (with a model) and the last (which errors).
        turn(t["a"], s_old, u_old, 0, 10, "old turn")
        _llm(client, project, t["a"], s_old, "m-old", 0.1)
        turn(t["b"], s_new, u_new, 60, 30, "last turn", status="ERROR")
        turn(t["c"], s_new, u_new, -10, 5, "first turn", model="m-new")
        # Neither a deleted turn nor another project's counts.
        turn(t["d"], s_new, u_new, -24 * 60, 5, "deleted", is_deleted=1)
        _root(client, other, t["o"], session=s_old, start_time=START)
        # A consolidated group: a turn under each of its ids.
        turn(t["x"], g_a, None, 5, 1, "group a")
        turn(t["y"], g_b, None, 1, 1, "group b", model="m-b")
        turn(t["z"], g_new, None, 9, 1, "group new")
        for old_id, new_id in session_remaps:
            _remap(client, "trace_session_id_remap", old_id, new_id)
        for old_id, new_id in user_remaps:
            _remap(client, "end_user_id_remap", old_id, new_id)
        yield {
            "project": project,
            "plain": plain,
            "s": (s_old, s_new),
            "g": (g_a, g_b, g_new),
            "u": (u_old, u_new, u_plain),
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
def test_a_sessions_facets_read_through_the_remap(facets_world):
    from tracer.services.clickhouse.v2 import get_reader

    world = facets_world
    project = world["project"]
    s_old, s_new = world["s"]
    g_a, g_b, g_new = world["g"]
    u_old, _, u_plain = world["u"]
    asked = [world["plain"], s_old, s_new, g_b, g_new, str(uuid.uuid4())]
    got = rows.read_facets(project, "sessions", asked)
    straddler = {
        "time": START - timedelta(minutes=10),
        "end_user_id": u_old,
        "model": "m-new",
        "turns": 3,
        "duration_ms": 70 * 60 * 1000 + 30 * 1000,
        "error": True,
        "excerpt": "first turn",
    }
    for session in (s_old, s_new):
        assert {k: got[session][k] for k in straddler} == straddler, session
    assert got[g_b] == got[g_new]
    assert (got[g_b]["turns"], got[g_b]["model"], got[g_b]["excerpt"]) == (
        3,
        "m-b",
        "group b",
    )
    assert got[world["plain"]]["turns"] == 2
    assert got[world["plain"]]["end_user_id"] == u_plain
    assert len(got) == 5
    with get_reader() as reader:
        expected = _facets_reference(reader, project, asked)
    fields = ("time", "turns", "duration_ms", "error", "model")
    assert {s: {k: f[k] for k in fields} for s, f in got.items()} == expected
    # A trace is only itself; a user under its new id reads as the survivor.
    trace = world["t"]["c"]
    got = rows.read_facets(project, "traces", [trace])
    assert (got[trace]["turns"], got[trace]["end_user_id"]) == (None, u_old)


@pytest.mark.integration
def test_batched_session_facets_read_the_same(facets_world, monkeypatch):
    """Read a session per statement, bounded by the extents of the ids asked
    alone (as the pick hands them over): every id of a group still counts."""
    world = facets_world
    s_old, s_new = world["s"]
    g_a, g_b, g_new = world["g"]
    asked = [world["plain"], s_old, s_new, g_b, g_new]
    whole = rows.read_facets(world["project"], "sessions", asked)
    extents = rows._row_extents(world["project"], "sessions", asked)
    monkeypatch.setattr(rows, "_FACET_BATCH", 1)
    got = rows.read_facets(world["project"], "sessions", asked, extents=extents)
    assert got == whole
