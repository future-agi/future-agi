"""Project-scoped span / trace resolution picks the newest live copy (B01).

A replay, re-import or shared provider account writes one trace / span id into
several projects. ``CHSpanReader.get(project_ids=...)`` and
``newest_trace_project`` must stay inside the given projects, answer the most
recently written copy, and never resurrect a copy whose newest write is a
tombstone. Proven against a real ReplacingMergeTree copied from ``spans``.
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

    def insert(project_id, *, version, deleted=0, span_id="root"):
        ch.insert(
            table,
            [
                [
                    project_id,
                    "conversation",
                    "svc",
                    "2026-09-22 16:27:36.543",
                    TRACE,
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
