"""The MCP OAuth endpoints refuse an oversized body before Starlette parses it.

/register, /authorize, /token and /revoke are served at the API root without
authentication (tfc/asgi.py ``_OAUTH_PATHS``) by the MCP SDK's handlers, which
call ``await request.form()``. Starlette 1.0.0's urlencoded FormParser ignores
max_fields/max_part_size (GHSA-82w8-qh3p-5jfq), so a large urlencoded body costs
CPU and memory before any client is known. Every test goes through the real
``http_router`` into the real OAuth app; the cap is 64 KiB (release-lead #321).
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from starlette.formparsers import FormParser
from starlette.requests import Request

from tfc import asgi_startup

pytestmark = pytest.mark.unit

CAP = 64 * 1024
CHUNK = 16 * 1024
MIB = 1024 * 1024

FORM = b"application/x-www-form-urlencoded"
JSON = b"application/json"


@pytest.fixture
def router(monkeypatch):
    """tfc/asgi.py's ``http_router`` with the real MCP OAuth app behind it.

    Telemetry, the Django app and the URL warm-up are stubbed as in
    tfc/tests/test_asgi_embedded_worker.py; ``_get_oauth_app`` is not.
    """
    from mcp_server import mcp_app

    django_calls = []

    async def django_app(scope, receive, send):
        django_calls.append(scope["path"])

    monkeypatch.setenv("MCP_SERVER_BASE_URL", "http://localhost:8000")
    # Built afresh under this base URL, and dropped again afterwards.
    monkeypatch.setattr(mcp_app, "_oauth_app", None)
    monkeypatch.setattr("tfc.telemetry.init_telemetry", lambda *, component: None)
    monkeypatch.setattr("django.core.asgi.get_asgi_application", lambda: django_app)
    monkeypatch.setattr(asgi_startup, "warm_http_urlconf", lambda: 0)
    module = runpy.run_path(str(Path(__file__).parents[2] / "tfc" / "asgi.py"))
    namespace = module["http_router"].__globals__
    namespace["_get_mcp_starlette_app"] = lambda: None
    namespace["django_calls"] = django_calls
    return namespace


@pytest.fixture
def starlette_reads(monkeypatch):
    """Every form Starlette parses and every body it reads, through its own classes."""
    reads = SimpleNamespace(forms=[], parses=0, bodies=[])
    original_form = Request.form
    original_stream = Request.stream
    original_parse = FormParser.parse

    def form(self, **kwargs):
        reads.forms.append(self.url.path)
        return original_form(self, **kwargs)

    def stream(self):
        chunks = []
        reads.bodies.append(chunks)

        async def tee():
            async for chunk in original_stream(self):
                chunks.append(chunk)
                yield chunk

        return tee()

    async def parse(self):
        reads.parses += 1
        return await original_parse(self)

    monkeypatch.setattr(Request, "form", form)
    monkeypatch.setattr(Request, "stream", stream)
    monkeypatch.setattr(FormParser, "parse", parse)
    return reads


class _Client:
    """One HTTP request as the server hands it to the app, counting what is pulled."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.messages = 0
        self.pulled = 0
        self.sent = []

    async def receive(self):
        if not self.chunks:
            # The body is over; a real server would next report the disconnect.
            return {"type": "http.disconnect"}
        chunk = self.chunks.pop(0)
        self.messages += 1
        self.pulled += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": bool(self.chunks)}

    async def send(self, message):
        self.sent.append(message)

    @property
    def status(self):
        return next(
            m["status"] for m in self.sent if m["type"] == "http.response.start"
        )

    @property
    def body(self):
        return b"".join(
            m.get("body", b"") for m in self.sent if m["type"] == "http.response.body"
        )


async def _request(
    router,
    path,
    *,
    method="POST",
    body=b"",
    content_type=FORM,
    content_length="declared",
    chunk=None,
    query=b"",
    extra_headers=(),
):
    """Send ``body`` to ``path`` through ``http_router``.

    ``content_length`` is "declared" (the true length), None (absent: the body is
    streamed with chunked transfer coding), or the raw header value(s) to send.
    """
    headers = [(b"host", b"localhost:8000"), *extra_headers]
    if content_type is not None:
        headers.append((b"content-type", content_type))
    if content_length == "declared":
        headers.append((b"content-length", str(len(body)).encode()))
    elif content_length is None:
        headers.append((b"transfer-encoding", b"chunked"))
    else:
        for value in content_length:
            headers.append((b"content-length", value))
    if chunk:
        chunks = [body[i : i + chunk] for i in range(0, len(body), chunk)] or [b""]
    else:
        chunks = [body]
    client = _Client(chunks)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("localhost", 8000),
    }
    await router["http_router"](scope, client.receive, client.send)
    return client


def _form(size=None, **fields):
    """An RFC 6749 form body, padded to exactly ``size`` bytes when given."""
    body = urlencode(fields).encode()
    if size is not None:
        body += b"&pad=" + b"a" * (size - len(body) - len(b"&pad="))
        assert len(body) == size
    return body


def _token_form(size=None):
    return _form(
        size,
        grant_type="authorization_code",
        code="not-a-real-code",
        redirect_uri="http://localhost:3000/callback",
        client_id="not-a-registered-client",
        code_verifier="v" * 43,
    )


def _revoke_form(size=None):
    return _form(size, token="not-a-real-token", client_id="not-a-registered-client")


def _authorize_form(size=None):
    return _form(
        size,
        response_type="code",
        client_id="not-a-registered-client",
        redirect_uri="http://localhost:3000/callback",
        code_challenge="c" * 43,
        code_challenge_method="S256",
    )


def _registration(size=None):
    metadata = {
        "redirect_uris": ["http://localhost:3000/callback"],
        "client_name": "body-limit test client",
    }
    body = json.dumps(metadata).encode()
    if size is not None:
        metadata["client_name"] = "a" * (
            size - len(body) + len("body-limit test client")
        )
        body = json.dumps(metadata).encode()
        assert len(body) == size
    return body


def _assert_refused_unread(client, reads):
    assert client.status == 413
    assert json.loads(client.body) == {
        "error": "invalid_request",
        "error_description": "Request body too large",
    }
    # Starlette never parsed a form, never read a byte of the body.
    assert reads.forms == []
    assert reads.parses == 0
    assert reads.bodies == []


def _assert_handler_read(reads, path, body):
    # The handler's own read saw exactly the bytes the client sent.
    assert reads.bodies, f"{path}: the handler never read the body"
    assert [b"".join(chunks) for chunks in reads.bodies] == [body] * len(reads.bodies)


# --- (a) oversized with Content-Length: 413 from the header alone ----------------


async def test_a_megabyte_form_post_to_token_is_413_before_any_parse(
    router, starlette_reads
):
    client = await _request(router, "/token", body=_token_form(MIB))

    _assert_refused_unread(client, starlette_reads)
    # Refused from Content-Length: not one byte of the body was pulled.
    assert client.messages == 0


# --- (b) oversized without Content-Length: cut off at the cap --------------------


async def test_a_streamed_megabyte_form_post_to_token_is_413_after_at_most_the_cap(
    router, starlette_reads
):
    client = await _request(
        router, "/token", body=_token_form(MIB), content_length=None, chunk=CHUNK
    )

    _assert_refused_unread(client, starlette_reads)
    # Reading stopped at the first chunk past the cap, not at the end of the body.
    assert client.pulled <= CAP + CHUNK
    assert client.messages < MIB // CHUNK


# --- (c) a normal token request reaches the handler unchanged --------------------


@pytest.mark.parametrize(
    "content_length, chunk",
    [("declared", None), (None, 7)],
    ids=["content-length", "streamed"],
)
async def test_a_small_token_post_reaches_the_handler_with_identical_bytes(
    router, starlette_reads, content_length, chunk
):
    body = _token_form()

    client = await _request(
        router, "/token", body=body, content_length=content_length, chunk=chunk
    )

    # An unknown client is refused by the handler itself, not by the cap.
    assert client.status == 401
    assert json.loads(client.body)["error"] == "unauthorized_client"
    assert starlette_reads.forms and set(starlette_reads.forms) == {"/token"}
    assert starlette_reads.parses == 1
    _assert_handler_read(starlette_reads, "/token", body)


async def test_a_body_at_the_cap_is_served_and_one_byte_more_is_413(
    router, starlette_reads
):
    at_cap = _token_form(CAP)
    served = await _request(router, "/token", body=at_cap, chunk=CHUNK)

    assert served.status == 401
    _assert_handler_read(starlette_reads, "/token", at_cap)

    starlette_reads.forms.clear()
    starlette_reads.parses = 0
    starlette_reads.bodies.clear()
    for content_length in ("declared", None):
        over = await _request(
            router,
            "/token",
            body=_token_form(CAP + 1),
            content_length=content_length,
            chunk=CHUNK,
        )
        _assert_refused_unread(over, starlette_reads)


# --- (d) the other body-reading OAuth endpoints ----------------------------------

OVERSIZED = [
    ("/token", FORM, _token_form),
    ("/token/", FORM, _token_form),
    ("/revoke", FORM, _revoke_form),
    ("/authorize", FORM, _authorize_form),
    ("/register", JSON, _registration),
]


@pytest.mark.parametrize(
    "content_length", ["declared", None], ids=["declared", "streamed"]
)
@pytest.mark.parametrize(
    "path, content_type, make", OVERSIZED, ids=[p for p, _, _ in OVERSIZED]
)
async def test_every_oauth_endpoint_refuses_a_megabyte_unread(
    router, starlette_reads, path, content_type, make, content_length
):
    client = await _request(
        router,
        path,
        body=make(MIB),
        content_type=content_type,
        content_length=content_length,
        chunk=CHUNK,
    )

    _assert_refused_unread(client, starlette_reads)
    assert client.pulled <= CAP + CHUNK


async def test_a_small_revoke_post_reaches_the_handler_with_identical_bytes(
    router, starlette_reads
):
    body = _revoke_form()

    client = await _request(router, "/revoke", body=body, chunk=5)

    assert client.status == 401
    assert set(starlette_reads.forms) == {"/revoke"}
    _assert_handler_read(starlette_reads, "/revoke", body)


async def test_a_small_registration_is_still_registered_with_identical_bytes(
    router, starlette_reads
):
    body = _registration()

    client = await _request(router, "/register", body=body, content_type=JSON, chunk=11)

    assert client.status == 201, client.body
    registered = json.loads(client.body)
    assert registered["client_name"] == "body-limit test client"
    assert registered["client_id"]
    _assert_handler_read(starlette_reads, "/register", body)


# --- (e) requests without a body pass through untouched --------------------------


async def test_discovery_get_is_served_without_the_guard_reading_anything(
    router, starlette_reads
):
    client = await _request(
        router,
        "/.well-known/oauth-authorization-server",
        method="GET",
        content_type=None,
        content_length=(),
    )

    assert client.status == 200
    metadata = json.loads(client.body)
    assert metadata["token_endpoint"] == "http://localhost:8000/token"
    # Nothing between the router and the handler touched receive().
    assert client.messages == 0


async def test_authorize_get_still_reaches_the_handler(router, starlette_reads):
    client = await _request(
        router,
        "/authorize",
        method="GET",
        content_type=None,
        content_length=(),
        query=_authorize_form(),
    )

    # An unknown client: the handler's own answer, from the query string, not the cap's.
    assert client.status == 400
    assert "not-a-registered-client" in json.loads(client.body)["error_description"]
    assert starlette_reads.forms == []


# --- OPTIONS and HEAD can carry a body a handler parses ---------------------------
#
# Without an Origin header (or without Access-Control-Request-Method) the SDK's
# CORSMiddleware hands OPTIONS /token, /register and /revoke to the handler itself,
# which parses the body; Starlette adds HEAD to the GET route /authorize, whose
# handler parses a form for every method but GET.

BODY_CARRYING_METHODS = [
    ("OPTIONS", "/token", FORM, _token_form, ()),
    ("OPTIONS", "/revoke", FORM, _revoke_form, ()),
    ("OPTIONS", "/register", JSON, _registration, ()),
    ("OPTIONS", "/token", FORM, _token_form, ((b"origin", b"https://client.example"),)),
    ("HEAD", "/authorize", FORM, _authorize_form, ()),
]


@pytest.mark.parametrize(
    "content_length", ["declared", None], ids=["declared", "streamed"]
)
@pytest.mark.parametrize(
    "method, path, content_type, make, extra_headers",
    BODY_CARRYING_METHODS,
    ids=[
        "OPTIONS /token",
        "OPTIONS /revoke",
        "OPTIONS /register",
        "OPTIONS /token with Origin",
        "HEAD /authorize",
    ],
)
async def test_options_and_head_with_a_megabyte_are_refused_unread(
    router,
    starlette_reads,
    method,
    path,
    content_type,
    make,
    extra_headers,
    content_length,
):
    client = await _request(
        router,
        path,
        method=method,
        body=make(MIB),
        content_type=content_type,
        content_length=content_length,
        chunk=CHUNK,
        extra_headers=extra_headers,
    )

    _assert_refused_unread(client, starlette_reads)
    assert client.pulled <= CAP + CHUNK


async def test_a_cors_preflight_is_still_answered(router, starlette_reads):
    client = await _request(
        router,
        "/token",
        method="OPTIONS",
        content_type=None,
        content_length=(),
        extra_headers=(
            (b"origin", b"http://localhost:6274"),
            (b"access-control-request-method", b"POST"),
        ),
    )

    assert client.status == 200
    start = next(m for m in client.sent if m["type"] == "http.response.start")
    headers = dict(start["headers"])
    assert headers[b"access-control-allow-origin"] == b"*"
    assert b"POST" in headers[b"access-control-allow-methods"]
    assert starlette_reads.bodies == []


async def test_discovery_head_is_still_served(router, starlette_reads):
    client = await _request(
        router,
        "/.well-known/oauth-authorization-server",
        method="HEAD",
        content_type=None,
        content_length=(),
    )

    # An empty HEAD is buffered and replayed; the server drops the response body.
    assert client.status == 200
    assert starlette_reads.bodies == []


# --- a Content-Length the guard cannot trust is refused, unread ------------------


@pytest.mark.parametrize(
    "content_length",
    [(b"abc",), (b"-1",), (b"1e3",), (b"40", b"41"), (b"9" * 5000,)],
    ids=["malformed", "negative", "exponent", "conflicting", "huge"],
)
async def test_an_untrustworthy_content_length_is_413_unread(
    router, starlette_reads, content_length
):
    client = await _request(
        router, "/token", body=_token_form(), content_length=content_length
    )

    _assert_refused_unread(client, starlette_reads)
    assert client.messages == 0


# --- the guard itself ------------------------------------------------------------


def test_the_cap_is_64_kib():
    from mcp_server.oauth_body_limit import MCP_OAUTH_MAX_BODY_BYTES

    assert MCP_OAUTH_MAX_BODY_BYTES == CAP == 65536


async def test_a_client_that_disconnects_mid_body_never_reaches_the_app():
    from mcp_server.oauth_body_limit import OAuthBodyLimit

    calls = []

    async def app(scope, receive, send):
        calls.append(scope["path"])

    messages = [
        {"type": "http.request", "body": b"client_id=x", "more_body": True},
        {"type": "http.disconnect"},
    ]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    await OAuthBodyLimit(app)(
        {"type": "http", "method": "POST", "path": "/token", "headers": []},
        receive,
        send,
    )

    assert calls == []
    assert sent == []
