"""``trace_units``: each trace's root as an evidence import reads it, from the root's
latest version, with its session resolved to the survivor. A deleted root, a child span
and another project's trace stay out.

Runs against the configured ClickHouse (``CH25_*``); data lives under fresh ids and is
deleted afterwards.
"""

from __future__ import annotations

import uuid

import pytest

from tracer.services.clickhouse.v2.id_remap_sql import NIL_UUID
from tracer.tests.test_session_trace_ids_prefilter import START, _client, _ids
from tracer.tests.test_span_reader_remap_roots import _remap, _root


@pytest.fixture()
def world():
    client = _client()
    project, other = _ids(2)
    session, old_session, new_session = _ids(3)
    names = [
        "call",
        "chained",
        "alone",
        "nil",
        "old",
        "new",
        "moved",
        "left",
        "gone",
        "other",
    ]
    t = dict(zip(names, _ids(len(names)), strict=True))
    try:
        _root(
            client, project, t["call"], session=session, observation_type="conversation"
        )
        _root(client, project, t["chained"], session=session, observation_type="chain")
        _root(client, project, t["alone"], observation_type="agent")
        # A child span of a trace is never its root.
        _root(
            client,
            project,
            t["alone"],
            observation_type="conversation",
            parent_span_id="root-span",
        )
        # A trace with no session that carries the nil id is no session either.
        _root(client, project, t["nil"], session=NIL_UUID, observation_type="agent")
        # A session the remap consolidated: a root under either id reads as the
        # group's survivor, so the two are one session.
        _root(client, project, t["old"], session=old_session)
        _root(client, project, t["new"], session=new_session)
        _remap(client, "trace_session_id_remap", old_session, new_session)
        # A root that moved to a session in a later version is read as it is now.
        moved = str(uuid.uuid4())
        _root(client, project, t["moved"], id=moved)
        _root(client, project, t["moved"], session=session, id=moved, _version=2)
        # A root that left its session in a later version has none now.
        left = str(uuid.uuid4())
        _root(client, project, t["left"], session=session, id=left)
        _root(client, project, t["left"], id=left, _version=2)
        # A root deleted in its latest version.
        gone = str(uuid.uuid4())
        _root(client, project, t["gone"], id=gone)
        _root(client, project, t["gone"], id=gone, _version=2, is_deleted=1)
        # The same trace id in another project.
        _root(client, other, t["other"], session=session)
        yield {
            "project": project,
            "session": session,
            "consolidated": (old_session, new_session),
            "t": t,
        }
    finally:
        client.command(
            "ALTER TABLE spans DELETE WHERE project_id IN (%(p)s, %(o)s) "
            "SETTINGS mutations_sync=2",
            parameters={"p": project, "o": other},
        )
        client.command(
            "ALTER TABLE trace_session_id_remap DELETE WHERE old_id = %(o)s "
            "SETTINGS mutations_sync=2",
            parameters={"o": old_session},
        )


@pytest.mark.integration
def test_each_trace_reads_its_root_as_it_is_now(world):
    from tracer.services.clickhouse.v2 import get_reader

    t = world["t"]
    with get_reader() as reader:
        units = reader.trace_units(list(t.values()), project_id=world["project"])
        survivor = reader.remap_groups("trace_session_id_remap", world["consolidated"])[
            world["consolidated"][1]
        ]

    assert survivor in world["consolidated"]
    assert {
        trace: (unit.observation_type, unit.session_id) for trace, unit in units.items()
    } == {
        t["call"]: ("conversation", world["session"]),
        t["chained"]: ("chain", world["session"]),
        t["alone"]: ("agent", None),
        t["nil"]: ("agent", None),
        t["old"]: ("agent", survivor),
        t["new"]: ("agent", survivor),
        t["moved"]: ("agent", world["session"]),
        t["left"]: ("agent", None),
    }
    assert {unit.start_time.replace(tzinfo=None) for unit in units.values()} == {
        START.replace(tzinfo=None)
    }


def test_the_read_is_pinned_and_capped():
    """The read binds the project and the traces before anything is read, and carries
    its own time and memory caps: it runs on the shared tables when a person opens Run
    Epoch on an issue."""
    from unittest.mock import MagicMock

    from tracer.services.clickhouse.v2.span_reader import CHSpanReader

    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = MagicMock()
    reader._client.query.return_value.result_rows = []
    trace = str(uuid.uuid4())

    assert reader.trace_units([trace, trace], project_id="p") == {}

    sql = reader._client.query.call_args.args[0]
    kwargs = reader._client.query.call_args.kwargs
    assert "PREWHERE project_id = %(pid)s AND trace_id IN %(tids)s" in sql
    assert "FINAL" not in sql
    assert kwargs["parameters"] == {"pid": "p", "tids": (trace,)}
    assert kwargs["settings"]["max_execution_time"] > 0
    assert kwargs["settings"]["max_memory_usage"] > 0
