from collections.abc import Mapping, Sequence

from rest_framework import status
from rest_framework.response import Response

from tfc.utils.api_errors import build_error_envelope


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


def sdk_validation_error_response(errors):
    """Return the SDK's typed validation-error envelope."""
    flattened_errors = _flatten_validation_errors(errors)
    return Response(
        {
            **build_error_envelope(
                "Validation failed",
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
