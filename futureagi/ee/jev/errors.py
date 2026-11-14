"""User-facing text for failed Jev runs, fixed per typed code.

The text is never built from an exception's message beyond the mapping
errors' own details: a gateway error may sit next to the org's key, the
internal key or TypeSafe's response body, and none of those may reach a user.
"""

from agentic_eval.core_evals.run_prompt.litellm_models import (
    MissingProviderKeyError,
    missing_provider_key_message,
)
from ee.jev.client import JevGatewayError
from ee.jev.mapping import JevMappingError
from ee.jev.models import JEV_PROVIDER
from ee.jev.validation import JevResponseError

_GATEWAY_MESSAGES = {
    "JEV_KEY_INVALID": (
        "TypeSafe rejected the API key saved under Settings → AI Providers. "
        "Update the key and try again."
    ),
    "JEV_RATE_LIMITED": "TypeSafe's rate limit was reached. Try again shortly.",
    "JEV_TIMEOUT": "TypeSafe didn't respond in time. Try again.",
}
_NOT_COMPLETED = "TypeSafe couldn't complete this evaluation."
# Mapping errors that keep their own code at run time. Any other rule the
# mapping check enforces is reported as JEV_MAPPING_INVALID with the rule's
# code in details, as the save and view-level checks report it.
_RUN_CODES = frozenset(
    {
        "JEV_MODEL_UNKNOWN",
        "JEV_MAPPING_INVALID",
        "JEV_MISSING_VARIABLE",
        "JEV_INPUT_UNSUPPORTED",
        "JEV_INPUT_TOO_LARGE",
    }
)


def key_messages():
    """The texts a failed run carries when the organization's TypeSafe key is
    missing or TypeSafe rejected it: both are fixed under Settings → AI
    Providers."""
    return frozenset(
        {
            _GATEWAY_MESSAGES["JEV_KEY_INVALID"],
            missing_provider_key_message(JEV_PROVIDER),
        }
    )


def _jev_cause(exc):
    """The Jev error behind exc: exc itself, or one it wraps (callers such as
    the SDK wrap the engine's exception in their own)."""
    seen = set()
    while isinstance(exc, BaseException) and id(exc) not in seen:
        if isinstance(exc, (JevGatewayError, JevMappingError, JevResponseError)):
            return exc
        if isinstance(exc, MissingProviderKeyError) and exc.provider == JEV_PROVIDER:
            return exc
        seen.add(id(exc))
        wrapped = exc.args[0] if exc.args else None
        exc = wrapped if isinstance(wrapped, BaseException) else exc.__cause__
    return None


def describe(exc):
    """{"code", "message", "attr", "details"} for a failed Jev run, or None
    when exc is not a Jev failure."""
    error = _jev_cause(exc)
    if error is None:
        return None
    if isinstance(error, JevMappingError):
        # Unknown models and mapping errors keep their specific messages,
        # without the code in front: that travels as "code".
        return {
            "code": error.code if error.code in _RUN_CODES else "JEV_MAPPING_INVALID",
            "message": error.detail,
            "attr": error.field,
            "details": error.details,
        }
    if isinstance(error, MissingProviderKeyError):
        # The engine reads the org's key before building the client, so a
        # missing key surfaces as the shared provider error, not JEV_KEY_MISSING.
        code = "JEV_KEY_MISSING"
    else:
        code = error.code
    if code == "JEV_KEY_MISSING":
        message = missing_provider_key_message(JEV_PROVIDER)
    elif code in _GATEWAY_MESSAGES:
        message = _GATEWAY_MESSAGES[code]
    elif getattr(error, "retryable", False):
        message = f"{_NOT_COMPLETED} Try again."
    else:
        message = _NOT_COMPLETED
    return {"code": code, "message": message, "attr": None, "details": None}
