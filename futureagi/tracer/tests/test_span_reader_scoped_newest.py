"""Project-scoped span / trace resolution picks the newest live copy (B01).

A replay, re-import or shared provider account writes one trace / span id into
several projects. ``CHSpanReader.get(project_ids=...)`` and
``newest_trace_project`` must stay inside the given projects, answer the most
recently written copy, and never resurrect a copy whose newest write is a
tombstone. ``first_span_by_type(project_id=...)`` must read only the given
project's copy. Proven against a real ReplacingMergeTree copied from ``spans``.
"""

import uuid

import pytest

from tracer.services.clickhouse.v2.span_reader import CHSpanReader

OLDER = "11111111-1111-4111-8111-111111111111"
NEWER = "22222222-2222-4222-8222-222222222222"
FOREIGN = "33333333-3333-4333-8333-333333333333"
TRACE = "44444444-4444-4444-8444-444444444444"


class _TableClient:
    """Route the reader's ``spans`` reads to a scratch copy of the table."""

    def __init__(self, client, table):
        self._client = client
        self._table = table

    def query(self, sql, parameters=None, settings=None):
        return self._client.query(
            sql.replace("FROM spans ", f"FROM {self._table} "),
            parameters=parameters,
            settings=settings,
        )


@pytest.fixture
def copies():
    import clickhouse_connect

    from conftest import _require_safe_ch25_test_target
    from tracer.services.clickhouse.v2 import get_v2_config

    config = get_v2_config()
    _require_safe_ch25_test_target(host=config["host"], database=config["database"])
    ch = clickhouse_connect.get_client(
        host=config["host"],
        port=config["http_port"],
        username=config["user"],
        password=config["password"],
        database=config["database"],
    )
    table = f"b01_scoped_newest_{uuid.uuid4().hex[:8]}"
    ch.command(f"CREATE TABLE {table} AS spans")

    def insert(
        project_id,
        *,
        version,
        deleted=0,
        span_id="root",
        trace_id=TRACE,
        observation_type="conversation",
        start_time="2026-09-22 16:27:36.543",
    ):
        ch.insert(
            table,
            [
                [
                    project_id,
                    observation_type,
                    "svc",
                    start_time,
                    trace_id,
                    span_id,
                    "",
                    f"copy-{project_id[:1]}",
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
                "parent_span_id",
                "name",
                "is_deleted",
                "_version",
            ],
        )

    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = _TableClient(ch, table)
    try:
        yield reader, insert
    finally:
        ch.command(f"DROP TABLE IF EXISTS {table}")
        ch.close()


@pytest.mark.django_db
def test_scoped_reads_pick_the_newest_live_copy_in_scope(copies):
    reader, insert = copies
    insert(OLDER, version=10)
    insert(NEWER, version=20)
    insert(FOREIGN, version=30)

    in_scope = [OLDER, NEWER]
    assert reader.get("root", project_ids=in_scope).project_id == NEWER
    assert reader.get("root", project_ids=[OLDER]).project_id == OLDER
    assert reader.get("root", project_ids=[]) is None
    assert reader.newest_trace_project(TRACE, in_scope) == NEWER
    assert reader.newest_trace_project(TRACE, [OLDER]) == OLDER
    assert reader.newest_trace_project(TRACE, []) is None

    # The newest write of NEWER's copy is a tombstone: it is not resurrected
    # and the remaining live copy wins.
    insert(NEWER, version=40, deleted=1)
    assert reader.get("root", project_ids=in_scope).project_id == OLDER
    assert reader.newest_trace_project(TRACE, in_scope) == OLDER


@pytest.mark.django_db
def test_scope_by_ids_reads_only_the_given_projects(copies):
    reader, insert = copies
    insert(OLDER, version=10)
    insert(FOREIGN, version=30)

    assert reader.scope_by_ids(["root"], project_ids=[OLDER])["root"].project_id == (
        OLDER
    )
    assert reader.scope_by_ids(["root"], project_ids=[NEWER]) == {}
    assert reader.scope_by_ids(["root"], project_ids=[]) == {}
    assert reader.scope_by_ids(["root"])["root"].project_id in {OLDER, FOREIGN}


@pytest.mark.django_db
def test_batched_newest_projects_answer_the_single_reads(copies):
    """``newest_trace_projects`` / ``newest_span_projects`` resolve many ids in
    one read with the rule ``newest_trace_project`` / ``get(project_ids=...)``
    apply to one: newest live write in scope, ties broken by project id, a
    tombstoned copy never resurrected."""
    reader, insert = copies
    lone_trace = "55555555-5555-4555-8555-555555555555"
    insert(OLDER, version=10)
    insert(NEWER, version=20)
    insert(FOREIGN, version=30)
    insert(OLDER, version=50, span_id="lone", trace_id=lone_trace)
    insert(FOREIGN, version=60, span_id="lone", trace_id=lone_trace)
    insert(OLDER, version=70, span_id="tie", trace_id=lone_trace)
    insert(NEWER, version=70, span_id="tie", trace_id=lone_trace)

    in_scope = [OLDER, NEWER]
    assert reader.newest_trace_projects([TRACE, lone_trace], in_scope) == {
        TRACE: NEWER,
        lone_trace: NEWER,
    }
    assert reader.newest_span_projects(["root", "lone", "tie", "gone"], in_scope) == {
        "root": NEWER,
        "lone": OLDER,
        "tie": NEWER,
    }
    for span_id in ("root", "lone", "tie"):
        assert (
            reader.newest_span_projects([span_id], in_scope)[span_id]
            == reader.get(span_id, project_ids=in_scope).project_id
        )
    assert reader.newest_span_projects(["lone"], [FOREIGN]) == {"lone": FOREIGN}
    assert reader.newest_trace_projects([TRACE], []) == {}
    assert reader.newest_span_projects(["root"], []) == {}
    assert reader.newest_trace_projects([], in_scope) == {}

    insert(NEWER, version=40, deleted=1)
    assert reader.newest_trace_projects([TRACE], in_scope) == {TRACE: OLDER}
    assert reader.newest_span_projects(["root"], in_scope) == {"root": OLDER}


@pytest.mark.django_db
def test_first_span_by_type_reads_only_the_given_project(copies):
    """The feed sidebar reads the model from the cluster project's first LLM
    span. Unscoped, the read keeps whichever copy's LLM span starts first."""
    reader, insert = copies
    for project_id, start_time in (
        (FOREIGN, "2026-09-22 16:27:10.000"),
        (OLDER, "2026-09-22 16:27:20.000"),
    ):
        insert(
            project_id,
            version=10,
            span_id="llm",
            observation_type="llm",
            start_time=start_time,
        )

    assert reader.first_span_by_type(TRACE, "llm").project_id == FOREIGN
    assert reader.first_span_by_type(TRACE, "llm", project_id=OLDER).project_id == (
        OLDER
    )
    assert reader.first_span_by_type(TRACE, "llm", project_id=NEWER) is None
