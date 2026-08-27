import json
import threading
from datetime import datetime, timedelta

import structlog
from django.db import close_old_connections
from django.db.models import CharField, Exists, OuterRef
from django.db.models.functions import Cast
from django.utils import timezone

from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell
from model_hub.models.run_prompt import RunPrompter
from model_hub.views.run_prompt import RunPrompts
from tfc.temporal import temporal_activity
from tfc.utils.distributed_locks import LockAcquisitionError, distributed_lock_manager
from tfc.utils.distributed_state import DistributedEvaluationTracker

logger = structlog.get_logger(__name__)


# How long a prompt can be in RUNNING status before considered stuck
STUCK_RUNNING_THRESHOLD_HOURS = 1


LEASE_RENEW_INTERVAL_SECONDS = 60
LEASE_TTL_SECONDS = 300  # ~ Temporal heartbeat_timeout (5 min)
LEASE_FRESH_SECONDS = 3 * LEASE_RENEW_INTERVAL_SECONDS
LOCK_TTL_SECONDS = 600  # lock auto-expiry; renewed alongside the lease

# Distributed tracker for run prompts (separate key prefix from evaluations).
run_prompt_tracker = DistributedEvaluationTracker(default_ttl=LEASE_TTL_SECONDS)
run_prompt_tracker.key_prefix = "running_prompt:"


class PromptAlreadyRunningElsewhere(Exception):
    """Another live instance holds the lease on this prompt.

    Raised (not swallowed) so the Temporal attempt is recorded as failed and
    retried later. Returning success here is a dead-end: Temporal would mark
    the workflow complete and the prompt would never be reprocessed even
    after the other owner dies.
    """


class OwnershipLease:
    """Keeps this worker's claim on a run prompt alive while it processes.

    A daemon thread renews the distributed tracker entry (liveness lease) and
    extends the Redis lock every LEASE_RENEW_INTERVAL_SECONDS. Without
    renewal the fixed TTLs (tracker 5 min, lock 10 min) are shorter than a
    legitimate run, so both must be kept alive for as long as the worker
    actually is.
    """

    def __init__(self, prompt_id, lock=None):
        self._prompt_id = prompt_id
        self._lock = lock
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(
            target=self._renew_loop,
            name=f"run-prompt-lease-{self._prompt_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return False

    def _renew_loop(self):
        while not self._stop.wait(LEASE_RENEW_INTERVAL_SECONDS):
            self.renew_once()

    def renew_once(self):
        try:
            run_prompt_tracker.refresh_running(
                self._prompt_id, ttl=LEASE_TTL_SECONDS
            )
        except Exception as e:
            logger.warning(
                "run_prompt_lease_refresh_failed",
                prompt_id=str(self._prompt_id),
                error=str(e),
            )
        # Local threading-lock fallback has no extend(); skip it.
        if self._lock is not None and hasattr(self._lock, "extend"):
            try:
                self._lock.extend(LOCK_TTL_SECONDS, replace_ttl=True)
            except Exception as e:
                logger.warning(
                    "run_prompt_lock_extend_failed",
                    prompt_id=str(self._prompt_id),
                    error=str(e),
                )


def _get_fresh_lease(prompt_id):
    """Return the tracker entry if a live worker holds it, else None.

    A lease is live when its last renewal (or start) is within
    LEASE_FRESH_SECONDS; owners renew every LEASE_RENEW_INTERVAL_SECONDS, so
    anything older belongs to a dead worker and is reclaimable.
    """
    info = run_prompt_tracker.get_running_info(prompt_id)
    if not info:
        return None
    stamp = (info.metadata or {}).get("renewed_at") or info.started_at
    try:
        renewed = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return info  # unknown age: assume live, the TTL will purge it
    if (datetime.utcnow() - renewed).total_seconds() < LEASE_FRESH_SECONDS:
        return info
    return None


def _held_by_other_live_instance(prompt_id) -> bool:
    """True if another live instance holds a fresh lease on this prompt."""
    lease = _get_fresh_lease(prompt_id)
    return bool(lease and lease.instance_id != run_prompt_tracker.instance_id)


def _claim_prompt(run_prompt_id, runner_info):
    """Take ownership of the prompt in the tracker (caller holds the lock).

    Clears any stale lease left behind by a dead worker first, so a Temporal
    retry can reclaim a crashed run instead of dead-ending on the leftover
    entry.
    """
    stale = run_prompt_tracker.get_running_info(run_prompt_id)
    if (
        stale
        and stale.instance_id != run_prompt_tracker.instance_id
        and _get_fresh_lease(run_prompt_id) is None
    ):
        logger.warning(
            "run_prompt_reclaiming_stale_lease",
            run_prompt_id=str(run_prompt_id),
            previous_owner=stale.instance_id,
        )
        run_prompt_tracker.mark_completed(run_prompt_id)

    run_prompt_tracker.mark_running(
        run_prompt_id, runner_info=runner_info, ttl=LEASE_TTL_SECONDS
    )


def process_not_started_prompt(run_prompt_id):
    """Process a newly created run prompt with distributed tracking."""
    close_old_connections()

    logger.info(
        "process_not_started_prompt_starting",
        run_prompt_id=str(run_prompt_id),
        instance_id=run_prompt_tracker.instance_id,
    )

    try:
        # Fail fast (and retryably) if a live instance already owns this
        # prompt. Stale leases from dead workers do not count.
        if _held_by_other_live_instance(run_prompt_id):
            logger.warning(
                "process_not_started_prompt_already_running",
                run_prompt_id=str(run_prompt_id),
                current_instance=run_prompt_tracker.instance_id,
            )
            raise PromptAlreadyRunningElsewhere(str(run_prompt_id))

        # Use distributed lock to prevent race conditions
        with distributed_lock_manager.lock(
            f"run_prompt:{run_prompt_id}",
            timeout=LOCK_TTL_SECONDS,  # renewed by OwnershipLease below
            blocking_timeout=10,
        ) as lock:
            # Double-check after acquiring lock
            if _held_by_other_live_instance(run_prompt_id):
                logger.warning(
                    "process_not_started_prompt_started_elsewhere",
                    run_prompt_id=str(run_prompt_id),
                )
                raise PromptAlreadyRunningElsewhere(str(run_prompt_id))

            _claim_prompt(
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
                with OwnershipLease(run_prompt_id, lock=lock):
                    runner = RunPrompts(run_prompt_id=run_prompt_id)
                    runner.run_prompt()
                logger.info(
                    "process_not_started_prompt_completed",
                    run_prompt_id=str(run_prompt_id),
                )
            finally:
                # Always clean up distributed tracking
                run_prompt_tracker.mark_completed(run_prompt_id)

    except (PromptAlreadyRunningElsewhere, LockAcquisitionError):
        # Another live instance owns this prompt. Do NOT mark the prompt
        # FAILED (it is being processed) and do NOT touch the tracker entry
        # (we don't own it) — just fail this attempt so Temporal retries.
        raise
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
        # An edit preempts a live run: request cancellation, then take over
        # once the lock is released (or expires).
        if _held_by_other_live_instance(run_prompt_id):
            logger.warning(
                "process_editing_prompt_already_running",
                run_prompt_id=str(run_prompt_id),
                current_instance=run_prompt_tracker.instance_id,
            )
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
            timeout=LOCK_TTL_SECONDS,  # renewed by OwnershipLease below
            blocking_timeout=30,  # Wait longer for edit as we may be waiting for cancel
        ) as lock:
            _claim_prompt(
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
                with OwnershipLease(run_prompt_id, lock=lock):
                    runner = RunPrompts(run_prompt_id=run_prompt_id)
                    runner.run_prompt(edit_mode=True)
                logger.info(
                    "process_editing_prompt_completed",
                    run_prompt_id=str(run_prompt_id),
                )
            finally:
                # Always clean up distributed tracking
                run_prompt_tracker.mark_completed(run_prompt_id)

    except LockAcquisitionError:
        # The current owner did not release within blocking_timeout. Fail
        # this attempt (Temporal retries) without marking the prompt FAILED —
        # the owner is still processing it.
        raise
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


@temporal_activity(time_limit=4 * 3600, queue="tasks_l")
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

        # If a live instance owns this prompt, fail the attempt so Temporal
        # retries later. Returning success here would end the workflow while
        # the prompt might still die unprocessed (retry dead-end).
        if prompt_type != "editing" and _held_by_other_live_instance(prompt_id):
            logger.warning(
                "process_prompts_single_already_running",
                prompt_id=prompt_id,
                current_instance=run_prompt_tracker.instance_id,
            )
            raise PromptAlreadyRunningElsewhere(str(prompt_id))

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
    except (PromptAlreadyRunningElsewhere, LockAcquisitionError):
        raise
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

        # Cell-write liveness is applied in SQL, before the batch slice, so
        # live long runs cannot crowd dead prompts out of the batch. Oldest
        # first so the longest-dead prompts are recovered first.
        # (RunPrompter.updated_at is not refreshed while rows are processed,
        # so the status/updated_at filter alone would flag healthy long runs.)
        recent_cell_writes = Cell.objects.filter(
            column__source=SourceChoices.RUN_PROMPT.value,
            column__source_id=Cast(OuterRef("id"), output_field=CharField()),
            updated_at__gte=threshold,
        )
        candidate_prompts = list(
            RunPrompter.objects.filter(
                status=StatusType.RUNNING.value,
                updated_at__lt=threshold,
            )
            .filter(~Exists(recent_cell_writes))
            .order_by("updated_at")
            .values_list("id", flat=True)[:20]  # Process max 20 at a time
        )

        # Primary liveness signal: the worker's renewed ownership lease.
        # Cell writes above are the fallback for when Redis is unavailable.
        stuck_prompts = [
            p for p in candidate_prompts if _get_fresh_lease(p) is None
        ]

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

            # Also flip their cells stuck in RUNNING to ERROR so the UI stops
            # spinning forever. "Running" (StatusType) is matched too because
            # older reruns wrote the wrong enum into Cell.status.
            timeout_message = (
                "Run prompt timed out or was interrupted. Please rerun this cell."
            )
            stuck_cells_updated = Cell.objects.filter(
                column__source=SourceChoices.RUN_PROMPT.value,
                column__source_id__in=[str(p) for p in stuck_prompts],
                status__in=[CellStatus.RUNNING.value, StatusType.RUNNING.value],
                deleted=False,
            ).update(
                status=CellStatus.ERROR.value,
                value=timeout_message,
                value_infos=json.dumps({"reason": timeout_message}),
            )

            # Clean up distributed tracker entries for stuck prompts
            for prompt_id in stuck_prompts:
                run_prompt_tracker.mark_completed(prompt_id)
                run_prompt_tracker.clear_cancel_flag(prompt_id)

            logger.info(
                "recover_stuck_run_prompts_marked_failed",
                count=stuck_count,
                stuck_cells_updated=stuck_cells_updated,
            )

        # Safety net for tracker entries whose TTL somehow failed to fire.
        # Must exceed the longest legitimate run (activity limit is 4h):
        # cleanup keys off started_at, and deleting a live long run's lease
        # would break both dedup and the liveness signal above.
        stale_cleaned = run_prompt_tracker.cleanup_stale(max_age_hours=5)
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
