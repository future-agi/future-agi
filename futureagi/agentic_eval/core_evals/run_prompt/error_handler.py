"""
Error handling utilities for LiteLLM API responses.

Provides centralized error parsing and formatting to ensure:
- Concise, user-friendly error messages for cell values
- Verbose structured logging for debugging
"""

import json
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Callable, Dict, Iterator, Optional, Union

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


@dataclass
class ErrorContext:
    """Structured request/user context attached to error logs.

    Replaces the loosely-typed dict that used to be threaded through the error
    pipeline so callers get field-name checking. Every field is optional; only
    the ones a given call site can populate are set, and unset (``None``) fields
    are dropped before logging.
    """

    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    message_count: Optional[int] = None
    output_format: Optional[str] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    top_p: Optional[float] = None
    provider: Optional[str] = None
    organization_id: Optional[Any] = None
    workspace_id: Optional[Any] = None
    template_id: Optional[Any] = None


def _as_context_dict(
    context: Optional[Union[Dict[str, Any], "ErrorContext"]],
) -> Dict[str, Any]:
    """Coerce an ``ErrorContext`` (or legacy dict) into a logging dict.

    ``ErrorContext`` fields left at their ``None`` default are omitted so the
    log output matches the old "only include populated keys" behaviour.
    """
    if context is None:
        return {}
    if is_dataclass(context) and not isinstance(context, type):
        return {k: v for k, v in asdict(context).items() if v is not None}
    return context

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
        root_exc = _find_root_api_error(exc)
        error_message = format_concise_error(parse_api_error(root_exc))
        if len(error_message) > 500:
            error_message = error_message[:500] + "..."
        _logger.error(f"{root_exc.__class__.__name__}: {root_exc}")
        if on_error:
            on_error(error_message)
        raise Exception(error_message) from root_exc
    except Exception as exc:
        root_exc = _find_root_api_error(exc)
        if root_exc is not exc:
            error_message = format_concise_error(parse_api_error(root_exc))
        else:
            error_message = get_error_message("FAILED_TO_PROCESS_ROW")
        _logger.error(f"Exception: {root_exc}")
        if default:
            default()
        raise Exception(error_message) from root_exc


_API_ERROR_MESSAGE_PATTERNS = (
    "api key",
    "authentication",
    "unauthorized",
    "forbidden",
    "rate limit",
    "quota",
    "billing",
    "bad request",
    "invalid request",
    "not found",
    "context window",
    "timeout",
    "timed out",
    "service unavailable",
    "unprocessable",
    "content policy",
    "openai",
    "anthropic",
    "gemini",
    "litellm",
)


def _iter_exception_chain(exception: Exception) -> Iterator[Exception]:
    seen = set()
    stack = [exception]

    while stack and len(seen) < 20:
        exc = stack.pop(0)
        if exc is None or id(exc) in seen:
            continue

        seen.add(id(exc))
        yield exc

        cause = getattr(exc, "__cause__", None)
        context = getattr(exc, "__context__", None)
        if cause is not None:
            stack.append(cause)
        if context is not None and context is not cause:
            stack.append(context)


def _api_error_score(exception: Exception) -> int:
    message = str(exception).strip()
    lower_message = message.lower()
    class_name = exception.__class__.__name__.lower()
    score = 0

    if isinstance(exception, tuple(LITELLM_EXCEPTION_ERROR_CODES.keys())):
        score += 100

    if any(pattern in lower_message for pattern in _API_ERROR_MESSAGE_PATTERNS):
        score += 40

    if any(
        pattern.replace(" ", "") in class_name
        for pattern in _API_ERROR_MESSAGE_PATTERNS
    ):
        score += 25

    if re.search(r"\b(?:4\d{2}|5\d{2})\b", message):
        score += 20

    if message:
        score += min(len(message), 200) // 40
    else:
        score -= 50

    if type(exception) is Exception and not any(
        pattern in lower_message for pattern in _API_ERROR_MESSAGE_PATTERNS
    ):
        score -= 10

    if isinstance(exception, (TypeError, ValueError)) and not any(
        pattern in lower_message for pattern in _API_ERROR_MESSAGE_PATTERNS
    ):
        score -= 20

    return score


def _find_root_api_error(exception: Exception) -> Exception:
    """
    Walk the exception chain to find the most meaningful API error.

    Instrumentation and wrapper layers can raise secondary exceptions that mask
    the provider error. Prefer chained exceptions with clear API-error signal,
    but keep the original exception when the chain does not contain anything
    more useful.
    """
    candidates = list(_iter_exception_chain(exception))
    if not candidates:
        return exception

    return max(
        enumerate(candidates),
        key=lambda item: (_api_error_score(item[1]), -item[0]),
    )[1]


def _extract_message_from_mapping(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("message", "error_message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value

    error = payload.get("error")
    if isinstance(error, dict):
        message = _extract_message_from_mapping(error)
        if message:
            return message
    elif isinstance(error, str) and error.strip():
        return error

    detail = payload.get("detail")
    if isinstance(detail, dict):
        message = _extract_message_from_mapping(detail)
        if message:
            return message
    elif isinstance(detail, str) and detail.strip():
        return detail

    return None


def _extract_error_type_from_mapping(payload: Dict[str, Any]) -> Optional[str]:
    nested_error_type = None
    error = payload.get("error")
    if isinstance(error, dict):
        nested_error_type = _extract_error_type_from_mapping(error)
        if nested_error_type and nested_error_type not in {"error", "errors"}:
            return nested_error_type

    detail = payload.get("detail")
    if isinstance(detail, dict):
        detail_error_type = _extract_error_type_from_mapping(detail)
        if detail_error_type and detail_error_type not in {"error", "errors"}:
            return detail_error_type

    for key in ("type", "status", "code"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value

    if nested_error_type:
        return nested_error_type

    return None


def _extract_status_code_from_mapping(payload: Dict[str, Any]) -> Optional[int]:
    for key in ("code", "status_code", "status"):
        value = payload.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)

    error = payload.get("error")
    if isinstance(error, dict):
        status_code = _extract_status_code_from_mapping(error)
        if status_code:
            return status_code

    detail = payload.get("detail")
    if isinstance(detail, dict):
        status_code = _extract_status_code_from_mapping(detail)
        if status_code:
            return status_code

    return None


def _strip_litellm_prefixes(message: str) -> str:
    message = message.strip()
    while True:
        stripped = re.sub(r"^(?:litellm\.)?\w+Error:\s*", "", message).strip()
        if stripped == message:
            return message
        message = stripped


def _extract_embedded_json_message(message: str) -> Optional[str]:
    json_match = re.search(r"\{.*\}", message, re.DOTALL)
    if not json_match:
        return None

    try:
        payload = json.loads(json_match.group(0))
    except json.JSONDecodeError:
        return None

    if isinstance(payload, dict):
        return _extract_message_from_mapping(payload)
    return None


def _normalize_provider_message(parsed_error: Dict[str, Any]) -> str:
    message = str(parsed_error["message"]).strip()
    error_type = str(parsed_error.get("error_type") or "").lower()
    status_code = parsed_error.get("status_code")

    is_not_found = status_code == 404 or "not_found" in error_type
    model_match = re.fullmatch(r"model\s*:\s*(.+)", message, flags=re.IGNORECASE)
    if is_not_found and model_match:
        model_name = model_match.group(1).strip()
        return (
            f"Model not found: {model_name}. Select a model available for the "
            "configured provider/account, or update the model/provider configuration."
        )

    return message


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
    status_code = getattr(exception, "status_code", None)
    provider = getattr(exception, "llm_provider", None)
    body = getattr(exception, "body", None)
    message = getattr(exception, "message", None) or error_str

    parsed = {
        "error_type": type(exception).__name__,
        "status_code": status_code if isinstance(status_code, int) else None,
        "message": message,
        "provider": provider or "Unknown",
        "raw_error": error_str,
    }

    if isinstance(body, dict):
        body_message = _extract_message_from_mapping(body)
        if body_message:
            parsed["message"] = body_message

        body_error_type = _extract_error_type_from_mapping(body)
        if body_error_type:
            parsed["error_type"] = body_error_type

        if parsed["status_code"] is None:
            parsed["status_code"] = _extract_status_code_from_mapping(body)

    embedded_message = _extract_embedded_json_message(str(parsed["message"]))
    if embedded_message:
        parsed["message"] = embedded_message
    else:
        parsed["message"] = _strip_litellm_prefixes(str(parsed["message"]))

    # Try to extract status code from various formats
    # Format 1: "Error code: 429 - ..." or "status: 429 -"
    status_match = re.search(r"(?:Error code:|status:)?\s*(\d{3})\s*-", error_str)
    if parsed["status_code"] is None and status_match:
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

            message = _extract_message_from_mapping(error_json)
            if message:
                parsed["message"] = message

            error_type = _extract_error_type_from_mapping(error_json)
            if error_type:
                parsed["error_type"] = error_type

            if parsed["status_code"] is None:
                parsed["status_code"] = _extract_status_code_from_mapping(error_json)

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

    if parsed["provider"] == "Unknown":
        parsed["provider"] = ""

    parsed["message"] = _normalize_provider_message(parsed)

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

    # Log with all structured fields
    logger.error(f"LiteLLM API error: {parsed_error['message']}", **log_fields)


def handle_api_error(
    exception: Exception,
    logger,
    context: Optional[Union[Dict[str, Any], "ErrorContext"]] = None,
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
    context = _as_context_dict(context)

    # Recover the real API error if the exception was masked by an
    # instrumentation bug (e.g. traceai_litellm __exit__ arg-order issue).
    exception = _find_root_api_error(exception)

    # Parse the error
    parsed_error = parse_api_error(exception)

    # Log verbose details
    log_verbose_error(logger, parsed_error, context)

    # Return concise format
    return format_concise_error(parsed_error)
