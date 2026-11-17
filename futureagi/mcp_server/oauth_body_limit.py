"""Request-body cap for the MCP OAuth endpoints, enforced before Starlette sees a byte.

`tfc/asgi.py` serves the MCP authorization server (`get_mcp_oauth_app`: /register,
/authorize, /token, /revoke and the discovery documents) at the API root, without
authentication. The MCP SDK's handlers call `await request.form()`, and Starlette
1.0.0's `application/x-www-form-urlencoded` FormParser ignores `max_fields` and
`max_part_size` (GHSA-82w8-qh3p-5jfq), so one large body costs CPU and memory before
any client is known. The SDK's own `RequestBodyLimitMiddleware` allows 4 MiB; a real
token, revocation, authorization or registration request is well under 4 KiB.

`OAuthBodyLimit` answers 413 from the Content-Length header alone. Without one (a
chunked or streamed body) it reads the body itself and stops at the first message
that takes it past the cap, so the overshoot is at most one server read chunk and
the app never runs. A body within the cap is replayed to the app byte for byte.
Only GET passes through untouched: no handler reads a GET body. HEAD and OPTIONS do
not: Starlette routes HEAD /authorize to a handler that parses a form for every
method but GET, and the SDK's CORSMiddleware hands an OPTIONS without Origin or
Access-Control-Request-Method to the /token, /register and /revoke handlers, which
parse the body. A Content-Length that is malformed, negative, absurdly long or
repeated with different values is answered 413 too (fail closed, as `HubBodyLimit`
does for the Hub), without reading the body.
"""

from __future__ import annotations

import json

# GHSA-82w8-qh3p-5jfq: Starlette's urlencoded form parser is unbounded, so cap every OAuth body.
MCP_OAUTH_MAX_BODY_BYTES = 64 * 1024

# The one method whose body no OAuth handler reads.
_UNREAD_BODY_METHOD = "GET"
# A longer Content-Length is absurd: refuse it rather than pay for `int()` on it.
_MAX_LENGTH_DIGITS = 19

_TOO_LARGE = json.dumps(
    {"error": "invalid_request", "error_description": "Request body too large"}
).encode("ascii")


def _declared_length_within(headers, limit: int) -> bool:
    """False when Content-Length is over `limit` or cannot be trusted; True when absent."""
    values = {
        value.strip() for name, value in headers if name.lower() == b"content-length"
    }
    if not values:
        return True
    if len(values) > 1:
        return False
    (value,) = values
    return value.isdigit() and len(value) <= _MAX_LENGTH_DIGITS and int(value) <= limit


async def _send_413(send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _TOO_LARGE})


class OAuthBodyLimit:
    """ASGI middleware: a request body over `limit` gets 413 and never reaches the app."""

    def __init__(self, app, limit: int = MCP_OAUTH_MAX_BODY_BYTES):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            # Exact match: methods are case-sensitive, and anything else is capped.
            or scope.get("method") == _UNREAD_BODY_METHOD
        ):
            await self.app(scope, receive, send)
            return
        if not _declared_length_within(scope.get("headers") or (), self.limit):
            await _send_413(send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # The client left before the body ended; there is no one to answer.
                return
            body += message.get("body", b"")
            if len(body) > self.limit:
                await _send_413(send)
                return
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                # Later reads (Starlette's disconnect checks) go to the server.
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)
