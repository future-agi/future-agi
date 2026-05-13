"""
Celery task: flush accumulated row IDs after debounce window and start
a Temporal eval batch workflow.

The debounce protocol (race-free, proven by DatasetAutoEval.tla):

  1. On each rows_appended signal: RPUSH row IDs atomically into a Redis
     list (one operation per call — no read-modify-write race).
  2. SET a lock key NX (only-if-not-exists) with TTL = debounce_seconds.
     The first signal within the window acquires the lock and schedules
     this task; subsequent signals within the window skip scheduling.
  3. This task fires after countdown = debounce_seconds, drains the list
     atomically via a Lua script (LRANGE + DEL in one round-trip), then
     starts the Temporal workflow.

TLA+ invariant NoDuplicateEval is maintained because:
  - Row IDs enter Redis exactly once (RPUSH after committed bulk_create).
  - The drain is atomic (Lua LRANGE+DEL): no concurrent flush sees the
    same IDs.
  - If the Temporal workflow fails, IDs are re-pushed (WorkflowFail
    action in the spec) under a new debounce window; no Celery retry
    fires (max_retries=0) to avoid a double-flush race.
"""

import logging
import uuid

import django
from celery import shared_task
from django.core.cache import cache

logger = logging.getLogger(__name__)

_PENDING_KEY = "auto_eval:{config_id}:pending"
_LOCK_KEY = "auto_eval:{config_id}:lock"


# Lua script: atomically read the whole list and delete it in one round-trip.
_DRAIN_LUA = """
local result = redis.call('LRANGE', KEYS[1], 0, -1)
redis.call('DEL', KEYS[1])
return result
"""


def _atomic_drain(pending_key: str) -> list:
    """
    Fetch and clear the Redis list in one atomic Lua script.
    Falls back to non-atomic get+delete for non-Redis backends (e.g. locmem).
    """
    try:
        from django_redis import get_redis_connection

        r = get_redis_connection("default")
        versioned = cache.make_key(pending_key)
        drain = r.register_script(_DRAIN_LUA)
        raw = drain(keys=[versioned])
        return [x.decode() if isinstance(x, bytes) else x for x in raw] if raw else []
    except Exception:
        val = cache.get(pending_key)
        cache.delete(pending_key)
        return val or []


def _rpush_pending(pending_key: str, row_ids: list, timeout: int) -> None:
    """
    Atomically append row_ids to the Redis list.  A single RPUSH is atomic
    so concurrent callers can't lose each other's IDs (unlike cache.get+set).
    Falls back to non-atomic extend for non-Redis backends.
    """
    if not row_ids:
        return
    try:
        from django_redis import get_redis_connection

        r = get_redis_connection("default")
        versioned = cache.make_key(pending_key)
        r.rpush(versioned, *[str(i) for i in row_ids])
        r.expire(versioned, timeout)
    except Exception:
        existing = cache.get(pending_key) or []
        cache.set(pending_key, existing + row_ids, timeout=timeout)


@shared_task(bind=True, max_retries=0)
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

    if config.created_by is None:
        logger.warning(
            "auto_eval_skipped_no_user",
            extra={"config_id": config_id},
        )
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
        # Delete the just-created evaluations (TLA+ WorkflowFail cleanup).
        from model_hub.models.evaluation import Evaluation

        Evaluation.objects.filter(id__in=evaluation_ids).delete()
        # Re-queue rows under a fresh debounce window.  No Celery retry: a
        # retry fires after default_retry_delay=30 s, which races with the
        # new debounce window and causes double-flush when
        # debounce_seconds < 30.
        _requeue_rows(config_id, row_ids, config.debounce_seconds)


def schedule_auto_eval(config_id: str, row_ids: list, debounce_seconds: int):
    """
    Called from the rows_appended signal handler (after transaction.on_commit).
    Appends row_ids to the pending list and arms the debounce timer if not
    already armed.
    """
    pending_key = _PENDING_KEY.format(config_id=config_id)
    lock_key = _LOCK_KEY.format(config_id=config_id)

    # Atomically append row IDs to the Redis list (RPUSH is single-op
    # atomic — no read-modify-write race between concurrent callers).
    _rpush_pending(pending_key, row_ids, timeout=debounce_seconds * 10)

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

    Idempotency guard: rows that already have a PENDING/PROCESSING Evaluation
    from a previous (partial) run of this config are skipped to prevent
    duplicate evaluation records (TLA+ NoDuplicateEval).
    """
    from model_hub.models.develop_dataset import Cell, Row
    from model_hub.models.evaluation import Evaluation, StatusChoices

    # Guard against duplicates when flush_auto_eval_batch is re-invoked for
    # rows whose Evaluation records survived a prior partial failure (e.g. the
    # process died after bulk_create but before the workflow-fail cleanup ran).
    surviving_ids: list[str] = []
    already_surviving_qs = Evaluation.objects.filter(
        eval_template=config.eval_template,
        organization=config.organization,
        status__in=[StatusChoices.PENDING, StatusChoices.PROCESSING],
        metadata__source="auto_eval",
        metadata__source_config_id=str(config.id),
    ).exclude(metadata__row_id=None)
    already_created_row_ids = set(
        str(v)
        for v in already_surviving_qs.values_list("metadata__row_id", flat=True)
    )
    if already_created_row_ids:
        skipped = [r for r in row_ids if str(r) in already_created_row_ids]
        if skipped:
            logger.info(
                "auto_eval_skip_already_created",
                extra={
                    "config_id": str(config.id),
                    "skipped_count": len(skipped),
                },
            )
            # Collect surviving eval IDs — they must reach Temporal even though
            # we are not re-creating them.
            surviving_ids = [
                str(i)
                for i in already_surviving_qs.filter(
                    metadata__row_id__in=[str(r) for r in skipped]
                ).values_list("id", flat=True)
            ]
        row_ids = [r for r in row_ids if str(r) not in already_created_row_ids]

    rows = list(
        Row.objects.filter(id__in=row_ids, deleted=False).prefetch_related(
            "cell_set__column"
        )
    )
    if not rows:
        return surviving_ids

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
    return [str(e.id) for e in created] + surviving_ids
