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
