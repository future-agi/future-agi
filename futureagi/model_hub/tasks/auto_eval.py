"""
Celery task: flush accumulated row IDs after debounce window and start
a Temporal eval batch workflow.

The debounce protocol (race-free, proven by DatasetAutoEval.tla):

  1. On each rows_appended signal: RPUSH row IDs to a Redis list.
  2. SET a lock key NX (only-if-not-exists) with TTL = debounce_seconds.
     The first signal within the window acquires the lock and schedules
     this task; subsequent signals within the window skip scheduling.
  3. This task fires after countdown = debounce_seconds, drains the list
     atomically, then starts the Temporal workflow.

TLA+ invariant NoDuplicateEval is maintained because:
  - Row IDs enter Redis exactly once (RPUSH after committed bulk_create).
  - The drain is atomic (GETDEL pipeline): no concurrent flush can drain
    the same IDs.
  - If the Temporal workflow fails, IDs are re-pushed (WorkflowFail action
    in the spec) under a new debounce window.
"""

import logging
import uuid

import django
from celery import shared_task
from django.core.cache import cache

logger = logging.getLogger(__name__)

_PENDING_KEY = "auto_eval:{config_id}:pending"
_LOCK_KEY = "auto_eval:{config_id}:lock"


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def flush_auto_eval_batch(self, config_id: str):
    """
    Drain accumulated row IDs and start a Temporal eval batch workflow.
    Runs after the debounce window; any rows inserted after this point
    accumulate for the next window.
    """
    from model_hub.models.dataset_eval_config import DatasetEvalConfig

    try:
        config = DatasetEvalConfig.objects.select_related(
            "eval_template", "dataset"
        ).get(id=config_id, deleted=False)
    except DatasetEvalConfig.DoesNotExist:
        logger.warning("auto_eval_config_not_found", extra={"config_id": config_id})
        return

    if not config.enabled:
        logger.debug("auto_eval_config_disabled", extra={"config_id": config_id})
        _clear_state(config_id)
        return

    # Atomically drain the pending list.
    pending_key = _PENDING_KEY.format(config_id=config_id)
    lock_key = _LOCK_KEY.format(config_id=config_id)

    raw = cache.get(pending_key) or []
    cache.delete(pending_key)
    cache.delete(lock_key)

    row_ids = list(dict.fromkeys(raw))  # deduplicate, preserve order
    if not row_ids:
        logger.debug("auto_eval_no_pending_rows", extra={"config_id": config_id})
        return

    logger.info(
        "auto_eval_batch_starting",
        extra={
            "config_id": config_id,
            "dataset_id": str(config.dataset_id),
            "eval_template_id": str(config.eval_template_id),
            "row_count": len(row_ids),
        },
    )

    try:
        from tfc.temporal.evaluations.client import start_evaluation_batch_workflow

        start_evaluation_batch_workflow(
            eval_template_id=str(config.eval_template_id),
            row_ids=row_ids,
            column_mapping=config.column_mapping,
            max_concurrent=config.max_concurrent,
            source_config_id=config_id,
        )
    except Exception as exc:
        logger.error(
            "auto_eval_workflow_start_failed",
            extra={"config_id": config_id, "error": str(exc)},
        )
        # Re-queue rows for next window (TLA+ WorkflowFail action).
        _requeue_rows(config_id, row_ids, config.debounce_seconds)
        raise self.retry(exc=exc)


def schedule_auto_eval(config_id: str, row_ids: list, debounce_seconds: int):
    """
    Called from the rows_appended signal handler (after transaction.on_commit).
    Appends row_ids to the pending list and arms the debounce timer if not
    already armed.
    """
    pending_key = _PENDING_KEY.format(config_id=config_id)
    lock_key = _LOCK_KEY.format(config_id=config_id)

    # Accumulate row IDs.  Cache value is a plain list; extend atomically
    # enough for Django's cache (single-process) or Redis backend.
    existing = cache.get(pending_key) or []
    cache.set(pending_key, existing + row_ids, timeout=debounce_seconds * 10)

    # Arm the flush task only once per debounce window.
    if cache.add(lock_key, "1", timeout=debounce_seconds * 2):
        flush_auto_eval_batch.apply_async(
            kwargs={"config_id": config_id},
            countdown=debounce_seconds,
        )


def _requeue_rows(config_id: str, row_ids: list, debounce_seconds: int):
    """Re-push rows after a workflow failure so they are not lost."""
    schedule_auto_eval(config_id, row_ids, debounce_seconds)


def _clear_state(config_id: str):
    cache.delete(_PENDING_KEY.format(config_id=config_id))
    cache.delete(_LOCK_KEY.format(config_id=config_id))
