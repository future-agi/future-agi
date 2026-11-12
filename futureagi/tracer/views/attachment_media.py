"""Same-origin media gateway: cookie exchange and authorized byte ranges.

``tracer.views.attachments`` mounts these handlers with the live authority
(current application session plus ClickHouse source state) and the trusted
media origin from ``FI_ATTACHMENTS_MEDIA_ORIGIN``. No request field constructs a
Viewer or an Authority. Every GET/HEAD/Range/conditional request re-authorizes
before any status or metadata is sent, and open streams re-authorize before and
after every bounded read. Bytes are charged to the viewer's egress budget first.
"""

import re
import time
from urllib.parse import urlsplit

from django.http import HttpResponse, StreamingHttpResponse
from django.utils.crypto import salted_hmac
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import PermissionDenied

from tracer.services.attachments import egress, playback
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.occurrences import not_found
from tracer.services.attachments.storage import ObjectIdentity

CHUNK_BYTES = 64 * 1024
MAX_RESPONSE_SECONDS = 30


CSP = "default-src 'none'; frame-ancestors 'none'"
# Anything that is not image/audio/video is inert and opens in an opaque origin.
SANDBOX_CSP = f"sandbox; {CSP}"


def private(response):
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    response["Content-Security-Policy"] = CSP
    response["Referrer-Policy"] = "no-referrer"
    response["Vary"] = "Cookie, Origin"
    return response


def same_origin(request, origin, *, issuance=False, csrf=True):
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise not_found()
    supplied = request.headers.get("Origin")
    site = request.headers.get("Sec-Fetch-Site")
    if (supplied is not None and supplied != origin) or site not in {
        None,
        "same-origin",
        "none",
    }:
        raise not_found()
    if issuance:
        if supplied != origin or request.method != "POST":
            raise not_found()
        if csrf:
            try:
                SessionAuthentication().enforce_csrf(request)
            except PermissionDenied:
                raise not_found() from None


def exchange_response(request, viewer, authority, *, origin):
    """Only a dedicated authenticated POST adapter may call this function.

    The exchange is authenticated by the application bearer token, which a
    browser never attaches by itself, plus an exact Origin match. A Django
    session cookie is not an accepted credential here, so CSRF-by-cookie does
    not apply; ``csrf`` re-enables the cookie check for session-backed callers.
    """
    try:
        same_origin(request, origin, issuance=True, csrf=False)
        _, token = playback.exchange(viewer, authority)
    except AttachmentError:
        return private(HttpResponse(status=404))
    response = private(HttpResponse(status=204))
    response.set_cookie(
        playback.COOKIE_NAME,
        token,
        max_age=playback.SESSION_SECONDS,
        path=playback.MEDIA_PATH,
        secure=True,
        httponly=True,
        samesite="Strict",
    )
    return response


def byte_range(value, size):
    """Exactly one bounded RFC 9110 byte range; no multipart amplification."""
    if value is None:
        return 0, size - 1, False
    if not isinstance(value, str) or len(value) > 128:
        raise AttachmentError("range_unsatisfiable", 416)
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()):
        raise AttachmentError("range_unsatisfiable", 416)
    left, right = match.groups()
    if not left:
        suffix = int(right)
        if suffix == 0:
            raise AttachmentError("range_unsatisfiable", 416)
        start, end = max(0, size - suffix), size - 1
    else:
        start, end = int(left), min(int(right), size - 1) if right else size - 1
    if start >= size or start > end:
        raise AttachmentError("range_unsatisfiable", 416)
    return start, end, True


class RevocableBody:
    """Lazy storage open; close() handles browser cancellation without a drain.

    Each bounded read is bracketed by live authorization. Deployment must qualify
    read/DB timeouts to establish the <=5s wall-clock revocation target; this class
    alone cannot promise that bound for a blocked database/socket.
    """

    def __init__(self, check, storage, blob, start, end, *, charge=None):
        self.authorize, self.storage, self.blob = check, storage, blob
        self.start, self.end, self.remaining = start, end, end - start + 1
        self.body, self.closed = None, False
        self.deadline = time.monotonic() + MAX_RESPONSE_SECONDS
        self.charge = charge

    def check(self):
        # Every bounded read is bracketed by a full live re-authorization.
        self.authorize()

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed or self.remaining == 0 or time.monotonic() >= self.deadline:
            self.close()
            raise StopIteration
        try:
            self.check()
            if self.body is None:
                self.body = self.storage.open_range(
                    ObjectIdentity(self.blob.storage_key, self.blob.storage_version),
                    start=self.start,
                    end=self.end,
                    expected_length=self.blob.byte_length,
                )
            size = min(CHUNK_BYTES, self.remaining)
            if self.charge is not None:
                self.charge(size)
            chunk = self.body.read(size)
            self.check()
            if (
                time.monotonic() >= self.deadline
                or not isinstance(chunk, bytes)
                or not chunk
                or len(chunk) > min(CHUNK_BYTES, self.remaining)
            ):
                self.close()
                raise StopIteration
            self.remaining -= len(chunk)
            if not self.remaining:
                self.close()
            return chunk
        except StopIteration:
            raise
        except Exception:
            # No raw storage errors, signed URLs or source details in WSGI logs.
            self.close()
            raise StopIteration from None

    def close(self):
        self.closed = True
        if self.body is not None:
            body, self.body = self.body, None
            try:
                body.close()
            except Exception:
                pass  # A close exception must not leak storage credentials.


def media_response(request, *, occurrence_id, grant_token, authority, storage, origin):
    if request.method not in {"GET", "HEAD"}:
        return private(HttpResponse(status=405))
    cookie = request.COOKIES.get(playback.COOKIE_NAME)

    def check():
        return playback.authorize(cookie, grant_token, occurrence_id, authority)

    try:
        same_origin(request, origin)
        grant, blob, viewer = check()
    except AttachmentError:
        return private(HttpResponse(status=404))
    # The validated type from the server-probed blob, never the declaration.
    served = playback.servable(blob, grant.representation)
    if served is None:
        return private(HttpResponse(status=404))
    principal = str(viewer.user_id)
    try:
        egress.admit(principal)
    except egress.EgressDenied as denied:
        response = private(HttpResponse(status=429))
        response["Retry-After"] = str(denied.retry_after)
        return response
    # Not the object hash: opaque ETag varies by grant and immutable blob identity.
    etag = (
        '"'
        + salted_hmac("fi.attachment.etag", f"{grant.id}:{blob.id}").hexdigest()
        + '"'
    )
    conditional = request.headers.get("If-None-Match", "")
    if len(conditional) <= 128 and (
        conditional == "*" or conditional.removeprefix("W/") == etag
    ):
        response = private(HttpResponse(status=304))
        response["ETag"] = etag
        return response
    try:
        # Range applies to GET of audio/video only; HEAD, images and documents
        # report and send the full selected representation.
        value = (
            request.headers.get("Range")
            if request.method == "GET" and served.media
            else None
        )
        if request.headers.get("If-Range") not in {None, etag}:
            value = None
        start, end, partial = byte_range(value, blob.byte_length)
    except AttachmentError:
        response = private(HttpResponse(status=416))
        response["Content-Range"] = f"bytes */{blob.byte_length}"
        return response
    response = (
        HttpResponse(status=200)
        if request.method == "HEAD"
        else StreamingHttpResponse(
            RevocableBody(
                check,
                storage,
                blob,
                start,
                end,
                charge=lambda size: egress.consume(principal, size),
            ),
            status=206 if partial else 200,
        )
    )
    private(response)
    response["Content-Type"] = blob.mime_type
    disposition = "inline" if served.inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{served.filename}"'
    if not served.inline:
        response["Content-Security-Policy"] = SANDBOX_CSP
    response["Accept-Ranges"] = "bytes" if served.media else "none"
    response["ETag"] = etag
    response["Content-Length"] = str(end - start + 1)
    if partial:
        response["Content-Range"] = f"bytes {start}-{end}/{blob.byte_length}"
    return response
