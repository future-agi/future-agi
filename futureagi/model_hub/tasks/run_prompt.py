import threading
import uuid
from datetime import datetime, timedelta

import structlog
from django.db import close_old_connections
from django.db.models import CharField, Exists, OuterRef
from django.db.models.functions import Cast
from django.utils import timezone
from redis.exceptions import LockNotOwnedError

from model_hub.models.choices import SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell
from model_hub.models.run_prompt import RunPrompter
from model_hub.views.run_prompt import RunPrompts, fail_pending_run_prompt_cells
from tfc.logging.temporal.context import try_activity_info
from tfc.temporal import temporal_activity
from tfc.utils.distributed_locks import LockContendedError, distributed_lock_manager
from tfc.utils.distributed_state import DistributedEvaluationTracker

logger = structlog.get_logger(__name__)


# How long a prompt can be in RUNNING status before considered stuck
STUCK_RUNNING_THRESHOLD_HOURS = 1


LEASE_RENEW_INTERVAL_SECONDS = 60
LEASE_TTL_SECONDS = 300  # ~ Temporal heartbeat_timeout (5 min)
# Renewed every 60s; must be < ACTIVITY_HEARTBEAT_TIMEOUT so a dead worker's lock is gone before its retry.
LOCK_TTL_SECONDS = 4 * LEASE_RENEW_INTERVAL_SECONDS  # 240s
# A lease is "live" exactly as long as its lock can still be held, so both views of a dead worker agree.
LEASE_FRESH_SECONDS = LOCK_TTL_SECONDS
# After this many consecutive failed extends the lock has certainly expired: ownership is gone.
LOCK_LOST_AFTER_FAILURES = LOCK_TTL_SECONDS // LEASE_RENEW_INTERVAL_SECONDS

# Recovery scans up to this many oldest candidates so live long runs can't shadow a dead one behind them.
RECOVERY_BATCH_SIZE = 20
RECOVERY_MAX_SCAN = 200

# Spread crash-reclaim retries over ~12 min instead of the default 15s budget.
PROCESS_PROMPT_MAX_RETRIES = 5
PROCESS_PROMPT_RETRY_DELAY_SECONDS = 60

# Distributed tracker for run prompts (separate key prefix from evaluations).
run_prompt_tracker = DistributedEvaluationTracker(default_ttl=LEASE_TTL_SECONDS)
run_prompt_tracker.key_prefix = "running_prompt:"


class PromptAlreadyRunningElsewhere(Exception):
    """Another live instance holds the lease; raised so Temporal retries instead of recording success."""


class OwnershipLease:
    """Daemon thread renewing the tracker lease and extending the lock (needs thread_local=False) every 60s."""

    def __init__(self, prompt_id, lock=None):
        self._prompt_id = prompt_id
        self._lock = lock
        self._stop = threading.Event()
        self._thread = None
        self._extend_failures = 0
        # Set once the lock is known to be gone; RunPrompts fences on it.
        self.lost = threading.Event()

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
        # Owner is releasing; a late refresh would resurrect the lease after mark_completed.
        if self._stop.is_set():
            return
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
                self._extend_failures = 0
            except Exception as e:
                # error, not warning: silent extend failures are how the lock lapsed before.
                self._extend_failures += 1
                logger.error(
                    "run_prompt_lock_extend_failed",
                    prompt_id=str(self._prompt_id),
                    consecutive_failures=self._extend_failures,
                    error=str(e),
                )
                # Not owned any more, or failed long enough that the TTL has run out: fence the run.
                if (
                    isinstance(e, LockNotOwnedError)
                    or self._extend_failures >= LOCK_LOST_AFTER_FAILURES
                ) and not self.lost.is_set():
                    self.lost.set()
                    logger.error(
                        "run_prompt_ownership_lost",
                        prompt_id=str(self._prompt_id),
                        consecutive_failures=self._extend_failures,
                    )


def _get_fresh_lease(prompt_id):
    """Return the tracker entry if renewed within LEASE_FRESH_SECONDS (live worker), else None."""
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


def _claim_prompt(run_prompt_id, runner_info) -> str:
    """Take ownership in the tracker (caller holds the lock), reclaiming a dead worker's stale lease first; returns this run's token."""
    # Clear a stale flag *before* publishing so a cancel aimed at us that lands after publish survives.
    run_prompt_tracker.clear_cancel_flag(run_prompt_id)
    run_token = uuid.uuid4().hex
    runner_info = {**(runner_info or {}), "run_token": run_token}
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

    if not run_prompt_tracker.mark_running(
        run_prompt_id, runner_info=runner_info, ttl=LEASE_TTL_SECONDS
    ):
        # SET NX lost: raise only if a live owner exists (False also means Redis is down).
        if _held_by_other_live_instance(run_prompt_id):
            raise PromptAlreadyRunningElsewhere(str(run_prompt_id))
        logger.warning(
            "run_prompt_claim_unconfirmed",
            run_prompt_id=str(run_prompt_id),
        )
    return run_token


def _is_final_attempt() -> bool:
    """True on the last Temporal retry (or outside Temporal), i.e. when a failure must be made visible."""
    info = try_activity_info()
    return info is None or info.attempt >= PROCESS_PROMPT_MAX_RETRIES + 1


def _mark_prompt_failed(run_prompt_id, log_prefix):
    """Set status FAILED; logs <log_prefix>_marked_failed / <log_prefix>_failed_to_update_status."""
    try:
        RunPrompter.objects.filter(id=run_prompt_id).update(
            status=StatusType.FAILED.value
        )
        logger.info(f"{log_prefix}_marked_failed", run_prompt_id=str(run_prompt_id))
    except Exception as db_error:
        logger.error(
            f"{log_prefix}_failed_to_update_status",
            run_prompt_id=str(run_prompt_id),
            error=str(db_error),
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
        # Fail fast (retryably) if a live instance owns it; stale leases don't count.
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
            thread_local=False,  # OwnershipLease extends this lock from another thread
        ) as lock:
            # Double-check after acquiring lock
            if _held_by_other_live_instance(run_prompt_id):
                logger.warning(
                    "process_not_started_prompt_started_elsewhere",
                    run_prompt_id=str(run_prompt_id),
                )
                raise PromptAlreadyRunningElsewhere(str(run_prompt_id))

            run_token = _claim_prompt(
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
                with OwnershipLease(run_prompt_id, lock=lock) as lease:
                    runner = RunPrompts(
                        run_prompt_id=run_prompt_id,
                        run_token=run_token,
                        fence=lease.lost,
                    )
                    runner.run_prompt()
                logger.info(
                    "process_not_started_prompt_completed",
                    run_prompt_id=str(run_prompt_id),
                )
            finally:
                # Always clean up distributed tracking
                run_prompt_tracker.mark_completed(run_prompt_id)

    except (PromptAlreadyRunningElsewhere, LockContendedError):
        # A live owner has it: fail the attempt for Temporal to retry, don't mark FAILED.
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
        _mark_prompt_failed(run_prompt_id, "process_not_started_prompt")
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
        # An edit preempts a live run (any instance, ours included): request cancel, take over once the lock frees.
        live_lease = _get_fresh_lease(run_prompt_id)
        if live_lease is not None:
            logger.warning(
                "process_editing_prompt_already_running",
                run_prompt_id=str(run_prompt_id),
                current_instance=run_prompt_tracker.instance_id,
            )
            # Aim the cancel at that run's token so it can't leak onto the run that replaces it.
            run_prompt_tracker.request_cancel(
                run_prompt_id,
                reason="Edit requested",
                target=(live_lease.metadata or {}).get("run_token"),
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
            thread_local=False,  # OwnershipLease extends this lock from another thread
        ) as lock:
            run_token = _claim_prompt(
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
                with OwnershipLease(run_prompt_id, lock=lock) as lease:
                    runner = RunPrompts(
                        run_prompt_id=run_prompt_id,
                        run_token=run_token,
                        fence=lease.lost,
                    )
                    runner.run_prompt(edit_mode=True)
                logger.info(
                    "process_editing_prompt_completed",
                    run_prompt_id=str(run_prompt_id),
                )
            finally:
                # Always clean up distributed tracking
                run_prompt_tracker.mark_completed(run_prompt_id)

    except (LockContendedError, PromptAlreadyRunningElsewhere):
        # The owner is draining (it honours the cancel flag per row); retry, and only FAILED once retries are spent.
        logger.warning(
            "process_editing_prompt_preempt_failed",
            run_prompt_id=str(run_prompt_id),
            final_attempt=_is_final_attempt(),
        )
        if _is_final_attempt():
            _mark_prompt_failed(run_prompt_id, "process_editing_prompt")
            # The edit already reset cells to running and nobody will rerun them; recovery only sees RUNNING prompts.
            fail_pending_run_prompt_cells([run_prompt_id])
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
        _mark_prompt_failed(run_prompt_id, "process_editing_prompt")
        raise
    finally:
        close_old_connections()


@temporal_activity(
    time_limit=4 * 3600,
    queue="tasks_l",
    max_retries=PROCESS_PROMPT_MAX_RETRIES,
    retry_delay=PROCESS_PROMPT_RETRY_DELAY_SECONDS,
)
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

        # Raise, don't return: a returned success closes the workflow with the prompt unprocessed.
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
    except (PromptAlreadyRunningElsewhere, LockContendedError):
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

        # Cell-write liveness filtered in SQL before the slice, oldest first, so live runs can't starve dead ones.
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
            .values_list("id", flat=True)[:RECOVERY_MAX_SCAN]
        )

        # Lease is the primary liveness signal; cell writes above are the Redis-outage fallback.
        # Scan past live leases (they keep their place in the ordering) until a batch of dead ones is found.
        stuck_prompts = []
        for prompt_id in candidate_prompts:
            if _get_fresh_lease(prompt_id) is None:
                stuck_prompts.append(prompt_id)
                if len(stuck_prompts) >= RECOVERY_BATCH_SIZE:
                    break

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

            stuck_cells_updated = fail_pending_run_prompt_cells(
                stuck_prompts,
                "Run prompt timed out or was interrupted. Please rerun this cell.",
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

        # TTL safety net; keyed off started_at so it must exceed the 4h activity limit.
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
