"""Policy and erasure guards for saved metric SQL results (REST and MCP)."""

from django.db.models import DateTimeField, Q
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Cast
from django.utils import timezone

from tracer.models.derived_metric import DerivedMetricRun, MetricErasureRequest
from tracer.models.sql_query import SQLExecution
from tracer.services.derived_metrics.source_retention import window_retained

from .compiler import validate
from .runner import QueryUnavailable

MESSAGE = "Metric source data changed or expired. Run the query again."


def uses_metrics(sql):
    _, references = validate(sql)
    return any(table.name == "derived_metrics" for table in references)


def erasures(organization_id, project_id):
    return (
        MetricErasureRequest.objects.filter(
            organization_key=organization_id,
        )
        .filter(Q(project_key=project_id) | Q(scope_kind="organization"))
        .filter(
            Q(
                scope_kind__in=(
                    "organization",
                    "project",
                    "users",
                    "sessions",
                    "metric",
                    "version",
                ),
                store_payload__protocol__isnull=True,
            )
            | Q(store_payload__protocol__in=("metric_expiry", "metric_policy_replan"))
        )
    )


def snapshot_deletions(organization_id, project_id, start, end=None):
    # A run-copy/history cleanup is NOT revocation. Only explicit snapshot
    # deletion records this durable window, which survives deletion of its PG
    # parent. Match the catalog's fully-contained window, not mere overlap.
    requests = MetricErasureRequest.objects.filter(
        organization_key=organization_id,
        project_key=project_id,
        scope_kind="run",
        store_payload__protocol__isnull=True,
    ).annotate(
        sql_start=Cast(
            KeyTextTransform(
                "start", KeyTransform("sql_source_window", "store_payload")
            ),
            DateTimeField(),
        ),
        sql_end=Cast(
            KeyTextTransform("end", KeyTransform("sql_source_window", "store_payload")),
            DateTimeField(),
        ),
    )
    window = Q(sql_start__gte=start)
    if end is not None:
        window &= Q(sql_end__lte=end)
    # Pre-window deletion intents can outlive even their PG run tombstone.
    # Without provenance, conservatively revoke every saved metric result in
    # this project, not just outputs whose source window can still be recovered.
    return requests.filter(window | Q(store_payload__sql_source_window__isnull=True))


def require_source(organization_id, project_id, start, sql, *, end=None):
    if uses_metrics(sql) and (
        not window_retained(organization_id, start)
        or erasures(organization_id, project_id).exclude(status="done").exists()
        or snapshot_deletions(organization_id, project_id, start, end)
        .exclude(status="done")
        .exists()
    ):
        raise QueryUnavailable(MESSAGE)


def invalidate(queryset):
    # The running-state CAS in execute_sql cannot publish after this update.
    # Keep a running job's deadline/finished_at: it still holds its native slot
    # until confirm_stopped, just like a user cancellation.
    now = timezone.now()
    active = queryset.filter(state="running").update(
        state="cancelled",
        result={},
        output_bytes=0,
        cache_key="",
        error=MESSAGE,
        expires_at=now,
        finished_at=None,
    )
    active += queryset.filter(state="queued").update(
        state="cancelled",
        result={},
        output_bytes=0,
        cache_key="",
        error=MESSAGE,
        expires_at=now,
        finished_at=now,
    )
    return active + queryset.exclude(state="running").update(
        result={},
        output_bytes=0,
        cache_key="",
        error=MESSAGE,
        expires_at=now,
    )


def invalidate_snapshot(request):
    using = request._state.db
    window = request.store_payload.get("sql_source_window")
    if not window:
        # Ordinary run-copy cleanup must remain independent of SQL output.
        if request.store_payload.get("protocol"):
            return
        # Match the read fence before attempting to recover any legacy window.
        # Without provenance, all saved metric output in this project is stale.
        invalidate_scope(request.organization_key, request.project_key)
        run = (
            DerivedMetricRun.objects.using(using)
            .filter(
                pk=request.run_key,
                kind="snapshot",
                deleted_at__isnull=False,
                definition__metric__organization_id=request.organization_key,
                definition__metric__project_id=request.project_key,
            )
            .first()
        )
        if run is None:
            return
        window = {
            "start": run.window_start.isoformat(),
            "end": run.window_end.isoformat(),
        }
        request.store_payload = {**request.store_payload, "sql_source_window": window}
        MetricErasureRequest.objects.using(using).filter(pk=request.pk).update(
            store_payload=request.store_payload
        )
    jobs = (
        SQLExecution.objects.using(using)
        .filter(
            organization_id=request.organization_key,
            project_id=request.project_key,
            start__lte=window["start"],
            end__gte=window["end"],
        )
        .filter(
            Q(state__in=("queued", "running")) | Q(output_bytes__gt=0) | ~Q(result={})
        )
    )
    for job in jobs.only("id", "sql").iterator(chunk_size=100):
        if uses_metrics(job.sql):
            invalidate(SQLExecution.objects.using(using).filter(pk=job.pk))


def invalidate_scope(organization_id, project_id=None):
    # Metric SQL has no per-subject lineage, but ordinary SQL is not an output
    # of this store. Parse references rather than treating comments/literals as
    # metric dependencies; preserve the running-state publication CAS.
    jobs = SQLExecution.objects.filter(organization_id=organization_id).filter(
        Q(state__in=("queued", "running")) | Q(output_bytes__gt=0) | ~Q(result={})
    )
    if project_id is not None:
        jobs = jobs.filter(project_id=project_id)
    for job in jobs.only("id", "sql").iterator(chunk_size=100):
        if uses_metrics(job.sql):
            invalidate(SQLExecution.objects.filter(pk=job.pk))


def result_allowed(job):
    if not uses_metrics(job.sql):
        return True
    return (
        window_retained(job.organization_id, job.start)
        and not erasures(job.organization_id, job.project_id)
        .filter(Q(requested_at__gte=job.created_at) | ~Q(status="done"))
        .exists()
        and not snapshot_deletions(
            job.organization_id, job.project_id, job.start, job.end
        )
        # A query admitted during deletion can have read the old native rows.
        # Completion (not request creation) is the safe lower bound for reuse.
        .filter(Q(completed_at__gte=job.created_at) | ~Q(status="done"))
        .exists()
    )


def guard_result(job):
    if not uses_metrics(job.sql):
        return job.expires_at > timezone.now()
    if not result_allowed(job):
        invalidate(SQLExecution.objects.filter(pk=job.pk))
        job.refresh_from_db()
        return False
    # A caller may hold an object loaded before erasure's atomic invalidation.
    job.refresh_from_db()
    return job.expires_at > timezone.now()


def expire_results(limit=1000):
    """Bounded cleanup independent of rollout; no deleted result can be reused."""
    count = 0
    # All saved metric results have bounded 24-hour lives. Check oldest first;
    # ordinary SQL bodies retain their existing lifecycle.
    jobs = SQLExecution.objects.filter(
        output_bytes__gt=0, sql__icontains="derived_metrics"
    ).order_by("start")[:limit]
    for job in jobs:
        if not result_allowed(job):
            invalidate(SQLExecution.objects.filter(pk=job.pk))
            count += 1
    return count
