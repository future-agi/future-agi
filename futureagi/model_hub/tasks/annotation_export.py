"""Background export of an annotation queue.

Small exports stay inside the HTTP request (see
``AnnotationQueueViewSet.export_annotations``). Above the sync cap the view
creates an ``AnnotationExportJob`` and hands the work here, so a large queue
produces a file instead of a 413 or a request timeout.
"""

import csv
import io
import tempfile
from datetime import timedelta

import structlog
from django.db import connections, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.utils.encoders import JSONEncoder

from model_hub.models.annotation_queues import AnnotationExportJob
from model_hub.models.choices import (
    AnnotationExportFormat,
    AnnotationExportJobStatus,
)
from tfc.routers import force_primary
from tfc.temporal import temporal_activity

logger = structlog.get_logger(__name__)

# Items resolved per batch. Every per-item read (scores, notes, eval metrics,
# ClickHouse sources, dataset cells) is batched over the chunk, so this bounds
# how many resolved items are alive at once. Call executions are the widest rows
# and are what the size is picked for.
EXPORT_CHUNK_SIZE = 200

# The rendered file is spooled: it stays in memory up to this size and rolls
# over to a temp file past it. Chunking alone would still have held the whole
# document in RAM, which just trades the request timeout for a worker OOM on
# the very queues this is meant to serve.
EXPORT_SPOOL_MAX_BYTES = 32 * 1024 * 1024

# A finished export leaves a DB row and a stored object; both are reclaimed
# this many days after the job finished. Time (not space) is the predicate for
# every cleanup in this repo, and finished_at is the natural cutoff here.
EXPORT_RETENTION_DAYS = 30


def _is_csv(job):
    return job.export_format == AnnotationExportFormat.CSV.value


def _iter_item_chunks(items_qs, chunk_size):
    """Bound SQL reads even when PgBouncer disables server-side cursors."""
    after = None
    while True:
        page = items_qs.order_by("order", "created_at", "pk")
        if after is not None:
            page = page.filter(
                Q(order__gt=after[0])
                | Q(order=after[0], created_at__gt=after[1])
                | Q(order=after[0], created_at=after[1], pk__gt=after[2])
            )
        chunk = list(page[:chunk_size])
        if not chunk:
            return
        after = (chunk[-1].order, chunk[-1].created_at, chunk[-1].pk)
        yield chunk
        if len(chunk) < chunk_size:
            return


def render_export(job):
    """Render a job's whole export into a spooled file, chunk by chunk.

    Returns ``(fileobj, item_count, size)`` with *fileobj* rewound and holding
    utf-8 bytes. Rows come from the same ``build_queue_export_rows`` the
    synchronous download uses, and the JSON branch encodes with DRF's encoder,
    so a queue exported either way yields the same bytes.

    The caller owns *fileobj* and must close it.
    """
    # Keyset pages alone are not stable when users edit order/status mid-export.
    # Start a fresh snapshot before the first read; release it before the worker
    # re-reads the job under a lock for upload/finalization.
    with force_primary(), transaction.atomic(using="default"):
        with connections["default"].cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        return _render_export_snapshot(job)


def _render_export_snapshot(job):
    from model_hub.views.annotation_queues import (
        QUEUE_EXPORT_CSV_HEADER,
        build_queue_export_rows,
        queue_export_items_queryset,
        write_queue_export_csv_rows,
    )

    queue = job.queue
    items_qs = queue_export_items_queryset(queue, job.status_filter)

    spool = tempfile.SpooledTemporaryFile(max_size=EXPORT_SPOOL_MAX_BYTES, mode="w+b")
    # newline="" is what csv.writer expects; write_through keeps the spool's
    # size accounting honest instead of buffering in the wrapper.
    text = io.TextIOWrapper(spool, encoding="utf-8", newline="", write_through=True)
    item_count = 0

    try:
        if _is_csv(job):
            writer = csv.writer(text)
            writer.writerow(QUEUE_EXPORT_CSV_HEADER)
            for chunk in _iter_item_chunks(items_qs, EXPORT_CHUNK_SIZE):
                write_queue_export_csv_rows(
                    writer, build_queue_export_rows(queue, chunk)
                )
                item_count += len(chunk)
        else:
            text.write("[")
            encoder = JSONEncoder()
            first = True
            for chunk in _iter_item_chunks(items_qs, EXPORT_CHUNK_SIZE):
                for row in build_queue_export_rows(queue, chunk):
                    if not first:
                        text.write(",")
                    text.write(encoder.encode(row))
                    first = False
                item_count += len(chunk)
            text.write("]")

        text.flush()
        size = spool.tell()
        # detach() leaves the spool open after the wrapper goes away.
        text.detach()
        spool.seek(0)
    except Exception:
        text.close()
        raise

    return spool, item_count, size


def _store_export_file(job, fileobj, size):
    """Put the rendered export in object storage and return (url, filename)."""
    from model_hub.services.annotation_export_storage import (
        download_path,
        ensure_export_bucket,
        verify_private_object,
    )
    from tfc.utils.storage_client import get_storage_client

    extension = "csv" if _is_csv(job) else "json"
    content_type = "text/csv" if _is_csv(job) else "application/json"
    object_key = (
        job.storage_key or f"annotation-exports/{job.queue_id}/{job.id}.{extension}"
    )
    filename = f"queue_{job.queue_id}_annotations.{extension}"

    client = get_storage_client()
    bucket = ensure_export_bucket(client)
    client.put_object(
        bucket_name=bucket,
        object_name=object_key,
        data=fileobj,
        length=size,
        content_type=content_type,
        metadata={"Content-Disposition": f'attachment; filename="{filename}"'},
        part_size=10 * 1024 * 1024,
    )
    try:
        verify_private_object(bucket, object_key)
    except Exception:
        client.remove_object(bucket, object_key)
        raise

    return download_path(job), filename


def _delete_export_object(job):
    """Remove a job's stored export from object storage.

    The key is the same deterministic one ``_store_export_file`` wrote, so a
    finished job's file is addressable without a list call. Remove it even if
    saving file_url failed after upload; deleting a missing object is idempotent.
    """
    from model_hub.services.annotation_export_storage import export_bucket_name
    from tfc.utils.storage_client import get_storage_client

    if not job.storage_key and job.queue_id is None:
        return
    extension = "csv" if _is_csv(job) else "json"
    object_key = (
        job.storage_key or f"annotation-exports/{job.queue_id}/{job.id}.{extension}"
    )

    client = get_storage_client()
    client.remove_object(export_bucket_name(), object_key)


def _temporal_will_retry():
    """True when Temporal still has attempts left for this activity.

    Outside an activity context — direct invocation, tests — nothing retries,
    so this is False and the failure is final.
    """
    from temporalio import activity

    from tfc.temporal.drop_in.workflow import DEFAULT_RETRY_POLICY

    if not activity.in_activity():
        return False
    attempt = activity.info().attempt

    max_attempts = getattr(DEFAULT_RETRY_POLICY, "maximum_attempts", None)
    if not max_attempts:
        return False
    return attempt < max_attempts


def _temporal_execution():
    """Retries are ordered within this job's single workflow activity execution."""
    from temporalio import activity

    if not activity.in_activity():
        return None
    info = activity.info()
    return f"{info.workflow_run_id}/{info.activity_id}", info.attempt


@temporal_activity(time_limit=3600, queue="tasks_l")
def export_annotation_queue_async(job_id):
    """Build a large queue export on a worker and store it for download.

    Triggered from ``AnnotationQueueViewSet.export_annotations`` when the queue
    is over the sync cap, so the HTTP request can return 202 immediately while
    the resolution of every item runs here.
    """
    execution = _temporal_execution()
    with transaction.atomic():
        job = AnnotationExportJob.no_workspace_objects.select_for_update().get(
            pk=job_id
        )
        if job.status == AnnotationExportJobStatus.SUCCEEDED.value:
            return _export_job_result(job)
        if execution is None:
            # Direct calls have no Temporal retry identity.
            execution = (job.activity_execution, job.activity_attempt + 1)
        execution_id, attempt = execution
        if job.activity_execution and job.activity_execution != execution_id:
            raise RuntimeError("This export belongs to a different workflow execution.")
        if attempt <= job.activity_attempt:
            raise RuntimeError("This export attempt was superseded.")
        job.activity_execution = execution_id
        job.activity_attempt = attempt
        job.status = AnnotationExportJobStatus.RUNNING.value
        job.started_at = timezone.now()
        job.error = None
        job.finished_at = None
        if not job.storage_key and job.queue_id:
            extension = "csv" if _is_csv(job) else "json"
            job.storage_key = f"annotation-exports/{job.queue_id}/{job.id}.{extension}"
        job.save(
            update_fields=[
                "status",
                "started_at",
                "error",
                "finished_at",
                "storage_key",
                "activity_execution",
                "activity_attempt",
                "updated_at",
            ]
        )
    attempt_started = job.started_at

    fileobj = None
    try:
        if job.queue_id is None:
            raise ValueError(
                "The annotation queue was deleted before export completed."
            )
        fileobj, item_count, size = render_export(job)
        # Fence old attempts before touching the shared object. The row lock also
        # serializes upload + success with redelivery and parent SET_NULL deletion.
        with transaction.atomic():
            job = AnnotationExportJob.no_workspace_objects.select_for_update().get(
                pk=job_id
            )
            if job.status == AnnotationExportJobStatus.SUCCEEDED.value:
                return _export_job_result(job)
            if (
                job.activity_execution != execution_id
                or job.activity_attempt != attempt
                or job.started_at != attempt_started
                or job.status != AnnotationExportJobStatus.RUNNING.value
            ):
                raise RuntimeError("This export attempt was superseded.")
            if job.queue_id is None:
                raise ValueError(
                    "The annotation queue was deleted before export completed."
                )
            file_url, file_name = _store_export_file(job, fileobj, size)
            job.status = AnnotationExportJobStatus.SUCCEEDED.value
            job.item_count = item_count
            job.file_url = file_url
            job.file_name = file_name
            job.finished_at = timezone.now()
            job.save(
                update_fields=[
                    "status",
                    "item_count",
                    "file_url",
                    "file_name",
                    "finished_at",
                    "updated_at",
                ]
            )
    except Exception as exc:
        logger.exception("annotation_export_failed", job_id=str(job_id), error=str(exc))
        if _temporal_will_retry():
            raise
        AnnotationExportJob.no_workspace_objects.filter(
            pk=job_id,
            status=AnnotationExportJobStatus.RUNNING.value,
            started_at=attempt_started,
            activity_execution=execution_id,
            activity_attempt=attempt,
        ).update(
            status=AnnotationExportJobStatus.FAILED.value,
            error=str(exc),
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        raise
    finally:
        if fileobj is not None:
            fileobj.close()

    logger.info(
        "annotation_export_succeeded", job_id=str(job_id), item_count=job.item_count
    )
    return _export_job_result(job)


def _export_job_result(job):
    return {
        "job_id": str(job.id),
        "item_count": job.item_count,
        "file_url": job.file_url,
    }


@temporal_activity(time_limit=600, queue="default")
def cleanup_expired_export_jobs():
    """Reclaim export jobs that finished more than the retention window ago.

    Each oversize export leaves one ``AnnotationExportJob`` row and one stored
    object, and nothing in the write path cleans either up, so they pile up.
    This mirrors ``delete_unused_compare_folder``: a periodic schedule that
    deletes the storage object and soft-deletes the row, with a per-item
    try/except so one failure doesn't stop the sweep.

    Only terminal jobs (``succeeded``/``failed``) past the cutoff are touched —
    an in-flight ``pending``/``running`` export is never deleted.
    """
    from model_hub.selectors.annotation_exports import resolve_export_job_state

    # Worker crashes/timeouts cannot execute the activity's exception handler.
    # Persist confirmed terminal states so their files also enter retention.
    active = AnnotationExportJob.no_workspace_objects.filter(
        status__in=[
            AnnotationExportJobStatus.PENDING.value,
            AnnotationExportJobStatus.RUNNING.value,
        ],
    )
    for job in active.iterator(chunk_size=200):
        job_status, job_error = resolve_export_job_state(job)
        if job_status == AnnotationExportJobStatus.FAILED.value:
            AnnotationExportJob.no_workspace_objects.filter(
                pk=job.pk, status=job.status
            ).update(status=job_status, error=job_error, finished_at=timezone.now())

    cutoff = timezone.now() - timedelta(days=EXPORT_RETENTION_DAYS)
    stale = AnnotationExportJob.no_workspace_objects.filter(
        deleted=False,
        status__in=[
            AnnotationExportJobStatus.SUCCEEDED.value,
            AnnotationExportJobStatus.FAILED.value,
        ],
        finished_at__lt=cutoff,
    )

    deleted = 0
    for job in stale.iterator():
        try:
            _delete_export_object(job)
            job.delete()  # BaseModel.delete is a soft delete.
            deleted += 1
        except Exception as exc:
            logger.exception(
                "annotation_export_cleanup_failed",
                job_id=str(job.id),
                error=str(exc),
            )

    logger.info("annotation_export_cleanup_completed", deleted=deleted)
    return {"deleted": deleted}
