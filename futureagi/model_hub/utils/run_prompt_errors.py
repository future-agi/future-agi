"""Public error messages for run-prompt execution."""


class RunPromptValidationError(ValueError):
    """User input rejected by a local template/media check, never by I/O."""


def get_run_prompt_error_message(error, *, phase=None):
    # Both provenance and phase must be known. In particular, a provider's
    # ValueError (or a validation-shaped message) is not safe to expose.
    if phase == "local_validation" and isinstance(error, RunPromptValidationError):
        return str(error)
    return (
        "Unable to run prompt. Please try again or contact support if the problem persists."
    )
