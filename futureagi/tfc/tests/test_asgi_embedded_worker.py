"""tfc.asgi wiring of the embedded Temporal worker (standalone install)."""

from __future__ import annotations

import asyncio
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from tfc import asgi_startup

pytestmark = pytest.mark.unit


@pytest.fixture
def asgi_module(monkeypatch):
    """tfc/asgi.py executed with telemetry, the Django app and the URL warm-up
    stubbed out, so the routing and lifespan code run without a full app."""
    django_calls = []

    async def django_app(scope, receive, send):
        django_calls.append(scope["path"])

    monkeypatch.setattr("tfc.telemetry.init_telemetry", lambda *, component: None)
    monkeypatch.setattr("django.core.asgi.get_asgi_application", lambda: django_app)
    monkeypatch.setattr(asgi_startup, "warm_http_urlconf", lambda: 0)
    # run_path returns a copy; patch the namespace the functions really use.
    module = runpy.run_path(str(Path(__file__).parents[1] / "asgi.py"))
    namespace = module["http_router"].__globals__
    namespace["_get_mcp_starlette_app"] = lambda: None
    namespace["django_calls"] = django_calls
    return namespace


def _unhealthy_state():
    return {
        "healthy": False,
        "fatal": True,
        "connected": False,
        "workers_running": False,
        "queues": [],
        "down_for_seconds": 0,
        "consecutive_failures": 1,
        "last_error": "ValueError: bad config",
    }


async def _get(module, path):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await module["http_router"](
        {"type": "http", "path": path, "method": "GET"}, receive, send
    )
    return sent


@pytest.mark.parametrize("path", ["/health/", "/health"])
async def test_health_is_503_with_worker_state_while_embedded_worker_is_down(
    asgi_module, path
):
    worker = SimpleNamespace(health=_unhealthy_state)
    asgi_module["get_embedded_worker"] = lambda: worker

    sent = await _get(asgi_module, path)

    assert sent[0]["status"] == 503
    body = json.loads(sent[1]["body"])
    assert body["status"] is False
    assert body["code"] == "service_unavailable"
    assert body["temporal_worker"]["last_error"] == "ValueError: bad config"
    assert asgi_module["django_calls"] == []


async def test_health_is_served_by_django_while_embedded_worker_is_healthy(
    asgi_module,
):
    worker = SimpleNamespace(health=lambda: {**_unhealthy_state(), "healthy": True})
    asgi_module["get_embedded_worker"] = lambda: worker

    await _get(asgi_module, "/health/")

    assert asgi_module["django_calls"] == ["/health/"]


async def test_health_is_served_by_django_without_embedded_worker(asgi_module):
    asgi_module["get_embedded_worker"] = lambda: None

    await _get(asgi_module, "/health/")
    await _get(asgi_module, "/api/other/")

    assert asgi_module["django_calls"] == ["/health/", "/api/other/"]


async def test_lifespan_starts_and_drains_the_embedded_worker(asgi_module):
    events = []

    class _Worker:
        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    asgi_module["get_embedded_worker"] = lambda: _Worker()
    incoming = asyncio.Queue()
    for message_type in ("lifespan.startup", "lifespan.shutdown"):
        incoming.put_nowait({"type": message_type})

    async def send(message):
        events.append(message["type"])

    await asgi_module["lifespan_handler"]({"type": "lifespan"}, incoming.get, send)

    assert events == [
        "start",
        "lifespan.startup.complete",
        "stop",
        "lifespan.shutdown.complete",
    ]


async def test_lifespan_is_unchanged_without_embedded_worker(asgi_module):
    asgi_module["get_embedded_worker"] = lambda: None
    incoming = asyncio.Queue()
    for message_type in ("lifespan.startup", "lifespan.shutdown"):
        incoming.put_nowait({"type": message_type})
    sent = []

    async def send(message):
        sent.append(message["type"])

    await asgi_module["lifespan_handler"]({"type": "lifespan"}, incoming.get, send)

    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
