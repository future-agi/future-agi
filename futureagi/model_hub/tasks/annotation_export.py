"""Background export of an annotation queue.

Small exports stay inside the HTTP request (see
``AnnotationQueueViewSet.export_annotations``). Above the sync cap the view
creates an ``AnnotationExportJob`` and hands the work here, so a large queue
produces a file instead of a 413 or a request timeout.
"""

import csv
import io
import tempfile

import structlog
from django.utils import timezone
from rest_framework.utils.encoders import JSONEncoder

from model_hub.models.annotation_queues import AnnotationExportJob
from model_hub.models.choices import (
    AnnotationExportFormat,
    AnnotationExportJobStatus,
)
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


def _is_csv(job):
    return job.export_format == AnnotationExportFormat.CSV.value


def _iter_item_chunks(items_qs, chunk_size):
    """Yield lists of items without materializing the whole queue."""
    chunk = []
    for item in items_qs.iterator(chunk_size=chunk_size):
        chunk.append(item)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def render_export(job):
    """Render a job's whole export into a spooled file, chunk by chunk.

    Returns ``(fileobj, item_count, size)`` with *fileobj* rewound and holding
    utf-8 bytes. Rows come from the same ``build_queue_export_rows`` the
    synchronous download uses, and the JSON branch encodes with DRF's encoder,
    so a queue exported either way yields the same bytes.

    The caller owns *fileobj* and must close it.
    """
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
    from tfc.settings.settings import UPLOAD_BUCKET_NAME
    from tfc.utils.storage_client import (
        ensure_bucket,
        get_object_url,
        get_storage_client,
    )

    extension = "csv" if _is_csv(job) else "json"
    content_type = "text/csv" if _is_csv(job) else "application/json"
    object_key = f"annotation-exports/{job.queue_id}/{job.id}.{extension}"

    client = get_storage_client()
    ensure_bucket(client, UPLOAD_BUCKET_NAME)
    client.put_object(
        bucket_name=UPLOAD_BUCKET_NAME,
        object_name=object_key,
        data=fileobj,
        length=size,
        content_type=content_type,
        part_size=10 * 1024 * 1024,
    )

    return (
        get_object_url(UPLOAD_BUCKET_NAME, object_key),
        f"queue_{job.queue_id}_annotations.{extension}",
    )


def _temporal_will_retry():
    """True when Temporal still has attempts left for this activity.

    Outside an activity context — direct invocation, tests — nothing retries,
    so this is False and the failure is final.
    """
    try:
        from temporalio import activity

        from tfc.temporal.drop_in.workflow import DEFAULT_RETRY_POLICY

        attempt = activity.info().attempt
    except Exception:
        return False

    max_attempts = getattr(DEFAULT_RETRY_POLICY, "maximum_attempts", None)
    if not max_attempts:
        return False
    return attempt < max_attempts


@temporal_activity(time_limit=3600, queue="tasks_l")
def export_annotation_queue_async(job_id):
    """Build a large queue export on a worker and store it for download.

    Triggered from ``AnnotationQueueViewSet.export_annotations`` when the queue
    is over the sync cap, so the HTTP request can return 202 immediately while
    the resolution of every item runs here.
    """
    job = AnnotationExportJob.objects.select_related("queue", "organization").get(
        pk=job_id
    )

    job.status = AnnotationExportJobStatus.RUNNING.value
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at", "updated_at"])

    fileobj = None
    try:
        fileobj, item_count, size = render_export(job)
        file_url, file_name = _store_export_file(job, fileobj, size)
    except Exception as exc:
        logger.exception("annotation_export_failed", job_id=str(job_id), error=str(exc))
        if _temporal_will_retry():
            # Another attempt is coming. Marking the job failed here would make
            # the polling client give up on an export that is still going to
            # finish, so the row stays running and only the log records this
            # attempt.
            raise
        # Last attempt: record the real reason so the client shows it instead
        # of a generic download failure.
        job.status = AnnotationExportJobStatus.FAILED.value
        job.error = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at", "updated_at"])
        raise
    finally:
        if fileobj is not None:
            fileobj.close()

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

    logger.info(
        "annotation_export_succeeded",
        job_id=str(job_id),
        queue_id=str(job.queue_id),
        item_count=item_count,
    )
    return {"job_id": str(job.id), "item_count": item_count, "file_url": file_url}
