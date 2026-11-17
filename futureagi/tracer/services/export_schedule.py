"""Serialize due-window claims; only a successful upload advances the watermark."""
from copy import deepcopy
from datetime import timedelta
from functools import partial

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tracer.models.bulk_export import BulkExportJob
from tracer.models.export_schedule import ExportSchedule
from tracer.serializers.bulk_export import validate_export_query
from tracer.services.bulk_export import JOB_TIMEOUT_SECONDS, fail_export


INTERVALS = {"hourly": timedelta(hours=1), "daily": timedelta(days=1)}


def _enqueue(job_id):
    from tracer.tasks.bulk_export import process_bulk_export

    try:
        process_bulk_export.delay(job_id)
    except Exception:
        fail_export(job_id, "Export could not be queued. It will be retried on the next schedule tick.")


def dispatch_due_exports(*, now=None):
    """Return queued job IDs. First window begins at schedule creation.

    Each window is [last successful window end, this tick), intersected with
    the saved Observe predicates. Dates describe row event time, not ingestion
    time: late-arriving rows older than the watermark are not replayed.
    """
    now = now or timezone.now()
    queued = []
    due = ExportSchedule.objects.filter(enabled=True).filter(
        Q(interval="hourly", last_run_at__lte=now - INTERVALS["hourly"])
        | Q(interval="hourly", last_run_at__isnull=True, created_at__lte=now - INTERVALS["hourly"])
        | Q(interval="daily", last_run_at__lte=now - INTERVALS["daily"])
        | Q(interval="daily", last_run_at__isnull=True, created_at__lte=now - INTERVALS["daily"])
    )
    for schedule_id in due.values_list("pk", flat=True).iterator(chunk_size=100):
        with transaction.atomic():
            schedule = ExportSchedule.objects.select_for_update().filter(pk=schedule_id, enabled=True).first()
            if schedule is None:
                continue
            lower = schedule.last_run_at or schedule.created_at
            if now < lower + INTERVALS[schedule.interval]:
                continue
            # Hard worker termination or a lost enqueue must not wedge a
            # schedule forever. Failed windows are retried without moving it.
            stale_before = now - timedelta(seconds=JOB_TIMEOUT_SECONDS + 300)
            active = BulkExportJob.objects.filter(schedule=schedule, status__in=["queued", "running"])
            stale = active.filter(
                Q(status="running", started_at__lt=stale_before)
                | Q(status="queued", created_at__lt=stale_before)
            )
            stale.update(status="failed", error="Export timed out. The window will be retried.", finished_at=now, updated_at=now)
            if active.exists():
                continue
            options = deepcopy(schedule.filters)
            validated = validate_export_query(schedule.row_type, options, schedule.project_id)
            options["filters"] = [*validated["filters"], {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime", "filter_op": "between",
                    "filter_value": [lower.isoformat(), now.isoformat()],
                },
            }]
            validate_export_query(schedule.row_type, options, schedule.project_id)
            job = BulkExportJob.objects.create(
                project_id=schedule.project_id, created_by_id=schedule.created_by_id,
                schedule=schedule, row_type=schedule.row_type, filters=options,
                format=schedule.format, destination=deepcopy(schedule.destination),
                encrypted_credentials=bytes(schedule.encrypted_credentials), window_end=now,
            )
            job_id = str(job.id)
            transaction.on_commit(partial(_enqueue, job_id))
            queued.append(job_id)
    return queued


def complete_scheduled_export(job, *, location, row_count):
    """Publish file and watermark together, using the scheduler's lock order."""
    now = timezone.now()
    with transaction.atomic():
        schedule = (
            ExportSchedule.objects.select_for_update().filter(pk=job.schedule_id).first()
            if job.schedule_id else None
        )
        completed = BulkExportJob.objects.filter(pk=job.pk, status="running").update(
            status="completed", file_location=location, row_count=row_count,
            finished_at=now, updated_at=now,
        )
        if completed and schedule and job.window_end and (
            schedule.last_run_at is None or schedule.last_run_at < job.window_end
        ):
            schedule.last_run_at = job.window_end
            schedule.save(update_fields=["last_run_at", "updated_at"])
