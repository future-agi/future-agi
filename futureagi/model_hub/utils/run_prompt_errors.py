"""Public error messages for run-prompt execution."""

import re

from tfc.utils.ssrf_guard import ENDPOINT_ERROR_MESSAGES, endpoint_status_message

# The guard's fixed copy for a failed call to a tenant's model endpoint. It
# names no host, address or response text and is written to be shown to the
# tenant; litellm_response re-raises an endpoint failure as a plain Exception
# whose text is exactly one of these strings. The status copy names only the
# HTTP code and its standard phrase; it is listed for every code from 400 to
# 999, since the endpoint clients accept any three-digit status.
_ENDPOINT_ERROR_COPY = frozenset(ENDPOINT_ERROR_MESSAGES.values()) | frozenset(
    endpoint_status_message(code) for code in range(400, 1000)
)

# A provider "custom" model's endpoint is called by CustomModelHandler, which
# puts one of two fixed prefixes before that copy; litellm_response passes the
# text on unchanged. The timeout's whole seconds are the only variable part.
_CUSTOM_MODEL_PREFIXED_COPY = re.compile(
    r"(?:Custom model API request failed: "
    r"|Custom model request timed out after [0-9]+ seconds: )"
    r"(?P<copy>.*)",
    re.DOTALL,
)


class RunPromptValidationError(ValueError):
    """User input rejected by a local template/media check, never by I/O."""


def _is_endpoint_copy(message):
    """The whole text is one fixed copy, alone or after one fixed prefix."""
    if not isinstance(message, str):
        return False
    if message in _ENDPOINT_ERROR_COPY:
        return True
    prefixed = _CUSTOM_MODEL_PREFIXED_COPY.fullmatch(message)
    return prefixed is not None and prefixed.group("copy") in _ENDPOINT_ERROR_COPY


def get_run_prompt_error_message(error, *, phase=None):
    # Both provenance and phase must be known. In particular, a provider's
    # ValueError (or a validation-shaped message) is not safe to expose.
    if phase == "local_validation" and isinstance(error, RunPromptValidationError):
        return str(error)
    # Endpoint copy passes only when the whole text equals one of the fixed
    # strings, alone or after one of CustomModelHandler's fixed prefixes;
    # copy with anything added to it is not trusted.
    try:
        message = str(error)
    except Exception:
        message = None
    if _is_endpoint_copy(message):
        return message
    return (
        "Unable to run prompt. Please try again or contact support if the problem persists."
    )
