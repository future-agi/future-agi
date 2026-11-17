"""Plain reasons for scenarios whose generation failed.

When a scenario goes ``Failed``, one sentence a person can act on is kept in
``Scenarios.metadata["failure_reason"]``. The scenario detail API returns it
and the scenario page shows it. The sentence is always one of the fixed texts
below, never an exception message, a provider's error body or the name of a
credential, so it is safe to show to anyone who can open the scenario. The
detail of what went wrong stays in the server log.
"""

import json

import structlog

from model_hub.models.choices import StatusType
from simulate.models import Scenarios

logger = structlog.get_logger(__name__)

FAILURE_REASON_KEY = "failure_reason"

NO_MODEL_PROVIDER = (
    "No working model provider is set up for scenario generation on this "
    "deployment. Ask your administrator to add one, then create the scenario "
    "again."
)
USAGE_LIMIT_REACHED = (
    "A usage or billing limit on your organization stopped this generation. "
    "Ask an admin to check your plan and budget, then create the scenario "
    "again."
)
MODEL_RATE_LIMITED = (
    "The model provider turned requests away because too many were sent. "
    "Wait a few minutes, then create the scenario again."
)
MODEL_REFUSED = (
    "The model refused to write scenarios for this agent or prompt. Change "
    "its wording, then create the scenario again."
)
MODEL_TIMED_OUT = (
    "The model did not answer in time. Create the scenario again, or ask for "
    "fewer scenarios."
)
UNEXPECTED_ERROR = (
    "Something unexpected stopped generation. Create the scenario again, and "
    "if it keeps failing, try a different agent or prompt version."
)

# Each known cause: its reason, the exception class names that mean it, and
# lower-case markers in the error text. Class names are matched on every
# exception in the cause chain and on each class's bases, so the litellm,
# openai, google-auth and httpx classes of one name all count without
# importing them. The first cause that matches wins.
_CAUSES = (
    (
        NO_MODEL_PROVIDER,
        frozenset(
            {
                "AuthenticationError",
                "DefaultCredentialsError",
                "PermissionDeniedError",
                "RefreshError",
            }
        ),
        (
            "google_application_credentials",
            "google_cloud_project environment variable",
            "missing key inputs argument",
            "api_key client option must be set",
            "could not automatically determine credentials",
            "defaultcredentialserror",
            "is not a valid json file",
            "incorrect api key",
            "invalid api key",
            "api key not valid",
            "no_enterprise_license",
        ),
    ),
    (
        MODEL_RATE_LIMITED,
        frozenset({"RateLimitError", "ResourceExhausted"}),
        ("rate limit", "too many requests", "resource exhausted"),
    ),
    (
        USAGE_LIMIT_REACHED,
        frozenset(),
        (
            "usage limit exceeded",
            "usage paused",
            # "Budget status is unavailable" (ee/usage/services/metering.py:125) means the billing check
            # itself is down, not that a limit was reached, so it falls through to the generic sentence.
            "free tier",
            "unpaid invoices",
        ),
    ),
    (
        MODEL_REFUSED,
        frozenset({"ContentPolicyViolationError"}),
        ("content policy", "content_policy"),
    ),
    (
        MODEL_TIMED_OUT,
        frozenset(
            {
                "APITimeoutError",
                "ConnectTimeout",
                "ReadTimeout",
                "Timeout",
                "TimeoutError",
                "TimeoutException",
            }
        ),
        ("timed out", "deadline exceeded"),
    ),
)

_MAX_CHAIN = 20


def _chain(error):
    """``error`` and the exceptions it was raised from or while handling."""
    seen = set()
    while error is not None and id(error) not in seen and len(seen) < _MAX_CHAIN:
        seen.add(id(error))
        yield error
        error = error.__cause__ or error.__context__


def failure_reason_for(error=None) -> str:
    """The fixed sentence for what stopped generation.

    ``error`` is the exception (or the error text) the failure came with;
    without one, or for a cause that is not a known one, the calm generic
    sentence.
    """
    if isinstance(error, BaseException):
        chain = list(_chain(error))
        names = {cls.__name__ for exc in chain for cls in type(exc).__mro__}
        texts = [str(exc).lower() for exc in chain]
    elif error:
        names = set()
        texts = [str(error).lower()]
    else:
        return UNEXPECTED_ERROR
    for reason, type_names, markers in _CAUSES:
        if names & type_names:
            return reason
        if any(marker in text for text in texts for marker in markers):
            return reason
    return UNEXPECTED_ERROR


def _metadata_dict(metadata):
    """Scenario metadata as a dict (some rows hold a JSON string), or None when
    it is something else and must be left as it is."""
    if not metadata:
        return {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return None
    return metadata if isinstance(metadata, dict) else None


def _recorded_reason(metadata):
    reason = metadata.get(FAILURE_REASON_KEY)
    return reason if isinstance(reason, str) and reason.strip() else None


def apply_failure_reason(scenario, error=None) -> bool:
    """Put the reason for ``error`` on ``scenario.metadata``; the caller saves.

    A scenario that already has a reason keeps it: the cause found where the
    failure happened must not be replaced by a later, vaguer one (such as the
    workflow's final "no dataset"). Returns whether a reason was added.

    Never raises, because it runs inside the handlers that mark a scenario
    failed.
    """
    try:
        metadata = _metadata_dict(scenario.metadata)
        if metadata is None or _recorded_reason(metadata):
            return False
        reason = failure_reason_for(error)
        scenario.metadata = {**metadata, FAILURE_REASON_KEY: reason}
    except Exception:
        logger.warning("scenario_failure_reason_not_recorded", exc_info=True)
        return False
    is_exception = isinstance(error, BaseException)
    try:
        logger.warning(
            "scenario_generation_failed",
            scenario_id=str(scenario.id),
            failure_reason=reason,
            error_type=type(error).__name__ if is_exception else None,
            exc_info=error if is_exception else None,
        )
    except Exception:
        pass
    return True


def record_failure_reason(scenario_id, error=None) -> None:
    """``apply_failure_reason`` for code that holds only the scenario id.

    Saves the metadata alone, so the scenario's status is never touched here.
    Never raises.
    """
    try:
        scenario = Scenarios.no_workspace_objects.filter(id=scenario_id).first()
        if scenario is not None and apply_failure_reason(scenario, error):
            scenario.save(update_fields=["metadata"])
    except Exception:
        logger.warning(
            "scenario_failure_reason_not_recorded",
            scenario_id=str(scenario_id),
            exc_info=True,
        )


def scenario_failure_reason(scenario):
    """The reason a failed scenario shows, or None.

    None when the scenario has not failed (a reason left from a step that was
    retried does not apply) or failed before reasons were recorded.
    """
    if scenario.status != StatusType.FAILED.value:
        return None
    metadata = _metadata_dict(scenario.metadata)
    return _recorded_reason(metadata) if metadata else None
