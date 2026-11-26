"""
PhoneNumberDispatcherWorkflow - Singleton phone number allocator for outbound calls.

Coordinates phone number acquisition across concurrent CallExecutionWorkflows
with application-wide and per-organization rate limits.

Design:
- Singleton workflow (single instance: "phone-number-dispatcher-singleton")
- FIFO queuing with org limits
- Signal-based coordination (receives requests, signals grants)
- Batch acquisition and signaling (via activities)
- Continue-as-new checkpointing (every 2000 events)

Follows the same architecture as CallDispatcherWorkflow: the dispatcher is a
pure slot manager. Phone DB operations (acquire/release) are handled by the
batch activity and the calling workflow respectively.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from temporalio import workflow

from simulate.temporal.constants import (
    DEFAULT_PHONE_ORG_LIMIT,
    PHONE_NUMBER_DISPATCHER_CONTINUE_AS_NEW_THRESHOLD,
    PHONE_NUMBER_SYNC_INTERVAL_SECONDS,
    QUEUE_L,
    QUEUE_S,
    STALE_PHONE_NUMBER_REAP_INTERVAL_SECONDS,
    STALE_PHONE_NUMBER_THRESHOLD_SECONDS,
)
from simulate.temporal.retry_policies import NO_RETRY_POLICY, SIGNAL_RETRY_POLICY
from simulate.temporal.types.activities import (
    AcquireAndSignalPhoneNumbersBatchInput,
    AcquireAndSignalPhoneNumbersBatchOutput,
    ReleasePhoneInput,
    ReportErrorInput,
)
from ee.voice.temporal.types.phone_number_dispatcher import (
    ActivePhoneNumber,
    PhoneNumberDispatcherState,
    PhoneNumberReleaseSignal,
    PhoneNumberRequest,
)


@workflow.defn
class PhoneNumberDispatcherWorkflow:
    """
    Singleton workflow managing phone number slot allocation for outbound calls.

    Coordinates phone number acquisition with FIFO queuing and per-org limits.

    Lifecycle:
    1. Receives SIGNAL_REQUEST_PHONE_NUMBER from CallExecutionWorkflows
    2. Queues requests in FIFO order
    3. Grants slots when limits allow (app-wide AND org-specific)
    4. Batch activity acquires phone from DB and signals workflow with phone details
    5. Receives SIGNAL_RELEASE_PHONE_NUMBER when calls complete
    6. Checkpoints via continue_as_new() every 2000 events
    """

    def __init__(self):
        self._state: PhoneNumberDispatcherState = PhoneNumberDispatcherState(
            pending_queue=[],
            pending_count=0,
            active_phone_numbers={},
            org_phone_number_counts={},
            app_limit=0,  # Set by sync_available_phone_numbers on startup
            org_limit=DEFAULT_PHONE_ORG_LIMIT,
            total_granted=0,
            total_released=0,
        )

        self._event_count = 0
        self._running = True

        # Pending grants batch (for efficient batch signaling)
        self._pending_grants: list[dict] = []

        # Time-based tracking for periodic tasks (None = run immediately)
        self._last_sync_time: Optional[datetime] = None
        self._last_reap_time: Optional[datetime] = None

        # Flag set by signal handlers to wake the main loop.
        # Cleared at the start of each iteration so the loop only wakes
        # for NEW signals, not stale unprocessable requests in pending_queue.
        self._new_signal = False

    @workflow.run
    async def run(self, state: Optional[PhoneNumberDispatcherState] = None) -> None:
        """Main dispatcher loop."""
        if state:
            self._state = state
            self._pending_grants = (
                state.pending_grants.copy() if state.pending_grants else []
            )
            workflow.logger.info(
                f"Phone number dispatcher restored: pending={state.pending_count}, "
                f"active={len(state.active_phone_numbers)}, granted={state.total_granted}, "
                f"pending_grants={len(self._pending_grants)}"
            )
        else:
            workflow.logger.info(
                "Phone number dispatcher initialized with default state"
            )

        while self._running:
            try:
                # Clear signal flag at the start of each iteration so we
                # only wake for NEW signals, not stale unprocessable requests
                # stuck in pending_queue (e.g., when at capacity).
                self._new_signal = False

                if (
                    self._event_count
                    >= PHONE_NUMBER_DISPATCHER_CONTINUE_AS_NEW_THRESHOLD
                ):
                    await self._checkpoint()
                    return

                # Sync app_limit from DB (on first iteration, then every ~5 min)
                if (
                    self._last_sync_time is None
                    or (workflow.now() - self._last_sync_time).total_seconds()
                    >= PHONE_NUMBER_SYNC_INTERVAL_SECONDS
                ):
                    self._last_sync_time = workflow.now()
                    await self._sync_phone_limits()

                # Grant slots to pending requests (check limits).
                # Runs BEFORE batch so all grantable requests are collected
                # into _pending_grants in one pass, then batched together.
                self._process_pending_requests()

                # Process pending grants (batch acquire + signal workflows)
                if self._pending_grants:
                    await self._signal_grants_batch()

                # Stale slot reaper (every ~60 seconds)
                if (
                    self._last_reap_time is None
                    or (workflow.now() - self._last_reap_time).total_seconds()
                    >= STALE_PHONE_NUMBER_REAP_INTERVAL_SECONDS
                ):
                    self._last_reap_time = workflow.now()
                    await self._reap_stale_phone_numbers()

                # Wait for new signals or periodic timeout. Uses _new_signal
                # flag instead of checking pending_queue directly to avoid
                # spinning when requests are queued but can't be granted
                # (e.g., app_limit=0 before first sync, or at capacity).
                # _pending_grants is NOT checked here because with the
                # grant-then-batch ordering above, pending_grants is always
                # empty at this point (batch clears them).
                # TimeoutError is expected — it's the periodic wake-up for
                # sync/reaper when no new signals arrive within 60s.
                try:
                    await workflow.wait_condition(
                        lambda: self._new_signal or not self._running,
                        timeout=timedelta(seconds=60),
                    )
                except TimeoutError:
                    # Expected: no new signals arrived within 60s.
                    # Loop continues to run periodic tasks (sync/reaper).
                    pass
                self._event_count += 1

            except Exception as e:
                workflow.logger.warning(
                    f"Phone number dispatcher error in main loop: {str(e)}"
                )
                workflow.start_activity(
                    "report_workflow_error",
                    ReportErrorInput(
                        workflow_name="PhoneNumberDispatcherWorkflow",
                        workflow_id=workflow.info().workflow_id,
                        error_message=str(e),
                        error_type=type(e).__name__,
                        context={
                            "pending_count": self._state.pending_count,
                            "active_count": len(self._state.active_phone_numbers),
                        },
                    ),
                    start_to_close_timeout=timedelta(seconds=10),
                    task_queue=QUEUE_S,
                )
                await workflow.sleep(1)

    # ========================================
    # SIGNAL HANDLERS
    # ========================================

    @workflow.signal
    async def request_phone_number(self, request: PhoneNumberRequest) -> None:
        """
        Receive phone number slot request from CallExecutionWorkflow.

        Args:
            request: PhoneNumberRequest with workflow_id, call_id, org_id, call_direction.
        """
        request.requested_at = workflow.now().isoformat()
        self._state.pending_queue.append(request)
        self._state.pending_count += 1
        self._new_signal = True

        workflow.logger.info(
            f"Phone number requested: call_id={request.call_id}, "
            f"org_id={request.org_id}, pending={self._state.pending_count}"
        )

    @workflow.signal
    async def release_phone_number_signal(
        self, release_info: PhoneNumberReleaseSignal
    ) -> None:
        """
        Receive phone number slot release from CallExecutionWorkflow.

        Handles two cases:
        1. Call has an active slot -> release from active_phone_numbers
        2. Call is still in pending queue -> remove from queue

        Args:
            release_info: PhoneNumberReleaseSignal with call_id.
        """
        call_id = release_info.call_id

        # Case 1: Call has an active slot
        if call_id in self._state.active_phone_numbers:
            active = self._state.active_phone_numbers.pop(call_id)
            org_id = active.org_id

            self._state.org_phone_number_counts[org_id] = max(
                0, self._state.org_phone_number_counts.get(org_id, 1) - 1
            )
            if self._state.org_phone_number_counts.get(org_id, 0) <= 0:
                self._state.org_phone_number_counts.pop(org_id, None)
            self._state.total_released += 1

            # Release phone from DB pool BEFORE waking main loop.
            # This prevents a race where the main loop grants a new call
            # and the batch activity tries to acquire a phone that is
            # still IN_USE in the DB (because this release hasn't finished).
            if active.phone_id:
                try:
                    await workflow.execute_activity(
                        "release_phone_number",
                        ReleasePhoneInput(phone_id=active.phone_id),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=SIGNAL_RETRY_POLICY,
                        task_queue=QUEUE_L,
                    )
                except Exception as e:
                    workflow.logger.warning(
                        f"Failed to release phone {active.phone_id} from DB: {e}"
                    )

            # Wake main loop AFTER phone is released from DB so the
            # next grant can successfully acquire the freed phone.
            self._new_signal = True

            workflow.logger.info(
                f"Phone number slot released: call_id={call_id}, org_id={org_id}, "
                f"phone_id={active.phone_id}, "
                f"active={len(self._state.active_phone_numbers)}"
            )
            return

        # Case 2: Still in pending queue (cancelled before grant)
        for i, req in enumerate(self._state.pending_queue):
            if req.call_id == call_id:
                self._state.pending_queue.pop(i)
                self._state.pending_count -= 1
                self._new_signal = True
                workflow.logger.info(
                    f"Removed cancelled call from phone number pending queue: "
                    f"call_id={call_id}, pending={self._state.pending_count}"
                )
                return

        workflow.logger.debug(
            f"Release phone number slot called for unknown call: {call_id}"
        )

    @workflow.signal
    async def update_limits(self, input: dict) -> None:
        """Update rate limits dynamically."""
        if "app_limit" in input:
            old_limit = self._state.app_limit
            self._state.app_limit = input["app_limit"]
            workflow.logger.info(
                f"Phone number app limit updated: {old_limit} -> {self._state.app_limit}"
            )

        if "org_limit" in input:
            old_limit = self._state.org_limit
            self._state.org_limit = input["org_limit"]
            workflow.logger.info(
                f"Phone number org limit updated: {old_limit} -> {self._state.org_limit}"
            )

    @workflow.signal
    async def reload(self) -> None:
        """Force continue-as-new to pick up new code after deployment."""
        workflow.logger.info(
            "Phone number dispatcher reload signal received, triggering continue-as-new"
        )
        await self._checkpoint()

    # ========================================
    # QUERY HANDLERS
    # ========================================

    @workflow.query
    def get_status(self) -> dict:
        """Query current dispatcher status."""
        pending_org_counts: dict[str, int] = {}
        for req in self._state.pending_queue:
            pending_org_counts[req.org_id] = pending_org_counts.get(req.org_id, 0) + 1

        return {
            "pending_count": self._state.pending_count,
            "active_count": len(self._state.active_phone_numbers),
            "active_call_ids": list(self._state.active_phone_numbers.keys()),
            "org_counts": dict(self._state.org_phone_number_counts),
            "pending_org_counts": pending_org_counts,
            "app_limit": self._state.app_limit,
            "org_limit": self._state.org_limit,
            "total_granted": self._state.total_granted,
            "total_released": self._state.total_released,
            "event_count": self._event_count,
            "pending_grants": len(self._pending_grants),
        }

    # ========================================
    # SLOT MANAGEMENT LOGIC
    # ========================================

    def _process_pending_requests(self) -> None:
        """
        Grant slots to pending requests using FIFO + org limits.

        Same algorithm as CallDispatcherWorkflow._process_pending_requests.
        """
        if not self._state.pending_queue or not self._can_grant_any():
            return

        granted_indices = []
        batch_size = 50

        for i, phone_number_request in enumerate(self._state.pending_queue):
            if len(granted_indices) >= batch_size:
                break

            if not self._can_grant_any():
                break

            if self._can_grant_to_org(phone_number_request.org_id):
                self._grant_phone_number(phone_number_request)
                granted_indices.append(i)

        # Remove granted requests from queue (reverse order to preserve indices)
        for i in reversed(granted_indices):
            self._state.pending_queue.pop(i)

        if granted_indices:
            workflow.logger.info(
                f"Granted {len(granted_indices)} phone number slots, "
                f"pending={self._state.pending_count}"
            )

    def _can_grant_any(self) -> bool:
        """Check if any slots can be granted (app-level capacity)."""
        return len(self._state.active_phone_numbers) < self._state.app_limit

    def _can_grant_to_org(self, org_id: str) -> bool:
        """Check if org has capacity for another slot."""
        org_active = self._state.org_phone_number_counts.get(org_id, 0)
        return org_active < self._state.org_limit

    def _grant_phone_number(self, phone_number_request: PhoneNumberRequest) -> None:
        """Grant a slot to a pending request."""
        active_phone_number = ActivePhoneNumber(
            call_id=phone_number_request.call_id,
            org_id=phone_number_request.org_id,
            granted_at=workflow.now().isoformat(),
        )
        self._state.active_phone_numbers[phone_number_request.call_id] = (
            active_phone_number
        )

        # Update org counter
        if phone_number_request.org_id not in self._state.org_phone_number_counts:
            self._state.org_phone_number_counts[phone_number_request.org_id] = 0
        self._state.org_phone_number_counts[phone_number_request.org_id] += 1

        # Update metrics
        self._state.pending_count -= 1
        self._state.total_granted += 1

        # Add to pending grants batch
        self._pending_grants.append(
            {
                "workflow_id": phone_number_request.workflow_id,
                "call_id": phone_number_request.call_id,
                "org_id": phone_number_request.org_id,
                "call_direction": phone_number_request.call_direction,
            }
        )

        workflow.logger.debug(
            f"Phone number slot granted: call_id={phone_number_request.call_id}, "
            f"org_id={phone_number_request.org_id}"
        )

    async def _reap_stale_phone_numbers(self) -> None:
        """
        Remove active slots held longer than STALE_PHONE_NUMBER_THRESHOLD_SECONDS.

        Safety net for slots never released due to signal failures or crashes.
        Releases both the dispatcher slot and the DB phone (via activity).
        """
        if not self._state.active_phone_numbers:
            return

        stale_call_ids = []

        for call_id, active_phone_number in self._state.active_phone_numbers.items():
            try:
                granted_at = datetime.fromisoformat(active_phone_number.granted_at)
                if granted_at.tzinfo is None:
                    granted_at = granted_at.replace(tzinfo=timezone.utc)
                elapsed = (workflow.now() - granted_at).total_seconds()
                if elapsed > STALE_PHONE_NUMBER_THRESHOLD_SECONDS:
                    stale_call_ids.append(call_id)
            except (ValueError, TypeError):
                stale_call_ids.append(call_id)

        if not stale_call_ids:
            return

        # Collect phone_ids for DB release before removing from state
        stale_phone_ids = []
        for call_id in stale_call_ids:
            active_phone_number = self._state.active_phone_numbers[call_id]
            if active_phone_number.phone_id:
                stale_phone_ids.append(active_phone_number.phone_id)

            org_id = active_phone_number.org_id
            del self._state.active_phone_numbers[call_id]
            self._state.org_phone_number_counts[org_id] = max(
                0, self._state.org_phone_number_counts.get(org_id, 1) - 1
            )
            self._state.total_released += 1

            if self._state.org_phone_number_counts.get(org_id, 0) <= 0:
                self._state.org_phone_number_counts.pop(org_id, None)

        workflow.logger.warning(
            f"Reaped {len(stale_call_ids)} stale phone number slots "
            f"(threshold={STALE_PHONE_NUMBER_THRESHOLD_SECONDS}s): "
            f"{stale_call_ids}, active_remaining={len(self._state.active_phone_numbers)}"
        )

        # Release acquired phones from DB (dispatcher state already cleaned
        # above, so use release_phone_number which only does DB release
        # without signaling the dispatcher back).
        for phone_id in stale_phone_ids:
            try:
                await workflow.execute_activity(
                    "release_phone_number",
                    ReleasePhoneInput(phone_id=phone_id),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=SIGNAL_RETRY_POLICY,
                    task_queue=QUEUE_L,
                )
            except Exception as e:
                workflow.logger.warning(
                    f"Failed to release stale phone {phone_id} from DB: {e}"
                )

    # ========================================
    # PHONE NUMBER SYNC
    # ========================================

    async def _sync_phone_limits(self) -> None:
        """
        Sync app_limit from DB by counting available outbound phone numbers.

        Runs on first iteration and periodically thereafter.
        """
        try:
            count = await workflow.execute_activity(
                "sync_available_phone_numbers",
                start_to_close_timeout=timedelta(seconds=10),
                task_queue=QUEUE_S,
                result_type=int,
            )

            if count != self._state.app_limit:
                old_limit = self._state.app_limit
                self._state.app_limit = count
                workflow.logger.info(
                    f"Phone number app limit synced: {old_limit} -> {count}"
                )
        except Exception as e:
            workflow.logger.warning(f"Failed to sync phone number limits: {str(e)}")

    # ========================================
    # BATCH ACTIVITY
    # ========================================

    async def _signal_grants_batch(self) -> None:
        """
        Acquire phone numbers and signal all pending grants in batch via activity.

        Uses NO_RETRY_POLICY because the activity is not idempotent (acquires
        phones from DB). If it partially succeeds then fails, Temporal retry
        would re-acquire phones for already-signaled calls. Instead, failed
        grants are cleaned up from active_phone_numbers here.
        """
        if not self._pending_grants:
            return

        try:
            result = await workflow.execute_activity(
                "acquire_and_signal_phone_numbers_batch",
                AcquireAndSignalPhoneNumbersBatchInput(grants=self._pending_grants),
                start_to_close_timeout=timedelta(seconds=60),
                heartbeat_timeout=timedelta(seconds=15),
                retry_policy=NO_RETRY_POLICY,
                task_queue=QUEUE_S,
                result_type=AcquireAndSignalPhoneNumbersBatchOutput,
            )

            workflow.logger.info(
                f"Phone number batch: {result.success_count} succeeded, "
                f"{result.failed_count} failed"
            )

            # Store phone_ids for successful grants so the stale reaper
            # can release phones from DB if needed.
            for call_id, phone_id in result.successful_grants.items():
                if call_id in self._state.active_phone_numbers:
                    self._state.active_phone_numbers[call_id].phone_id = phone_id

            # Release slots for failed grants (phone was not acquired or
            # signal failed — slot is in active_phone_numbers but no workflow
            # will ever release it, so clean up here).
            for call_id in result.failed_call_ids:
                if call_id in self._state.active_phone_numbers:
                    active = self._state.active_phone_numbers.pop(call_id)
                    org_id = active.org_id
                    self._state.org_phone_number_counts[org_id] = max(
                        0,
                        self._state.org_phone_number_counts.get(org_id, 1) - 1,
                    )
                    if self._state.org_phone_number_counts.get(org_id, 0) <= 0:
                        self._state.org_phone_number_counts.pop(org_id, None)
                    self._state.total_released += 1

            self._pending_grants.clear()

        except Exception as e:
            workflow.logger.warning(
                f"Failed to signal phone number grants batch: {str(e)}"
            )
            workflow.start_activity(
                "report_workflow_error",
                ReportErrorInput(
                    workflow_name="PhoneNumberDispatcherWorkflow",
                    workflow_id=workflow.info().workflow_id,
                    error_message=f"Failed to signal phone number grants batch: {str(e)}",
                    error_type=type(e).__name__,
                    context={"pending_grants_count": len(self._pending_grants)},
                ),
                start_to_close_timeout=timedelta(seconds=10),
                task_queue=QUEUE_S,
            )
            # On total failure, release all pending grant slots since
            # NO_RETRY_POLICY means Temporal won't retry the activity.
            for grant in self._pending_grants:
                call_id = grant["call_id"]
                if call_id in self._state.active_phone_numbers:
                    active = self._state.active_phone_numbers.pop(call_id)
                    org_id = active.org_id
                    self._state.org_phone_number_counts[org_id] = max(
                        0,
                        self._state.org_phone_number_counts.get(org_id, 1) - 1,
                    )
                    if self._state.org_phone_number_counts.get(org_id, 0) <= 0:
                        self._state.org_phone_number_counts.pop(org_id, None)
                    self._state.total_released += 1
            self._pending_grants.clear()

    # ========================================
    # CHECKPOINTING
    # ========================================

    async def _checkpoint(self) -> None:
        """Checkpoint state via continue_as_new."""
        self._state.pending_grants = self._pending_grants.copy()

        workflow.logger.info(
            f"Checkpointing phone number dispatcher: events={self._event_count}, "
            f"pending={self._state.pending_count}, "
            f"active={len(self._state.active_phone_numbers)}, "
            f"granted={self._state.total_granted}, "
            f"pending_grants={len(self._pending_grants)}"
        )

        workflow.continue_as_new(self._state)
