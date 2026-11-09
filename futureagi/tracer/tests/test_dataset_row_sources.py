"""The Observe import worker records where each new row came from.

The kind (trace, span or session) is taken from the import's source context,
never guessed from the spans; the ids come from the ClickHouse span rows the
worker reads. Without a context nothing is recorded and the worker reads and
writes exactly what it did before. Rows and their sources commit or roll back
together.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.row_sources import RowSource
from model_hub.services import row_sources
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _tenant
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.clickhouse.v2 import span_reader
from tracer.services.clickhouse.v2.span_reader import CHSpanReader
from tracer.tasks.dataset import (
    _export_span_field_value,
    _mapped_span_fields,
    _serialize_cell_value,
    process_spans_chunk_task,
)
from tracer.tests._ch_seed import (
    _get_ch_client,
    _pg_row_from_django_span,
    seed_ch_spans,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def row_source_on(settings):
    """This module is the unit's ON arm: the export reads trace_session_id only
    while the server switch is on. OFF (the default) is pinned in
    model_hub/tests/test_dataset_row_source_gate.py."""

    settings.DSX_DATASET_ROW_SOURCE_ENABLED = True


def _observe(tenant, *, with_session=True, spans=2):
    """An Observe project in the tenant's workspace with one trace of spans."""

    project = Project.no_workspace_objects.create(
        name=f"Row source project {uuid.uuid4().hex[:6]}",
        organization=tenant["org"],
        workspace=tenant["workspace"],
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    session = (
        TraceSession.no_workspace_objects.create(project=project, name="chat-1")
        if with_session
        else None
    )
    trace = Trace.no_workspace_objects.create(
        project=project,
        session=session,
        name="Row source trace",
        input={"prompt": "hi"},
        output={"response": "hello"},
    )
    now = timezone.now()
    made = [
        ObservationSpan.no_workspace_objects.create(
            id=f"rs_{i}_{uuid.uuid4().hex[:12]}",
            project=project,
            trace=trace,
            name=f"turn {i}",
            observation_type="llm",
            start_time=now - timedelta(seconds=10 - i),
            end_time=now - timedelta(seconds=9 - i),
            input={"messages": [{"role": "user", "content": f"question {i}"}]},
            output={"choices": [{"message": {"content": f"answer {i}"}}]},
            model="stub-model",
            status="OK",
        )
        for i in range(spans)
    ]
    return project, session, trace, made


def _import_target(tenant, label):
    dataset = Dataset.no_workspace_objects.create(
        name=f"rs-{label}-{uuid.uuid4().hex[:6]}",
        organization=tenant["org"],
        workspace=tenant["workspace"],
        user=tenant["user"],
        source=DatasetSourceChoices.OBSERVE.value,
    )
    columns = [
        Column.no_workspace_objects.create(
            name=name,
            data_type=DataTypeChoices.TEXT.value,
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
        )
        for name in ("input", "output")
    ]
    mapping = [
        {"column_id": str(c.id), "column_name": c.name, "span_field": c.name}
        for c in columns
    ]
    return dataset, mapping


def _context(kind, project, session=None):
    return {
        "v": 1,
        "kind": kind,
        "project_id": str(project.id),
        "session_ids": [str(session.id)] if session is not None else [],
    }


def _run(spans, dataset, mapping, project, tenant, **kwargs):
    return process_spans_chunk_task.run_sync(
        [s.id for s in spans],
        str(dataset.id),
        mapping,
        str(project.id),
        str(tenant["org"].id),
        **kwargs,
    )


@pytest.fixture
def tenant_a(db):
    tenant = _tenant("rs-a")
    yield tenant
    clear_workspace_context()


@pytest.fixture
def tenant_b(db):
    tenant = _tenant("rs-b")
    yield tenant
    clear_workspace_context()


class TestWorkerRecordsRowSources:
    def test_the_kind_comes_from_the_context_for_the_same_spans(
        self, tenant_a, ch_seed
    ):
        project, session, trace, spans = _observe(tenant_a)
        ch_seed(spans)

        for kind in ("trace", "span", "session"):
            dataset, mapping = _import_target(tenant_a, kind)
            result = _run(
                spans,
                dataset,
                mapping,
                project,
                tenant_a,
                source_context=_context(
                    kind, project, session if kind == "session" else None
                ),
            )
            assert result == {"rows_created": 2, "cells_created": 4}
            records = list(RowSource.objects.filter(dataset=dataset))
            assert len(records) == 2
            rows = {r.id for r in Row.no_workspace_objects.filter(dataset=dataset)}
            assert {r.row_id for r in records} == rows
            for record in records:
                assert record.kind == kind
                assert record.trace_id == str(trace.id)
                assert record.span_id in {s.id for s in spans}
                assert str(record.project_id) == str(project.id)
                assert record.session_id == str(session.id)
                assert record.session_missing is False
                assert record.organization_id == tenant_a["org"].id
                assert record.workspace_id == tenant_a["workspace"].id

    def test_no_context_records_nothing_and_reads_only_the_mapped_fields(
        self, tenant_a, ch_seed
    ):
        project, session, _, spans = _observe(tenant_a)
        ch_seed(spans)
        legacy, mapping = _import_target(tenant_a, "legacy")
        mapped = _mapped_span_fields(mapping)
        requested = []
        original = CHSpanReader.export_fields_by_ids

        def spy(reader, span_ids, field_names, **kwargs):
            requested.append(set(field_names))
            return original(reader, span_ids, field_names, **kwargs)

        with patch.object(CHSpanReader, "export_fields_by_ids", spy):
            assert _run(spans, legacy, mapping, project, tenant_a) == {
                "rows_created": 2,
                "cells_created": 4,
            }
            assert requested == [mapped]
            assert not RowSource.objects.filter(dataset=legacy).exists()

            requested.clear()
            sourced, mapping_b = _import_target(tenant_a, "sourced")
            _run(
                spans,
                sourced,
                mapping_b,
                project,
                tenant_a,
                source_context=_context("trace", project),
            )
            assert requested == [
                _mapped_span_fields(mapping_b) | row_sources.ROW_SOURCE_SPAN_FIELDS
            ]

        # The provenance read changes no cell value.
        def values(dataset):
            return sorted(
                (cell.column.name, cell.value)
                for cell in Cell.no_workspace_objects.filter(dataset=dataset)
            )

        assert values(legacy) == values(sourced)

    def test_a_session_import_without_a_session_id_is_recorded_as_missing(
        self, tenant_a, ch_seed
    ):
        project, _, trace, spans = _observe(tenant_a, with_session=False, spans=1)
        ch_seed(spans)
        dataset, mapping = _import_target(tenant_a, "nosession")

        result = _run(
            spans,
            dataset,
            mapping,
            project,
            tenant_a,
            source_context=_context("session", project),
        )

        assert result["rows_created"] == 1
        record = RowSource.objects.get(dataset=dataset)
        assert record.kind == "session"
        assert record.session_id is None
        assert record.session_missing is True
        assert record.trace_id == str(trace.id)
        payload = row_sources.source_payload(record)
        assert payload == {
            "type": "session",
            "label": "Session (no session id)",
            "project_id": str(project.id),
        }

    def test_a_failing_writer_rolls_the_chunk_back(self, tenant_a, ch_seed):
        project, _, _, spans = _observe(tenant_a)
        ch_seed(spans)
        dataset, mapping = _import_target(tenant_a, "rollback")

        with patch.object(
            row_sources,
            "record_row_sources",
            side_effect=RuntimeError("provenance store unavailable"),
        ) as writer:
            with pytest.raises(RuntimeError, match="provenance store unavailable"):
                _run(
                    spans,
                    dataset,
                    mapping,
                    project,
                    tenant_a,
                    source_context=_context("trace", project),
                )
        assert writer.call_count == 1
        assert writer.call_args.kwargs["dataset_id"] == str(dataset.id)
        assert not Row.no_workspace_objects.filter(dataset=dataset).exists()
        assert not Cell.no_workspace_objects.filter(dataset=dataset).exists()
        assert not RowSource.objects.filter(dataset=dataset).exists()

    def test_a_span_outside_the_contexts_project_rolls_the_chunk_back(
        self, tenant_a, ch_seed
    ):
        project, _, _, spans = _observe(tenant_a)
        other, _, _, _ = _observe(tenant_a, spans=1)
        ch_seed(spans)
        dataset, mapping = _import_target(tenant_a, "mismatch")

        with pytest.raises(row_sources.RowSourceContextError):
            _run(
                spans,
                dataset,
                mapping,
                project,
                tenant_a,
                source_context=_context("trace", other),
            )
        assert not Row.no_workspace_objects.filter(dataset=dataset).exists()
        assert not RowSource.objects.filter(dataset=dataset).exists()

    def test_provenance_never_carries_another_tenants_session(
        self, tenant_a, tenant_b, ch_seed
    ):
        project_a, session_a, _, spans_a = _observe(tenant_a, spans=1)
        _, session_b, _, spans_b = _observe(tenant_b, spans=1)
        ch_seed([*spans_a, *spans_b])
        dataset, mapping = _import_target(tenant_a, "two-tenant")

        # Org B's span named in org A's import is not read, so nothing is written.
        with pytest.raises(RuntimeError):
            _run(
                spans_b,
                dataset,
                mapping,
                project_a,
                tenant_a,
                source_context=_context("session", project_a, session_a),
            )
        assert not RowSource.objects.filter(dataset=dataset).exists()

        _run(
            spans_a,
            dataset,
            mapping,
            project_a,
            tenant_a,
            source_context=_context("session", project_a, session_a),
        )
        assert list(
            RowSource.objects.filter(dataset=dataset).values_list(
                "session_id", flat=True
            )
        ) == [str(session_a.id)]
        assert not RowSource.objects.filter(session_id=str(session_b.id)).exists()


class TestWriterAttributesOnlyKnownProjects:
    def test_spans_from_no_known_project_are_refused(self, tenant_a):
        project, _, trace, spans = _observe(tenant_a, spans=1)
        dataset, _ = _import_target(tenant_a, "noproject")
        row = Row.no_workspace_objects.create(dataset=dataset, order=0)
        rows = [(row.id, {"id": spans[0].id, "trace_id": str(trace.id)})]
        context = _context("trace", project)

        with pytest.raises(row_sources.RowSourceContextError):
            row_sources.record_row_sources(
                dataset_id=dataset.id, rows=rows, source_context=context
            )
        assert not RowSource.objects.filter(dataset=dataset).exists()

        # The project the worker read the spans from attributes them.
        assert (
            row_sources.record_row_sources(
                dataset_id=dataset.id,
                rows=rows,
                source_context=context,
                project_id=str(project.id),
            )
            == 1
        )
        assert str(RowSource.objects.get(row=row).project_id) == str(project.id)


class TestExportReadsTheSessionIdOnlyWhenAsked:
    def test_the_session_id_is_exported_only_when_requested(self, tenant_a, ch_seed):
        project, session, _, spans = _observe(tenant_a, spans=1)
        ch_seed(spans)
        span_id = spans[0].id
        with span_reader_for_tests() as reader:
            plain = reader.export_fields_by_ids(
                [span_id], {"input"}, project_id=str(project.id)
            )
            asked = reader.export_fields_by_ids(
                [span_id], {"input", "trace_session_id"}, project_id=str(project.id)
            )
        assert "trace_session_id" not in plain[span_id]
        assert asked[span_id]["trace_session_id"] == str(session.id)


def span_reader_for_tests():
    from tracer.services.clickhouse.v2 import get_reader

    return get_reader()


class _CapturingClient:
    def __init__(self):
        self.queries = []

    def query(self, sql, parameters=None, settings=None):
        self.queries.append((sql, dict(parameters or {})))
        return type("Result", (), {"result_rows": []})()


def _export_sql(field_names, *, without_session_entry=False):
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = _CapturingClient()
    columns = dict(span_reader._EXPORT_COLUMN_SQL)
    if without_session_entry:
        columns.pop("trace_session_id")
    with patch.dict(span_reader._EXPORT_COLUMN_SQL, columns, clear=True):
        reader.export_fields_by_ids(
            ["span-1", "span-2"], set(field_names), project_id="p-1", org_id="o-1"
        )
    return reader._client.queries[0]


class TestExportSqlForExistingCallers:
    def test_every_existing_field_set_reads_byte_identical_sql(self):
        field_sets = [{name} for name in span_reader._EXPORT_FIELD_COLUMNS]
        field_sets.append(set(span_reader._EXPORT_FIELD_COLUMNS))
        field_sets.append({"input", "output"})
        for fields in field_sets:
            assert _export_sql(fields) == _export_sql(
                fields, without_session_entry=True
            ), fields

    def test_the_session_column_is_selected_only_when_requested(self):
        plain, _ = _export_sql({"input", "trace_id", "project_id"})
        asked, params = _export_sql(
            {"input", "trace_id", "project_id", "trace_session_id"}
        )
        session_select = (
            "argMax(toString(trace_session_id), _version) AS trace_session_id"
        )
        assert session_select not in plain
        assert session_select in asked
        # Tenant scoping is the same WHERE clause either way.
        where = "PREWHERE id IN %(ids)s AND project_id = %(pid)s AND org_id = %(oid)s"
        assert where in plain and where in asked
        assert params == {"ids": ("span-1", "span-2"), "pid": "p-1", "oid": "o-1"}


# The export read, as the spec means it: a span's latest version among the
# rows of the import's own project and organization (filtered before the
# versions are aggregated), with the project read from those rows.
_OWN_PROJECT_SQL = (
    "SELECT id, argMax(toString(project_id), _version) AS span_project, "
    "argMax(trace_id, _version) AS span_trace, "
    "argMax(toString(trace_session_id), _version) AS span_session, "
    "argMax(input, _version) AS span_input "
    "FROM spans PREWHERE id IN %(ids)s AND project_id = %(pid)s "
    "AND org_id = %(oid)s GROUP BY id HAVING argMax(is_deleted, _version) = 0"
)
# The same read with the project checked only after aggregation.
_PROJECT_AFTER_AGGREGATION_SQL = (
    "SELECT id, argMax(toString(project_id), _version) AS span_project, "
    "argMax(trace_id, _version) AS span_trace, "
    "argMax(toString(trace_session_id), _version) AS span_session, "
    "argMax(input, _version) AS span_input "
    "FROM spans PREWHERE id IN %(ids)s AND org_id = %(oid)s GROUP BY id "
    "HAVING argMax(is_deleted, _version) = 0 AND span_project = %(pid)s"
)


def _clickhouse_rows(sql, parameters):
    client = _get_ch_client()
    try:
        rows = client.query(sql, parameters=parameters).result_rows
    finally:
        client.close()
    return {
        str(span_id): {
            "project_id": str(project),
            "trace_id": str(trace),
            "session_id": None if session in (None, "", "NULL") else str(session),
            "input": _serialize_cell_value(
                _export_span_field_value({"id": span_id, "input": value}, "input")
            ),
        }
        for span_id, project, trace, session, value in rows
    }


def _recorded(dataset):
    inputs = {
        cell.row_id: cell.value
        for cell in Cell.no_workspace_objects.filter(
            dataset=dataset, column__name="input"
        )
    }
    return {
        record.span_id: {
            "project_id": str(record.project_id),
            "trace_id": record.trace_id,
            "session_id": record.session_id,
            "input": inputs[record.row_id],
        }
        for record in RowSource.objects.filter(dataset=dataset)
    }


def _seed_with_newer_rows_elsewhere(spans, elsewhere):
    """Seed ``spans`` and, for each ``(project, org)``, a NEWER row per span id.

    The other rows share every span id but carry another trace, session and
    input, so any of them that counts shows in the provenance or the cells.
    """

    start = timezone.now() - timedelta(hours=3)
    rows = []
    for span in spans:
        own = _pg_row_from_django_span(span)
        own["created_at"] = start
        rows.append(own)
    for offset, (project, org) in enumerate(elsewhere, start=1):
        for span in spans:
            other = _pg_row_from_django_span(span)
            other.update(
                project_id=str(project.id),
                org_id=str(org.id),
                trace_id=str(uuid.uuid4()),
                trace_session_id=str(uuid.uuid4()),
                input={"messages": [{"role": "user", "content": "elsewhere"}]},
                created_at=start + timedelta(hours=offset),
            )
            rows.append(other)
    return seed_ch_spans(rows, version_from_created_at=True)


class _QueryLog:
    """Wraps the reader's client so each export read's SQL is kept."""

    def __init__(self):
        self.reads = []

    def spy(self):
        log = self
        original = CHSpanReader.export_fields_by_ids

        def export_fields_by_ids(reader, span_ids, field_names, **kwargs):
            real = reader._client

            class Recording:
                def query(self, sql, parameters=None, settings=None):
                    log.reads.append((sql, dict(parameters or {})))
                    return real.query(sql, parameters=parameters, settings=settings)

            reader._client = Recording()
            try:
                return original(reader, span_ids, field_names, **kwargs)
            finally:
                reader._client = real

        return patch.object(CHSpanReader, "export_fields_by_ids", export_fields_by_ids)


class TestTheProjectIsFilteredBeforeAggregation:
    def test_rows_of_the_same_span_id_elsewhere_never_count(
        self, tenant_a, tenant_b, ch_seed
    ):
        project, session, trace, spans = _observe(tenant_a)
        sibling, _, _, _ = _observe(tenant_a, spans=1)
        foreign, _, _, _ = _observe(tenant_b, spans=1)
        seeded = _seed_with_newer_rows_elsewhere(
            spans, [(sibling, tenant_a["org"]), (foreign, tenant_b["org"])]
        )
        assert seeded == 3 * len(spans)
        dataset, mapping = _import_target(tenant_a, "shared-ids")

        _run(
            spans,
            dataset,
            mapping,
            project,
            tenant_a,
            source_context=_context("trace", project),
        )

        recorded = _recorded(dataset)
        parameters = {
            "ids": tuple(s.id for s in spans),
            "pid": str(project.id),
            "oid": str(tenant_a["org"].id),
        }
        expected = _clickhouse_rows(_OWN_PROJECT_SQL, parameters)
        assert recorded == expected
        assert set(recorded) == {s.id for s in spans}
        for values in recorded.values():
            assert values["project_id"] == str(project.id)
            assert values["trace_id"] == str(trace.id)
            assert values["session_id"] == str(session.id)
            assert "elsewhere" not in values["input"]
        # Checked after aggregation, the newer sibling-project rows win the
        # versions and the import's own spans drop out: the fixture tells
        # the two orders apart.
        assert _clickhouse_rows(_PROJECT_AFTER_AGGREGATION_SQL, parameters) == {}

    def test_the_export_filters_project_and_org_before_grouping(
        self, tenant_a, ch_seed
    ):
        project, _, _, spans = _observe(tenant_a)
        ch_seed(spans)
        dataset, mapping = _import_target(tenant_a, "prewhere")
        log = _QueryLog()

        with log.spy():
            _run(
                spans,
                dataset,
                mapping,
                project,
                tenant_a,
                source_context=_context("span", project),
            )

        assert log.reads, "the export read was not reached"
        for sql, parameters in log.reads:
            before, grouped = sql.split(" GROUP BY ", 1)
            prewhere = before.split(" PREWHERE ", 1)[1]
            assert "project_id = %(pid)s" in prewhere
            assert "org_id = %(oid)s" in prewhere
            assert "project_id" not in grouped and "org_id" not in grouped
            assert parameters["pid"] == str(project.id)
            assert parameters["oid"] == str(tenant_a["org"].id)
        assert RowSource.objects.filter(dataset=dataset).count() == len(spans)


def _other_workspace(tenant, *, is_default=False):
    return Workspace.objects.create(
        name=f"Row source workspace {uuid.uuid4().hex[:6]}",
        organization=tenant["org"],
        is_default=is_default,
        is_active=True,
        created_by=tenant["user"],
    )


class TestAnUnfilteredReadIsRefusedBeforeReading:
    @pytest.mark.parametrize(
        "case",
        [
            "no-project",
            "other-project",
            "no-org",
            "other-org",
            "project-in-another-workspace",
            "dataset-in-another-workspace",
            "deleted-project",
        ],
    )
    def test_nothing_is_read_or_written(self, tenant_a, tenant_b, ch_seed, case):
        project, _, _, spans = _observe(tenant_a)
        ch_seed(spans)
        dataset, mapping = _import_target(tenant_a, case)
        task_project, task_org = str(project.id), str(tenant_a["org"].id)
        if case == "no-project":
            task_project = None
        elif case == "other-project":
            task_project = str(_observe(tenant_a, spans=1)[0].id)
        elif case == "no-org":
            task_org = None
        elif case == "other-org":
            task_org = str(tenant_b["org"].id)
        elif case == "project-in-another-workspace":
            Project.no_workspace_objects.filter(id=project.id).update(
                workspace=_other_workspace(tenant_a)
            )
        elif case == "dataset-in-another-workspace":
            Dataset.no_workspace_objects.filter(id=dataset.id).update(
                workspace=_other_workspace(tenant_a)
            )
        elif case == "deleted-project":
            Project.no_workspace_objects.filter(id=project.id).update(deleted=True)
        log = _QueryLog()

        with log.spy(), pytest.raises(row_sources.RowSourceContextError):
            process_spans_chunk_task.run_sync(
                [s.id for s in spans],
                str(dataset.id),
                mapping,
                task_project,
                task_org,
                source_context=_context("trace", project),
            )

        assert log.reads == []
        assert not Row.no_workspace_objects.filter(dataset=dataset).exists()
        assert not RowSource.objects.filter(dataset=dataset).exists()

    @pytest.mark.parametrize("project_workspace", ["unassigned", "same", "default"])
    def test_projects_the_datasets_workspace_sees_are_read(
        self, tenant_a, ch_seed, project_workspace
    ):
        project, _, _, spans = _observe(tenant_a)
        ch_seed(spans)
        dataset, mapping = _import_target(tenant_a, project_workspace)
        if project_workspace == "unassigned":
            Project.no_workspace_objects.filter(id=project.id).update(workspace=None)
        elif project_workspace == "same":
            workspace = _other_workspace(tenant_a)
            Project.no_workspace_objects.filter(id=project.id).update(
                workspace=workspace
            )
            Dataset.no_workspace_objects.filter(id=dataset.id).update(
                workspace=workspace
            )

        result = _run(
            spans,
            dataset,
            mapping,
            project,
            tenant_a,
            source_context=_context("trace", project),
        )

        assert result["rows_created"] == len(spans)
        assert RowSource.objects.filter(dataset=dataset).count() == len(spans)
