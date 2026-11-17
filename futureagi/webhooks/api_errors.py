"""The webhooks API's error codes and their copy (DESIGN.md section 10; messages from sections 6, 7.8 and 8).

Every refusal is FA's common error envelope (``tfc.utils.api_errors.build_error_envelope``) with ``code`` set to one
of the upper-case codes below, so the CLI, the SDK and the UI branch on the code, never on the message. The messages
are FA's registry entries under the same names (``tfc/utils/error_codes.py``), read from there.
"""

from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.response import Response

from tfc.utils.api_errors import build_error_envelope
from tfc.utils.error_codes import err_dict

CODES: tuple[str, ...] = (
    "WEBHOOK_URL_NOT_HTTPS",
    "WEBHOOK_URL_INVALID",
    "WEBHOOK_URL_UNSAFE_ADDRESS",
    "WEBHOOK_HOST_NOT_ALLOWED",
    "WEBHOOK_HEADER_RESERVED",
    "WEBHOOK_HEADER_INVALID",
    "WEBHOOK_SECRET_INVALID",
    "WEBHOOK_EVENT_TYPE_UNKNOWN",
    "WEBHOOK_FILTER_INVALID",
    "WEBHOOK_ENDPOINT_LIMIT",
    "WEBHOOK_ENDPOINT_DISABLED",
    "WEBHOOK_BODY_EXPIRED",
    "WEBHOOK_PAYLOAD_FULL_NOT_ALLOWED",
    "WEBHOOK_RATE_LIMITED",
    "WEBHOOK_TEST_RATE_LIMITED",
    "WEBHOOK_LISTEN_EXPIRED",
    "WEBHOOK_KIND_UNAVAILABLE",
    "WEBHOOK_SENDING_OFF",
    "WEBHOOK_KEYS_MISSING",
    "WEBHOOK_DELIVERY_IN_FLIGHT",
    "WEBHOOK_DELIVERY_NOT_REDELIVERABLE",
    "WEBHOOK_BULK_TOO_LARGE",
    "WEBHOOK_NAME_TAKEN",
    "WEBHOOK_POLICY_INVALID",
    "WEBHOOK_NOT_FOUND",
    "WEBHOOK_FORBIDDEN",
)

# The copy has one source, FA's registry (``tfc/utils/error_codes.py``), so the two cannot drift apart; a
# code missing there fails this import.
MESSAGES: dict[str, str] = {code: err_dict[code][0] for code in CODES}

STATUS_OF: dict[str, int] = {
    "WEBHOOK_ENDPOINT_DISABLED": status.HTTP_409_CONFLICT,
    "WEBHOOK_BODY_EXPIRED": status.HTTP_409_CONFLICT,
    "WEBHOOK_DELIVERY_IN_FLIGHT": status.HTTP_409_CONFLICT,
    "WEBHOOK_DELIVERY_NOT_REDELIVERABLE": status.HTTP_409_CONFLICT,
    "WEBHOOK_NAME_TAKEN": status.HTTP_409_CONFLICT,
    "WEBHOOK_LISTEN_EXPIRED": status.HTTP_410_GONE,
    "WEBHOOK_RATE_LIMITED": status.HTTP_429_TOO_MANY_REQUESTS,
    "WEBHOOK_TEST_RATE_LIMITED": status.HTTP_429_TOO_MANY_REQUESTS,
    "WEBHOOK_SENDING_OFF": status.HTTP_503_SERVICE_UNAVAILABLE,
    "WEBHOOK_KEYS_MISSING": status.HTTP_503_SERVICE_UNAVAILABLE,
    "WEBHOOK_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "WEBHOOK_FORBIDDEN": status.HTTP_403_FORBIDDEN,
}


class WebhookApiError(Exception):
    """A refusal with one of the codes above. ``message`` overrides the stock copy when the case needs names in it
    (for example the allowed-hosts list); ``extra`` lands at the top level of the envelope (``attr``, ``count``)."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        http_status: int | None = None,
        **extra: Any,
    ):
        if code not in MESSAGES:
            raise ValueError(f"unknown webhooks error code {code!r}")
        self.code = code
        self.message = message or MESSAGES[code]
        self.http_status = http_status or STATUS_OF.get(
            code, status.HTTP_400_BAD_REQUEST
        )
        self.extra = extra
        super().__init__(code)


def error_response(err: WebhookApiError) -> Response:
    body = build_error_envelope(
        err.message, status_code=err.http_status, code=err.code, extra=err.extra
    )
    headers = {}
    if err.http_status == status.HTTP_429_TOO_MANY_REQUESTS:
        headers["Retry-After"] = str(err.extra.get("retry_after_s", 60))
    return Response(body, status=err.http_status, headers=headers)


def validation_response(errors) -> Response:
    """A request that does not match its serializer: FA's 400 envelope (``validated_request``)."""
    return Response(
        build_error_envelope(errors, status_code=status.HTTP_400_BAD_REQUEST),
        status=status.HTTP_400_BAD_REQUEST,
    )
