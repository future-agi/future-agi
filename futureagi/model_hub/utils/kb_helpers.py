"""
Knowledge Base helper functions.

This module contains shared utility functions for KB operations,
used by both views and tasks.
"""

import asyncio
import os
import time

import structlog
from django.db import transaction

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from tfc.utils.storage import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_object_url, get_storage_client

logger = structlog.get_logger(__name__)

# Strong references to fire-and-forget workflow cancellations. The event loop
# only keeps weak references, so an unreferenced ensure_future() task can be
# garbage-collected before the cancellation reaches Temporal.
_pending_cancellations: set["asyncio.Future[None]"] = set()


def is_kb_deleted_or_cancelled(kb_id):
    """
    Check if a KB has been deleted or marked for cancellation.

    Returns True if:
    - KB doesn't exist
    - KB is soft-deleted (deleted=True) - handled by BaseModel manager
    - KB status is DELETING (cancellation in progress)
    """
    try:
        kb = KnowledgeBaseFile.objects.get(id=kb_id)
        # Also treat DELETING status as cancelled
        return kb.status == StatusType.DELETING.value
    except KnowledgeBaseFile.DoesNotExist:
        return True


def get_kb_workflow_id(kb_id):
    """Get deterministic workflow ID for a KB ingestion activity."""
    return f"kb-ingest-{kb_id}"


def ingest_kb_files_impl(file_metadata, kb_id, org_id):
    """
    Wait for S3 uploads to complete, then trigger ingestion for KB files.

    S3 uploads happen in background daemon threads (fire-and-forget).
    This function polls S3 to check if files exist before triggering ingestion.

    Args:
        file_metadata: Dict mapping file_id -> {name, extension}
        kb_id: Knowledge Base ID
        org_id: Organization ID
    """
    if not file_metadata:
        return None

    # Check if KB was deleted before we even start
    if is_kb_deleted_or_cancelled(kb_id):
        logger.info(f"KB {kb_id} was deleted, skipping ingestion")
        return None

    # Initialize storage client
    minio_client = get_storage_client()

    # Poll for file existence in S3
    max_wait_time = 600  # 10 minutes max wait
    poll_interval = 2  # Check every 2 seconds
    elapsed_time = 0

    file_urls = {}
    pending_files = set(file_metadata.keys())

    while pending_files and elapsed_time < max_wait_time:
        # Check if KB was deleted during polling
        if is_kb_deleted_or_cancelled(kb_id):
            logger.info(f"KB {kb_id} was deleted during S3 polling, stopping")
            return None

        for file_id in list(pending_files):
            meta = file_metadata[file_id]
            extension = meta.get("extension", "")
            s3_file_name = f"{file_id}.{extension}" if extension else file_id
            object_key = f"knowledge-base/{kb_id}/{s3_file_name}"

            try:
                # Check if file exists in S3
                minio_client.stat_object(UPLOAD_BUCKET_NAME, object_key)

                # File exists, build URL
                url = get_object_url(UPLOAD_BUCKET_NAME, object_key)

                file_urls[file_id] = url
                pending_files.remove(file_id)
                logger.info(f"File {file_id} found in S3: {object_key}")

            except Exception as e:
                # Log non-404 errors for debugging
                error_str = str(e)
                if "NoSuchKey" not in error_str and "Not Found" not in error_str:
                    logger.warning(f"Error checking S3 for file {file_id}: {e}")

        if pending_files:
            time.sleep(poll_interval)
            elapsed_time += poll_interval

    # Final check before triggering ingestion
    if is_kb_deleted_or_cancelled(kb_id):
        logger.info(f"KB {kb_id} was deleted, skipping ingestion trigger")
        return None

    # Mark files that failed to upload as FAILED
    if pending_files:
        logger.error(f"Files not found in S3 after {max_wait_time}s: {pending_files}")
        Files.objects.filter(id__in=pending_files).update(
            status=StatusType.FAILED.value
        )

    # Trigger ingestion for successfully uploaded files
    if file_urls:
        # Import here to avoid circular import
        from model_hub.tasks.develop_dataset import ingest_files_to_s3

        ingest_files_to_s3.delay(file_urls, str(kb_id), str(org_id))
    else:
        # All files failed, mark KB as failed
        KnowledgeBaseFile.objects.filter(id=kb_id).update(
            status=StatusType.FAILED.value,
            last_error="All files failed to upload to S3",
        )


def schedule_kb_ingestion_on_commit(file_metadata, kb_id, org_id):
    """
    Schedule KB file ingestion activity to run after transaction commits.

    S3 uploads are in progress (fire-and-forget daemon threads). This function
    schedules the ingestion activity which will poll for file existence
    before triggering ingestion.

    Args:
        file_metadata: Dict mapping file_id -> {name, extension}
        kb_id: Knowledge Base ID
        org_id: Organization ID
    """
    if not file_metadata:
        return

    # Capture values for closure
    metadata = file_metadata
    kb_id_str = str(kb_id)
    org_id_str = str(org_id)
    workflow_id = get_kb_workflow_id(kb_id)

    def _start_ingestion():
        import tfc.temporal.background_tasks.activities  # noqa: F401
        from tfc.temporal.drop_in import start_activity

        start_activity(
            "ingest_kb_files_activity",
            args=(metadata, kb_id_str, org_id_str),
            queue="default",
            task_id=workflow_id,
        )

    transaction.on_commit(_start_ingestion)


def cancel_kb_ingestion_workflow(kb_id):
    """
    Cancel a running KB ingestion workflow.

    Called when a KB in PROCESSING state is deleted or cancelled.

    Steps:
    1. Mark KB as DELETING - this signals background tasks to stop
    2. Cancel the Temporal workflow
    """
    # Step 1: Mark KB as DELETING to signal background tasks to stop
    try:
        KnowledgeBaseFile.objects.filter(id=kb_id).update(
            status=StatusType.DELETING.value
        )
        logger.info(f"Marked KB {kb_id} as DELETING to stop background tasks")
    except Exception as e:
        logger.warning(f"Could not mark KB {kb_id} as DELETING: {e}")

    # Step 2: Cancel the Temporal workflow
    workflow_id = f"task-{get_kb_workflow_id(kb_id)}"

    async def _cancel():
        from tfc.temporal.common.client import get_client

        try:
            client = await get_client()
            handle = client.get_workflow_handle(workflow_id)
            await handle.cancel()
            logger.info(f"Cancelled KB ingestion workflow: {workflow_id}")
        except Exception as e:
            # Workflow may not exist or already completed - that's OK
            logger.debug(f"Could not cancel workflow {workflow_id}: {e}")

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            task = asyncio.ensure_future(_cancel())
            _pending_cancellations.add(task)
            task.add_done_callback(_pending_cancellations.discard)
        else:
            asyncio.run(_cancel())
    except RuntimeError:
        # No event loop, create one
        asyncio.run(_cancel())
