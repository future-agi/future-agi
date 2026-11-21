"""Limit Skills imports before Django ASGI's own whole-request body spool."""

from __future__ import annotations

import json

from model_hub.services.skills_hub import limits
from tfc.utils.api_errors import build_error_envelope

_IMPORT_PATHS = {"/model-hub/skills/import", "/model-hub/skills/import/preview"}


async def _send_too_large(send):
    body = json.dumps(
        build_error_envelope(
            "The upload exceeds the file or import size limit.",
            status_code=413,
            code="payload_too_large",
        )
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class SkillsImportBodyLimit:
    """Wrap Django, not its middleware: ASGIHandler buffers before middleware.

    Multipart handlers still enforce per-file and decoded aggregate limits. This
    outer cap bounds the initial ASGI spool as well, including chunked requests
    without Content-Length. Other HTTP routes and protocols are untouched.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        prefix = scope.get("root_path", "")
        if prefix and path.startswith(prefix):
            path = path[len(prefix) :]
        if scope["type"] != "http" or path.rstrip("/") not in _IMPORT_PATHS:
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    continue  # Still count actual bytes; Django validates headers.
                if length > limits.MAX_MULTIPART_BYTES:
                    await _send_too_large(send)
                    return
        received = 0
        rejected = False
        complete = False

        async def bounded_receive():
            nonlocal received, rejected, complete
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request" and not complete:
                received += len(message.get("body", b""))
                if received > limits.MAX_MULTIPART_BYTES:
                    rejected = True
                    # A normal disconnect makes ASGIHandler close its partially
                    # filled spool and return before dispatching any view. Raising
                    # here instead would bypass Django's read_body cleanup.
                    return {"type": "http.disconnect"}
                complete = not message.get("more_body", False)
            return message

        await self.app(scope, bounded_receive, send)
        if rejected:
            await _send_too_large(send)
