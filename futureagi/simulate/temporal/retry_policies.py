"""
Retry policies for Temporal activities.

Different activities have different retry requirements based on their
failure modes and idempotency characteristics.
"""

from datetime import timedelta

from temporalio.common import RetryPolicy

# =============================================================================
# Database Operations
# =============================================================================

DB_RETRY_POLICY = RetryPolicy(
    # Retry up to 3 times for transient DB errors
    maximum_attempts=3,
    # Start with 1 second delay
    initial_interval=timedelta(seconds=1),
    # Cap at 30 seconds between retries
    maximum_interval=timedelta(seconds=30),
    # Exponential backoff
    backoff_coefficient=2.0,
    # Don't retry on validation errors
    non_retryable_error_types=[
        "ValueError",
        "ValidationError",
        "HostedRunnerBuildError",
    ],
)


# =============================================================================
# Provider API Calls (VAPI, etc.)
# =============================================================================

PROVIDER_RETRY_POLICY = RetryPolicy(
    # Retry up to 3 times for provider API errors
    maximum_attempts=3,
    # Start with 5 second delay (provider rate limits)
    initial_interval=timedelta(seconds=5),
    # Cap at 1 minute between retries
    maximum_interval=timedelta(minutes=1),
    # Exponential backoff
    backoff_coefficient=2.0,
    # Don't retry on client errors (4xx) or config/validation errors
    non_retryable_error_types=[
        "ProviderAuthenticationError",
        "ProviderValidationError",
        "InsufficientBalanceError",
        "ValueError",
    ],
)


# =============================================================================
# Workflow Signals
# =============================================================================

SIGNAL_RETRY_POLICY = RetryPolicy(
    # More retries for signals (they're critical for coordination)
    maximum_attempts=5,
    # Start fast (500ms) - signals should be quick
    initial_interval=timedelta(milliseconds=500),
    # Cap at 5 seconds
    maximum_interval=timedelta(seconds=5),
    # Exponential backoff to avoid overwhelming target workflow
    backoff_coefficient=2.0,
)


# =============================================================================
# Evaluation Activities
# =============================================================================

EVAL_RETRY_POLICY = RetryPolicy(
    # Only retry twice for evaluations (they're expensive)
    maximum_attempts=2,
    # Start with 30 second delay
    initial_interval=timedelta(seconds=30),
    # Cap at 2 minutes
    maximum_interval=timedelta(minutes=2),
    # Exponential backoff
    backoff_coefficient=2.0,
    # Don't retry on evaluation logic errors
    non_retryable_error_types=["EvalConfigurationError"],
)


# =============================================================================
# Phone Number Pool
# =============================================================================

PHONE_RETRY_POLICY = RetryPolicy(
    # Retry many times for phone acquisition (phones may take a while to be released)
    maximum_attempts=30,
    # Start with 10 second delay (give time for phones to be released)
    initial_interval=timedelta(seconds=10),
    # Cap at 30 seconds (don't wait too long between attempts)
    maximum_interval=timedelta(seconds=30),
    # Linear-ish backoff (1.2x) to keep attempts frequent
    backoff_coefficient=1.2,
)


# =============================================================================
# No Retry Policy
# =============================================================================

NO_RETRY_POLICY = RetryPolicy(
    # Single attempt - for activities that handle their own retries
    # or where retries don't make sense (e.g., monitoring loops)
    maximum_attempts=1,
)
