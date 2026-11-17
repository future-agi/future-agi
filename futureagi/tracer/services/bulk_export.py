"""Cursor-walk Observe rows to disk, then stream a stable-schema export.

Memory is bounded by a single list page/Parquet row group. A disk JSONL spool
allows columns first seen on later pages to appear in the header without keeping
all rows in memory. Parquet uses nullable UTF-8 columns (nested values are JSON),
matching the display-oriented CSV projection rather than raw OTLP types.
"""
import csv
import json
from dataclasses import dataclass, field
from datetime import timedelta
from inspect import unwrap
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.utils import timezone

from tracer.models.bulk_export import BulkExportJob
from tracer.services.export_destination import (
    PRIVATE_EXPORT_PREFIX, ExportConfigurationError, get_export_destination,
)
from tracer.utils.bounded_csv import _format_csv_cell

PAGE_SIZE = 100
PARQUET_ROW_GROUP_SIZE = 500
JOB_TIMEOUT_SECONDS = 3600
QUEUED_JOB_TIMEOUT_SECONDS = 30 * 60


@dataclass
class ExportPage:
    rows: list[dict]
    fields: list[str] = field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    complete: bool = True


def read_observe_page(job, cursor):
    """Reuse authoritative CH25 filtering, principal binding and signed cursors.

    Do not call the bounded CSV endpoint: it deliberately resets pagination.
    Sorting is presentation only; use the list's stable default keyset ordering.
    """
    from tfc.middleware.workspace_context import workspace_context
    from tracer.serializers.bulk_export import validate_export_query
    from tracer.services.clickhouse.read_budget import ReadDeadline
    from tracer.utils.workspace_scope import project_queryset_for_request
    from tracer.views.observation_span import ObservationSpanView
    from tracer.views.trace import TraceView
    from tracer.views.trace_session import TraceSessionView

    project = job.project
    user = job.created_by
    if (
        user is None
        or not user.is_active
        or not user.can_access_organization(project.organization)
    ):
        raise ValueError("Export principal is no longer available")
    if project.workspace_id and (
        not project.workspace.is_active
        or not user.can_access_workspace(project.workspace)
    ):
        raise ValueError("Export workspace is no longer available")
    options = validate_export_query(job.row_type, job.filters, job.project_id)
    options.update(page_number=0, page_size=PAGE_SIZE, cursor_mode=True, page_wall=False)
    options.pop("sort_params", None)
    if cursor:
        options["cursor"] = cursor
    request = SimpleNamespace(
        user=user, organization=project.organization, workspace=project.workspace,
        validated_query_data=options, query_params={}, data={}, method="GET", auth=None,
    )
    if not project_queryset_for_request(request).filter(pk=project.pk).exists():
        raise ValueError("Export project is no longer available")
    view_class, method = {
        "traces": (TraceView, "list_traces_of_session"),
        "spans": (ObservationSpanView, "list_spans_observe"),
        "sessions": (TraceSessionView, "list_sessions"),
    }[job.row_type]
    view = view_class()
    view.request = request
    view.action = method
    view.kwargs = {}
    with workspace_context(project.workspace, organization=project.organization, user=user):
        # Background pages are not bound by the interactive header's wall,
        # but still have a finite per-page deadline inside the job deadline.
        deadline_key = "_session_list_read_deadline" if job.row_type == "sessions" else "read_deadline"
        response = unwrap(getattr(view_class, method))(
            view, request, **{deadline_key: ReadDeadline.start(60_000)},
        )
    if response.status_code != 200:
        raise ValueError("Observe page could not be read")
    result = response.data.get("result", {})
    metadata = result.get("metadata", {})
    rows = result.get("table", [])
    fields = [column["id"] for column in result.get("config", []) if isinstance(column, dict) and column.get("id")]
    # Require explicit completeness, including membership and ordering. A
    # lower-bound total is fine between pages, never at the end of the chain.
    exact_empty = (
        not rows and metadata.get("total_rows") == 0
        and "query_complete" not in metadata and not metadata.get("has_more")
    )
    complete = (
        (metadata.get("query_complete") is True or exact_empty)
        and metadata.get("query_exact") is not False
        and metadata.get("ordering_exact") is not False
        and not (not metadata.get("has_more") and metadata.get("total_rows_is_lower_bound"))
    )
    return ExportPage(rows=rows, fields=fields, next_cursor=metadata.get("next_cursor"), has_more=bool(metadata.get("has_more")), complete=complete)


def _text(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _write_file(spool, output, fields, file_format):
    with spool.open(encoding="utf-8") as source:
        if file_format == "csv":
            with output.open("w", newline="", encoding="utf-8") as target:
                writer = csv.writer(target)
                writer.writerow([_format_csv_cell(key) for key in fields])
                for line in source:
                    row = json.loads(line)
                    writer.writerow([_format_csv_cell(row.get(key)) for key in fields])
            return
        import pyarrow as pa
        import pyarrow.parquet as pq

        schema = pa.schema([(key, pa.string()) for key in fields])
        with pq.ParquetWriter(output, schema) as writer:
            batch = []
            for line in source:
                row = json.loads(line)
                batch.append({key: _text(row.get(key)) for key in fields})
                if len(batch) == PARQUET_ROW_GROUP_SIZE:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    batch.clear()
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))


def fail_export(job_id, message="Export could not be completed. Please retry."):
    now = timezone.now()
    BulkExportJob.objects.filter(pk=job_id, status__in=["queued", "running"]).update(
        status="failed", error=message, finished_at=now, updated_at=now,
    )


def expire_queued_export(job_id, *, now=None):
    """Atomically settle a lost/late one-shot enqueue without failing a worker
    that already claimed it. Scheduled windows have their own recovery path.
    """
    now = now or timezone.now()
    return BulkExportJob.objects.filter(
        pk=job_id, schedule__isnull=True, status="queued",
        created_at__lte=now - timedelta(seconds=QUEUED_JOB_TIMEOUT_SECONDS),
    ).update(
        status="failed", error="Export queue wait timed out. Please retry.",
        finished_at=now, updated_at=now,
    )


def run_export(job_id, *, destination=None, read_page=None):
    """Claim once; expose no file until every page and upload has succeeded."""
    now = timezone.now()
    expire_queued_export(job_id, now=now)
    if not BulkExportJob.objects.filter(pk=job_id, status="queued").update(
        status="running", started_at=now, updated_at=now,
    ):
        return
    try:
        job = BulkExportJob.objects.select_related(
            "project__organization", "project__workspace", "created_by",
        ).get(pk=job_id)
        destination = destination or get_export_destination(job)
        read_page = read_page or read_observe_page
        cursor = None
        row_count = 0
        fields = {}
        with TemporaryDirectory(prefix="observe-export-") as directory:
            spool = Path(directory) / "rows.jsonl"
            with spool.open("w", encoding="utf-8") as target:
                while True:
                    if (timezone.now() - now).total_seconds() >= JOB_TIMEOUT_SECONDS:
                        raise ValueError("Export deadline exceeded")
                    page = read_page(job, cursor)
                    if not page.complete or (page.has_more and (not page.next_cursor or page.next_cursor == cursor)):
                        raise ValueError("Export page is incomplete or cannot continue")
                    if page.next_cursor and not page.has_more:
                        raise ValueError("Export continuation is inconsistent")
                    fields.update(dict.fromkeys(page.fields))
                    for row in page.rows:
                        fields.update(dict.fromkeys(row))
                        target.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")
                    row_count += len(page.rows)
                    BulkExportJob.objects.filter(pk=job_id, status="running").update(row_count=row_count, updated_at=timezone.now())
                    if not page.has_more:
                        break
                    cursor = page.next_cursor
            if not fields:
                fields[{"traces": "trace_id", "spans": "span_id", "sessions": "session_id"}[job.row_type]] = None
            output = Path(directory) / f"export.{job.format}"
            _write_file(spool, output, list(fields), job.format)
            prefix = "observe-exports/" if job.destination else PRIVATE_EXPORT_PREFIX
            location = f"{prefix}{job.project.organization_id}/{job.project_id}/{job.id}.{job.format}"
            with output.open("rb") as stream:
                destination.write(location, stream)
            from tracer.services.export_schedule import complete_scheduled_export

            complete_scheduled_export(job, location=location, row_count=row_count)
    except ExportConfigurationError as exc:
        fail_export(job_id, str(exc))
    except Exception:
        # Never persist vendor errors, filter values or credentials in status.
        fail_export(job_id)
