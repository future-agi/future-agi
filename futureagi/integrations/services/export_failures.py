"""Fixed sync diagnostics. Neither vendor text nor destination URLs are messages.

Classification uses exception classes and structured status/code only. The same
renderer projects historical rows, so sanitizing error_details cannot leave a
raw exception in an adjacent error_message or status_message field.
"""

import os
import traceback
from dataclasses import dataclass
from types import SimpleNamespace

import requests

from integrations.models import IntegrationPlatform

ERROR_DETAIL_FIELDS = ("kind", "type", "http_status", "vendor_code")
RATE_LIMITED = "rate_limited"
CREDENTIALS_REFUSED = "credentials_refused"
SERVER_ERROR = "server_error"
REQUEST_REFUSED = "request_refused"
EVENTS_REFUSED = "events_refused"
TIMEOUT = "timeout"
UNREACHABLE = "unreachable"
NO_BUCKET = "no_bucket"
UPLOAD_REFUSED = "upload_refused"
UNEXPECTED = "unexpected"
NOT_FOUND = "not_found"
KINDS = frozenset(
    {
        RATE_LIMITED,
        CREDENTIALS_REFUSED,
        SERVER_ERROR,
        REQUEST_REFUSED,
        EVENTS_REFUSED,
        TIMEOUT,
        UNREACHABLE,
        NO_BUCKET,
        UPLOAD_REFUSED,
        UNEXPECTED,
        NOT_FOUND,
    }
)
S3_CREDENTIAL_CODES = frozenset(
    {
        "AccessDenied",
        "AllAccessDisabled",
        "ExpiredToken",
        "InvalidAccessKeyId",
        "InvalidToken",
        "SignatureDoesNotMatch",
    }
)
# Enumerated codes, not an alphanumeric-shaped value that might itself be a key.
SAFE_VENDOR_CODES = S3_CREDENTIAL_CODES | {
    "NoSuchBucket",
    "SlowDown",
    "RequestTimeout",
    "InternalError",
    "ServiceUnavailable",
    "InvalidRequest",
    "InvalidArgument",
    "EntityTooLarge",
    "NoSuchKey",
    "InvalidBucketName",
    "PermanentRedirect",
}
SAFE_TYPES = frozenset(
    {
        "HTTPError",
        "ConnectionError",
        "ReadTimeout",
        "ConnectTimeout",
        "Timeout",
        "RequestException",
        "ChunkedEncodingError",
        "SSLError",
        "InvalidHeader",
        "InvalidURL",
        "ClientError",
        "EndpointConnectionError",
        "NoCredentialsError",
        "PartialCredentialsError",
        "ParamValidationError",
        "RuntimeError",
        "ValueError",
        "TypeError",
        "KeyError",
        "OSError",
        "Exception",
        "MixpanelRejected",
        "ImproperlyConfigured",
        "JSONDecodeError",
        "HttpResponseError",
        "ServiceRequestError",
    }
)
CREDENTIALS = {
    IntegrationPlatform.DATADOG: "API key",
    IntegrationPlatform.POSTHOG: "project API key",
    IntegrationPlatform.MIXPANEL: "project token",
    IntegrationPlatform.CLOUD_STORAGE: "access keys",
}
RETRY_NOTE = "It is retried automatically; Retry tries again now."


def safe_details(details) -> dict:
    """Validate values as well as keys, including untrusted historical rows."""
    if not isinstance(details, dict):
        return {}
    clean = {}
    for key in ERROR_DETAIL_FIELDS:
        if key not in details:
            continue
        value = details[key]
        if key == "http_status":
            clean[key] = value if type(value) is int and 100 <= value <= 599 else None
        elif key == "vendor_code":
            clean[key] = (
                value if isinstance(value, str) and value in SAFE_VENDOR_CODES else None
            )
        elif key == "kind":
            clean[key] = (
                value if isinstance(value, str) and value in KINDS else UNEXPECTED
            )
        else:
            clean[key] = (
                value if isinstance(value, str) and value in SAFE_TYPES else "Exception"
            )
    return clean


@dataclass(frozen=True)
class ExportFailure:
    message: str
    kind: str
    http_status: int | None = None
    vendor_code: str | None = None

    @property
    def rate_limited(self) -> bool:
        return self.kind == RATE_LIMITED

    def details(self, exc: BaseException) -> dict:
        return safe_details(
            {
                "kind": self.kind,
                "type": type(exc).__name__,
                "http_status": self.http_status,
                "vendor_code": self.vendor_code,
            }
        )


def raised_at(exc: BaseException, limit: int = 8) -> list[str]:
    """File/line/function only, never source text, locals or exception values."""
    return [
        f"{os.path.basename(f.filename)}:{f.lineno} in {f.name}"
        for f in traceback.extract_tb(exc.__traceback__)[-limit:]
    ]


def _vendor(connection) -> str:
    if connection.platform == IntegrationPlatform.CLOUD_STORAGE:
        return "Amazon S3" if ".s3." in (connection.host_url or "") else "Cloud storage"
    try:
        return IntegrationPlatform(connection.platform).label
    except ValueError:
        return "Integration"


def failure_from_details(connection, details) -> ExportFailure:
    details = safe_details(details)
    kind = details.get("kind", UNEXPECTED)
    code = details.get("vendor_code")
    status = details.get("http_status")
    vendor = _vendor(connection)
    noun = CREDENTIALS.get(connection.platform, "credentials")
    pronoun = "them" if noun.endswith("s") else "it"
    replace = f"Replace {pronoun} in Edit, then Sync Now."
    is_import = connection.platform == IntegrationPlatform.LANGFUSE
    operation = "sync" if is_import else "export"
    data = "traces" if is_import else "request logs"
    http = f"HTTP {status}" if status is not None else "no HTTP status"
    messages = {
        RATE_LIMITED: f"{vendor} asked to slow down (HTTP 429). The next sync {'fetches' if is_import else 'sends'} these {data}.",
        CREDENTIALS_REFUSED: f"{vendor} refused the {noun} ({code or http}). {replace}",
        SERVER_ERROR: f"{vendor} answered {http}. {RETRY_NOTE}",
        REQUEST_REFUSED: f"{vendor} refused the {data} ({http}). {RETRY_NOTE}",
        EVENTS_REFUSED: f"Mixpanel accepted none of the request logs in a batch (check the {noun}). {replace}",
        TIMEOUT: f"{vendor} did not answer in time. {RETRY_NOTE}",
        UNREACHABLE: f"Could not reach {vendor}. {RETRY_NOTE}",
        NO_BUCKET: f"{vendor} has no such bucket. Check the bucket, then Retry.",
        UPLOAD_REFUSED: f"{vendor} refused the upload ({code or 'no error code'}). {RETRY_NOTE}",
        NOT_FOUND: "Langfuse project not found. It may have been deleted.",
        UNEXPECTED: f"The {operation} to {vendor} stopped on an unexpected error. {RETRY_NOTE}",
    }
    return ExportFailure(messages[kind], kind, status, code)


def describe_export_failure(connection, exc: BaseException) -> ExportFailure:
    from botocore import exceptions as boto

    from integrations.services.mixpanel_service import MixpanelRejected

    kind, status, code = UNEXPECTED, None, None
    if isinstance(exc, MixpanelRejected):
        kind = EVENTS_REFUSED
    elif isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        if status == 429:
            kind = RATE_LIMITED
        elif status in (401, 403):
            kind = CREDENTIALS_REFUSED
        elif status == 404 and connection.platform == IntegrationPlatform.LANGFUSE:
            kind = NOT_FOUND
        elif status >= 500:
            kind = SERVER_ERROR
        else:
            kind = REQUEST_REFUSED
    elif isinstance(exc, requests.exceptions.Timeout):
        kind = TIMEOUT
    elif isinstance(exc, (requests.exceptions.ConnectionError, boto.ConnectionError)):
        kind = UNREACHABLE
    elif isinstance(exc, boto.ClientError):
        answer = exc.response or {}
        code = answer.get("Error", {}).get("Code")
        status = answer.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if isinstance(code, str) and code in S3_CREDENTIAL_CODES:
            kind = CREDENTIALS_REFUSED
        elif code == "NoSuchBucket":
            kind = NO_BUCKET
        else:
            kind = UPLOAD_REFUSED
    return failure_from_details(
        connection, {"kind": kind, "http_status": status, "vendor_code": code}
    )


def exception_fields(exc: BaseException) -> dict:
    """Safe fields for integration-owned logs that do not have a connection."""
    failure = describe_export_failure(SimpleNamespace(platform="", host_url=""), exc)
    return {**failure.details(exc), "raised_at": raised_at(exc)}


def validation_failure(platform: str, exc: BaseException, logger) -> dict:
    """Stdlib logging: fixed fields only, without exception/frame capture."""
    fields = exception_fields(exc)
    logger.warning("integration_validation_failed %s", {"platform": platform, **fields})
    failure = describe_export_failure(
        SimpleNamespace(platform=platform, host_url=""), exc
    )
    status = f" (HTTP {failure.http_status})" if failure.http_status else ""
    code = f" ({failure.vendor_code})" if failure.vendor_code else ""
    return {
        "valid": False,
        "error": f"Validation failed{status}{code}. Check the connection settings and credentials.",
    }
