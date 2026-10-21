"""Application operations shared by REST, MCP and saved dashboard queries."""

import csv
import io
import os
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models.organization import Organization
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.models.sql_query import SavedSQLQuery, SQLExecution

from .access import AccessDenied, authorize
from .catalog import MAX_CONFIGS, Scope
from .compiler import compile_query, validate
from .runner import QueryUnavailable, connection_config


class AdmissionFull(Exception):
    pass


def query_scope(actor, project, start, end, sql):
    _, references = validate(sql)
    configs = ()
    if any(table.name == "eval_results" for table in references):
        configs = tuple(
            CustomEvalConfig.no_workspace_objects.filter(
                project=project,
                project__organization_id=actor.organization_id,
                project__workspace_id=actor.workspace_id,
            ).values_list("id", flat=True)[: MAX_CONFIGS + 1]
        )
    return Scope(
        actor.organization_id,
        actor.workspace_id,
        project.id,
        start,
        end,
        configs,
        getattr(settings, "CH25_EVAL_LOGGER_TABLE", "tracer_eval_logger"),
        settings.CLICKHOUSE_V2["CH25_DATABASE"],
        os.environ.get("SQL_WORKBENCH_DERIVED_METRICS_ENABLED", "false").lower()
        == "true",
    )


def submit(actor, *, project_id, sql, start, end):
    project = authorize(actor, project_id)
    compile_query(sql, query_scope(actor, project, start, end, sql))
    connection_config()  # Fail before admitting jobs on unconfigured installations.
    now = timezone.now()
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '2s'")
                cursor.execute("SET LOCAL statement_timeout = '5s'")
        # Shared across all API/worker processes. A process-local semaphore would
        # admit too many concurrent scans on a horizontally scaled deployment.
        Organization.objects.select_for_update().get(id=actor.organization_id)
        active = SQLExecution.objects.filter(
            organization_id=actor.organization_id,
            state__in=["queued", "running"],
            deadline__gt=now,
        )
        if active.count() >= 2 or active.filter(created_by_id=actor.user_id).exists():
            raise AdmissionFull(
                "A query is already running. Cancel it or wait for completion."
            )
        job = SQLExecution.objects.create(
            project=project,
            organization_id=actor.organization_id,
            workspace_id=actor.workspace_id,
            created_by_id=actor.user_id,
            sql=sql,
            start=start,
            end=end,
            deadline=now + timedelta(seconds=90),
            expires_at=now + timedelta(hours=24),
        )

    def dispatch():
        from .tasks import execute_sql

        try:
            execute_sql.apply_async(
                args=[str(job.id)],
                task_id=f"sql-query-{job.id}",
                dispatch_timeout_seconds=5,
            )
        except Exception:
            SQLExecution.objects.filter(id=job.id).update(
                state="failed",
                error="Query worker unavailable.",
                finished_at=timezone.now(),
            )
            job.state = "failed"
            job.error = "Query worker unavailable. Try again later."

    # Also safe inside a caller-owned transaction / ATOMIC_REQUESTS: a worker
    # must never consume an id whose INSERT is still invisible or may roll back.
    transaction.on_commit(dispatch)
    return job


def execution(actor, project_id, job_id):
    authorize(actor, project_id)
    job = SQLExecution.objects.filter(
        id=job_id,
        project_id=project_id,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        created_by_id=actor.user_id,
    ).first()
    if job is None:
        raise AccessDenied("Query not available.")
    return job


def snapshot(job, *, include_result=True):
    expired = job.expires_at <= timezone.now()
    stale = job.deadline <= timezone.now() and job.state in {"queued", "running"}
    return {
        "id": str(job.id),
        "project_id": str(job.project_id),
        "sql": job.sql,
        "state": "failed" if stale else job.state,
        "created_at": job.created_at,
        "expires_at": job.expires_at,
        "error": "Query deadline expired." if stale else job.error,
        "result": job.result if include_result and not expired else {},
        "expired": expired,
    }


def history(actor, project_id, offset):
    authorize(actor, project_id)
    jobs = (
        SQLExecution.objects.filter(
            project_id=project_id,
            organization_id=actor.organization_id,
            workspace_id=actor.workspace_id,
            created_by_id=actor.user_id,
        )
        .defer("result")
        .order_by("-created_at")[offset : offset + 21]
    )
    values = list(jobs)
    return {
        "items": [snapshot(job, include_result=False) for job in values[:20]],
        "has_more": len(values) > 20,
    }


def cancel(actor, project_id, job_id):
    job = execution(actor, project_id, job_id)
    SQLExecution.objects.filter(id=job.id, state__in=["queued", "running"]).update(
        state="cancelled", finished_at=timezone.now(), result={}
    )
    return snapshot(execution(actor, project_id, job_id))


def saved_queries(actor, project_id):
    authorize(actor, project_id)
    return SavedSQLQuery.no_workspace_objects.filter(project_id=project_id).filter(
        Q(created_by_id=actor.user_id) | Q(visibility="project")
    )


def saved_query(actor, project_id, saved_id):
    saved = saved_queries(actor, project_id).filter(id=saved_id).first()
    if saved is None:
        raise AccessDenied("Saved query not available.")
    return saved


def save_query(actor, *, project_id, name, sql, visibility, chart, window_hours):
    project = authorize(actor, project_id, write=True)
    validate(sql)
    return SavedSQLQuery.no_workspace_objects.create(
        project=project,
        created_by_id=actor.user_id,
        name=name,
        sql=sql,
        visibility=visibility,
        chart=chart,
        window_hours=window_hours,
    )


def update_saved(
    actor, saved_id, *, project_id, name, sql, visibility, chart, window_hours
):
    authorize(actor, project_id, write=True)
    saved = saved_query(actor, project_id, saved_id)
    if saved.created_by_id != actor.user_id:
        raise AccessDenied("Only the query owner can edit it. Save a copy instead.")
    validate(sql)
    saved.name, saved.sql, saved.visibility = name, sql, visibility
    saved.chart, saved.window_hours = chart, window_hours
    saved.save(
        update_fields=[
            "name",
            "sql",
            "visibility",
            "chart",
            "window_hours",
            "updated_at",
        ]
    )
    return saved


def delete_saved(actor, project_id, saved_id):
    authorize(actor, project_id, write=True)
    saved = saved_query(actor, project_id, saved_id)
    if saved.created_by_id != actor.user_id:
        raise AccessDenied("Only the query owner can delete it.")
    saved.deleted = True
    saved.save(update_fields=["deleted", "updated_at"])


def add_widget(actor, project_id, saved_id, dashboard_id):
    authorize(actor, project_id, write=True)
    saved = saved_query(actor, project_id, saved_id)
    if saved.visibility != "project":
        raise AccessDenied(
            "Share this query with the project before adding it to a dashboard."
        )
    dashboard = Dashboard.no_workspace_objects.filter(
        id=dashboard_id,
        workspace_id=actor.workspace_id,
        workspace__organization_id=actor.organization_id,
    ).first()
    if dashboard is None:
        raise AccessDenied("Dashboard not available.")
    return DashboardWidget.no_workspace_objects.create(
        dashboard=dashboard,
        height=380,
        created_by_id=actor.user_id,
        name=saved.name,
        query_config={
            "source": "sql",
            "saved_query_id": str(saved.id),
            "project_id": str(saved.project_id),
        },
        chart_config=saved.chart,
    )


def export_result(actor, project_id, job_id, format):
    job = execution(actor, project_id, job_id)
    if job.state != "succeeded" or job.expires_at <= timezone.now():
        raise QueryUnavailable("Only completed, unexpired results can be downloaded.")
    columns, rows = job.result["columns"], job.result["rows"]
    names = [column["name"] for column in columns]
    if format == "csv":

        def safe(value):
            # CSV is often opened in spreadsheets: neutralize formula injection,
            # including whitespace-prefixed formulas and malicious column aliases.
            if isinstance(value, str) and value.lstrip().startswith(
                ("=", "+", "-", "@", "\t", "\r")
            ):
                return "'" + value
            return value

        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow([safe(name) for name in names])
        writer.writerows([[safe(value) for value in row] for row in rows])
        return buffer.getvalue().encode("utf-8-sig"), "text/csv"
    import pyarrow as pa
    import pyarrow.parquet as pq

    arrays = []
    for i in range(len(names)):
        values = [row[i] for row in rows]
        if any(isinstance(value, str) for value in values):
            values = [None if value is None else str(value) for value in values]
        arrays.append(pa.array(values))
    table = pa.Table.from_arrays(arrays, names=names)
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="snappy")
    return buffer.getvalue(), "application/vnd.apache.parquet"
