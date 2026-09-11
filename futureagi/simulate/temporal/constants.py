"""
Constants for Temporal call execution workflows.

These constants control workflow behavior, queue routing, and scalability limits.
"""

import os

# =============================================================================
# Task Queues
# =============================================================================

# Small queue: Lightweight, quick operations
# - Dispatch signals, slot management
# - Phone number acquire/release
# - Status updates
QUEUE_S = "tasks_s"

# Large queue: Standard operations
# - Workflows (TestExecution, CallExecution)
# - Call provider API calls
# - Call monitoring (long-running)
# - Database operations
QUEUE_L = "tasks_l"

# XL queue: Resource-intensive operations
# - Evaluations (high CPU/memory)
# - LLM calls
# - Eval summary generation
QUEUE_XL = "tasks_xl"

# Hosted-runner queue: platform-triggered SDK execution
# - SimulationRunnerWorkflow + hosted_runner activities
# - Polled by the dedicated simulation-runner worker, which spawns the
#   released Agent Learning Kit as a child process (plan §9.1)
QUEUE_RUNNER = "simulation_runner"


# =============================================================================
# Scalability Constants
# =============================================================================

# Number of child workflows to launch in parallel batches
# Higher = faster startup, but more memory pressure
LAUNCH_BATCH_SIZE = 50

# Number of child workflows to launch before pausing to let agent workers
# accept dispatches.  Prevents thundering-herd 503s from LiveKit when many
# calls are initiated simultaneously.
LAUNCH_SUB_BATCH_SIZE = int(os.getenv("LAUNCH_SUB_BATCH_SIZE", "10"))

# Seconds to sleep between sub-batches during child workflow launch.
LAUNCH_SUB_BATCH_DELAY_SECONDS = float(
    os.getenv("LAUNCH_SUB_BATCH_DELAY_SECONDS", "5.0")
)

# History events before TestExecutionWorkflow checkpoints via continue-as-new
# Temporal recommends staying under 10,000 events for performance
CONTINUE_AS_NEW_THRESHOLD = 500

# History events before CallDispatcherWorkflow checkpoints
# Lower threshold since dispatcher is busier with many signals
DISPATCHER_CONTINUE_AS_NEW_THRESHOLD = 2000

# Maximum calls per orchestrator before partitioning
# Temporal recommends max ~1000 child workflows per parent
MAX_CALLS_PER_ORCHESTRATOR = 500

# Hosted SDK jobs execute dataset rows sequentially inside one child process.
# Ten voice cases can legitimately consume almost an hour when each reaches its
# readiness/conversation ceiling, so the supervising activity must not inherit
# the single-call deadline.
HOSTED_RUNNER_MAX_DURATION_SECONDS = int(
    os.getenv("HOSTED_RUNNER_MAX_DURATION_SECONDS", str(65 * 60))
)


# =============================================================================
# Rate Limits (Defaults)
# =============================================================================

# Application-wide concurrent call limit
DEFAULT_APP_LIMIT = int(os.getenv("DEFAULT_APP_LIMIT", "100"))

# Per-organization concurrent call limit
DEFAULT_ORG_LIMIT = int(os.getenv("DEFAULT_ORG_LIMIT", "25"))


# =============================================================================
# Timeouts (in seconds)
# =============================================================================

# Maximum duration for a single call (30 minutes)
MAX_CALL_DURATION_SECONDS = 30 * 60

# Stale slot threshold: slots active longer than this are auto-released by the
# dispatcher reaper. Must exceed worst-case legitimate hold time:
# call_duration (30 min) + overhead = ~35 min.
# Using 40 minutes to match the phone number dispatcher.
STALE_SLOT_THRESHOLD_SECONDS = 40 * 60  # 40 minutes

# How often the dispatcher runs the stale slot reaper (every N main loop iterations).
# At 0.5s per iteration, 120 iterations ≈ every 60 seconds.
# Used by the old polling path only.
STALE_SLOT_REAP_INTERVAL = 120

# How often the call dispatcher runs the stale slot reaper (in seconds).
# Used by the signal-driven path (time-based instead of counter-based).
STALE_SLOT_REAP_INTERVAL_SECONDS = 60

# Poll interval for call status monitoring
CALL_MONITOR_POLL_INTERVAL_SECONDS = 20

# When transcript is unavailable, allow evals/CSAT only if call audio duration
# is above this threshold.
# NOTE: Kept as a code constant (not env-driven) because workflows replay and
# must remain deterministic across worker restarts/deploys.
MIN_DURATION_SECONDS_WITHOUT_TRANSCRIPT = 5

# Heartbeat timeout for long-running activities
MONITOR_HEARTBEAT_TIMEOUT_SECONDS = 60


# =============================================================================
# Workflow IDs
# =============================================================================

# Singleton workflow ID for the call dispatcher
DISPATCHER_WORKFLOW_ID = "call-dispatcher-singleton"

# Prefix for test execution workflow IDs
TEST_EXECUTION_WORKFLOW_ID_PREFIX = "test-exec"

# Prefix for call execution workflow IDs
CALL_EXECUTION_WORKFLOW_ID_PREFIX = "call-exec"

# Prefix for rerun coordinator workflow IDs
RERUN_COORDINATOR_WORKFLOW_ID_PREFIX = "rerun-coord"

# Prefix for hosted simulation-runner workflow IDs
SIMULATION_RUNNER_WORKFLOW_ID_PREFIX = "sim-runner"

# Prefix for rerun call execution workflow IDs (includes timestamp for uniqueness)
RERUN_CALL_EXECUTION_WORKFLOW_ID_PREFIX = "call-exec-rerun"


# =============================================================================
# Phone Number Dispatcher
# =============================================================================

# Singleton workflow ID for the phone number dispatcher
PHONE_NUMBER_DISPATCHER_WORKFLOW_ID = "phone-number-dispatcher-singleton"

# History events before PhoneNumberDispatcherWorkflow checkpoints
PHONE_NUMBER_DISPATCHER_CONTINUE_AS_NEW_THRESHOLD = 2000

# Per-organization concurrent phone number limit
DEFAULT_PHONE_ORG_LIMIT = int(os.getenv("DEFAULT_PHONE_ORG_LIMIT", "25"))

# Stale phone number threshold: phone held longer than this is auto-released.
# Max call duration is 30 minutes, plus 10 minute buffer = 40 minutes.
STALE_PHONE_NUMBER_THRESHOLD_SECONDS = 40 * 60  # 40 minutes

# How often the phone dispatcher runs the stale reaper (in seconds).
STALE_PHONE_NUMBER_REAP_INTERVAL_SECONDS = 60

# How often the phone dispatcher syncs available phone count from DB (in seconds).
PHONE_NUMBER_SYNC_INTERVAL_SECONDS = 300  # 5 minutes
