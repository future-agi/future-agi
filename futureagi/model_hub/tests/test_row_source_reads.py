"""Dataset reads project each row's import source as ``row_meta.source``.

get-row-data, the public row read and get-dataset-table show the source a
row was imported from, scoped to the dataset's organization and workspace.
The single-row reads also say whether the source is still in Observe: a
deleted or foreign trace, span, session or project reads as ``expired``.
Rows without a source keep their old shape.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status

from model_hub.models.ai_model import AIModel
from model_hub.models.row_sources import RowSource
from model_hub.services import row_sources
from model_hub.tests.test_dataset_row_read import _second_workspace
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.tests._ch_seed import (
    _get_ch_client,
    seed_ch_spans,
    seed_ch_trace_sessions,
    truncate_ch_spans,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

ROW_READ = "/model-hub/datasets/{dataset_id}/rows/{row_id}/"


@pytest.fixture(autouse=True)
def row_source_on(settings):
    """This module is the unit's ON arm: row sources are read only while the
    server switch is on. OFF (the default) is pinned in
    test_dataset_row_source_gate.py."""

    settings.DSX_DATASET_ROW_SOURCE_ENABLED = True


@pytest.fixture
def tenants(db):
    a = _tenant("src-a")
    b = _tenant("src-b")
    clients = {"a": _client(a), "b": _client(b)}
    yield a, b, clients
    for client in clients.values():
        client.stop_workspace_injection()
    clear_workspace_context()
    truncate_ch_spans()


def _observe(tenant, *, session=False):
    project = Project.no_workspace_objects.create(
        name=f"Source project {uuid.uuid4().hex[:6]}",
        organization=tenant["org"],
        workspace=tenant["workspace"],
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    trace_session = (
        TraceSession.no_workspace_objects.create(project=project, name="chat-7")
        if session
        else None
    )
    trace = Trace.no_workspace_objects.create(
        project=project, session=trace_session, name="Source trace"
    )
    now = timezone.now()
    span = ObservationSpan.no_workspace_objects.create(
        id=f"src_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="answer",
        observation_type="llm",
        start_time=now - timedelta(seconds=2),
        end_time=now - timedelta(seconds=1),
        input={"q": "refund window?"},
        output={"a": "30 days"},
        status="OK",
    )
    return project, trace_session, trace, span


def _record(tenant, row, kind, project, trace, span, trace_session=None):
    """Write the row's source through the import worker's writer."""

    written = row_sources.record_row_sources(
        dataset_id=tenant["dataset"].id,
        rows=[
            (
                row.id,
                {
                    "id": span.id,
                    "trace_id": str(trace.id),
                    "project_id": str(project.id),
                    "trace_session_id": (
                        str(trace_session.id) if trace_session else None
                    ),
                },
            )
        ],
        source_context={
            "v": 1,
            "kind": kind,
            "project_id": str(project.id),
            "session_ids": [str(trace_session.id)] if trace_session else [],
        },
    )
    assert written == 1


def _current(client, dataset_id, row_id):
    response = client.post(
        f"/model-hub/develops/{dataset_id}/get-row-data/",
        {"row_id": str(row_id)},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()["result"]["current"]


def _read(client, dataset_id, row_id):
    return client.get(ROW_READ.format(dataset_id=dataset_id, row_id=row_id))


def _source(client, tenant, row):
    return _current(client, tenant["dataset"].id, row.id)["row_meta"]["source"]


def _delete_session_in_clickhouse(trace_session):
    client = _get_ch_client()
    try:
        client.insert(
            "trace_sessions",
            [
                (
                    str(trace_session.project_id),
                    str(trace_session.id),
                    trace_session.name or "",
                    datetime.now(UTC) - timedelta(minutes=5),
                    datetime.now(UTC) + timedelta(minutes=5),
                    1,
                )
            ],
            column_names=[
                "project_id",
                "trace_session_id",
                "external_session_id",
                "first_seen",
                "version",
                "is_deleted",
            ],
        )
    finally:
        client.close()


class TestRowSourceProjection:
    def test_row_data_and_the_row_read_show_a_live_trace_source(self, tenants):
        a, _, clients = tenants
        project, _, trace, span = _observe(a)
        seed_ch_spans([span])
        sourced, plain = a["rows"]
        _record(a, sourced, "trace", project, trace, span)

        current = _current(clients["a"], a["dataset"].id, sourced.id)
        assert current["row_meta"]["source"] == {
            "type": "trace",
            "label": f"Trace {str(trace.id)[:8]}",
            "project_id": str(project.id),
            "trace_id": str(trace.id),
            "span_id": span.id,
            "expired": False,
        }

        read = _read(clients["a"], a["dataset"].id, sourced.id)
        assert read.status_code == status.HTTP_200_OK, read.content
        result = read.json()["result"]
        assert result["row_meta"] == current["row_meta"]
        assert result["row_id"] == current["row_id"] == str(sourced.id)
        assert result["cells"] == {
            key: value
            for key, value in current.items()
            if key not in {"row_id", "order", "row_meta"}
        }
        assert result["cells"][str(a["column"].id)]["cell_value"] == "src-a-value-0"

        # A row with no source keeps get-row-data's old shape.
        assert "row_meta" not in _current(clients["a"], a["dataset"].id, plain.id)
        plain_read = _read(clients["a"], a["dataset"].id, plain.id).json()
        assert plain_read["result"]["row_meta"] == {}

    def test_table_rows_carry_the_source_from_one_query(self, tenants):
        a, _, clients = tenants
        project, _, trace, span = _observe(a)
        sourced, plain = a["rows"]
        _record(a, sourced, "span", project, trace, span)

        response = clients["a"].get(
            f"/model-hub/develops/{a['dataset'].id}/get-dataset-table/"
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        rows = {r["row_id"]: r for r in response.json()["result"]["table"]}
        # The span was never seen in Observe: the grid row reads as expired.
        assert rows[str(sourced.id)]["row_meta"]["source"] == {
            "type": "span",
            "label": f"Span {span.id[:8]}",
            "project_id": str(project.id),
            "trace_id": str(trace.id),
            "span_id": span.id,
            "expired": True,
        }
        assert "row_meta" not in rows[str(plain.id)]

        page = [{"row_id": str(sourced.id)}, {"row_id": str(plain.id)}]
        with CaptureQueriesContext(connection) as queries:
            row_sources.attach_row_sources(page, dataset=a["dataset"])
        assert len(queries.captured_queries) == 1
        assert page[0]["row_meta"]["source"]["span_id"] == span.id

    def test_a_deleted_span_or_project_reads_as_expired(self, tenants):
        a, _, clients = tenants
        project, _, trace, span = _observe(a)
        row = a["rows"][0]
        _record(a, row, "trace", project, trace, span)

        # Never seen in Observe (or already gone): the saved copy only.
        assert _source(clients["a"], a, row)["expired"] is True
        seed_ch_spans([span])
        assert _source(clients["a"], a, row)["expired"] is False
        truncate_ch_spans()
        assert _source(clients["a"], a, row)["expired"] is True

        seed_ch_spans([span])
        Project.no_workspace_objects.filter(id=project.id).update(deleted=True)
        source = _source(clients["a"], a, row)
        assert source["expired"] is True
        read = _read(clients["a"], a["dataset"].id, row.id).json()["result"]
        assert read["row_meta"]["source"]["expired"] is True

    def test_a_session_source_expires_when_the_session_is_deleted(self, tenants):
        a, _, clients = tenants
        project, trace_session, trace, span = _observe(a, session=True)
        seed_ch_trace_sessions([trace_session])
        row = a["rows"][0]
        _record(a, row, "session", project, trace, span, trace_session)

        source = _source(clients["a"], a, row)
        assert source == {
            "type": "session",
            "label": f"Session {str(trace_session.id)[:8]}",
            "project_id": str(project.id),
            "session_id": str(trace_session.id),
            "expired": False,
        }
        assert "trace_id" not in source

        _delete_session_in_clickhouse(trace_session)
        assert _source(clients["a"], a, row)["expired"] is True

    def test_liveness_never_reads_another_tenants_span(self, tenants):
        a, b, clients = tenants
        project_a, _, trace_a, _ = _observe(a)
        _, _, _, span_b = _observe(b)
        seed_ch_spans([span_b])
        row = a["rows"][0]
        # A record naming org B's span id is checked in org A's Observe only.
        RowSource.objects.create(
            row=row,
            dataset=a["dataset"],
            organization=a["org"],
            workspace=a["workspace"],
            kind="span",
            project_id=project_a.id,
            trace_id=str(trace_a.id),
            span_id=span_b.id,
        )
        assert _source(clients["a"], a, row)["expired"] is True

    def test_unavailable_liveness_leaves_expired_unset(self, tenants):
        a, _, clients = tenants
        project, _, trace, span = _observe(a)
        row = a["rows"][0]
        _record(a, row, "trace", project, trace, span)

        with patch(
            "tracer.services.clickhouse.v2.get_reader",
            side_effect=ConnectionError("clickhouse down"),
        ) as reader:
            source = _source(clients["a"], a, row)
        assert reader.called
        assert "expired" not in source
        assert source["trace_id"] == str(trace.id)


class TestRowSourceTenancy:
    def test_other_tenants_and_workspaces_get_404_and_no_source(self, tenants):
        a, b, clients = tenants
        project, _, trace, span = _observe(a)
        seed_ch_spans([span])
        row = a["rows"][0]
        _record(a, row, "trace", project, trace, span)
        assert _read(clients["a"], a["dataset"].id, row.id).status_code == 200

        foreign = _read(clients["b"], a["dataset"].id, row.id)
        assert foreign.status_code == status.HTTP_404_NOT_FOUND
        assert str(trace.id) not in foreign.content.decode()
        assert span.id not in foreign.content.decode()
        row_data = clients["b"].post(
            f"/model-hub/develops/{a['dataset'].id}/get-row-data/",
            {"row_id": str(row.id)},
            format="json",
        )
        assert row_data.status_code == status.HTTP_404_NOT_FOUND
        assert str(trace.id) not in row_data.content.decode()

        workspace, _, _ = _second_workspace(a, "src")
        clients["a"].set_workspace(workspace)
        try:
            hidden = _read(clients["a"], a["dataset"].id, row.id)
            assert hidden.status_code == status.HTTP_404_NOT_FOUND
            assert str(trace.id) not in hidden.content.decode()
        finally:
            clients["a"].set_workspace(a["workspace"])

    def test_a_record_outside_the_datasets_tenant_is_never_projected(self, tenants):
        a, b, clients = tenants
        project, _, trace, span = _observe(a)
        row = a["rows"][1]
        RowSource.objects.create(
            row=row,
            dataset=a["dataset"],
            organization=b["org"],
            workspace=b["workspace"],
            kind="trace",
            project_id=project.id,
            trace_id=str(trace.id),
            span_id=span.id,
        )
        assert "row_meta" not in _current(clients["a"], a["dataset"].id, row.id)
        result = _read(clients["a"], a["dataset"].id, row.id).json()["result"]
        assert result["row_meta"] == {}


# row_meta.source as the dataset read contract declares it.
SOURCE_KEYS = {
    "type",
    "label",
    "trace_id",
    "span_id",
    "project_id",
    "session_id",
    "file_name",
    "expired",
}


def _table_source(client, tenant, row):
    response = client.get(
        f"/model-hub/develops/{tenant['dataset'].id}/get-dataset-table/"
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    rows = {r["row_id"]: r for r in response.json()["result"]["table"]}
    return rows[str(row.id)]["row_meta"]["source"]


class _ExportCalls:
    def __init__(self):
        self.calls = []

    def spy(self):
        from tracer.services.clickhouse.v2.span_reader import CHSpanReader

        calls = self.calls
        original = CHSpanReader.export_fields_by_ids

        def export_fields_by_ids(reader, span_ids, field_names, **kwargs):
            calls.append((list(span_ids), set(field_names), dict(kwargs)))
            return original(reader, span_ids, field_names, **kwargs)

        return patch.object(CHSpanReader, "export_fields_by_ids", export_fields_by_ids)


class TestRowSourceContractShape:
    def test_every_kind_reads_with_the_contract_keys_and_its_project(self, tenants):
        a, _, clients = tenants
        project, trace_session, trace, span = _observe(a, session=True)
        _, _, _, bare = _observe(a)
        seed_ch_spans([span])
        seed_ch_trace_sessions([trace_session])
        trace_row, session_row = a["rows"]
        _record(a, trace_row, "trace", project, trace, span)
        _record(a, session_row, "session", project, trace, span, trace_session)
        # A session import whose span carried no session id.
        missing = row_sources.record_row_sources(
            dataset_id=a["dataset"].id,
            rows=[
                (
                    _extra_row(a).id,
                    {"id": bare.id, "trace_id": str(bare.trace_id)},
                )
            ],
            source_context={
                "v": 1,
                "kind": "session",
                "project_id": str(bare.project_id),
                "session_ids": [],
            },
            project_id=str(bare.project_id),
        )
        assert missing == 1
        records = RowSource.objects.filter(dataset=a["dataset"])
        assert records.count() == 3

        for record in records:
            row_id = record.row_id
            current = _current(clients["a"], a["dataset"].id, row_id)
            read = _read(clients["a"], a["dataset"].id, row_id).json()["result"]
            table = clients["a"].get(
                f"/model-hub/develops/{a['dataset'].id}/get-dataset-table/"
            )
            in_table = {r["row_id"]: r for r in table.json()["result"]["table"]}[
                str(row_id)
            ]
            for source in (
                current["row_meta"]["source"],
                read["row_meta"]["source"],
                in_table["row_meta"]["source"],
            ):
                assert set(source) <= SOURCE_KEYS, set(source) - SOURCE_KEYS
                assert {"type", "label", "project_id"} <= set(source)
                assert source["type"] == record.kind
                assert source["project_id"] == str(record.project_id)
                assert isinstance(source.get("expired"), bool)

    def test_a_session_without_a_session_id_reads_as_a_bare_session(self, tenants):
        a, _, clients = tenants
        project, _, trace, span = _observe(a)
        row = a["rows"][0]
        row_sources.record_row_sources(
            dataset_id=a["dataset"].id,
            rows=[(row.id, {"id": span.id, "trace_id": str(trace.id)})],
            source_context={
                "v": 1,
                "kind": "session",
                "project_id": str(project.id),
                "session_ids": [],
            },
            project_id=str(project.id),
        )
        assert RowSource.objects.get(row=row).session_missing is True
        assert _source(clients["a"], a, row) == {
            "type": "session",
            "label": "Session (no session id)",
            "project_id": str(project.id),
            "expired": True,
        }


def _extra_row(tenant):
    from model_hub.models.develop_dataset import Cell, Row

    row = Row.no_workspace_objects.create(dataset=tenant["dataset"], order=99)
    Cell.no_workspace_objects.create(
        dataset=tenant["dataset"],
        row=row,
        column=tenant["column"],
        value="extra-value",
    )
    return row


class TestGridRowsSayWhetherTheSourceExpired:
    def test_a_table_page_reads_liveness_once_per_project(self, tenants):
        a, _, clients = tenants
        project, _, trace, span = _observe(a)
        _, _, _, gone = _observe(a)
        seed_ch_spans([span])
        live_row, gone_row = a["rows"]
        _record(a, live_row, "trace", project, trace, span)
        _record(a, gone_row, "span", gone.project, gone.trace, gone)

        assert _table_source(clients["a"], a, live_row)["expired"] is False
        assert _table_source(clients["a"], a, gone_row)["expired"] is True

        page = [{"row_id": str(r.id)} for r in a["rows"]]
        calls = _ExportCalls()
        with calls.spy(), CaptureQueriesContext(connection) as queries:
            row_sources.attach_row_sources(page, dataset=a["dataset"], live=True)
        # One query for the records, one for their projects; one ClickHouse
        # read per project on the page, filtered on that project.
        assert len(queries.captured_queries) == 2
        assert sorted(c[2]["project_id"] for c in calls.calls) == sorted(
            [str(project.id), str(gone.project_id)]
        )
        for _, fields, kwargs in calls.calls:
            assert fields == {"id"}
            assert kwargs["org_id"] == str(a["org"].id)
        assert [p["row_meta"]["source"]["expired"] for p in page] == [False, True]

    def test_a_page_from_one_project_costs_one_read(self, tenants):
        a, _, clients = tenants
        project, _, trace, span = _observe(a)
        seed_ch_spans([span])
        rows = [*a["rows"], _extra_row(a)]
        for row in rows:
            _record(a, row, "trace", project, trace, span)
        calls = _ExportCalls()
        with calls.spy():
            response = clients["a"].get(
                f"/model-hub/develops/{a['dataset'].id}/get-dataset-table/"
            )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert len(calls.calls) == 1
        assert sorted(calls.calls[0][0]) == [span.id] * len(rows)
        table = {r["row_id"]: r for r in response.json()["result"]["table"]}
        for row in rows:
            assert table[str(row.id)]["row_meta"]["source"]["expired"] is False


class TestLivenessIsCheckedInTheRecordsOwnProject:
    def test_a_span_id_live_only_in_another_project_reads_as_expired(self, tenants):
        a, _, clients = tenants
        project, _, trace, span = _observe(a)
        sibling, _, _, sibling_span = _observe(a)
        row = a["rows"][0]
        _record(a, row, "trace", project, trace, span)
        # The same span id lives in another project of the same organization;
        # the import's own project no longer has it.
        shadow = _pg_row(sibling_span)
        shadow["id"] = span.id
        seed_ch_spans([shadow])
        assert _source(clients["a"], a, row)["expired"] is True
        assert _table_source(clients["a"], a, row)["expired"] is True

        seed_ch_spans([span])
        assert _source(clients["a"], a, row)["expired"] is False
        assert sibling.id != project.id

    def test_a_session_live_only_in_another_project_reads_as_expired(self, tenants):
        a, _, clients = tenants
        project, _, trace, span = _observe(a)
        sibling, sibling_session, sibling_trace, sibling_span = _observe(
            a, session=True
        )
        seed_ch_trace_sessions([sibling_session])
        own_row, sibling_row = a["rows"]
        # A record that names the sibling project's session under its own
        # project, beside a record of the sibling project itself.
        _record(
            a,
            sibling_row,
            "session",
            sibling,
            sibling_trace,
            sibling_span,
            sibling_session,
        )
        RowSource.objects.create(
            row=own_row,
            dataset=a["dataset"],
            organization=a["org"],
            workspace=a["workspace"],
            kind="session",
            project_id=project.id,
            trace_id=str(trace.id),
            span_id=span.id,
            session_id=str(sibling_session.id),
        )
        assert _source(clients["a"], a, sibling_row)["expired"] is False
        assert _source(clients["a"], a, own_row)["expired"] is True
        # A page holding both records reads the session once for both
        # projects; each record is still live only in its own project.
        assert _table_source(clients["a"], a, sibling_row)["expired"] is False
        assert _table_source(clients["a"], a, own_row)["expired"] is True


def _pg_row(span):
    from tracer.tests._ch_seed import _pg_row_from_django_span

    return _pg_row_from_django_span(span)
