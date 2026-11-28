"""Live ClickHouse 25.3: one trace id in two tenants never crosses.

A private database gets the complete v2 schema (``040_trajectory_items.sql``
included). Two projects in different organizations hold the same trace id,
span id, item scope and item hash, each with its own item content, which is
how a replay, re-import or shared provider account lands in production. Every
span read pinned to one project returns that project's span, hydrated from
that project's items only, and an unscoped read refuses to run. The tracing
tool reads only the verified trace's project.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from django.test import override_settings

from conftest import (
    _ch_test_apply_v2_schema,
    _ch_test_owned_database,
    _open_ch_test_http_client,
)
from tracer.services.clickhouse.v2.span_reader import (
    CHSpanReader,
    UnscopedSpanReadError,
    _active_select_sql,
    _ReadScope,
    _row_to_chspan,
)

pytestmark = pytest.mark.integration

OWN_ORG = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
FOREIGN_ORG = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
FOREIGN = "99999999-9999-4999-8999-999999999999"
SCOPE_ID = "22222222-2222-4222-8222-222222222222"
ITEM_HASH = "c" * 32
START = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
ENVELOPE = json.dumps({"v": 1, "in": {"f": "msg_array"}})


class _ScratchClient:
    """Routes the reader's ``spans`` / ``trajectory_items`` reads to this
    test's scratch copies of those tables."""

    def __init__(self, client, spans_table, items_table):
        self._client = client
        self._spans = spans_table
        self._items = items_table

    def query(self, sql, parameters=None, settings=None):
        sql = sql.replace("FROM spans ", f"FROM {self._spans} ").replace(
            "FROM trajectory_items", f"FROM {self._items}"
        )
        return self._client.query(sql, parameters=parameters, settings=settings)

    def close(self):
        return None  # the fixture owns the client


@pytest.fixture(scope="module")
def ch_tables():
    with _ch_test_owned_database("test_span_tenancy_") as database:
        _ch_test_apply_v2_schema(database)
        client = _open_ch_test_http_client(database=database)
        try:
            assert (
                client.query("SELECT version()").result_rows[0][0].startswith("25.3.")
            )
            yield client
        finally:
            client.close()


@pytest.fixture
def tenancy(ch_tables):
    """``(reader, insert_span, insert_item)`` over fresh scratch tables."""
    suffix = uuid.uuid4().hex[:8]
    spans_table, items_table = f"tenancy_spans_{suffix}", f"tenancy_items_{suffix}"
    ch_tables.command(f"CREATE TABLE {spans_table} AS spans")
    ch_tables.command(f"CREATE TABLE {items_table} AS trajectory_items")

    def insert_span(project_id, org_id, *, trace_id, span_id, preview, items_v1):
        row = {
            "project_id": uuid.UUID(project_id),
            "org_id": uuid.UUID(org_id),
            "observation_type": "llm",
            "service_name": "svc",
            "start_time": START,
            "trace_id": trace_id,
            "id": span_id,
            "parent_span_id": "",
            "name": f"call-{project_id[:4]}",
            "input": preview,
            "output": "ok",
            "is_deleted": 0,
            "_version": 1,
        }
        if items_v1:
            row.update(
                {
                    "input_item_hashes": [ITEM_HASH],
                    "item_scope_id": SCOPE_ID,
                    "payload_encoding": "items_v1",
                    "payload_envelope": ENVELOPE,
                }
            )
        ch_tables.insert(spans_table, [list(row.values())], column_names=list(row))

    def insert_item(project_id, content):
        ch_tables.insert(
            items_table,
            [[uuid.UUID(project_id), SCOPE_ID, ITEM_HASH, content]],
            column_names=["project_id", "scope_id", "item_hash", "content"],
        )

    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = _ScratchClient(ch_tables, spans_table, items_table)
    try:
        yield reader, insert_span, insert_item
    finally:
        ch_tables.command(f"DROP TABLE IF EXISTS {spans_table}")
        ch_tables.command(f"DROP TABLE IF EXISTS {items_table}")


def _message(text):
    return json.dumps({"role": "user", "content": text})


def _seed_shared_trace(insert_span, insert_item, own_project, trace_id, *, items_v1):
    insert_span(
        own_project,
        OWN_ORG,
        trace_id=trace_id,
        span_id="shared-span",
        preview="own preview" if items_v1 else _message("own inline question"),
        items_v1=items_v1,
    )
    insert_span(
        FOREIGN,
        FOREIGN_ORG,
        trace_id=trace_id,
        span_id="shared-span",
        preview="foreign preview" if items_v1 else _message("foreign secret"),
        items_v1=items_v1,
    )
    if items_v1:
        insert_item(own_project, _message("own question"))
        insert_item(FOREIGN, _message("foreign secret"))


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=True)
def test_shared_trace_id_two_projects_hydrates_only_caller_project(tenancy):
    reader, insert_span, insert_item = tenancy
    own = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    _seed_shared_trace(insert_span, insert_item, own, trace_id, items_v1=True)
    own_content = [{"role": "user", "content": "own question"}]

    spans = reader.list_by_trace(trace_id, project_id=own)
    assert [(s.project_id, json.loads(s.input)) for s in spans] == [(own, own_content)]

    # ``project_ids`` (the caller's projects) on the reads that gained it.
    spans = reader.list_by_trace_ids([trace_id], project_ids=[own])
    assert [(s.project_id, json.loads(s.input)) for s in spans] == [(own, own_content)]

    span = reader.get("shared-span", project_id=own)
    assert (span.project_id, json.loads(span.input)) == (own, own_content)

    span = reader.first_span_by_type(trace_id, "llm", project_ids=[own])
    assert (span.project_id, json.loads(span.input)) == (own, own_content)

    tree = reader.child_tree_spans_by_trace_ids([trace_id], project_ids=[own])
    assert [json.loads(d["input"]) for d in tree[trace_id]] == [own_content]

    assert reader.trace_ids_for_span_ids(["shared-span"], project_ids=[own]) == {
        "shared-span": trace_id
    }

    exported = reader.export_fields_by_ids(["shared-span"], {"input"}, project_id=own)
    assert json.loads(exported["shared-span"]["input"]) == own_content

    # The foreign tenant's own read sees only its own content.
    foreign_spans = reader.list_by_trace(trace_id, project_ids=[FOREIGN])
    assert [json.loads(s.input) for s in foreign_spans] == [
        [{"role": "user", "content": "foreign secret"}]
    ]

    with pytest.raises(UnscopedSpanReadError):
        reader.list_by_trace(trace_id)


def _both_tenants_rows(reader, sql_tail, trace_id):
    """Both tenants' rows for ``trace_id``, as a read with no project
    predicate returns them (the input the hydration guard exists for)."""
    return reader._client.query(sql_tail, parameters={"trace_id": trace_id})


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=True)
def test_hydration_guard_leaves_a_row_outside_the_scope_unfilled(tenancy):
    """Defence in depth on live ``trajectory_items``: handed both tenants'
    rows under one tenant's scope, the hydrator fills only that tenant's row;
    the other keeps its stored preview and none of its items are read."""
    reader, insert_span, insert_item = tenancy
    own = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    _seed_shared_trace(insert_span, insert_item, own, trace_id, items_v1=True)
    own_content = [{"role": "user", "content": "own question"}]
    scope = _ReadScope.of(project_id=own)

    rows = _both_tenants_rows(
        reader,
        f"SELECT {_active_select_sql(True)} FROM spans FINAL "
        "WHERE trace_id = %(trace_id)s ORDER BY project_id",
        trace_id,
    ).result_rows
    spans = reader._hydrate_chspans([_row_to_chspan(r) for r in rows], scope=scope)
    by_project = {s.project_id: s.input for s in spans}
    assert set(by_project) == {own, FOREIGN}
    assert json.loads(by_project[own]) == own_content
    assert by_project[FOREIGN] == "foreign preview"

    result = _both_tenants_rows(
        reader,
        "SELECT id, toString(project_id) AS project_id, input, "
        "input_item_hashes, output_item_hashes, tool_def_hashes, "
        "toString(item_scope_id) AS item_scope_id, payload_encoding, "
        "payload_envelope FROM spans FINAL WHERE trace_id = %(trace_id)s",
        trace_id,
    )
    dicts = [dict(zip(result.column_names, r, strict=True)) for r in result.result_rows]
    reader._hydrate_dict_rows(dicts, scope=scope, attr_key="attributes_extra")
    by_project = {d["project_id"]: d["input"] for d in dicts}
    assert json.loads(by_project[own]) == own_content
    assert by_project[FOREIGN] == "foreign preview"


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=False)
def test_shared_trace_id_inline_read_returns_only_caller_project(tenancy):
    """With the trajectory store off (every deployment today), the project
    predicate alone keeps the other tenant's inline content out."""
    reader, insert_span, insert_item = tenancy
    own = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    _seed_shared_trace(insert_span, insert_item, own, trace_id, items_v1=False)

    spans = reader.list_by_trace(trace_id, project_id=own)
    assert [(s.project_id, s.input) for s in spans] == [
        (own, _message("own inline question"))
    ]
    assert reader.list_by_trace(trace_id, project_ids=[]) == []
    with pytest.raises(UnscopedSpanReadError):
        reader.get("shared-span")


@pytest.mark.django_db
@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=True)
def test_foreign_org_span_with_same_trace_id_not_returned(
    tenancy, project, monkeypatch
):
    """The tracing tool verifies the trace in the caller's organization, then
    reads only that trace's project: another organization's span under the
    same trace id is neither listed nor readable."""
    from contextlib import contextmanager

    from ai_tools.base import ToolContext
    from ai_tools.registry import registry
    from tracer.models.trace import Trace

    reader, insert_span, insert_item = tenancy
    trace = Trace.objects.create(project=project, name="shared id")
    trace_id = str(trace.id)
    insert_span(
        str(project.id),
        str(project.organization_id),
        trace_id=trace_id,
        span_id="own-span",
        preview="own preview",
        items_v1=True,
    )
    insert_item(str(project.id), _message("own question"))
    insert_span(
        FOREIGN,
        FOREIGN_ORG,
        trace_id=trace_id,
        span_id="foreign-span",
        preview="foreign preview",
        items_v1=True,
    )
    insert_item(FOREIGN, _message("foreign secret"))

    @contextmanager
    def _reader():
        yield reader

    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", _reader)
    ctx = ToolContext(
        project.workspace.created_by, project.organization, project.workspace
    )
    tool = registry.get("read_trace_span")

    foreign = tool.run({"trace_id": trace_id, "span_id": "foreign-span"}, ctx)
    assert foreign.is_error and foreign.error_code == "NOT_FOUND"
    assert "foreign-span" not in foreign.content.split("Valid span IDs", 1)[1]
    assert "foreign secret" not in foreign.content

    own = tool.run({"trace_id": trace_id, "span_id": "own-span"}, ctx)
    assert not own.is_error
    assert "own question" in own.content
    assert "foreign secret" not in own.content
