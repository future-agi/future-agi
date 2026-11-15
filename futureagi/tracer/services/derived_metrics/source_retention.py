"""Source policy applies to every derived copy, not only per-unit evidence.

A boundary bucket is unavailable until a retained-only answer is proved. Never
clip a stale total and call it retained-only. Deletion uses the existing durable
outbox; a scheduling interval is not a physical-deletion completion guarantee.
"""

from datetime import datetime

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string

from tracer.services.derived_metrics.retention import retention_cutoff


def current_cutoff(organization_id, *, now=None):
    provider = import_string(settings.METRICS_RETENTION_DAYS_PROVIDER)
    return retention_cutoff(provider(organization_id), now=now)


def retained_start(organization_id, *, manifest=None, now=None):
    cutoff = current_cutoff(organization_id, now=now)
    if manifest is not None:
        floor = manifest.published_by.source_ceilings.get("retention_floor")
        if floor:
            floor = datetime.fromisoformat(str(floor))
            cutoff = max(cutoff, floor) if cutoff else floor
    return cutoff


def window_retained(organization_id, start, *, manifest=None, now=None):
    cutoff = retained_start(organization_id, manifest=manifest, now=now)
    return cutoff is None or start >= cutoff


def require_window(organization_id, start, *, manifest=None):
    from tracer.services.derived_metrics.service import MetricUnavailableError

    if not window_retained(organization_id, start, manifest=manifest):
        raise MetricUnavailableError(
            "retention_expired",
            "This window includes source data outside current retention. Choose a later start.",
        )


def expire_run(run, *, now=None):
    """Permanently revoke a frozen namespace before any physical deletion."""
    from tracer.models.derived_metric import DerivedMetricRun, MetricGenerationPin
    from tracer.services.derived_metrics.deletion_protocol import _intent, serialized_project

    now = now or timezone.now()
    metric = run.definition.metric
    with serialized_project(metric.project_id):
        DerivedMetricRun.objects.filter(pk=run.pk, deleted_at__isnull=True).update(
            deleted_at=now, cleanup_status="pending"
        )
        MetricGenerationPin.objects.filter(pk=run.pin_id).update(
            invalidated_at=now, invalidation_reason="retention_expired"
        )
        _intent(
            organization_id=metric.organization_id,
            project_id=metric.project_id,
            scope="run",
            metric_key=metric.pk,
            version_key=run.definition_id,
            run_key=run.pk,
            snapshot_key=run.snapshot_id,
            result_key=run.result_id,
            deletion_key=f"run:{run.pk}",
        )


def require_run(run):
    from tracer.services.derived_metrics.errors import MetricInputError

    if run.deleted_at or not window_retained(
        run.definition.metric.organization_id, run.window_start
    ):
        expire_run(run)
        raise MetricInputError("This result is no longer available under source retention.")


def expire_runs(*, limit=100):
    """Bounded round-robin by oldest window; remove all expired copies, legacy too."""
    from tracer.models.derived_metric import DerivedMetricRun

    # Scan organizations separately: an unlimited tenant must not starve another.
    organizations = DerivedMetricRun.objects.filter(deleted_at__isnull=True).values_list(
        "definition__metric__organization_id", flat=True
    ).order_by().distinct()
    count = 0
    for organization_id in organizations.iterator(chunk_size=100):
        cutoff = current_cutoff(organization_id)
        if cutoff is None:
            continue
        runs = DerivedMetricRun.objects.filter(
            definition__metric__organization_id=organization_id,
            deleted_at__isnull=True, window_start__lt=cutoff,
        ).select_related("definition__metric").order_by("window_start")[:limit - count]
        for run in runs:
            expire_run(run)
            count += 1
        if count >= limit:
            break
    return count
