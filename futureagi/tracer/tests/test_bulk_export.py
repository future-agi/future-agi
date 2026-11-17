"""Bulk exports use the production job/writer with a network-free destination."""
import csv
from unittest.mock import patch

import pyarrow.parquet as pq
import pytest

from accounts.models import Organization
from tracer.models.project import Project

pytestmark = pytest.mark.django_db


@pytest.fixture
def export_project(organization, workspace):
    return Project.objects.create(
        name="Export test", organization=organization, workspace=workspace,
        trace_type="observe", model_type="GenerativeLLM",
    )


@pytest.mark.parametrize("row_type", ["traces", "spans", "sessions"])
@pytest.mark.parametrize("file_format", ["csv", "parquet"])
def test_all_pages_header_and_transitions(auth_client, user, export_project, tmp_path, django_capture_on_commit_callbacks, row_type, file_format):
    from tracer.models.bulk_export import BulkExportJob
    from tracer.services.bulk_export import ExportPage, run_export
    from tracer.services.export_destination import FilesystemExportDestination

    with patch("tracer.views.bulk_export.process_bulk_export.delay") as enqueue:
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post("/tracer/bulk-exports/", {
                "project_id": str(export_project.id), "row_type": row_type,
                "filters": {"filters": []}, "format": file_format,
            }, format="json")
    assert response.status_code == 202
    job = BulkExportJob.objects.get(id=response.data["id"])
    assert job.status == "queued"
    enqueue.assert_called_once_with(str(job.id))
    destination = FilesystemExportDestination(tmp_path)
    cursors = []

    def read_page(current, cursor):
        current.refresh_from_db()
        assert current.status == "running"
        cursors.append(cursor)
        start = 0 if cursor is None else 500
        end = 500 if cursor is None else 503
        return ExportPage(
            rows=[{"id": str(i), "name": f"row-{i}", **({"late": "yes"} if i == 502 else {})} for i in range(start, end)],
            fields=["id", "name"], next_cursor="page-2" if cursor is None else None,
            has_more=cursor is None, complete=True,
        )

    run_export(str(job.id), destination=destination, read_page=read_page)
    job.refresh_from_db()
    assert cursors == [None, "page-2"]
    assert job.status == "completed"
    assert job.row_count == 503
    assert job.finished_at and job.started_at
    path = tmp_path / job.file_location
    if file_format == "csv":
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            assert reader.fieldnames == ["id", "name", "late"]
    else:
        table = pq.read_table(path)
        assert table.column_names == ["id", "name", "late"]
        rows = table.to_pylist()
    assert len(rows) == 503
    assert rows[-1]["id"] == "502"
    assert rows[-1]["late"] == "yes"
    with patch("tracer.views.bulk_export.get_export_destination", return_value=destination):
        status = auth_client.get(f"/tracer/bulk-exports/{job.id}/")
    assert status.status_code == 200
    assert status.data["download_url"] == destination.url(job.file_location)
    # A duplicate delivery must not rewrite a finished job.
    run_export(str(job.id), destination=destination, read_page=lambda *_: pytest.fail("duplicate read"))


@pytest.mark.parametrize("page_data", [
    {"rows": [{"id": "1"}], "fields": ["id"], "complete": False},
    {"rows": [{"id": "1"}], "fields": ["id"], "has_more": True},
])
def test_incomplete_pages_never_publish(user, export_project, tmp_path, page_data):
    from tracer.models.bulk_export import BulkExportJob
    from tracer.services.bulk_export import ExportPage, run_export
    from tracer.services.export_destination import FilesystemExportDestination

    page = ExportPage(**page_data)
    job = BulkExportJob.objects.create(project=export_project, created_by=user, row_type="traces")
    run_export(str(job.id), destination=FilesystemExportDestination(tmp_path), read_page=lambda *_: page)
    job.refresh_from_db()
    assert job.status == "failed"
    assert not job.file_location
    assert job.error
    assert job.finished_at


def test_other_org_project_and_job_are_not_accessible(auth_client, user, export_project):
    from tracer.models.bulk_export import BulkExportJob

    other = Organization.objects.create(name="Other organization")
    project = Project.no_workspace_objects.create(name="Other", organization=other, trace_type="observe", model_type="GenerativeLLM")
    job = BulkExportJob.objects.create(project=project, created_by=user, row_type="spans")
    with patch("tracer.views.bulk_export.process_bulk_export.delay") as enqueue:
        response = auth_client.post("/tracer/bulk-exports/", {"project_id": str(project.id), "row_type": "spans"}, format="json")
    assert response.status_code == 404
    enqueue.assert_not_called()
    assert auth_client.get(f"/tracer/bulk-exports/{job.id}/").status_code == 404


def test_enqueue_failure_is_visible(auth_client, export_project, django_capture_on_commit_callbacks):
    from tracer.models.bulk_export import BulkExportJob

    with patch("tracer.views.bulk_export.process_bulk_export.delay", side_effect=RuntimeError("private detail")):
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post("/tracer/bulk-exports/", {"project_id": str(export_project.id), "row_type": "traces"}, format="json")
    job = BulkExportJob.objects.get(id=response.data["id"])
    assert job.status == "failed"
    assert "private detail" not in job.error


def test_invalid_filter_options_refused(auth_client, export_project):
    response = auth_client.post("/tracer/bulk-exports/", {
        "project_id": str(export_project.id), "row_type": "traces",
        "filters": {"project_id": str(export_project.id)},
    }, format="json")
    assert response.status_code == 400


@pytest.mark.parametrize("row_type,view_path,method", [
    ("traces", "tracer.views.trace.TraceView", "list_traces_of_session"),
    ("spans", "tracer.views.observation_span.ObservationSpanView", "list_spans_observe"),
    ("sessions", "tracer.views.trace_session.TraceSessionView", "list_sessions"),
])
def test_observe_adapter_carries_filters_and_cursor(user, export_project, row_type, view_path, method):
    from rest_framework.response import Response

    from tracer.models.bulk_export import BulkExportJob
    from tracer.services.bulk_export import read_observe_page

    filters = [{"column_id": "status", "filter_config": {
        "col_type": "NORMAL", "filter_type": "text", "filter_op": "equals", "filter_value": "OK",
    }}]
    job = BulkExportJob.objects.create(project=export_project, created_by=user, row_type=row_type, filters={"filters": filters})
    requests = []

    def page(view, request, **kwargs):
        options = request.validated_query_data
        requests.append(options)
        assert options["filters"] == filters
        assert str(options["project_id"]) == str(export_project.id)
        assert options["cursor_mode"] is True
        assert options["page_wall"] is False
        assert request.organization == export_project.organization
        assert request.workspace == export_project.workspace
        more = not options.get("cursor")
        return Response({"result": {
            "table": [{"id": "1" if more else "2"}], "config": [{"id": "id"}],
            "metadata": {"query_complete": True, "query_exact": True, "ordering_exact": True,
                         "has_more": more, "next_cursor": "next" if more else None,
                         "total_rows_is_lower_bound": more},
        }})

    with patch(f"{view_path}.{method}", new=page):
        first = read_observe_page(job, None)
        second = read_observe_page(job, first.next_cursor)
    assert first.complete and second.complete
    assert requests[1]["cursor"] == "next"
    assert first.fields == ["id"]
    assert not second.has_more


@pytest.mark.parametrize("file_format", ["csv", "parquet"])
def test_empty_exports_keep_header(user, export_project, tmp_path, file_format):
    from tracer.models.bulk_export import BulkExportJob
    from tracer.services.bulk_export import ExportPage, run_export
    from tracer.services.export_destination import FilesystemExportDestination

    job = BulkExportJob.objects.create(project=export_project, created_by=user, row_type="traces", format=file_format)
    destination = FilesystemExportDestination(tmp_path)
    run_export(str(job.id), destination=destination, read_page=lambda *_: ExportPage(rows=[], fields=["trace_id"]))
    job.refresh_from_db()
    assert job.status == "completed"
    assert job.row_count == 0
    if file_format == "csv":
        assert (tmp_path / job.file_location).read_text().strip() == "trace_id"
    else:
        assert pq.read_table(tmp_path / job.file_location).column_names == ["trace_id"]


def test_storage_failure_never_publishes_file(user, export_project, tmp_path):
    from tracer.models.bulk_export import BulkExportJob
    from tracer.services.bulk_export import ExportPage, run_export
    from tracer.services.export_destination import FilesystemExportDestination

    job = BulkExportJob.objects.create(project=export_project, created_by=user, row_type="traces")
    destination = FilesystemExportDestination(tmp_path)
    with patch.object(destination, "write", side_effect=RuntimeError("private storage detail")):
        run_export(str(job.id), destination=destination, read_page=lambda *_: ExportPage(rows=[{"id": "1"}]))
    job.refresh_from_db()
    assert job.status == "failed"
    assert not job.file_location
    assert "private storage detail" not in job.error
