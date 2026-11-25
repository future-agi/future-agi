"""Policy refusals crossing activity boundaries are terminal, not infrastructure errors."""

from temporalio.exceptions import ActivityError, ApplicationError


def terminal_evaluation_denial(denial):
    """Preserve the refusal details in Temporal without replaying paid work."""
    return ApplicationError(
        str(denial), denial.as_dict(), type="EvaluationDenied", non_retryable=True
    )


def evaluation_denial_message(error):
    """Recover the actionable refusal rather than persisting 'Activity task failed'."""
    if isinstance(error, ActivityError):
        error = error.cause
    if isinstance(error, ApplicationError) and error.type == "EvaluationDenied":
        denial = error.details[0]
        return f"{denial['code']}: {denial['message']}"
    return None
