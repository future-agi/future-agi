"""Policy and erasure guards for saved metric SQL results (REST and MCP)."""

from django.db.models import Q
from django.utils import timezone

from tracer.models.derived_metric import MetricErasureRequest
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


def require_source(organization_id, project_id, start, sql):
    if uses_metrics(sql) and (
        not window_retained(organization_id, start)
        or erasures(organization_id, project_id).exclude(status="done").exists()
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
