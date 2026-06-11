"""
Error handling utilities for LiteLLM API responses.

Provides centralized error parsing and formatting to ensure:
- Concise, user-friendly error messages for cell values
- Verbose structured logging for debugging
"""

import json
import re
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional

import structlog
from litellm import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    AuthenticationError,
    BadRequestError,
    BudgetExceededError,
    ContentPolicyViolationError,
    ContextWindowExceededError,
    InternalServerError,
    InvalidRequestError,
    JSONSchemaValidationError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
    UnprocessableEntityError,
    UnsupportedParamsError,
)

from tfc.utils.error_codes import get_error_message

_logger = structlog.get_logger(__name__)

LITELLM_EXCEPTION_ERROR_CODES = {
    BadRequestError: "LITELLM_BAD_REQUEST",
    UnsupportedParamsError: "LITELLM_UNSUPPORTED_PARAMS",
    ContextWindowExceededError: "LITELLM_CONTEXT_WINDOW_EXCEEDED",
    ContentPolicyViolationError: "LITELLM_CONTENT_POLICY_VIOLATION",
    InvalidRequestError: "LITELLM_INVALID_REQUEST",
    AuthenticationError: "LITELLM_AUTHENTICATION_ERROR",
    NotFoundError: "LITELLM_NOT_FOUND",
    Timeout: "LITELLM_TIMEOUT",
    UnprocessableEntityError: "LITELLM_UNPROCESSABLE_ENTITY",
    RateLimitError: "LITELLM_RATE_LIMIT",
    APIConnectionError: "LITELLM_API_CONNECTION_ERROR",
    APIError: "LITELLM_API_ERROR",
    InternalServerError: "LITELLM_INTERNAL_SERVER_ERROR",
    BudgetExceededError: "LITELLM_BUDGET_EXCEEDED",
    JSONSchemaValidationError: "LITELLM_JSON_SCHEMA_VALIDATION_ERROR",
    ServiceUnavailableError: "LITELLM_SERVICE_UNAVAILABLE",
    APIResponseValidationError: "LITELLM_API_RESPONSE_VALIDATION_ERROR",
}


@contextmanager
def litellm_try_except(
    on_error: Optional[Callable] = None,
    default: Optional[Callable] = None,
):
    """
    Context manager to catch litellm errors and return an appropriate error code and message,
    or call a default handler for unexpected errors.
    """
    try:
        yield
    except tuple(LITELLM_EXCEPTION_ERROR_CODES.keys()) as exc:
        error_message = str(exc)
        if len(error_message) > 500:
            error_message = error_message[:500] + "..."
        _logger.error(f"{exc.__class__.__name__}: {exc}")
        if on_error:
            on_error(error_message)
        raise Exception(error_message)
    except Exception as exc:
        _logger.error(f"Exception: {exc}")
        error_message = get_error_message("FAILED_TO_PROCESS_ROW")
        if default:
            default()
        raise Exception(error_message)


# Exception messages produced by instrumentation bugs (e.g. traceai_litellm
# calling context-manager __exit__ with swapped arguments) that carry no
# useful information for the end user.
_UNINFORMATIVE_MESSAGES = frozenset(
    {
        "instance exception may not have a separate value",
    }
)


def _find_root_api_error(exception: Exception) -> Exception:
    """
    Walk the exception chain to find the most meaningful API error.

    When instrumentation libraries (e.g. traceai_litellm) have bugs in their
    error handling, they can raise secondary exceptions (like TypeError) that
    mask the original API error.  This function detects known uninformative
    wrappers and traverses ``__cause__`` / ``__context__`` to recover the
    real error.
    """
    current_msg = str(exception).strip().lower()
    if current_msg not in _UNINFORMATIVE_MESSAGES:
        return exception

    seen = {id(exception)}
    for attr in ("__cause__", "__context__"):
        exc = getattr(exception, attr, None)
        depth = 0
        while exc is not None and id(exc) not in seen and depth < 10:
            seen.add(id(exc))
            depth += 1
            exc_msg = str(exc).strip().lower()
            if exc_msg not in _UNINFORMATIVE_MESSAGES and exc_msg:
                return exc
            exc = getattr(exc, attr, None)

    return exception


def parse_api_error(exception: Exception) -> Dict[str, Any]:
    """
    Parse an API error exception into structured components.

    Attempts to extract:
    - error_type: The type/category of error
    - status_code: HTTP status code if available
    - message: Human-readable error message
    - provider: API provider name if identifiable
    - raw_error: Original error string for logging

    Args:
        exception: The caught exception from an API call

    Returns:
        Dictionary with parsed error components
    """
    error_str = str(exception)

    parsed = {
        "error_type": type(exception).__name__,
        "status_code": None,
        "message": error_str,
        "provider": "Unknown",
        "raw_error": error_str,
    }

    # Try to extract status code from various formats
    # Format 1: "Error code: 429 - ..." or "status: 429 -"
    status_match = re.search(r"(?:Error code:|status:)?\s*(\d{3})\s*-", error_str)
    if status_match:
        parsed["status_code"] = int(status_match.group(1))

    # Format 2: "status_code: 404" or "status_code=404" (httpx/SDK format)
    if parsed["status_code"] is None:
        status_match2 = re.search(r"status_code[=:]\s*(\d{3})", error_str)
        if status_match2:
            parsed["status_code"] = int(status_match2.group(1))

    # Try to parse JSON error response
    # Common format: 'Error code: 429 - {"error": {...}}'
    json_match = re.search(r"\{.*\}", error_str, re.DOTALL)
    if json_match:
        try:
            error_json = json.loads(json_match.group(0))

            if "error" in error_json:
                error_obj = error_json["error"]

                if isinstance(error_obj, dict):
                    if "type" in error_obj:
                        parsed["error_type"] = error_obj["type"]

                    if "message" in error_obj:
                        parsed["message"] = error_obj["message"]

                    if parsed["status_code"] is None and "code" in error_obj:
                        if isinstance(error_obj["code"], int):
                            parsed["status_code"] = error_obj["code"]

            # Handle nested detail format: body: {'detail': {'message': '...'}}
            if "detail" in error_json:
                detail = error_json["detail"]
                if isinstance(detail, dict):
                    if "message" in detail:
                        parsed["message"] = detail["message"]
                    if "status" in detail:
                        parsed["error_type"] = detail["status"]
                elif isinstance(detail, str):
                    parsed["message"] = detail

        except json.JSONDecodeError:
            pass

    # Try to extract message from verbose SDK error format (non-JSON, Python dict repr)
    # Format: body: {'detail': {'status': 'voice_not_found', 'message': 'A voice...'}}
    if len(parsed["message"]) > 200:  # Only for verbose messages
        # Try to extract 'message': '...' pattern
        msg_match = re.search(r"['\"]message['\"]\s*:\s*['\"]([^'\"]+)['\"]", error_str)
        if msg_match:
            parsed["message"] = msg_match.group(1)

        # Try to extract 'status': '...' pattern for error_type
        status_match = re.search(
            r"['\"]status['\"]\s*:\s*['\"]([^'\"]+)['\"]", error_str
        )
        if status_match:
            parsed["error_type"] = status_match.group(1)

    # Detect provider from error message
    error_lower = error_str.lower()
    parsed["provider"] = ""

    return parsed


def format_concise_error(parsed_error: Dict[str, Any]) -> str:
    """
    Format a parsed error into a concise, user-friendly message.

    Returns just the error message, truncated if needed.

    Args:
        parsed_error: Dictionary from parse_api_error()

    Returns:
        Concise error string suitable for cell values
    """
    message = parsed_error["message"]

    # Truncate very long messages (max 200 chars)
    max_message_length = 200
    if len(message) > max_message_length:
        message = message[:max_message_length] + "..."

    return message


def log_verbose_error(
    logger, parsed_error: Dict[str, Any], context: Dict[str, Any]
) -> None:
    """
    Log verbose error details with structured fields for debugging.

    Logs at ERROR level with fields:
    - Error details: error_type, status_code, message, provider, raw_error
    - Request context: model, temperature, max_tokens, message_count, output_format
    - User context: organization_id, workspace_id, template_id

    Args:
        logger: structlog logger instance
        parsed_error: Dictionary from parse_api_error()
        context: Dictionary with request and user context
    """
    # Build structured log fields
    log_fields = {
        # Error details
        "error_type": parsed_error["error_type"],
        "status_code": parsed_error["status_code"],
        "error_message": parsed_error["message"],
        "provider": parsed_error["provider"],
        "raw_error": parsed_error["raw_error"],
    }

    # Add request context if available
    request_fields = [
        "model",
        "temperature",
        "max_tokens",
        "message_count",
        "output_format",
        "frequency_penalty",
        "presence_penalty",
        "top_p",
    ]
    for field in request_fields:
        if field in context:
            log_fields[field] = context[field]

    # Add user context if available
    user_fields = ["organization_id", "workspace_id", "template_id"]
    for field in user_fields:
        if field in context:
            log_fields[field] = context[field]

    # Log with all structured fields. "No audio input found for STT" is an
    # expected user misconfiguration (text-only input to an audio eval), not an
    # API failure; downgrade only that case to warning so genuine API errors
    # keep creating Sentry issues.
    if "No audio input found in messages for STT." in str(
        parsed_error.get("raw_error", "")
    ):
        logger.warning(f"LiteLLM API error: {parsed_error['message']}", **log_fields)
    else:
        logger.error(f"LiteLLM API error: {parsed_error['message']}", **log_fields)


def handle_api_error(
    exception: Exception, logger, context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Complete error handling pipeline: parse, log, and format.

    This is a convenience function that:
    1. Parses the exception into structured components
    2. Logs verbose error details with structured fields
    3. Returns a concise, user-friendly error message

    Args:
        exception: The caught exception from an API call
        logger: structlog logger instance
        context: Optional dict with request/user context

    Returns:
        Concise error string suitable for cell values
    """
    if context is None:
        context = {}

    # Recover the real API error if the exception was masked by an
    # instrumentation bug (e.g. traceai_litellm __exit__ arg-order issue).
    exception = _find_root_api_error(exception)

    # Parse the error
    parsed_error = parse_api_error(exception)

    # Log verbose details
    log_verbose_error(logger, parsed_error, context)

    # Return concise format
    return format_concise_error(parsed_error)
