"""HUB-17: an oversized body to a Hub route is refused before any of it is read.

Production runs Django under ASGI, where `ASGIHandler.read_body` spools the whole body (up to
FILE_UPLOAD_MAX_MEMORY_SIZE, 1 GB, in memory) before a view runs. These tests drive the real
`HubBodyLimit` around the real Django ASGI handler: a 50 MB POST is answered 413 from its headers
without one `receive()`, and a body sent without a length is cut off at the cap. The view-level
check covers WSGI and the test client. The live number (granian, worker RSS) is the rig's
`body-limit-live` receipt, not this file.
"""

import asyncio
import json
import time
import tracemalloc

import pytest
from django.core.asgi import get_asgi_application

from omega_hub import body_limit
from omega_hub.body_limit import HubBodyLimit, limit_for
from omega_hub.models import Submission
from omega_hub.tests.helpers import entry_files, submit, token_client

FIFTY_MB = 50 * 1024 * 1024


def _scope(path: str, length: int | None, method: str = "POST") -> dict:
    headers = [(b"host", b"testserver"), (b"content-type", b"multipart/form-data; boundary=x")]
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
            "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": headers,
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80)}


class Wire:
    """The client side of one ASGI request: the body it sends, and what the app sent back.
    `body` is sent as one message; `chunks` x `chunk_size` zero bytes are streamed with no length."""

    def __init__(self, body=b"", chunks=0, chunk_size=0):
        self.body, self.chunks_left, self.chunk_size, self.receives, self.sent = body, chunks, chunk_size, 0, []

    async def receive(self):
        self.receives += 1
        if self.chunk_size:
            self.chunks_left -= 1
            return {"type": "http.request", "body": b"\0" * self.chunk_size, "more_body": self.chunks_left > 0}
        return {"type": "http.request", "body": self.body, "more_body": False}

    async def send(self, message):
        self.sent.append(message)

    @property
    def status(self):
        starts = [m for m in self.sent if m["type"] == "http.response.start"]
        return starts[0]["status"] if starts else None

    @property
    def error_code(self):
        body = b"".join(m.get("body", b"") for m in self.sent if m["type"] == "http.response.body")
        return json.loads(body)["error"]["code"]


def test_a_50_mb_submission_is_refused_from_its_headers_without_reading_the_body():
    inner_calls = []

    async def inner(scope, receive, send):  # stands in for Django: it must never be reached
        inner_calls.append(scope["path"])

    wire = Wire()
    tracemalloc.start()
    started = time.perf_counter()
    asyncio.run(HubBodyLimit(inner)(_scope("/omega-hub/v1/submissions", FIFTY_MB), wire.receive, wire.send))
    elapsed_ms = (time.perf_counter() - started) * 1000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert wire.status == 413 and wire.error_code == "OMEGA_HUB_BUNDLE_TOO_LARGE"
    assert wire.receives == 0 and inner_calls == []
    assert elapsed_ms < 50, elapsed_ms
    assert peak < 5 * 1024 * 1024, peak


@pytest.mark.django_db
def test_a_body_without_a_length_is_cut_off_at_the_cap_by_the_real_django_handler():
    """Chunked, no Content-Length: Django spools what arrives, so the guard ends the stream at the cap.
    Peak memory is bounded by the cap (2.5 MiB plus the spool's growth), not by the 50 MB sent."""
    chunk = 256 * 1024
    wire = Wire(chunks=FIFTY_MB // chunk, chunk_size=chunk)
    app = HubBodyLimit(get_asgi_application())
    tracemalloc.start()
    asyncio.run(app(_scope("/omega-hub/v1/submissions", None), wire.receive, wire.send))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert wire.status == 413 and wire.error_code == "OMEGA_HUB_BUNDLE_TOO_LARGE"
    assert wire.receives * chunk <= body_limit.SUBMISSION_LIMIT + chunk
    assert peak < 12 * 1024 * 1024, peak


def test_paths_outside_the_hub_pass_through_untouched():
    calls = []

    async def inner(scope, receive, send):
        calls.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wire = Wire(body=b"x" * 10)
    asyncio.run(HubBodyLimit(inner)(_scope("/accounts/token/", FIFTY_MB), wire.receive, wire.send))
    assert wire.status == 204 and calls and calls[0]["body"] == b"x" * 10
    assert limit_for("/accounts/token/") is None and limit_for("/omega-hubx/v1") is None


@pytest.mark.parametrize(
    ("path", "limit"),
    [
        ("/omega-hub/v1/submissions", body_limit.SUBMISSION_LIMIT),
        ("/omega-hub/v1/publishers/c-test/avatar", body_limit.AVATAR_LIMIT),
        ("/omega-hub/internal/checker/jobs/7d5f1f0e-2a51-4d3a-9d6f-1f0e2a514d3a/result", body_limit.CHECKER_RESULT_LIMIT),
        ("/omega-hub/v1/reports", body_limit.DEFAULT_LIMIT),
    ],
)
def test_each_route_accepts_its_cap_and_refuses_one_byte_more(path, limit):
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    for length, status in ((limit, 200), (limit + 1, 413)):
        wire = Wire()
        asyncio.run(HubBodyLimit(inner)(_scope(path, length), wire.receive, wire.send))
        assert wire.status == status, (path, length)


def test_a_malformed_content_length_is_refused():
    async def inner(scope, receive, send):
        raise AssertionError("reached the app")

    for value in (b"12abc", b"-5", b"1e9"):
        scope = _scope("/omega-hub/v1/reports", None)
        scope["headers"].append((b"content-length", value))
        wire = Wire()
        asyncio.run(HubBodyLimit(inner)(scope, wire.receive, wire.send))
        assert wire.status == 413, value


def test_the_largest_real_bundle_still_fits():
    """The cap leaves room for the multipart envelope around a 2 MiB bundle and its fields."""
    from omega_hub.services import intake

    assert body_limit.SUBMISSION_LIMIT - intake.MAX_RAW_BYTES >= 256 * 1024


@pytest.mark.django_db
def test_wsgi_refuses_a_declared_50_mb_upload_before_parsing_it(world, monkeypatch):
    from rest_framework.parsers import MultiPartParser

    def never(*args, **kwargs):
        raise AssertionError("the body was parsed")

    monkeypatch.setattr(MultiPartParser, "parse", never)
    client = token_client(world.token_c)
    response = client.post("/omega-hub/v1/submissions", data=b"--x--", content_type="multipart/form-data; boundary=x",
                           CONTENT_LENGTH=str(FIFTY_MB))
    assert response.status_code == 413 and response.json()["error"]["code"] == "OMEGA_HUB_BUNDLE_TOO_LARGE"
    assert Submission.objects.count() == 0


@pytest.mark.django_db
def test_a_real_submission_under_the_cap_is_still_accepted(world):
    created = submit(token_client(world.token_c), "c-test", entry_files("first-agent"), name="first-agent",
                     version="1.0.0")
    assert created.status_code == 201, created.content


@pytest.mark.django_db
def test_uploads_per_account_are_throttled_before_the_body_is_used(world, monkeypatch):
    from omega_hub.throttles import HubSubmitThrottle

    monkeypatch.setattr(HubSubmitThrottle, "rate", "2/hour")
    client = token_client(world.token_c)
    files = entry_files("first-agent")
    responses = [submit(client, "c-test", files) for _ in range(3)]
    assert [r.status_code for r in responses] == [201, 201, 429]
    assert responses[2].json()["error"]["code"] == "OMEGA_HUB_THROTTLED"
