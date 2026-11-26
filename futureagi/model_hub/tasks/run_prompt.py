from datetime import timedelta

import structlog
from django.db import close_old_connections
from django.utils import timezone

from model_hub.models.choices import StatusType
from model_hub.models.run_prompt import RunPrompter
from model_hub.views.run_prompt import RunPrompts
from tfc.temporal import temporal_activity
from tfc.utils.distributed_locks import distributed_lock_manager
from tfc.utils.distributed_state import DistributedEvaluationTracker

logger = structlog.get_logger(__name__)


# How long a prompt can be in RUNNING status before considered stuck
STUCK_RUNNING_THRESHOLD_HOURS = 1

# Distributed tracker for run prompts (separate key prefix from evaluations)
run_prompt_tracker = DistributedEvaluationTracker()
run_prompt_tracker.key_prefix = "running_prompt:"


def process_not_started_prompt(run_prompt_id):
    """Process a newly created run prompt with distributed tracking."""
    close_old_connections()
    tracking_key = f"prompt_{run_prompt_id}"

    logger.info(
        "process_not_started_prompt_starting",
        run_prompt_id=str(run_prompt_id),
        instance_id=run_prompt_tracker.instance_id,
    )

    try:
        # Check if already running on another instance
        if run_prompt_tracker.is_running(run_prompt_id):
            running_info = run_prompt_tracker.get_running_info(run_prompt_id)
            if (
                running_info
                and running_info.instance_id != run_prompt_tracker.instance_id
            ):
                logger.warning(
                    "process_not_started_prompt_already_running",
                    run_prompt_id=str(run_prompt_id),
                    running_on=running_info.instance_id,
                    current_instance=run_prompt_tracker.instance_id,
                )
                return

        # Use distributed lock to prevent race conditions
        with distributed_lock_manager.lock(
            f"run_prompt:{run_prompt_id}",
            timeout=3600,  # 1 hour max
            blocking_timeout=10,
        ):
            # Double-check after acquiring lock
            if run_prompt_tracker.is_running(run_prompt_id):
                existing = run_prompt_tracker.get_running_info(run_prompt_id)
                if existing and existing.instance_id != run_prompt_tracker.instance_id:
                    logger.warning(
                        "process_not_started_prompt_started_elsewhere",
                        run_prompt_id=str(run_prompt_id),
                    )
                    return

            # Mark as running in distributed tracker
            run_prompt_tracker.mark_running(
                run_prompt_id,
                runner_info={
                    "type": "not_started",
                    "instance": run_prompt_tracker.instance_id,
                },
            )

            try:
                logger.info(
                    "process_not_started_prompt_executing",
                    run_prompt_id=str(run_prompt_id),
                )
                runner = RunPrompts(run_prompt_id=run_prompt_id)
                runner.run_prompt()
                logger.info(
                    "process_not_started_prompt_completed",
                    run_prompt_id=str(run_prompt_id),
                )
            finally:
                # Always clean up distributed tracking
                run_prompt_tracker.mark_completed(run_prompt_id)

    except Exception as e:
        logger.exception(
            "process_not_started_prompt_failed",
            run_prompt_id=str(run_prompt_id),
            error=str(e),
            error_type=type(e).__name__,
        )
        # Clean up distributed tracking on failure
        run_prompt_tracker.mark_completed(run_prompt_id)
        # Set status to FAILED so it doesn't get stuck in RUNNING
        try:
            RunPrompter.objects.filter(id=run_prompt_id).update(
                status=StatusType.FAILED.value
            )
            logger.info(
                "process_not_started_prompt_marked_failed",
                run_prompt_id=str(run_prompt_id),
            )
        except Exception as db_error:
            logger.error(
                "process_not_started_prompt_failed_to_update_status",
                run_prompt_id=str(run_prompt_id),
                error=str(db_error),
            )
        raise
    finally:
        close_old_connections()


def process_editing_prompt(run_prompt_id):
    """Process an edited/re-run prompt with distributed tracking."""
    close_old_connections()

    logger.info(
        "process_editing_prompt_starting",
        run_prompt_id=str(run_prompt_id),
        instance_id=run_prompt_tracker.instance_id,
    )

    try:
        # Check if already running on another instance
        if run_prompt_tracker.is_running(run_prompt_id):
            running_info = run_prompt_tracker.get_running_info(run_prompt_id)
            if (
                running_info
                and running_info.instance_id != run_prompt_tracker.instance_id
            ):
                logger.warning(
                    "process_editing_prompt_already_running",
                    run_prompt_id=str(run_prompt_id),
                    running_on=running_info.instance_id,
                    current_instance=run_prompt_tracker.instance_id,
                )
                # Request cancellation of the existing run
                run_prompt_tracker.request_cancel(
                    run_prompt_id, reason="Edit requested"
                )
                logger.info(
                    "process_editing_prompt_cancel_requested",
                    run_prompt_id=str(run_prompt_id),
                )

        # Use distributed lock to prevent race conditions
        with distributed_lock_manager.lock(
            f"run_prompt:{run_prompt_id}",
            timeout=3600,  # 1 hour max
            blocking_timeout=30,  # Wait longer for edit as we may be waiting for cancel
        ):
            # Mark as running in distributed tracker
            run_prompt_tracker.mark_running(
                run_prompt_id,
                runner_info={
                    "type": "editing",
                    "instance": run_prompt_tracker.instance_id,
                },
            )

            try:
                logger.info(
                    "process_editing_prompt_executing",
                    run_prompt_id=str(run_prompt_id),
                )
                runner = RunPrompts(run_prompt_id=run_prompt_id)
                runner.run_prompt(edit_mode=True)
                logger.info(
                    "process_editing_prompt_completed",
                    run_prompt_id=str(run_prompt_id),
                )
            finally:
                # Always clean up distributed tracking
                run_prompt_tracker.mark_completed(run_prompt_id)

    except Exception as e:
        logger.exception(
            "process_editing_prompt_failed",
            run_prompt_id=str(run_prompt_id),
            error=str(e),
            error_type=type(e).__name__,
        )
        # Clean up distributed tracking on failure
        run_prompt_tracker.mark_completed(run_prompt_id)
        # Set status to FAILED so it doesn't get stuck in RUNNING
        try:
            RunPrompter.objects.filter(id=run_prompt_id).update(
                status=StatusType.FAILED.value
            )
            logger.info(
                "process_editing_prompt_marked_failed",
                run_prompt_id=str(run_prompt_id),
            )
        except Exception as db_error:
            logger.error(
                "process_editing_prompt_failed_to_update_status",
                run_prompt_id=str(run_prompt_id),
                error=str(db_error),
            )
        raise
    finally:
        close_old_connections()


@temporal_activity(time_limit=3600, queue="tasks_l")
def process_prompts_single(prompt):
    """
    Process a single run prompt. This activity is triggered directly from the API
    when a run prompt is created or edited (no scheduler needed).

    Uses distributed locking to prevent duplicate processing across instances.

    Args:
        prompt: dict with "type" ("not_started" or "editing") and "prompt_id"
    """
    close_old_connections()
    prompt_id = prompt["prompt_id"]
    prompt_type = prompt.get("type", "unknown")

    logger.info(
        "process_prompts_single_starting",
        prompt_id=prompt_id,
        prompt_type=prompt_type,
        instance_id=run_prompt_tracker.instance_id,
    )

    try:
        prompt_obj = RunPrompter.objects.get(id=prompt_id)

        # Idempotency check - verify status is still RUNNING
        if prompt_obj.status != StatusType.RUNNING.value:
            logger.warning(
                "process_prompts_single_skip_not_running",
                prompt_id=prompt_id,
                current_status=prompt_obj.status,
                expected_status=StatusType.RUNNING.value,
            )
            return

        # Check if already being processed by another instance
        if run_prompt_tracker.is_running(prompt_id):
            running_info = run_prompt_tracker.get_running_info(prompt_id)
            if (
                running_info
                and running_info.instance_id != run_prompt_tracker.instance_id
            ):
                logger.warning(
                    "process_prompts_single_already_running",
                    prompt_id=prompt_id,
                    running_on=running_info.instance_id,
                    current_instance=run_prompt_tracker.instance_id,
                )
                return

        if prompt_type == "not_started":
            process_not_started_prompt(prompt_id)
        elif prompt_type == "editing":
            process_editing_prompt(prompt_id)
        else:
            logger.error(
                "process_prompts_single_unknown_type",
                prompt_id=prompt_id,
                prompt_type=prompt_type,
            )

        logger.info(
            "process_prompts_single_finished",
            prompt_id=prompt_id,
            prompt_type=prompt_type,
        )

    except RunPrompter.DoesNotExist:
        logger.error(
            "process_prompts_single_not_found",
            prompt_id=prompt_id,
            prompt_type=prompt_type,
        )
    except Exception as e:
        logger.exception(
            "process_prompts_single_error",
            prompt_id=prompt_id,
            prompt_type=prompt_type,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise
    finally:
        close_old_connections()


@temporal_activity(time_limit=300, queue="default")
def recover_stuck_run_prompts():
    """
    Recovery task for run prompts stuck in RUNNING status.

    This handles cases where:
    - API crashed between setting status=RUNNING and triggering workflow
    - Workflow failed without proper error handling
    - Worker crashed mid-processing

    Also cleans up stale entries from the distributed tracker.
    Runs periodically to find and recover stuck prompts.
    """
    close_old_connections()

    try:
        threshold = timezone.now() - timedelta(hours=STUCK_RUNNING_THRESHOLD_HOURS)

        # Find prompts stuck in RUNNING for too long
        stuck_prompts = RunPrompter.objects.filter(
            status=StatusType.RUNNING.value,
            updated_at__lt=threshold,
        ).values_list("id", flat=True)[:20]  # Process max 20 at a time

        stuck_count = len(stuck_prompts)
        if stuck_count == 0:
            logger.debug("recover_stuck_run_prompts: no stuck prompts found")
        else:
            logger.warning(
                "recover_stuck_run_prompts_found",
                count=stuck_count,
                prompt_ids=[str(p) for p in stuck_prompts],
            )

            # Mark stuck prompts as FAILED
            # They've been running for > threshold hours without update, likely dead
            RunPrompter.objects.filter(id__in=stuck_prompts).update(
                status=StatusType.FAILED.value
            )

            # Clean up distributed tracker entries for stuck prompts
            for prompt_id in stuck_prompts:
                run_prompt_tracker.mark_completed(prompt_id)
                run_prompt_tracker.clear_cancel_flag(prompt_id)

            logger.info(
                "recover_stuck_run_prompts_marked_failed",
                count=stuck_count,
            )

        # Also clean up stale entries in distributed tracker
        stale_cleaned = run_prompt_tracker.cleanup_stale(
            max_age_hours=STUCK_RUNNING_THRESHOLD_HOURS * 2
        )
        if stale_cleaned > 0:
            logger.info(
                "recover_stuck_run_prompts_cleaned_stale_tracker_entries",
                count=stale_cleaned,
            )

    except Exception as e:
        logger.exception("recover_stuck_run_prompts_error", error=str(e))
    finally:
        close_old_connections()


def get_running_prompts_status() -> list:
    """
    Get status of all running prompts across all instances.

    Useful for debugging and monitoring dashboards.

    Returns:
        List of dicts with prompt info including instance, started_at, etc.
    """
    running = run_prompt_tracker.get_all_running()
    return [
        {
            "prompt_id": info.task_id,
            "instance": info.instance_id,
            "started_at": info.started_at,
            "cancel_requested": info.cancel_requested,
            "metadata": info.metadata,
        }
        for info in running
    ]


def cancel_running_prompt(prompt_id: int, reason: str = "Manual cancellation") -> bool:
    """
    Request cancellation of a running prompt.

    This sets a cancel flag that the runner should check periodically.

    Args:
        prompt_id: The prompt ID to cancel.
        reason: Reason for cancellation.

    Returns:
        True if cancel request was sent.
    """
    if run_prompt_tracker.is_running(prompt_id):
        return run_prompt_tracker.request_cancel(prompt_id, reason=reason)
    return False
