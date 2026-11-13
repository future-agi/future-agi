"""Shared request limits for the MCP SDK OAuth routes mounted at the server root.

tfc/asgi.py sends ``/token``, ``/revoke`` and ``/register`` to the MCP SDK's
Starlette OAuth app; this ASGI wrapper applies the Django token endpoint's
(``POST /mcp/oauth/token/``) limits before the SDK handles the request:

- ``/token`` and ``/revoke`` share the Django token endpoint's address and
  client counters and its in-flight accounting.
- ``/register`` uses a per-address window.

Rate-limited requests return ``429`` with ``{"error": "slow_down", ...}`` and
``Retry-After``.

Token and revocation bodies use Starlette's form parser. Unsupported media
types and repeated ``client_id`` fields return ``400 invalid_request``
(RFC 6749 sections 3.1, 3.2, 4.1.3 and 6; RFC 7009 section 2.1).
"""

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import structlog
from asgiref.sync import sync_to_async
from mcp.server.streamable_http import MCP_PROTOCOL_VERSION_HEADER

# The function starlette.requests uses to decide how to parse a form body.
from python_multipart.multipart import parse_options_header
from rest_framework.exceptions import Throttled
from starlette.datastructures import FormData, Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import ClientDisconnect, Request

from mcp_server.exceptions import RateLimitBackendUnavailableError
from mcp_server.throttles import (
    MCPOAuthRegisterIPThrottle,
    MCPOAuthTokenClientThrottle,
    MCPOAuthTokenIPThrottle,
    MCPOAuthTokenLockoutThrottle,
    clear_invalid_token_attempts,
    log_token_warning,
    record_invalid_token_attempt,
    release_token_attempt_reservation,
)

logger = structlog.get_logger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

TOKEN_PATHS = frozenset({"/token", "/revoke"})
REGISTER_PATH = "/register"
# OAuth token, revocation and registration requests are a few hundred bytes.
# The body is buffered to read client_id, so cap it.
MAX_BODY_BYTES = 64 * 1024

# The only media type Starlette parses as a url-encoded form, compared after
# the same parse_options_header() normalization.
URLENCODED_MEDIA_TYPE = b"application/x-www-form-urlencoded"

# SDK responses that mean a client secret, code or refresh token was wrong.
_INVALID_CREDENTIAL_ERRORS = frozenset(
    {"unauthorized_client", "invalid_client", "invalid_grant"}
)


class _ThrottleRequest:
    """The parts of a DRF request that the token throttle helpers read."""

    def __init__(self, scope: Scope, data: dict[str, str] | None = None) -> None:
        headers = Headers(scope=scope)
        client = scope.get("client") or ("", 0)
        self.META = {"REMOTE_ADDR": client[0] or ""}
        # Join repeated lines as Django's ASGIRequest does, so a proxy that
        # adds its own line is read the same way as on the Django endpoint.
        forwarded_for = headers.getlist("x-forwarded-for")
        if forwarded_for:
            self.META["HTTP_X_FORWARDED_FOR"] = ",".join(forwarded_for)
        self.data: dict[str, str] = data or {}


def _is_urlencoded(scope: Scope) -> bool:
    """True if Starlette, and so the SDK, would parse the body as a url-encoded form."""
    media_type, _ = parse_options_header(Headers(scope=scope).get("content-type"))
    return media_type == URLENCODED_MEDIA_TYPE


async def _parse_form(scope: Scope, messages: list[Message]) -> FormData:
    """Parse the buffered body exactly as the SDK handler's request.form() will."""
    pending = list(messages)

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    return await Request(scope, receive).form()


async def _no_body() -> Message:
    return {"type": "http.disconnect"}


async def _send_json(
    scope: Scope,
    send: Send,
    status: int,
    payload: dict,
    headers: dict[str, str] | None = None,
) -> None:
    body = json.dumps(payload).encode()
    raw_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
    ]
    for name, value in (headers or {}).items():
        raw_headers.append((name.lower().encode(), value.encode()))

    async def respond(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {"type": "http.response.start", "status": status, "headers": raw_headers}
        )
        await send({"type": "http.response.body", "body": body})

    # Responses sent here never reach the SDK routes, so they get the CORS
    # policy those routes use (mcp.server.auth.routes), and browser clients
    # can read Retry-After.
    app = CORSMiddleware(
        respond,
        allow_origins=["*"],
        allow_methods=["POST", "OPTIONS"],
        allow_headers=[MCP_PROTOCOL_VERSION_HEADER],
        expose_headers=["Retry-After"],
    )
    await app(scope, _no_body, send)


async def _send_invalid_request(scope: Scope, send: Send, detail: str) -> None:
    await _send_json(
        scope, send, 400, {"error": "invalid_request", "error_description": detail}
    )


async def _send_throttled(
    scope: Scope, send: Send, wait: float | int | None, detail: str
) -> None:
    await _send_json(
        scope,
        send,
        429,
        {"error": "slow_down", "error_description": detail},
        {"Retry-After": str(max(int(wait or 1), 1))},
    )


async def _read_body(receive: Receive) -> tuple[bytes | None, list[Message]]:
    """Buffer the request body. Returns (None, messages) when it is too large."""
    chunks: list[bytes] = []
    messages: list[Message] = []
    size = 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            return b"".join(chunks), messages
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None, messages
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks), messages


def _admit_token_request(request: _ThrottleRequest) -> tuple[float | int, str] | None:
    """Apply the token-endpoint throttles. Returns (wait, detail) if refused."""
    try:
        MCPOAuthTokenLockoutThrottle().allow_request(request, None)
    except Throttled as exc:
        return exc.wait, exc.detail["error_description"]

    # Like DRF's check_throttles, count the request in every window and
    # refuse with the longest wait.
    waits = []
    for throttle_class in (MCPOAuthTokenIPThrottle, MCPOAuthTokenClientThrottle):
        throttle = throttle_class()
        if not throttle.allow_request(request, None):
            waits.append(throttle.wait() or 1)
    if waits:
        release_token_attempt_reservation(request)
        log_token_warning(
            request,
            "mcp_oauth_token_rate_limited",
            retry_after_seconds=max(waits),
            route="mcp_sdk",
        )
        return max(waits), "Too many token requests"
    return None


def _admit_register_request(
    request: _ThrottleRequest,
) -> tuple[float | int, str] | None:
    throttle = MCPOAuthRegisterIPThrottle()
    if throttle.allow_request(request, None):
        return None
    wait = throttle.wait() or 1
    logger.warning(
        "mcp_oauth_register_rate_limited",
        retry_after_seconds=wait,
    )
    return wait, "Too many client registrations"


def _finish_token_request(
    request: _ThrottleRequest, status: int | None, error: str | None
) -> None:
    try:
        if status == 200:
            clear_invalid_token_attempts(request)
        elif error in _INVALID_CREDENTIAL_ERRORS:
            lockout_seconds = record_invalid_token_attempt(request)
            log_token_warning(
                request,
                "mcp_oauth_token_invalid_attempt",
                reason=error,
                lockout_seconds=lockout_seconds or None,
                route="mcp_sdk",
            )
    except RateLimitBackendUnavailableError:
        # The response is already sent; the reservation still bounds bursts.
        logger.error("mcp_oauth_token_attempt_record_failed", exc_info=True)
    finally:
        release_token_attempt_reservation(request)


class MCPSDKOAuthThrottleMiddleware:
    """Throttle POST /token, /revoke and /register before the MCP SDK app."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or (path not in TOKEN_PATHS and path != REGISTER_PATH)
        ):
            await self.app(scope, receive, send)
            return

        is_token_path = path in TOKEN_PATHS
        if is_token_path and not _is_urlencoded(scope):
            await _send_invalid_request(
                scope, send, "Content-Type must be application/x-www-form-urlencoded"
            )
            return

        body, messages = await _read_body(receive)
        if body is None:
            await _send_json(
                scope,
                send,
                413,
                {
                    "error": "invalid_request",
                    "error_description": "Request body too large",
                },
            )
            return

        if is_token_path:
            try:
                form = await _parse_form(scope, messages)
            except ClientDisconnect:
                return
            if len(form.getlist("client_id")) > 1:
                await _send_invalid_request(
                    scope, send, "client_id must not be repeated"
                )
                return
            # dict() keeps the last value of each field, as the SDK's
            # dict(form_data) does. Only text fields can carry client_id.
            request = _ThrottleRequest(
                scope,
                {key: value for key, value in form.items() if isinstance(value, str)},
            )
            admit = _admit_token_request
            outage_detail = "Token throttling temporarily unavailable"
        else:
            request = _ThrottleRequest(scope)
            admit = _admit_register_request
            outage_detail = "Client registration throttling temporarily unavailable"
        try:
            refused = await sync_to_async(admit)(request)
        except RateLimitBackendUnavailableError as exc:
            await sync_to_async(release_token_attempt_reservation)(request)
            await _send_throttled(scope, send, exc.retry_after, outage_detail)
            return
        if refused is not None:
            await _send_throttled(scope, send, *refused)
            return

        pending = list(messages)

        async def replay() -> Message:
            if pending:
                return pending.pop(0)
            return await receive()

        if not is_token_path:
            await self.app(scope, replay, send)
            return

        status: int | None = None
        response_body = bytearray()

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body" and status != 200:
                if len(response_body) < 4096:
                    response_body.extend(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay, capture)
        finally:
            error = None
            if status is not None and status != 200:
                try:
                    error = json.loads(bytes(response_body)).get("error")
                except (ValueError, AttributeError):
                    error = None
            await sync_to_async(_finish_token_request)(request, status, error)
