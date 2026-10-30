"""``read_facets`` reads more rows than one read takes in the order their
spans start, each read bounded to its rows' spans, and gives exactly what one
read of the whole window gives.

The world is a few thousand synthetic sessions: some straddle the window's
edges, some run over several days, some roots never end, some child spans
start before their root or minutes after it ends, and some spans are deleted.
The reference is the facet read as it was: every read covers the whole window
(a user's sessions: all of time).

Runs against the configured ClickHouse (``CH25_*``); data lives under a fresh
project id and is deleted afterwards.
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracer.services import instant_eval_rows as rows
from tracer.services import instant_eval_users
from tracer.tests.test_session_trace_ids_prefilter import _client

W0 = datetime(2025, 3, 1, tzinfo=UTC)
# A short window keeps the world to a few parts: the table's projections make
# each part costly to write and to delete.
W1 = datetime(2025, 3, 4, tzinfo=UTC)
SESSIONS = 6000
COLUMNS = [
    "project_id",
    "observation_type",
    "start_time",
    "end_time",
    "trace_id",
    "id",
    "parent_span_id",
    "name",
    "trace_session_id",
    "end_user_id",
    "model",
    "status",
    "input",
    "attrs_string",
    "is_deleted",
]


def _session(rng, project, sid, user, start, gap, turns):
    """One session's spans, a turn every ``gap()`` seconds from ``start``."""
    out, t = [], start
    for _ in range(turns):
        trace, root = str(uuid.uuid4()), str(uuid.uuid4())
        took = timedelta(seconds=rng.uniform(1, 90))
        end = None if rng.random() < 0.03 else t + took
        out.append(
            [project, "agent", t, end, trace, root, "", "turn", sid, user, "", "OK"]
            + [json.dumps([{"role": "user", "content": f"turn {t:%H%M%S}"}]), {}, 0]
        )
        for _ in range(rng.randint(0, 3)):
            if rng.random() < 0.05:
                # A child that starts minutes after its root ends.
                c = t + took + timedelta(seconds=rng.uniform(60, 600))
            else:
                # Clock skew: a child can start a moment before its root.
                c = t + timedelta(seconds=rng.uniform(-2, took.total_seconds()))
            out.append(
                [project, "llm", c, c + timedelta(seconds=1), trace]
                + [str(uuid.uuid4()), root, "call", sid, None]
                + [rng.choice(["gpt-4.1", "gpt-4.1-mini"])]
                + ["ERROR" if rng.random() < 0.05 else "OK", "", {}]
                + [int(rng.random() < 0.02)]
            )
        t += timedelta(seconds=gap())
    return out


@pytest.fixture(scope="module")
def world():
    client = _client()
    rng = random.Random(8103)
    project = str(uuid.uuid4())
    users = [str(uuid.uuid4()) for _ in range(500)]
    sessions, spans, by_user, long = [], [], {}, []
    for n in range(SESSIONS):
        sid, user = str(uuid.uuid4()), rng.choice(users)
        if n % 50 == 0:
            # Three turns a day or more apart, inside the window.
            start = W0 + timedelta(hours=rng.uniform(0, 6))
            spans += _session(
                rng, project, sid, user, start, lambda: rng.uniform(26, 30) * 3600, 3
            )
            long.append(sid)
        else:
            # Minutes long, starting up to half a day either side of the window.
            start = W0 + timedelta(hours=rng.uniform(-12, 3 * 24 + 12))
            spans += _session(
                rng,
                project,
                sid,
                user,
                start,
                lambda: rng.uniform(5, 600),
                rng.randint(1, 5),
            )
        sessions.append(sid)
        by_user.setdefault(user, []).append(sid)
    for row in spans:
        row[0], row[8] = uuid.UUID(row[0]), uuid.UUID(row[8])
        row[9] = uuid.UUID(row[9]) if row[9] else None
    client.insert("spans", spans, column_names=COLUMNS)
    try:
        yield {
            "project": project,
            "sessions": sessions,
            "users": users,
            "long": long,
            "traces": sorted({r[4] for r in spans}),
            "spans": [r[5] for r in spans],
        }
    finally:
        client.command(
            "ALTER TABLE spans DELETE WHERE project_id = %(p)s "
            "SETTINGS mutations_sync=2",
            parameters={"p": project},
        )


def _md5(facets):
    lines = sorted(
        json.dumps([k, v], sort_keys=True, default=str) for k, v in facets.items()
    )
    return hashlib.md5("\n".join(lines).encode()).hexdigest()


def _whole_window(project, row_type, ids, start=W0, end=W1):
    """The read as it was: every read covers the whole window."""
    out = {}
    for chunk in rows._chunks(ids, 5000):
        out.update(rows.read_facets(project, row_type, chunk, start=start, end=end))
    return out


def _bounded(monkeypatch, batch):
    """Read ``batch`` rows at a time, recording each read's bounds."""
    monkeypatch.setattr(rows, "_FACET_BATCH", batch)
    reads, ch = [], rows._ch
    monkeypatch.setattr(
        rows,
        "_ch",
        lambda sql, params: reads.append(params) or ch(sql, params),
    )
    return reads


@pytest.mark.integration
@pytest.mark.parametrize("batch", [1000, 5000])
def test_sessions_read_a_slice_at_a_time_match_the_whole_window(
    world, monkeypatch, batch
):
    ids = [*world["sessions"], str(uuid.uuid4())]
    expected = _whole_window(world["project"], "sessions", ids)
    reads = _bounded(monkeypatch, batch)
    got = rows.read_facets(world["project"], "sessions", ids, start=W0, end=W1)
    assert len(got) == SESSIONS
    assert _md5(got) == _md5(expected)
    # Each read covers a slice of the window, not all of it.
    slices = [p["b_end"] - p["b_start"] for p in reads if "b_start" in p]
    window = W1 - W0 + timedelta(days=2)
    assert len(slices) == -(-SESSIONS // batch)
    assert all(width < window for width in slices)
    assert sum(slices, timedelta()) < len(slices) * window


@pytest.mark.integration
def test_a_session_longer_than_a_day_reads_every_turn(world, monkeypatch):
    expected = _whole_window(world["project"], "sessions", world["sessions"])
    _bounded(monkeypatch, 1000)
    got = rows.read_facets(
        world["project"], "sessions", world["sessions"], start=W0, end=W1
    )
    for sid in world["long"]:
        assert got[sid] == expected[sid]
    longest = max(world["long"], key=lambda s: got[s]["duration_ms"] or 0)
    assert got[longest]["duration_ms"] > 2 * 24 * 3600 * 1000
    assert got[longest]["turns"] == 3


@pytest.mark.integration
@pytest.mark.parametrize("row_type", ["traces", "spans"])
def test_traces_and_spans_read_a_slice_at_a_time_match(world, monkeypatch, row_type):
    ids = world[row_type][:6000]
    expected = _whole_window(world["project"], row_type, ids)
    _bounded(monkeypatch, 1000)
    got = rows.read_facets(world["project"], row_type, ids, start=W0, end=W1)
    assert got and _md5(got) == _md5(expected)


@pytest.mark.integration
def test_a_users_sessions_read_in_the_window_match_all_of_time(world, monkeypatch):
    """A user question's sessions were read over all of time; they are now
    read in the run's window and a day either side, a slice at a time."""
    sids = instant_eval_users.user_sessions(world["project"], world["users"], W0, W1)
    assert len(sids) > 1000
    expected = _whole_window(world["project"], "sessions", sids, None, None)
    _bounded(monkeypatch, 1000)
    got = rows.read_facets(world["project"], "sessions", sids, start=W0, end=W1)
    assert _md5(got) == _md5(expected)


@pytest.mark.integration
@pytest.mark.django_db
def test_the_pick_reads_each_rows_extent(world):
    """The pick hands over the same extents ``read_facets`` would read."""
    filters, start, end = rows._frozen_filters(
        [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [W0.isoformat(), W1.isoformat()],
                },
            }
        ],
        "sessions",
    )
    for unit in ("sessions", "traces"):
        plain = rows.pick_rows(world["project"], unit, filters, n=3000, after=None)
        extents = {}
        picked = rows.pick_rows(
            world["project"],
            unit,
            filters,
            n=3000,
            after=None,
            extents=extents,
            window=(start, end),
        )
        assert picked == plain and len(picked) == 3000
        ids = [rid for rid, _ in picked]
        assert extents == rows._row_extents(
            world["project"], rows.ROW_TYPE[unit], ids, start=start, end=end
        )


def test_every_users_label_is_found_however_many_users_a_read_has(monkeypatch):
    """The label lookup can't parse a statement for more than about 2,000
    users, and comes back empty; a read of 3,000 users still labels them all."""
    uids = [str(uuid.uuid4()) for _ in range(3000)]
    monkeypatch.setattr(
        rows,
        "_ch",
        lambda sql, params: [
            {"rid": rid, "uid": uid, "t": W0, "turns": 1, "dur": 0, "err": 0}
            for rid, uid in zip(params["ids"], uids, strict=False)
        ],
    )

    def labels(ids):
        ids = list(ids)
        return {} if len(ids) > 2000 else {i: f"user {i}" for i in ids}

    monkeypatch.setattr(rows, "_user_labels", labels)
    got = rows.read_facets("p", "sessions", [str(uuid.uuid4()) for _ in uids])
    assert len(got) == 3000
    assert all(f["user_label"] == f"user {f['end_user_id']}" for f in got.values())
