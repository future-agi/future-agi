"""Throttling for the MCP SDK OAuth routes mounted at the server root.

tfc/asgi.py sends ``/token``, ``/revoke`` and ``/register`` to the MCP SDK's
Starlette OAuth app. Those handlers verify client secrets, authorization codes
and refresh tokens, and register clients, without any throttling of their own.
This ASGI wrapper applies the same limits as the Django token endpoint
(``POST /mcp/oauth/token/``) before the SDK sees the request:

- ``/token`` and ``/revoke``: the invalid-attempt lockout per address and
  ``client_id`` (with in-flight accounting), the per-address window and the
  per-client window. They share counters with the Django token endpoint, so
  using both routes does not double any budget.
- ``/register``: a per-address registration window.

Refusals use the Django endpoint's OAuth error shape: ``429`` with
``{"error": "slow_down", ...}`` and ``Retry-After``.
"""

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import parse_qsl

import structlog
from asgiref.sync import sync_to_async
from rest_framework.exceptions import Throttled

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

# SDK responses that mean a client secret, code or refresh token was wrong.
_INVALID_CREDENTIAL_ERRORS = frozenset(
    {"unauthorized_client", "invalid_client", "invalid_grant"}
)


class _ThrottleRequest:
    """The parts of a DRF request that the token throttle helpers read."""

    def __init__(self, scope: Scope, body: bytes) -> None:
        headers = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope.get("headers") or []
        }
        client = scope.get("client") or ("", 0)
        self.META = {"REMOTE_ADDR": client[0] or ""}
        if "x-forwarded-for" in headers:
            self.META["HTTP_X_FORWARDED_FOR"] = headers["x-forwarded-for"]
        self.data: dict[str, str] = {}
        content_type = headers.get("content-type", "")
        if content_type.split(";")[0].strip() == "application/x-www-form-urlencoded":
            for key, value in parse_qsl(body.decode("utf-8", "replace")):
                self.data.setdefault(key, value)


async def _send_json(
    send: Send, status: int, payload: dict, headers: dict[str, str] | None = None
) -> None:
    body = json.dumps(payload).encode()
    raw_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
    ]
    for name, value in (headers or {}).items():
        raw_headers.append((name.lower().encode(), value.encode()))
    await send(
        {"type": "http.response.start", "status": status, "headers": raw_headers}
    )
    await send({"type": "http.response.body", "body": body})


async def _send_throttled(send: Send, wait: float | int | None, detail: str) -> None:
    await _send_json(
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

        body, messages = await _read_body(receive)
        if body is None:
            await _send_json(
                send,
                413,
                {
                    "error": "invalid_request",
                    "error_description": "Request body too large",
                },
            )
            return

        request = _ThrottleRequest(scope, body)
        admit = _admit_token_request if path in TOKEN_PATHS else _admit_register_request
        try:
            refused = await sync_to_async(admit)(request)
        except RateLimitBackendUnavailableError as exc:
            release_token_attempt_reservation(request)
            await _send_throttled(
                send, exc.retry_after, "Token throttling temporarily unavailable"
            )
            return
        if refused is not None:
            await _send_throttled(send, *refused)
            return

        pending = list(messages)

        async def replay() -> Message:
            if pending:
                return pending.pop(0)
            return await receive()

        if path not in TOKEN_PATHS:
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
