"""Request-body caps for every Hub route (HUB-17), enforced before any byte of the body is read.

Production serves Django through ASGI (granian, `tfc/asgi.py`). Django's `ASGIHandler.read_body`
spools the whole body into a `SpooledTemporaryFile` whose in-memory size is
`FILE_UPLOAD_MAX_MEMORY_SIZE` (1 GB in `tfc/settings`) before any middleware or view runs, so a check
inside a view comes too late: the worker has already buffered the upload. `HubBodyLimit` wraps the
Django ASGI app and answers 413 from the request headers alone; a body sent without a length is
counted as it arrives and cut off at the cap. `refuse_oversized` repeats the header check for WSGI
and the Django test client, where the body is read lazily.

The caps are the largest body each route can legitimately need:
- a submission is a bundle of at most `intake.MAX_RAW_BYTES` (2 MiB) plus the multipart envelope and
  its text fields;
- an avatar is at most 256 KiB (`PublisherAvatarView`);
- a checker result carries four parts of at most 8 MiB each (`checker_views.MAX_PART`);
- every other Hub route takes a small JSON object.
"""

from __future__ import annotations

import json
import re

KIB = 1024
MIB = 1024 * KIB
PREFIX = "/omega-hub/"

SUBMISSION_LIMIT = 2 * MIB + 512 * KIB
AVATAR_LIMIT = 256 * KIB + 64 * KIB
CHECKER_RESULT_LIMIT = 4 * 8 * MIB + MIB
DEFAULT_LIMIT = 1 * MIB

_ROUTES = (
    (re.compile(r"^/omega-hub/v1/submissions/?$"), SUBMISSION_LIMIT, "OMEGA_HUB_BUNDLE_TOO_LARGE"),
    (re.compile(r"^/omega-hub/v1/publishers/[^/]+/avatar/?$"), AVATAR_LIMIT, "OMEGA_HUB_BODY_TOO_LARGE"),
    (re.compile(r"^/omega-hub/internal/checker/jobs/[^/]+/result/?$"), CHECKER_RESULT_LIMIT, "OMEGA_HUB_BODY_TOO_LARGE"),
)


def limit_for(path: str) -> tuple[int, str] | None:
    """(cap in bytes, error code) for a Hub path, or None for a path outside the Hub."""
    if not path.startswith(PREFIX):
        return None
    for pattern, limit, code in _ROUTES:
        if pattern.match(path):
            return limit, code
    return DEFAULT_LIMIT, "OMEGA_HUB_BODY_TOO_LARGE"


def declared_length(value) -> int | None:
    """The Content-Length as an int, None when absent; a malformed value counts as too large."""
    if value in (None, b"", ""):
        return None
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    value = value.strip()
    if not value.isdigit():
        return -1
    return int(value)


def _too_large(length: int | None, limit: int) -> bool:
    return length is not None and (length < 0 or length > limit)


def _error_bytes(code: str) -> bytes:
    from omega_hub.errors import error_body

    return json.dumps(error_body(code)).encode("utf-8")


async def _send_413(send, code: str) -> None:
    payload = _error_bytes(code)
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("ascii")),
            (b"x-content-type-options", b"nosniff"),
        ],
    })
    await send({"type": "http.response.body", "body": payload})


class HubBodyLimit:
    """ASGI middleware: Hub requests over their cap get 413 before the body is read; others pass."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        rule = limit_for(scope.get("path", ""))
        if rule is None:
            return await self.app(scope, receive, send)
        limit, code = rule
        headers = dict(scope.get("headers") or ())
        if _too_large(declared_length(headers.get(b"content-length")), limit):
            return await _send_413(send, code)

        seen = 0
        cut = False

        async def counted_receive():
            nonlocal seen, cut
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    # Django's read_body takes a disconnect as RequestAborted and answers nothing,
                    # which frees what it spooled; the 413 is sent below.
                    cut = True
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, counted_receive, send)
        if cut:
            await _send_413(send, code)


def refuse_oversized(request) -> None:
    """The same cap for a WSGI request, from its headers, before `request.data` is touched."""
    from omega_hub.errors import HubError

    rule = limit_for(request.path)
    if rule is None:
        return
    limit, code = rule
    if _too_large(declared_length(request.META.get("CONTENT_LENGTH")), limit):
        raise HubError(code, 413)
