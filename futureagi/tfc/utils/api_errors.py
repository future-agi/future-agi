from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from rest_framework import status


class ApiErrorType(StrEnum):
    VALIDATION_ERROR = "validation_error"
    AUTHENTICATION_ERROR = "authentication_error"
    PAYMENT_REQUIRED = "payment_required"
    ENTITLEMENT_ERROR = "entitlement_error"
    PERMISSION_ERROR = "permission_error"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    CLIENT_ERROR = "client_error"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    TIMEOUT = "timeout"
    API_ERROR = "api_error"


class ApiErrorCode(StrEnum):
    INVALID = "invalid"
    NOT_AUTHENTICATED = "not_authenticated"
    PAYMENT_REQUIRED = "payment_required"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    GONE = "gone"
    REQUEST_TOO_LARGE = "request_too_large"
    EXPORT_TOO_LARGE = "export_too_large"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    TIMEOUT = "timeout"
    ERROR = "error"
    REQUIRED = "required"
    # Domain-specific 413 variants: a specific cap was exceeded, so the FE can
    # distinguish "narrow your selection" from a generic request-too-large.
    ITEMS_TOO_LARGE = "items_too_large"


API_ERROR_TYPE_CHOICES = [(item.value, item.value) for item in ApiErrorType]

ERROR_TYPE_BY_STATUS = {
    status.HTTP_400_BAD_REQUEST: ApiErrorType.VALIDATION_ERROR,
    status.HTTP_401_UNAUTHORIZED: ApiErrorType.AUTHENTICATION_ERROR,
    status.HTTP_402_PAYMENT_REQUIRED: ApiErrorType.PAYMENT_REQUIRED,
    status.HTTP_403_FORBIDDEN: ApiErrorType.PERMISSION_ERROR,
    status.HTTP_404_NOT_FOUND: ApiErrorType.NOT_FOUND,
    status.HTTP_409_CONFLICT: ApiErrorType.CONFLICT,
    status.HTTP_410_GONE: ApiErrorType.CLIENT_ERROR,
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: ApiErrorType.CLIENT_ERROR,
    status.HTTP_429_TOO_MANY_REQUESTS: ApiErrorType.RATE_LIMIT,
    status.HTTP_500_INTERNAL_SERVER_ERROR: ApiErrorType.SERVER_ERROR,
    status.HTTP_503_SERVICE_UNAVAILABLE: ApiErrorType.SERVICE_UNAVAILABLE,
    status.HTTP_504_GATEWAY_TIMEOUT: ApiErrorType.TIMEOUT,
}

DEFAULT_CODE_BY_STATUS = {
    status.HTTP_400_BAD_REQUEST: ApiErrorCode.INVALID,
    status.HTTP_401_UNAUTHORIZED: ApiErrorCode.NOT_AUTHENTICATED,
    status.HTTP_402_PAYMENT_REQUIRED: ApiErrorCode.PAYMENT_REQUIRED,
    status.HTTP_403_FORBIDDEN: ApiErrorCode.PERMISSION_DENIED,
    status.HTTP_404_NOT_FOUND: ApiErrorCode.NOT_FOUND,
    status.HTTP_409_CONFLICT: ApiErrorCode.CONFLICT,
    status.HTTP_410_GONE: ApiErrorCode.GONE,
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: ApiErrorCode.REQUEST_TOO_LARGE,
    status.HTTP_429_TOO_MANY_REQUESTS: ApiErrorCode.RATE_LIMITED,
    status.HTTP_500_INTERNAL_SERVER_ERROR: ApiErrorCode.SERVER_ERROR,
    status.HTTP_503_SERVICE_UNAVAILABLE: ApiErrorCode.SERVICE_UNAVAILABLE,
    status.HTTP_504_GATEWAY_TIMEOUT: ApiErrorCode.TIMEOUT,
}


def stringify_error_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "; ".join(
            f"{key}: {stringify_error_value(item)}" for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return ", ".join(stringify_error_value(item) for item in value)
    return str(value)


def error_details(value: Any) -> dict[str, list[str]] | None:
    if isinstance(value, Mapping):
        return {
            str(key): (
                [stringify_error_value(item) for item in item_value]
                if isinstance(item_value, (list, tuple, set))
                else [stringify_error_value(item_value)]
            )
            for key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        indexed_details = {}
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                continue
            for key, item_value in item.items():
                indexed_key = f"{index}.{key}"
                indexed_details[indexed_key] = (
                    [stringify_error_value(error) for error in item_value]
                    if isinstance(item_value, (list, tuple, set))
                    else [stringify_error_value(item_value)]
                )
        if indexed_details:
            return indexed_details
        return {"non_field_errors": [stringify_error_value(item) for item in value]}
    return None


def error_message(value: Any) -> str:
    if isinstance(value, str) or value is None:
        return value or "Request failed."
    if isinstance(value, Mapping):
        for key in ("message", "detail", "error", "non_field_errors"):
            if key in value:
                return stringify_error_value(value[key])
        if len(value) == 1:
            key, item = next(iter(value.items()))
            return f"{key}: {stringify_error_value(item)}"
    return stringify_error_value(value)


def first_error_code(value: Any, default_code: str = "error") -> str:
    code = getattr(value, "code", None)
    if code:
        return str(code)
    if isinstance(value, Mapping):
        explicit_code = value.get("error_code") or value.get("code")
        if explicit_code:
            return str(explicit_code)
        for item in value.values():
            nested_code = first_error_code(item, "")
            if nested_code:
                return nested_code
    if isinstance(value, (list, tuple, set)):
        for item in value:
            nested_code = first_error_code(item, "")
            if nested_code:
                return nested_code
    return default_code


def first_exception_code(value: Any, default_code: str = "error") -> str:
    if isinstance(value, Mapping):
        for item in value.values():
            nested_code = first_exception_code(item, "")
            if nested_code:
                return nested_code
    if isinstance(value, (list, tuple, set)):
        for item in value:
            nested_code = first_exception_code(item, "")
            if nested_code:
                return nested_code
    if value:
        return str(value)
    return default_code


def first_error_attr(details: Mapping[str, Any] | None) -> str | None:
    if not details:
        return None
    for key in details:
        if key not in {"detail", "error", "message", "non_field_errors"}:
            return str(key)
    return None


def exception_code(exc: Exception, default_code: str = "error") -> str:
    if hasattr(exc, "get_codes"):
        return first_exception_code(exc.get_codes(), default_code)
    return str(getattr(exc, "default_code", default_code) or default_code)


def build_error_envelope(
    value: Any,
    *,
    status_code: int = status.HTTP_400_BAD_REQUEST,
    code: str | None = None,
    error_type: str | None = None,
    details: dict[str, list[str]] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_details = details if details is not None else error_details(value)
    default_code = DEFAULT_CODE_BY_STATUS.get(status_code, ApiErrorCode.ERROR).value
    if code:
        resolved_code = code
    elif getattr(value, "code", None):
        resolved_code = str(value.code)
    elif isinstance(value, str) or value is None:
        resolved_code = default_code
    else:
        resolved_code = first_error_code(value, default_code)
    resolved_type = error_type or ERROR_TYPE_BY_STATUS.get(
        status_code, ApiErrorType.API_ERROR
    ).value
    message = error_message(value)

    # When the caller passes a dict with an explicit ``error_code`` key (e.g.
    # the login view's structured error responses), preserve the full dict as
    # ``result`` so consumers can access ``data["result"]["error_code"]`` etc.,
    # and promote the error_code to the top-level ``code`` field.
    _has_error_code = isinstance(value, Mapping) and "error_code" in value
    if _has_error_code and not code:
        resolved_code = value["error_code"]

    body: dict[str, Any] = {
        "status": False,
        "type": resolved_type,
        "code": str(resolved_code or "error"),
        "detail": message,
        "message": message,
        "error": message,
        "result": dict(value) if _has_error_code else message,
    }
    attr = first_error_attr(resolved_details)
    if attr:
        body["attr"] = attr
    if resolved_details:
        body["details"] = resolved_details
    if extra:
        body.update(extra)
    return body
