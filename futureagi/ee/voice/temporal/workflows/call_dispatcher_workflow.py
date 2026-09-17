"""
CallDispatcherWorkflow - Singleton rate limiter for call executions.

Coordinates slot allocation across 1000+ concurrent CallExecutionWorkflows
with application-wide and per-organization rate limits.

Design:
- Singleton workflow (single instance: "call-dispatcher-singleton")
- FIFO queuing with org limits (simpler than round-robin, lower latency)
- Signal-based coordination (receives requests, signals grants)
- Batch granting (up to 10 slots per iteration for efficiency)
- Continue-as-new checkpointing (every 2000 events)

Key Optimization:
- CallExecutionWorkflows release slots immediately after call completion
- This allows evals to run without consuming voice call capacity
- Result: 2-3x throughput improvement (600 vs 240 calls/hour)
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from temporalio import workflow

from simulate.temporal.constants import (
    DEFAULT_APP_LIMIT,
    DEFAULT_ORG_LIMIT,
    DISPATCHER_CONTINUE_AS_NEW_THRESHOLD,
    QUEUE_S,
    STALE_SLOT_REAP_INTERVAL,
    STALE_SLOT_REAP_INTERVAL_SECONDS,
    STALE_SLOT_THRESHOLD_SECONDS,
)
from simulate.temporal.retry_policies import SIGNAL_RETRY_POLICY
from simulate.temporal.types.activities import ReportErrorInput, SignalSlotBatchInput
from simulate.temporal.types.dispatcher import (
    ActiveCall,
    DispatcherState,
    SlotRequest,
)


@workflow.defn
class CallDispatcherWorkflow:
    """
    Singleton workflow managing rate-limited call execution slots.

    Coordinates slot allocation with FIFO queuing and per-org limits.

    Lifecycle:
    1. Receives SIGNAL_REQUEST_SLOT from CallExecutionWorkflows
    2. Queues requests in FIFO order
    3. Grants slots when limits allow (app-wide AND org-specific)
    4. Receives SIGNAL_RELEASE_SLOT when calls complete
    5. Checkpoints via continue_as_new() every 2000 events

    Slot Granting Algorithm (FIFO + Org Limits):
    - Process pending_queue from front to back
    - For each request:
      - Check app-level capacity (active < app_limit)
      - Check org-level capacity (org_active < org_limit)
      - If both allow, grant slot (add to batch)
      - Otherwise, skip to next request
    - Signal all grants in batch
    """

    def __init__(self):
        # Workflow state (checkpointed via continue_as-new)
        # Initialize with default state so signals can be handled before run() executes
        self._state: DispatcherState = DispatcherState(
            pending_queue=[],
            pending_count=0,
            active_calls={},
            org_call_counts={},
            app_limit=DEFAULT_APP_LIMIT,
            org_limit=DEFAULT_ORG_LIMIT,
            total_granted=0,
            total_released=0,
        )

        # Event tracking for continue_as_new
        self._event_count = 0

        # Processing loop control
        self._running = True

        # Pending grants batch (for efficient batch signaling)
        self._pending_grants: list[dict] = []

        # Stale slot reaper counter (runs every STALE_SLOT_REAP_INTERVAL iterations)
        self._reap_counter = 0

        # Time-based tracking for periodic tasks (signal-driven path)
        self._last_reap_time: Optional[datetime] = None

        # Flag set by signal handlers to wake the main loop (signal-driven path)
        self._new_signal = False

    @workflow.run
    async def run(self, state: Optional[DispatcherState] = None) -> None:
        """
        Main dispatcher loop.

        Args:
            state: Checkpointed state from previous execution (for continue_as_new)
        """
        # Restore state from checkpoint if provided (continue-as-new scenario)
        if state:
            self._state = state
            # Restore pending grants from state (CRITICAL: prevents slot leaks)
            # Without this, granted slots stay in active_calls but workflows
            # never receive SLOT_GRANTED signal after continue-as-new
            self._pending_grants = (
                state.pending_grants.copy() if state.pending_grants else []
            )
            workflow.logger.info(
                f"Dispatcher restored: pending={state.pending_count}, "
                f"active={len(state.active_calls)}, granted={state.total_granted}, "
                f"pending_grants={len(self._pending_grants)}"
            )
        else:
            # State already initialized in __init__ with defaults
            workflow.logger.info("Dispatcher initialized with default state")

        # Main processing loop
        while self._running:
            try:
                # Check if we need to checkpoint
                if self._event_count >= DISPATCHER_CONTINUE_AS_NEW_THRESHOLD:
                    await self._checkpoint()
                    return  # Workflow will restart via continue_as_new

                if workflow.patched("signal-driven-loop"):
                    # =============================================
                    # NEW PATH: Signal-driven with wait_condition
                    # =============================================
                    # Clear signal flag at the start of each iteration so we
                    # only wake for NEW signals, not stale unprocessable
                    # requests stuck in pending_queue (e.g., when at capacity).
                    self._new_signal = False

                    # Grant slots to pending requests (FIFO with org limits)
                    await self._process_pending_requests()

                    # Process pending grants (batch signal workflows)
                    if self._pending_grants:
                        await self._signal_grants_batch()

                    # Periodically reap stale slots (time-based)
                    if (
                        self._last_reap_time is None
                        or (workflow.now() - self._last_reap_time).total_seconds()
                        >= STALE_SLOT_REAP_INTERVAL_SECONDS
                    ):
                        self._last_reap_time = workflow.now()
                        self._reap_stale_slots()

                    # Wait for new signals or periodic timeout (60s for reaping).
                    # TimeoutError is expected — it's the periodic wake-up for
                    # the stale reaper when no new signals arrive within 60s.
                    try:
                        await workflow.wait_condition(
                            lambda: self._new_signal or not self._running,
                            timeout=timedelta(seconds=60),
                        )
                    except TimeoutError:
                        pass
                    self._event_count += 1
                else:
                    # =============================================
                    # OLD PATH: 0.5s polling loop (for in-flight workflows)
                    # =============================================
                    # Process pending grants (batch signal workflows)
                    if self._pending_grants:
                        await self._signal_grants_batch()

                    # Grant slots to pending requests (FIFO with org limits)
                    await self._process_pending_requests()

                    # Periodically reap stale slots (counter-based)
                    self._reap_counter += 1
                    if self._reap_counter >= STALE_SLOT_REAP_INTERVAL:
                        self._reap_counter = 0
                        self._reap_stale_slots()

                    await workflow.sleep(0.5)
                    self._event_count += 1

            except Exception as e:
                workflow.logger.warning(f"Dispatcher error in main loop: {str(e)}")
                # Report to Sentry via activity (fire-and-forget)
                workflow.start_activity(
                    "report_workflow_error",
                    ReportErrorInput(
                        workflow_name="CallDispatcherWorkflow",
                        workflow_id=workflow.info().workflow_id,
                        error_message=str(e),
                        error_type=type(e).__name__,
                        context={
                            "pending_count": self._state.pending_count,
                            "active_count": len(self._state.active_calls),
                        },
                    ),
                    start_to_close_timeout=timedelta(seconds=10),
                    task_queue=QUEUE_S,
                )
                await workflow.sleep(1)  # Prevent tight error loop

    # ========================================
    # SIGNAL HANDLERS
    # ========================================

    @workflow.signal
    async def request_slot(self, request: dict) -> None:
        """
        Receive slot request from CallExecutionWorkflow.

        Args:
            request: {workflow_id, call_id, org_id}
        """
        self._event_count += 1

        slot_request = SlotRequest(
            workflow_id=request["workflow_id"],
            call_id=request["call_id"],
            org_id=request["org_id"],
            requested_at=workflow.now().isoformat(),
            agent_definition_id=request.get("agent_definition_id"),
            agent_concurrency_limit=request.get("agent_concurrency_limit"),
        )

        # Add to FIFO queue
        self._state.pending_queue.append(slot_request)
        self._state.pending_count += 1
        self._new_signal = True

        workflow.logger.info(
            f"Slot requested: call_id={slot_request.call_id}, "
            f"org_id={slot_request.org_id}, pending={self._state.pending_count}"
        )

    @workflow.signal
    async def release_slot(self, call_id: str) -> None:
        """
        Receive slot release from CallExecutionWorkflow.

        Handles two cases:
        1. Call has an active slot -> release from active_calls
        2. Call is still in pending_queue -> remove from queue

        Args:
            call_id: Call identifier to release
        """
        self._event_count += 1

        # Case 1: Call has an active slot
        if call_id in self._state.active_calls:
            active_call = self._state.active_calls[call_id]
            org_id = active_call.org_id

            # Remove from active tracking
            agent_id = active_call.agent_definition_id
            del self._state.active_calls[call_id]
            self._state.org_call_counts[org_id] = max(
                0, self._state.org_call_counts.get(org_id, 1) - 1
            )
            self._state.total_released += 1

            # Clean up org if no more active calls
            if self._state.org_call_counts.get(org_id, 0) <= 0:
                self._state.org_call_counts.pop(org_id, None)

            # Decrement agent counter
            if agent_id and agent_id in self._state.agent_call_counts:
                self._state.agent_call_counts[agent_id] = max(
                    0, self._state.agent_call_counts.get(agent_id, 1) - 1
                )
                if self._state.agent_call_counts.get(agent_id, 0) <= 0:
                    self._state.agent_call_counts.pop(agent_id, None)

            self._new_signal = True
            workflow.logger.info(
                f"Slot released: call_id={call_id}, org_id={org_id}, "
                f"active={len(self._state.active_calls)}"
            )
            return

        # Case 2: Call is still in pending queue (cancelled before slot grant)
        for i, req in enumerate(self._state.pending_queue):
            if req.call_id == call_id:
                self._state.pending_queue.pop(i)
                self._state.pending_count -= 1
                self._new_signal = True
                workflow.logger.info(
                    f"Removed cancelled call from pending queue: call_id={call_id}, "
                    f"pending={self._state.pending_count}"
                )
                return

        # Call not found in either active or pending
        workflow.logger.debug(f"Release slot called for unknown call: {call_id}")

    @workflow.signal
    async def update_limits(self, input: dict) -> None:
        """
        Update rate limits dynamically.

        Args:
            input: {app_limit?, org_limit?}
        """
        self._event_count += 1

        if "app_limit" in input:
            old_limit = self._state.app_limit
            self._state.app_limit = input["app_limit"]
            workflow.logger.info(
                f"App limit updated: {old_limit} -> {self._state.app_limit}"
            )

        if "org_limit" in input:
            old_limit = self._state.org_limit
            self._state.org_limit = input["org_limit"]
            workflow.logger.info(
                f"Org limit updated: {old_limit} -> {self._state.org_limit}"
            )

        self._new_signal = True

    @workflow.signal
    async def reload(self) -> None:
        """
        Force continue-as-new to pick up new code after deployment.

        Usage: Send this signal after deploying new code to restart the
        dispatcher with the updated code while preserving state.
        """
        workflow.logger.info("Reload signal received, triggering continue-as-new")
        await self._checkpoint()

    # ========================================
    # QUERY HANDLERS
    # ========================================

    @workflow.query
    def get_status(self) -> dict:
        """Query current dispatcher status."""
        # Build pending counts per org from the queue
        pending_org_counts: dict[str, int] = {}
        for req in self._state.pending_queue:
            pending_org_counts[req.org_id] = pending_org_counts.get(req.org_id, 0) + 1

        return {
            "pending_count": self._state.pending_count,
            "active_count": len(self._state.active_calls),
            "active_call_ids": list(self._state.active_calls.keys()),
            "org_counts": dict(self._state.org_call_counts),
            "pending_org_counts": pending_org_counts,
            "app_limit": self._state.app_limit,
            "org_limit": self._state.org_limit,
            "total_granted": self._state.total_granted,
            "total_released": self._state.total_released,
            "event_count": self._event_count,
        }

    @workflow.query
    def get_org_status(self, org_id: str) -> dict:
        """Query status for specific organization."""
        # Count pending requests for this org
        pending_for_org = sum(
            1 for req in self._state.pending_queue if req.org_id == org_id
        )

        return {
            "org_id": org_id,
            "pending_count": pending_for_org,
            "active_count": self._state.org_call_counts.get(org_id, 0),
            "org_limit": self._state.org_limit,
        }

    # ========================================
    # SLOT MANAGEMENT LOGIC
    # ========================================

    async def _process_pending_requests(self) -> None:
        """
        Grant slots to pending requests using FIFO + org limits.

        Algorithm:
        1. Check app-level capacity (active < app_limit)
        2. Iterate through pending_queue (FIFO order)
        3. For each request:
           - Check org-level capacity (org_active < org_limit)
           - If both limits allow, grant slot
           - Otherwise, skip (request stays in queue)
        4. Batch signal all grants
        5. Remove granted requests from queue

        Batch limit: 10 grants per iteration to prevent starvation
        """
        if not self._state.pending_queue or not self._can_grant_any():
            return

        granted_indices = []  # Track which requests were granted
        batch_size = 50  # Max grants per iteration (increased from 10 for throughput)

        # Process queue in FIFO order
        for i, slot_request in enumerate(self._state.pending_queue):
            if len(granted_indices) >= batch_size:
                break  # Batch limit reached

            if not self._can_grant_any():
                break  # App limit reached

            # Check if this org and agent can receive a slot
            if self._can_grant_to_org(slot_request.org_id) and self._can_grant_to_agent(
                slot_request
            ):
                self._grant_slot(slot_request)
                granted_indices.append(i)

        # Remove granted requests from queue (reverse order to preserve indices)
        for i in reversed(granted_indices):
            self._state.pending_queue.pop(i)

        if granted_indices:
            workflow.logger.info(
                f"Granted {len(granted_indices)} slots, pending={self._state.pending_count}"
            )

    def _can_grant_any(self) -> bool:
        """Check if any slots can be granted (app-level capacity)."""
        return len(self._state.active_calls) < self._state.app_limit

    def _can_grant_to_org(self, org_id: str) -> bool:
        """Check if org has capacity for another slot."""
        org_active = self._state.org_call_counts.get(org_id, 0)
        return org_active < self._state.org_limit

    def _can_grant_to_agent(self, slot_request: SlotRequest) -> bool:
        """Check if agent has capacity for another slot (LiveKit concurrency).

        If the slot request includes an agent_definition_id and a concurrency
        limit, enforce it. Otherwise, always allow (no agent-level cap).
        """
        agent_id = getattr(slot_request, "agent_definition_id", None)
        if not agent_id:
            return True  # No agent-level limit
        agent_limit = getattr(slot_request, "agent_concurrency_limit", None)
        if not agent_limit:
            return True  # No limit configured
        # Store the limit for this agent (may change between requests)
        self._state.agent_limits[agent_id] = agent_limit
        agent_active = self._state.agent_call_counts.get(agent_id, 0)
        return agent_active < agent_limit

    def _grant_slot(self, slot_request: SlotRequest) -> None:
        """Grant a slot to a pending request."""
        # Track as active
        # Use workflow.now() for deterministic time (required by Temporal for replay safety)
        agent_id = getattr(slot_request, "agent_definition_id", None)
        active_call = ActiveCall(
            call_id=slot_request.call_id,
            org_id=slot_request.org_id,
            granted_at=workflow.now().isoformat(),
            agent_definition_id=agent_id,
        )
        self._state.active_calls[slot_request.call_id] = active_call

        # Update org counter
        if slot_request.org_id not in self._state.org_call_counts:
            self._state.org_call_counts[slot_request.org_id] = 0
        self._state.org_call_counts[slot_request.org_id] += 1

        # Update agent counter (if agent-level tracking)
        if agent_id:
            self._state.agent_call_counts[agent_id] = (
                self._state.agent_call_counts.get(agent_id, 0) + 1
            )

        # Update metrics
        self._state.pending_count -= 1
        self._state.total_granted += 1

        # Add to pending grants batch
        self._pending_grants.append(
            {
                "workflow_id": slot_request.workflow_id,
                "call_id": slot_request.call_id,
            }
        )

        workflow.logger.debug(
            f"Slot granted: call_id={slot_request.call_id}, org_id={slot_request.org_id}"
        )

    def _reap_stale_slots(self) -> None:
        """
        Remove active slots that have been held longer than STALE_SLOT_THRESHOLD_SECONDS.

        This is a safety net for slots that were never released due to:
        - Signal delivery failures
        - Workflow crashes/timeouts
        - Failed grant signals that weren't cleaned up

        Uses workflow.now() for deterministic time (required by Temporal).
        """
        if not self._state.active_calls:
            return

        now = workflow.now()
        stale_call_ids = []

        for call_id, active_call in self._state.active_calls.items():
            try:
                granted_at = datetime.fromisoformat(active_call.granted_at)
                # Ensure timezone-aware comparison
                if granted_at.tzinfo is None:
                    granted_at = granted_at.replace(tzinfo=timezone.utc)
                elapsed = (now - granted_at).total_seconds()
                if elapsed > STALE_SLOT_THRESHOLD_SECONDS:
                    stale_call_ids.append(call_id)
            except (ValueError, TypeError):
                # If granted_at is malformed, consider it stale
                stale_call_ids.append(call_id)

        if not stale_call_ids:
            return

        # Release all stale slots
        for call_id in stale_call_ids:
            active_call = self._state.active_calls[call_id]
            org_id = active_call.org_id

            agent_id = getattr(active_call, "agent_definition_id", None)
            del self._state.active_calls[call_id]
            self._state.org_call_counts[org_id] = max(
                0, self._state.org_call_counts.get(org_id, 1) - 1
            )
            self._state.total_released += 1

            if self._state.org_call_counts.get(org_id, 0) <= 0:
                self._state.org_call_counts.pop(org_id, None)

            # Clean up agent counts
            if agent_id and agent_id in self._state.agent_call_counts:
                self._state.agent_call_counts[agent_id] = max(
                    0, self._state.agent_call_counts.get(agent_id, 1) - 1
                )
                if self._state.agent_call_counts.get(agent_id, 0) <= 0:
                    self._state.agent_call_counts.pop(agent_id, None)

        workflow.logger.warning(
            f"Reaped {len(stale_call_ids)} stale slots (threshold={STALE_SLOT_THRESHOLD_SECONDS}s): "
            f"{stale_call_ids}, active_remaining={len(self._state.active_calls)}"
        )

    async def _signal_grants_batch(self) -> None:
        """
        Signal all pending grants in batch via activity.

        Uses signal_slots_granted_batch activity for efficiency.
        """
        if not self._pending_grants:
            return

        try:
            await workflow.execute_activity(
                "signal_slots_granted_batch",
                SignalSlotBatchInput(grants=self._pending_grants),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=SIGNAL_RETRY_POLICY,
                task_queue=QUEUE_S,
            )

            workflow.logger.info(f"Signaled {len(self._pending_grants)} slot grants")
            self._pending_grants.clear()

        except Exception as e:
            workflow.logger.warning(f"Failed to signal grants batch: {str(e)}")
            # Report to Sentry via activity (fire-and-forget)
            workflow.start_activity(
                "report_workflow_error",
                ReportErrorInput(
                    workflow_name="CallDispatcherWorkflow",
                    workflow_id=workflow.info().workflow_id,
                    error_message=f"Failed to signal grants batch: {str(e)}",
                    error_type=type(e).__name__,
                    context={"pending_grants_count": len(self._pending_grants)},
                ),
                start_to_close_timeout=timedelta(seconds=10),
                task_queue=QUEUE_S,
            )
            # Keep pending_grants for retry on next iteration

    # ========================================
    # CHECKPOINTING
    # ========================================

    async def _checkpoint(self) -> None:
        """
        Checkpoint state via continue_as_new.

        Prevents workflow history from growing unbounded.
        CRITICAL: Must preserve _pending_grants to avoid slot leaks.
        """
        # Save pending grants to state before checkpoint (CRITICAL)
        # Without this, granted slots stay in active_calls but workflows
        # never receive SLOT_GRANTED signal after continue-as-new
        self._state.pending_grants = self._pending_grants.copy()

        workflow.logger.info(
            f"Checkpointing dispatcher: events={self._event_count}, "
            f"pending={self._state.pending_count}, active={len(self._state.active_calls)}, "
            f"granted={self._state.total_granted}, pending_grants={len(self._pending_grants)}"
        )

        # Continue as new with current state (positional arg, not keyword)
        workflow.continue_as_new(self._state)
