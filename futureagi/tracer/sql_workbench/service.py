"""Application operations shared by REST, MCP and saved dashboard queries."""

import csv
import hashlib
import io
import json
import os
import time
from datetime import UTC, timedelta
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.db.models import BigIntegerField, Case, Count, F, Q, Sum, Value, When
from django.utils import timezone

from accounts.models.organization import Organization
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.models.sql_query import SavedSQLQuery, SQLExecution

from .access import AccessDenied, authorization_fingerprint, authorize
from .catalog import (
    CATALOG_VERSION,
    EVAL_CONFIG_LIMIT_MESSAGE,
    MAX_CONFIGS,
    EvalConfigLimitExceeded,
    Scope,
)
from .compiler import MAX_RESULT_BYTES, MAX_RESULT_ROWS, compile_query, validate
from .runner import QUERY_SETTINGS, QueryUnavailable, connection_config


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
# Result reuse: an identical request by the same person within this many
# seconds returns their completed result without a new scan (0 turns it off).
RESULT_CACHE_SECONDS = 60
MAX_RESULT_CACHE_SECONDS = 3600


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


def eval_config_scope(project):
    """All configurations the project owns, deleted ones included and flagged.

    Ownership is the configuration's project, which a soft delete does not
    change, so historical results stay attributable. More than ``MAX_CONFIGS``
    raises a user-visible limit instead of returning a partial dataset.
    """
    rows = list(
        CustomEvalConfig._base_manager.filter(
            project=project,
            project__organization_id=project.organization_id,
            project__workspace_id=project.workspace_id,
        )
        .order_by("id")
        .values_list("id", "deleted")[: MAX_CONFIGS + 1]
    )
    if len(rows) > MAX_CONFIGS:
        raise EvalConfigLimitExceeded(EVAL_CONFIG_LIMIT_MESSAGE)
    return (
        tuple(config_id for config_id, _ in rows),
        tuple(config_id for config_id, deleted in rows if deleted),
    )


def eval_results_unavailable_reason(project):
    try:
        eval_config_scope(project)
    except EvalConfigLimitExceeded as exc:
        return str(exc)
    return ""


def _resource_limit(name, default):
    value = getattr(settings, name, default)
    if type(value) is not int or value <= 0:
        raise QueryUnavailable(
            "SQL workbench resource budgets must be positive integers."
        )
    return value


def query_scope(actor, project, start, end, sql):
    _, references = validate(sql)
    if (
        project.organization_id != actor.organization_id
        or project.workspace_id != actor.workspace_id
    ):
        raise AccessDenied("Project not available.")
    configs, deleted = (), ()
    if any(table.name == "eval_results" for table in references):
        configs, deleted = eval_config_scope(project)
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
        deleted,
    )


def result_cache_seconds():
    value = getattr(
        settings, "SQL_WORKBENCH_RESULT_CACHE_SECONDS", RESULT_CACHE_SECONDS
    )
    if type(value) is not int or not 0 <= value <= MAX_RESULT_CACHE_SECONDS:
        raise QueryUnavailable("SQL workbench result reuse must be 0 to 3,600 seconds.")
    return value


def result_cache_key(actor, project, scope, sql):
    """Everything a result depends on, and everything its reader's access rests on.

    The person (and organization, workspace and project), their current
    membership rows, the catalog version, the query as parsed and re-rendered
    (so whitespace, comments and keyword case do not matter), the exact UTC
    window, the evaluation configurations in scope, the source database and
    store, and the server limits. Any change is a miss. Never substitute a
    different time predicate, even within the same minute.
    """

    tree, _ = validate(sql)
    payload = {
        "catalog_version": CATALOG_VERSION,
        "actor": [
            str(actor.user_id),
            str(actor.organization_id),
            str(actor.workspace_id),
        ],
        "project": str(project.id),
        "access": authorization_fingerprint(actor),
        "sql": tree.sql(dialect="clickhouse", comments=False),
        "window_exact": [
            value.astimezone(UTC).isoformat(timespec="microseconds")
            for value in (scope.start, scope.end)
        ],
        "eval_configs": sorted(str(value) for value in scope.eval_config_ids),
        "deleted_eval_configs": sorted(
            str(value) for value in scope.deleted_eval_config_ids
        ),
        "eval_table": scope.eval_table,
        "database": scope.database,
        # A deployment can switch stores or service principals while snapshots
        # remain in PostgreSQL. Never reuse the previous connection's data.
        "connection": [
            settings.CLICKHOUSE_V2.get("CH25_HOST"),
            int(settings.CLICKHOUSE_V2.get("CH25_TCP_PORT") or 9000),
            os.environ.get("SQL_WORKBENCH_CH_USER", ""),
            os.environ.get("SQL_WORKBENCH_CH_SECURE", "false").lower() == "true",
        ],
        "derived_metrics": scope.derived_metrics_enabled,
        "limits": sorted((key, str(value)) for key, value in QUERY_SETTINGS.items()),
        "result_limits": [MAX_RESULT_ROWS, MAX_RESULT_BYTES],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def reusable_result(actor, project, key, now):
    """The person's own completed, unexpired result for this key, if fresh."""
    seconds = result_cache_seconds()
    if not seconds or not key:
        return None
    return (
        SQLExecution.objects.filter(
            cache_key=key,
            project_id=project.id,
            organization_id=actor.organization_id,
            workspace_id=actor.workspace_id,
            created_by_id=actor.user_id,
            state="succeeded",
            finished_at__gte=now - timedelta(seconds=seconds),
            expires_at__gt=now,
        )
        .order_by("-finished_at")
        .first()
    )


def submit(actor, *, project_id, sql, start, end, reuse=True):
    project = authorize(actor, project_id)
    scope = query_scope(actor, project, start, end, sql)
    compile_query(sql, scope)
    connection_config()  # Fail before admitting jobs on unconfigured installations.
    now = timezone.now()
    key = (
        result_cache_key(actor, project, scope, sql)
        if reuse and result_cache_seconds()
        else ""
    )
    # Reuse takes no admission slot, quota or scan: the result already exists
    # and belongs to this person under the same access and catalog.
    reused = reusable_result(actor, project, key, now)
    if reused is not None:
        return reused
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
            cache_key=key,
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


ESTIMATES_PER_MINUTE = 30
ESTIMATE_UNAVAILABLE = "Estimate unavailable. Run is not affected."
ESTIMATE_WITHHELD = (
    "Not estimated: this source's index is shared across projects, so an "
    "index estimate would include other projects' rows."
)
DEFAULT_INDEX_GRANULARITY = 8192


def _estimate_window():
    return int(time.time() // 60)


def _estimate_allowed(actor):
    """Fixed one-minute window per user; no limiter means no estimate."""
    from django.core.cache import cache

    limit = _resource_limit("SQL_WORKBENCH_ESTIMATES_PER_MINUTE", ESTIMATES_PER_MINUTE)
    key = f"sql-workbench:estimate:{actor.user_id}:{_estimate_window()}"
    try:
        cache.add(key, 0, timeout=120)
        return cache.incr(key) <= limit
    except Exception:
        return False


def _primary_key_columns(primary_key):
    """Top-level comma-separated primary-index expressions, in order."""
    columns, depth, current = [], 0, []
    for char in primary_key:
        if char == "," and depth == 0:
            columns.append("".join(current).strip())
            current = []
            continue
        depth += {"(": 1, ")": -1}.get(char, 0)
        current.append(char)
    if "".join(current).strip():
        columns.append("".join(current).strip())
    return columns


def _tenant_local(primary_key, ownership, project_column):
    """Require the actual primary index to start with ownership through project.

    ORDER BY is not evidence: an explicit PRIMARY KEY can be a shorter prefix
    or empty. Only the primary index can establish the project's key range
    (apart from the granules at its edges).
    """
    for column in _primary_key_columns(primary_key):
        if column == project_column:
            return True
        if column not in ownership:
            return False
    return False


def _granularity(engine_full):
    import re

    match = re.search(r"\bindex_granularity\s*=\s*(\d+)", engine_full)
    value = int(match.group(1)) if match else DEFAULT_INDEX_GRANULARITY
    return value if value > 0 else DEFAULT_INDEX_GRANULARITY


def estimate(actor, *, project_id, sql, start, end):
    """Advisory pre-run estimate: same authorization and SQL as a run.

    It creates no execution record or execution-principal admission slot.
    Any estimate failure is reported as ``unavailable``. Shared-server physical
    resources are not isolated; execution's per-user concurrency slots are.
    """
    from .catalog import additional_table_filters
    from .runner import QUERY_SETTINGS
    from .runner import estimate as index_estimate

    project = authorize(actor, project_id)
    if not _estimate_allowed(actor):
        raise AdmissionFull("Too many estimates. The estimate resumes shortly.")
    scope = query_scope(actor, project, start, end, sql)
    compiled = compile_query(sql, scope)
    started = time.monotonic()
    try:
        raw = index_estimate(
            compiled,
            table_filters=additional_table_filters(scope),
            database=scope.database,
        )
        result = summarize_estimate(raw, scope.database)
    except Exception:
        result = {
            "status": "unavailable",
            "reason": ESTIMATE_UNAVAILABLE,
            "sources": [],
            "max_rows_to_read": QUERY_SETTINGS["max_rows_to_read"],
            "exceeds_scan_bound": None,
        }
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


def summarize_estimate(raw, database):
    """The tenant-safe view of one ``EXPLAIN ESTIMATE`` result.

    Per source it reports parts, index granules and the most rows those
    granules can hold (granules x index granularity), never the raw row total
    of the granules, which can include an adjacent project's rows. Sources
    whose primary index does not start with project ownership are withheld.
    ``exceeds_scan_bound`` is unknown if any source is withheld: even a
    threshold comparison would disclose information about that source's rows.
    """
    from .catalog import ESTIMATE_SOURCES
    from .runner import QUERY_SETTINGS

    bound = QUERY_SETTINGS["max_rows_to_read"]
    sources, granule_rows = {}, 0
    for item in raw["tables"]:
        known = ESTIMATE_SOURCES.get(item["table"])
        layout = raw["layout"].get(item["table"])
        dataset = known[0] if known else "other"
        entry = sources.setdefault(
            dataset,
            {
                "dataset": dataset,
                "estimated": True,
                "parts": 0,
                "marks": 0,
                "max_rows": 0,
                "reason": "",
            },
        )
        local = (
            item["database"] == database
            and known is not None
            and layout is not None
            and _tenant_local(layout[0], known[1], known[2])
        )
        if not local or not entry["estimated"]:
            entry.update(
                estimated=False,
                parts=None,
                marks=None,
                max_rows=None,
                reason=ESTIMATE_WITHHELD,
            )
            continue
        granule_rows += item["rows"]
        entry["parts"] += item["parts"]
        entry["marks"] += item["marks"]
        entry["max_rows"] += item["marks"] * _granularity(layout[1])
    return {
        "status": "ok",
        "reason": "",
        "sources": sorted(sources.values(), key=lambda value: value["dataset"]),
        "max_rows_to_read": bound,
        "exceeds_scan_bound": (
            granule_rows > bound
            if all(entry["estimated"] for entry in sources.values())
            else None
        ),
    }


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
