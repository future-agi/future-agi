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


def _atomic_drain(pending_key: str) -> list:
    """
    Fetch and delete the pending list in one operation to eliminate the
    get→delete race window.  Uses Redis GETDEL when django-redis is available;
    falls back to non-atomic get+delete for other backends (e.g. test locmem).
    """
    try:
        import pickle

        from django_redis import get_redis_connection

        r = get_redis_connection("default")
        versioned = cache.make_key(pending_key)
        raw = r.execute_command("GETDEL", versioned)
        return pickle.loads(raw) if raw else []
    except Exception:
        val = cache.get(pending_key)
        cache.delete(pending_key)
        return val or []


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
            "eval_template", "dataset", "created_by"
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

    raw = _atomic_drain(pending_key)
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

    # Create evaluation records before starting the workflow.  Separated from
    # the workflow-start try/except so a workflow failure triggers deletion of
    # these records rather than re-creating them on retry (TLA+ NoDuplicateEval).
    evaluation_ids = _create_evaluations_from_rows(config, row_ids)
    if not evaluation_ids:
        logger.warning(
            "auto_eval_no_evaluations_created",
            extra={"config_id": config_id, "row_count": len(row_ids)},
        )
        return

    from tfc.temporal.evaluations.client import start_evaluation_batch_workflow

    try:
        start_evaluation_batch_workflow(
            evaluation_ids=evaluation_ids,
            max_concurrent=config.max_concurrent,
            workflow_id_prefix=f"auto_eval_{config_id[:8]}",
        )
    except Exception as exc:
        logger.error(
            "auto_eval_workflow_start_failed",
            extra={"config_id": config_id, "error": str(exc)},
        )
        # Delete the just-created evaluations so the retry starts clean.
        from model_hub.models.evaluation import Evaluation

        Evaluation.objects.filter(id__in=evaluation_ids).delete()
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


def _create_evaluations_from_rows(config, row_ids: list) -> list:
    """
    For each Row ID, build an Evaluation.input_data from the row's cells
    using config.column_mapping, then bulk-create Evaluation objects in PENDING
    state. Returns a list of evaluation ID strings for the Temporal batch workflow.

    column_mapping maps dataset column names → eval template input field names.
    Example: {"user_input": "input", "response": "output"}

    Rows whose cells cannot be mapped are skipped with a warning (jitokim Q3:
    permissive with per-row failure logging rather than strict validation).
    """
    from model_hub.models.develop_dataset import Cell, Row
    from model_hub.models.evaluation import Evaluation, StatusChoices

    rows = list(
        Row.objects.filter(id__in=row_ids, deleted=False).prefetch_related(
            "cell_set__column"
        )
    )
    if not rows:
        return []

    mapping = config.column_mapping  # {"col_name": "input_field"}

    evaluations = []
    for row in rows:
        cells_by_col = {
            cell.column.name: cell.value
            for cell in row.cell_set.all()
            if hasattr(cell, "column") and cell.column
        }

        if not mapping:
            # No explicit mapping: pass all cell values as-is.
            input_data = cells_by_col
        else:
            input_data = {}
            missing = []
            for col_name, input_field in mapping.items():
                if col_name in cells_by_col:
                    input_data[input_field] = cells_by_col[col_name]
                else:
                    missing.append(col_name)
            if missing:
                logger.warning(
                    "auto_eval_row_column_missing",
                    extra={
                        "config_id": str(config.id),
                        "row_id": str(row.id),
                        "missing_columns": missing,
                    },
                )

        evaluations.append(
            Evaluation(
                eval_template=config.eval_template,
                input_data=input_data,
                user=config.created_by,
                organization=config.organization,
                workspace=config.workspace,
                status=StatusChoices.PENDING,
                metadata={
                    "source": "auto_eval",
                    "source_config_id": str(config.id),
                    "dataset_id": str(config.dataset_id),
                    "row_id": str(row.id),
                },
            )
        )

    created = Evaluation.objects.bulk_create(evaluations)
    return [str(e.id) for e in created]
