from collections.abc import Iterator, Mapping, Sequence

from rest_framework import status
from rest_framework.response import Response

from agentic_eval.core_evals.fi_utils.exceptions import (
    CodeEvalInternalServiceError,
    CodeEvalSetupError,
    CustomCodeEvalPausedError,
)
from tfc.utils.api_errors import build_error_envelope

# The reasons a person reads for an evaluation that failed with an error that
# is not a typed refusal. That error's own text can carry a provider's answer
# or a key.
EVALUATION_COULD_NOT_START = "This evaluation could not start."
EVALUATION_COULD_NOT_RUN = "This evaluation could not run."
CODE_EVAL_SETUP_ERROR = "CODE_EVAL_SETUP_ERROR"


def _append_error(errors, key, value):
    field = key or "non_field_errors"
    errors.setdefault(field, []).append(str(value))


def _flatten_validation_errors(value, *, prefix="", errors=None):
    if errors is None:
        errors = {}

    if isinstance(value, Mapping):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            _flatten_validation_errors(child, prefix=child_prefix, errors=errors)
        return errors

    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        if not value:
            errors.setdefault(prefix or "non_field_errors", [])
            return errors

        for index, child in enumerate(value):
            if isinstance(child, Mapping) or (
                isinstance(child, Sequence) and not isinstance(child, str | bytes)
            ):
                child_prefix = f"{prefix}.{index}" if prefix else str(index)
                _flatten_validation_errors(child, prefix=child_prefix, errors=errors)
            else:
                _append_error(errors, prefix, child)
        return errors

    _append_error(errors, prefix, value)
    return errors


def _first_field_error(flattened_errors):
    """The refusal's message: the first field error, after its field.

    The first sentence of the first field that has one, in the order details
    lists them. One sentence, so the message stays short however many fields
    or items failed; details and errors still list every error.
    ``non_field_errors`` is not a field, so it is left out of the prefix:
    ``eval_config.non_field_errors`` reads ``eval_config: ...`` and
    ``non_field_errors`` reads the sentence alone. "Validation failed" when no
    field has a sentence.
    """
    for key, messages in flattened_errors.items():
        sentence = next((text.strip() for text in messages if text.strip()), "")
        if not sentence:
            continue
        parts = [part for part in key.split(".") if part != "non_field_errors"]
        field = ".".join(parts)
        return f"{field}: {sentence}" if field else sentence
    return "Validation failed"


def sdk_validation_error_response(errors):
    """Return the SDK's typed validation-error envelope."""
    flattened_errors = _flatten_validation_errors(errors)
    return Response(
        {
            **build_error_envelope(
                _first_field_error(flattened_errors),
                status_code=status.HTTP_400_BAD_REQUEST,
                code="invalid",
                error_type="validation_error",
                details=flattened_errors,
            ),
            "errors": flattened_errors,
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


class SDKAdmissionError(Exception):
    """A refused SDK evaluation, before any engine work is admitted.

    Keep this transport independent of EE imports so OSS evaluation paths can
    still load without the optional billing package.
    """

    def __init__(self, code, reason, *, metadata=None):
        super().__init__(reason)
        self.code = code
        self.metadata = metadata or {}

    @classmethod
    def from_usage_check(cls, check):
        metadata = {
            "dimension": check.dimension,
            "current_usage": check.current_usage,
            "limit": check.limit,
        }
        if check.upgrade_cta:
            metadata["upgrade_cta"] = check.upgrade_cta.model_dump()
        return cls(
            check.error_code or "USAGE_LIMIT_EXCEEDED",
            check.reason or "Usage limit exceeded",
            metadata=metadata,
        )

    @classmethod
    def unavailable(cls):
        # Dependency exception messages can contain internal diagnostics.
        return cls(
            "USAGE_CHECK_UNAVAILABLE",
            "Usage validation is temporarily unavailable. Please retry.",
        )

    def to_response(self):
        http_status = {
            "PAYMENT_REQUIRED": status.HTTP_402_PAYMENT_REQUIRED,
            "BALANCE_EXHAUSTED": status.HTTP_402_PAYMENT_REQUIRED,
            "INSUFFICIENT_CREDITS": status.HTTP_402_PAYMENT_REQUIRED,
            "BUDGET_CHECK_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
            "USAGE_CHECK_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
        }.get(self.code, status.HTTP_429_TOO_MANY_REQUESTS)
        return Response(
            build_error_envelope(
                str(self),
                status_code=http_status,
                code=self.code,
                extra={
                    **self.metadata,
                    "error_code": self.code,
                    "retryable": (
                        http_status == status.HTTP_503_SERVICE_UNAVAILABLE
                        or self.code == "RATE_LIMITED"
                    ),
                },
            ),
            status=http_status,
        )


def _wrapped_errors(exc: BaseException) -> Iterator[BaseException]:
    """``exc`` and the errors it wraps, each once: its first argument when that
    is an error, and its explicit cause."""
    seen = set()
    pending = [exc]
    while pending:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if current.args and isinstance(current.args[0], BaseException):
            pending.append(current.args[0])
        pending.append(current.__cause__)


def code_eval_setup_error(exc: BaseException) -> CodeEvalSetupError | None:
    """The error behind ``exc`` that says this deployment cannot run a code
    evaluation (no code executor, no Node.js, a sandbox that cannot start a
    run, or custom code paused), or None for any other error.

    Code that calls an internal service is the caller's to change, so its
    CodeEvalInternalServiceError gives None. sdk.utils.evaluations._run_eval
    wraps the engine's error in StandaloneEvaluationError, so the wrapped
    errors are read too.
    """
    for error in _wrapped_errors(exc):
        if isinstance(error, CodeEvalInternalServiceError):
            return None
        if isinstance(error, CodeEvalSetupError):
            return error
    return None


def code_eval_setup_error_response(error: CodeEvalSetupError) -> Response:
    """503 for a code evaluation this deployment cannot run: the error's own
    sentence, code and error_code CUSTOM_CODE_EVALS_PAUSED for the pause or
    CODE_EVAL_SETUP_ERROR otherwise, and retryable false.

    Nothing in the request causes it, so it is not a 400. Sending the request
    again does not help until the deployment changes, so unlike the admission
    503s it is not retryable: SDKAdmissionError.to_response marks every 503
    retryable and is not used here.
    """
    code = (
        error.error_code
        if isinstance(error, CustomCodeEvalPausedError)
        else CODE_EVAL_SETUP_ERROR
    )
    return Response(
        build_error_envelope(
            str(error),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=code,
            extra={"error_code": code, "retryable": False},
        ),
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def evaluation_failure_reason(exc: BaseException) -> str:
    """The reason a person reads for an evaluation that raised ``exc`` while
    it ran.

    A typed refusal gives its own text: a code evaluation this install cannot
    run (CodeEvalSetupError and its subclasses, whose texts are the backend's
    own) or a refused admission (SDKAdmissionError, whose text is its reason).
    sdk.utils.evaluations._run_eval raises some of them wrapped in
    StandaloneEvaluationError, so the wrapped error and the explicit cause are
    read too, never the wrapper's own text. Otherwise a failed Jev run's fixed
    text, and otherwise EVALUATION_COULD_NOT_RUN.
    """
    for current in _wrapped_errors(exc):
        if isinstance(current, CodeEvalSetupError | SDKAdmissionError):
            sentence = str(current).strip()
            if sentence:
                return sentence

    from tfc.ee_gates import jev_error_message

    return jev_error_message(exc) or EVALUATION_COULD_NOT_RUN
