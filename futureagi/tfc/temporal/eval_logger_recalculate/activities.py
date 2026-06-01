"""Activities that touch the DB. Django imports stay here, not in workflows.py."""

from django.db import close_old_connections
from django.utils import timezone
from temporalio import activity

from tfc.telemetry import otel_sync_to_async
from tfc.temporal.eval_logger_recalculate.types import (
    DispatchRerunInput,
    DispatchRerunOutput,
    SoftDeleteSiblingsInput,
    SoftDeleteSiblingsOutput,
)


def _soft_delete_siblings_sync(
    eval_task_id: str, custom_eval_config_id: str
) -> int:
    from tracer.models.observation_span import EvalLogger

    return EvalLogger.objects.filter(
        eval_task_id=eval_task_id,
        custom_eval_config_id=custom_eval_config_id,
        deleted=False,
    ).update(deleted=True, deleted_at=timezone.now())


def _dispatch_rerun_sync(input: DispatchRerunInput) -> DispatchRerunOutput:
    from tracer.models.observation_span import EvalTargetType
    from tracer.utils.eval import (
        evaluate_observation_span_observe,
        evaluate_trace_observe,
        evaluate_trace_session_observe,
    )

    if input.target_type == EvalTargetType.SPAN:
        ok = evaluate_observation_span_observe(
            input.observation_span_id,
            input.custom_eval_config_id,
            input.eval_task_id,
            input.feedback_id,
        )
    elif input.target_type == EvalTargetType.TRACE:
        ok = evaluate_trace_observe(
            input.trace_id,
            input.custom_eval_config_id,
            input.eval_task_id,
            input.feedback_id,
        )
    elif input.target_type == EvalTargetType.SESSION:
        ok = evaluate_trace_session_observe(
            input.trace_session_id,
            input.custom_eval_config_id,
            input.eval_task_id,
            input.feedback_id,
        )
    else:
        return DispatchRerunOutput(
            target_type=input.target_type,
            status="failed",
            error=f"Unhandled target_type={input.target_type!r}",
        )
    return DispatchRerunOutput(
        target_type=input.target_type,
        status="completed" if ok else "failed",
    )


def _soft_delete_siblings_with_connection_lifecycle(
    eval_task_id: str, custom_eval_config_id: str
) -> int:
    close_old_connections()
    try:
        return _soft_delete_siblings_sync(eval_task_id, custom_eval_config_id)
    finally:
        close_old_connections()


@activity.defn
async def soft_delete_sibling_eval_loggers_activity(
    input: SoftDeleteSiblingsInput,
) -> SoftDeleteSiblingsOutput:
    """One UPDATE soft-deleting every live sibling EvalLogger under the eval task."""
    activity.logger.info(
        f"Soft-deleting siblings for eval_task={input.eval_task_id} "
        f"eval_config={input.custom_eval_config_id}"
    )
    deleted = await otel_sync_to_async(
        _soft_delete_siblings_with_connection_lifecycle, thread_sensitive=False
    )(input.eval_task_id, input.custom_eval_config_id)
    activity.logger.info(f"Soft-deleted {deleted} rows")
    return SoftDeleteSiblingsOutput(deleted_count=deleted)


def _dispatch_rerun_with_connection_lifecycle(
    input: DispatchRerunInput,
) -> DispatchRerunOutput:
    close_old_connections()
    try:
        return _dispatch_rerun_sync(input)
    finally:
        close_old_connections()


@activity.defn
async def dispatch_rerun_activity(input: DispatchRerunInput) -> DispatchRerunOutput:
    """Dispatch the right per-target evaluate_*_observe for one row.

    The prior bulk soft-delete already cleared the live row; the rerun is
    idempotent because each evaluate_*_observe dedups on
    (eval_task_id, target, eval_config).
    """
    from tfc.temporal.common.heartbeat import Heartbeater

    activity.logger.info(
        f"Dispatching rerun target_type={input.target_type} "
        f"eval_task={input.eval_task_id}"
    )
    try:
        async with Heartbeater():
            result = await otel_sync_to_async(
                _dispatch_rerun_with_connection_lifecycle, thread_sensitive=False
            )(input)
        return result
    except Exception as e:
        activity.logger.exception(
            f"Error in dispatch_rerun_activity target_type={input.target_type}: {e}"
        )
        return DispatchRerunOutput(
            target_type=input.target_type, status="failed", error=str(e)
        )


__all__ = [
    "soft_delete_sibling_eval_loggers_activity",
    "dispatch_rerun_activity",
]
