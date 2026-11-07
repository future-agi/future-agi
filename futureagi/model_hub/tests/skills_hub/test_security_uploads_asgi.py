"""Pre-parser body caps exercise Django's real ASGI spool and deployment wiring."""

import asyncio
import json
import runpy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from django.core.exceptions import RequestAborted
from django.core.handlers import asgi as django_asgi

from model_hub.services.skills_hub import limits
from model_hub.views.skills_uploads_asgi import SkillsImportBodyLimit
from tfc import asgi_startup

pytestmark = pytest.mark.unit


@pytest.fixture
def body_app(settings, monkeypatch):
    # Force the real Django spool onto disk too, to prove cleanup on rejection.
    settings.FILE_UPLOAD_MAX_MEMORY_SIZE = 1
    original = django_asgi.tempfile.SpooledTemporaryFile
    spools = []
    dispatched = []

    def spool(*args, **kwargs):
        value = original(*args, **kwargs)
        spools.append(value)
        return value

    monkeypatch.setattr(django_asgi.tempfile, "SpooledTemporaryFile", spool)
    handler = object.__new__(django_asgi.ASGIHandler)

    async def app(scope, receive, send):
        # This is the production read_body implementation, not a stub that raises
        # our limit error itself. Skip URL dispatch/DB after body ingestion only.
        try:
            body = await handler.read_body(receive)
        except RequestAborted:
            return
        try:
            content = body.read()
            dispatched.append(content)
        finally:
            body.close()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": content})

    return app, spools, dispatched


def scope(path="/model-hub/skills/import/preview/", headers=(), **extra):
    return {
        "type": "http",
        "path": path,
        "method": "POST",
        "headers": list(headers),
        **extra,
    }


async def exercise(app, request_scope, chunks):
    sent = []
    index = 0

    async def receive():
        nonlocal index
        assert index < len(chunks), "The rejected upload must not be drained"
        message = chunks[index]
        index += 1
        return message

    async def send(message):
        sent.append(message)

    await app(request_scope, receive, send)
    return sent, index


def chunk(body, *, more=True):
    return {"type": "http.request", "body": body, "more_body": more}


def assert_413(sent):
    assert [message["type"] for message in sent] == [
        "http.response.start",
        "http.response.body",
    ]
    assert sent[0]["status"] == 413
    error = json.loads(sent[1]["body"])
    assert error["status"] is False
    assert error["code"] == "payload_too_large"


@pytest.mark.parametrize(
    "path", ["/model-hub/skills/import/", "/model-hub/skills/import/preview/"]
)
async def test_declared_900_mib_never_enters_django_or_reads_body(path):
    app = AsyncMock(
        side_effect=AssertionError("Do not enter Django for a known oversized request")
    )
    receive = AsyncMock(side_effect=AssertionError("Do not read an oversized body"))
    send = AsyncMock()
    await SkillsImportBodyLimit(app)(
        scope(path, [(b"content-length", str(900 * 1024**2).encode())]), receive, send
    )
    app.assert_not_awaited()
    receive.assert_not_awaited()
    assert_413([call.args[0] for call in send.await_args_list])


@pytest.mark.parametrize(
    "headers", [[], [(b"content-length", b"1")], [(b"content-length", b"invalid")]]
)
@pytest.mark.parametrize("prefix", ["", "/hosted"])
async def test_actual_bytes_stop_django_spool_with_no_drain_and_cleanup(
    body_app, monkeypatch, headers, prefix
):
    monkeypatch.setattr(limits, "MAX_MULTIPART_BYTES", 8)
    app, spools, dispatched = body_app
    sent, read_count = await exercise(
        SkillsImportBodyLimit(app),
        scope(prefix + "/model-hub/skills/import/", headers, root_path=prefix),
        [
            chunk(b"1234"),
            chunk(b"5678"),
            chunk(b"9"),
            chunk(b"unread tail", more=False),
        ],
    )
    assert_413(sent)
    assert read_count == 3
    assert dispatched == []
    assert len(spools) == 1 and spools[0].closed


async def test_exact_request_limit_preserves_bytes_and_does_not_mutate_global_threshold(
    body_app, monkeypatch, settings
):
    monkeypatch.setattr(limits, "MAX_MULTIPART_BYTES", 8)
    app, spools, dispatched = body_app
    sent, read_count = await exercise(
        SkillsImportBodyLimit(app),
        scope(),
        [chunk(b"1234"), chunk(b"5678", more=False)],
    )
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b"12345678"
    assert read_count == 2 and dispatched == [b"12345678"]
    assert spools[0].closed
    assert settings.FILE_UPLOAD_MAX_MEMORY_SIZE == 1


async def test_real_disconnect_closes_spool_without_sending_spurious_413(body_app):
    app, spools, dispatched = body_app
    sent, read_count = await exercise(
        SkillsImportBodyLimit(app),
        scope(),
        [chunk(b"1234"), {"type": "http.disconnect"}],
    )
    assert sent == [] and dispatched == []
    assert read_count == 2 and spools[0].closed


@pytest.mark.parametrize(
    "path", ["/model-hub/skills/publish/", "/unrelated/upload/", "/mcp"]
)
async def test_other_routes_retain_their_existing_upload_policy(
    body_app, monkeypatch, path
):
    monkeypatch.setattr(limits, "MAX_MULTIPART_BYTES", 1)
    app, spools, dispatched = body_app
    sent, _ = await exercise(
        SkillsImportBodyLimit(app),
        scope(path, [(b"content-length", b"4")]),
        [chunk(b"1234", more=False)],
    )
    assert sent[0]["status"] == 200
    assert dispatched == [b"1234"] and spools[0].closed


async def test_request_byte_counters_are_not_shared_between_concurrent_uploads(
    body_app, monkeypatch
):
    monkeypatch.setattr(limits, "MAX_MULTIPART_BYTES", 8)
    app = SkillsImportBodyLimit(body_app[0])
    results = await asyncio.gather(
        *[
            exercise(app, scope(), [chunk(b"1234"), chunk(b"5678", more=False)])
            for _ in range(2)
        ]
    )
    assert all(sent[0]["status"] == 200 for sent, _ in results)
    assert len(body_app[1]) == 2 and all(spool.closed for spool in body_app[1])


async def test_deployed_http_router_wraps_django_before_body_buffering(
    monkeypatch, body_app
):
    monkeypatch.setattr(limits, "MAX_MULTIPART_BYTES", 8)
    monkeypatch.setattr("tfc.telemetry.init_telemetry", lambda *, component: None)
    monkeypatch.setattr("django.core.asgi.get_asgi_application", lambda: body_app[0])
    monkeypatch.setattr(asgi_startup, "warm_http_urlconf", lambda: 0)
    # Execute the actual composition root with only unrelated startup adapters
    # disabled. Reverting the tfc.asgi wiring must fail this regression too.
    module = runpy.run_path(str(Path(__file__).parents[3] / "tfc" / "asgi.py"))
    sent, count = await exercise(
        module["http_router"],
        scope(),
        [chunk(b"12345678"), chunk(b"9"), chunk(b"unread", more=False)],
    )
    assert_413(sent)
    assert count == 2 and body_app[2] == []
    assert body_app[1][0].closed
