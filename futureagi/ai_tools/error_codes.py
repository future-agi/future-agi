"""Standardized error codes for tool results."""

NOT_FOUND = "NOT_FOUND"
PERMISSION_DENIED = "PERMISSION_DENIED"
VALIDATION_ERROR = "VALIDATION_ERROR"
RATE_LIMITED = "RATE_LIMITED"
INTERNAL_ERROR = "INTERNAL_ERROR"
EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
TIMEOUT_ERROR = "TIMEOUT_ERROR"

EXCEPTION_TO_CODE = {
    "DoesNotExist": NOT_FOUND,
    "ObjectDoesNotExist": NOT_FOUND,
    "Http404": NOT_FOUND,
    "NotFound": NOT_FOUND,  # rest_framework.exceptions.NotFound
    "PermissionDenied": PERMISSION_DENIED,
    "NotAuthenticated": PERMISSION_DENIED,
    "AuthenticationFailed": PERMISSION_DENIED,
    "Throttled": RATE_LIMITED,
    "ValidationError": VALIDATION_ERROR,
    "ValueError": VALIDATION_ERROR,
    "TypeError": VALIDATION_ERROR,
    "IntegrityError": VALIDATION_ERROR,
    "ImportError": CONFIGURATION_ERROR,
    "TimeoutError": TIMEOUT_ERROR,
    "ConnectionError": EXTERNAL_SERVICE_ERROR,
}


def code_from_exception(exc: Exception) -> str:
    """Derive an error code from an exception type."""
    exc_name = type(exc).__name__
    # Check exact match first
    if exc_name in EXCEPTION_TO_CODE:
        return EXCEPTION_TO_CODE[exc_name]
    # Check parent classes (e.g., SomeModel.DoesNotExist inherits ObjectDoesNotExist)
    for parent in type(exc).__mro__:
        if parent.__name__ in EXCEPTION_TO_CODE:
            return EXCEPTION_TO_CODE[parent.__name__]
    return INTERNAL_ERROR
