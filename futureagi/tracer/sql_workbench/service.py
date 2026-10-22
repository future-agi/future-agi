"""Application operations shared by REST, MCP and saved dashboard queries."""

import csv
import io
import os
import time
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.db.models import BigIntegerField, Case, Count, F, Q, Sum, Value, When
from django.utils import timezone

from accounts.models.organization import Organization
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.models.sql_query import SavedSQLQuery, SQLExecution

from .access import AccessDenied, authorize
from .catalog import MAX_CONFIGS, Scope
from .compiler import MAX_RESULT_BYTES, compile_query, validate
from .runner import QueryUnavailable, connection_config


class AdmissionFull(Exception):
    pass


MAX_ACTOR_RESULT_BYTES_24H = 128 * 1024 * 1024
MAX_ORG_RESULT_BYTES_24H = 512 * 1024 * 1024
MAX_ACTOR_QUERIES_24H = 1500
MAX_ORG_QUERIES_24H = 30000
TERMINAL_STATES = ("succeeded", "failed", "cancelled")
# A queued job expires if no worker starts it within this window.
QUEUE_SECONDS = 90
# Measured from the worker's actual start, never from submission: the Temporal
# activity limit, which also bounds the 30-second ClickHouse execution ceiling.
EXECUTION_SECONDS = 45
# A cancel request waits at most this long for the worker to confirm that its
# native query stopped (the watchdog polls every 0.5 s) before it returns. The
# slot stays held either way until that confirmation or the execution deadline.
CANCEL_CONFIRM_SECONDS = 3.0
CANCEL_POLL_SECONDS = 0.2


def active_filter(now):
    """Jobs that hold an admission slot.

    A running job that was cancelled keeps its slot (state ``cancelled`` with no
    ``finished_at``) until its worker confirms the ClickHouse query stopped or
    its execution deadline passes, so a slot is never handed out while the
    previous scan may still be running, and a lost worker cannot hold it forever.
    """
    return Q(state__in=("queued", "running"), deadline__gt=now) | Q(
        state="cancelled", finished_at__isnull=True, deadline__gt=now
    )


def object_id(value):
    """Canonical UUID or ``None``; malformed ids are never sent to a query."""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _resource_limit(name, default):
    value = getattr(settings, name, default)
    if type(value) is not int or value <= 0:
        raise QueryUnavailable(
            "SQL workbench resource budgets must be positive integers."
        )
    return value


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
        recent = Q(created_at__gte=now - timedelta(hours=24))
        active = active_filter(now)
        actor_filter = Q(created_by_id=actor.user_id)
        reservation = Case(
            When(active, then=Value(MAX_RESULT_BYTES)),
            When(recent & Q(state="succeeded"), then=F("output_bytes")),
            default=Value(0),
            output_field=BigIntegerField(),
        )
        # One statement snapshot covers finished output AND in-flight reserves:
        # a worker completing between separate count/sum reads cannot disappear.
        usage = (
            SQLExecution.objects.filter(organization_id=actor.organization_id)
            .filter(recent | active)
            .aggregate(
                org_active=Count("id", filter=active),
                actor_active=Count("id", filter=active & actor_filter),
                org_queries=Count("id", filter=recent),
                actor_queries=Count("id", filter=recent & actor_filter),
                org_bytes=Sum(reservation),
                actor_bytes=Sum(reservation, filter=actor_filter),
            )
        )
        if usage["org_active"] >= 2 or usage["actor_active"]:
            raise AdmissionFull(
                "A query is already running. Cancel it or wait for completion."
            )
        if usage["actor_queries"] >= _resource_limit(
            "SQL_WORKBENCH_MAX_ACTOR_QUERIES_24H", MAX_ACTOR_QUERIES_24H
        ) or usage["org_queries"] >= _resource_limit(
            "SQL_WORKBENCH_MAX_ORG_QUERIES_24H", MAX_ORG_QUERIES_24H
        ):
            raise AdmissionFull(
                "Query audit submission limit reached for the last 24 hours."
            )
        if (usage["actor_bytes"] or 0) + MAX_RESULT_BYTES > _resource_limit(
            "SQL_WORKBENCH_MAX_ACTOR_RESULT_BYTES_24H", MAX_ACTOR_RESULT_BYTES_24H
        ) or (usage["org_bytes"] or 0) + MAX_RESULT_BYTES > _resource_limit(
            "SQL_WORKBENCH_MAX_ORG_RESULT_BYTES_24H", MAX_ORG_RESULT_BYTES_24H
        ):
            raise AdmissionFull(
                "Result storage budget reached for the last 24 hours. Retry after older results expire."
            )
        job = SQLExecution.objects.create(
            project=project,
            organization_id=actor.organization_id,
            workspace_id=actor.workspace_id,
            created_by_id=actor.user_id,
            sql=sql,
            start=start,
            end=end,
            deadline=now + timedelta(seconds=QUEUE_SECONDS),
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
            SQLExecution.objects.filter(id=job.id, state="queued").update(
                state="failed",
                error="Query worker unavailable.",
                finished_at=timezone.now(),
            )
        finally:
            # An accepted workflow may already be running/finished when the
            # dispatch acknowledgement times out. Never overwrite its state.
            try:
                job.refresh_from_db()
            except SQLExecution.DoesNotExist as exc:
                raise QueryUnavailable("Query is no longer available.") from exc

    # Also safe inside a caller-owned transaction / ATOMIC_REQUESTS: a worker
    # must never consume an id whose INSERT is still invisible or may roll back.
    transaction.on_commit(dispatch)
    return job


def execution(actor, project_id, job_id):
    authorize(actor, project_id)
    job_id = object_id(job_id)
    if job_id is None:
        raise AccessDenied("Query not available.")
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


def purge_expired_results(*, batch_size=1000):
    """Expire result bodies; preserve audit metadata unless an operator opted in."""
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("Cleanup batches must contain 1 to 1,000 records.")
    retention_days = getattr(settings, "SQL_WORKBENCH_AUDIT_RETENTION_DAYS", None)
    if retention_days is not None and (
        type(retention_days) is not int or retention_days <= 0
    ):
        raise QueryUnavailable("Audit retention must be unset or a positive day count.")
    now = timezone.now()
    terminal = SQLExecution.objects.filter(
        state__in=TERMINAL_STATES, expires_at__lte=now
    )
    ids = list(
        terminal.exclude(result={})
        .order_by("expires_at")
        .values_list("id", flat=True)[:batch_size]
    )
    erased = SQLExecution.objects.filter(id__in=ids, state__in=TERMINAL_STATES).update(
        result={}, output_bytes=0
    )
    deleted = 0
    if retention_days is not None:
        old_ids = list(
            terminal.filter(created_at__lt=now - timedelta(days=retention_days))
            .order_by("created_at")
            .values_list("id", flat=True)[: max(0, batch_size - erased)]
        )
        if old_ids:
            deleted = SQLExecution.objects.filter(
                id__in=old_ids, state__in=TERMINAL_STATES
            ).delete()[0]
    return {"expired_result_bodies": erased, "old_audit_rows": deleted}


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
    # A queued job never reached ClickHouse, so it is finished at once.
    SQLExecution.objects.filter(id=job.id, state="queued").update(
        state="cancelled", finished_at=timezone.now(), result={}
    )
    # A running scan stops only when the worker's watchdog sends the native
    # cancel and the worker returns. Until it confirms (finished_at) or the
    # execution deadline passes, the job still holds its admission slot.
    if (
        SQLExecution.objects.filter(id=job.id, state="running").update(
            state="cancelled", finished_at=None, result={}
        )
        # Inside a caller's transaction the worker cannot see this cancel yet.
        and not connection.in_atomic_block
    ):
        wait_until = time.monotonic() + CANCEL_CONFIRM_SECONDS
        while time.monotonic() < wait_until:
            if not SQLExecution.objects.filter(
                active_filter(timezone.now()), id=job.id
            ).exists():
                break
            time.sleep(CANCEL_POLL_SECONDS)
    return snapshot(execution(actor, project_id, job_id))


def confirm_stopped(job_id):
    """Worker-side: the native query has ended, so a cancelled job frees its slot."""
    return SQLExecution.objects.filter(
        id=job_id, state="cancelled", finished_at__isnull=True
    ).update(finished_at=timezone.now())


def saved_queries(actor, project_id):
    authorize(actor, project_id)
    return SavedSQLQuery.no_workspace_objects.filter(project_id=project_id).filter(
        Q(created_by_id=actor.user_id) | Q(visibility="project")
    )


def saved_query(actor, project_id, saved_id):
    saved_id = object_id(saved_id)
    if saved_id is None:
        raise AccessDenied("Saved query not available.")
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
    dashboard_id = object_id(dashboard_id)
    if dashboard_id is None:
        raise AccessDenied("Dashboard not available.")
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
