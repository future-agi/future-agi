"""Real requests/httpx/ClickHouse clients with socket-blocked fake wire endpoints.

Run using an existing Python environment containing the project dependencies.
No Django/DB setup. No credentials, provider sends or dependency downloads.
"""

import asyncio
import json
import runpy
import threading
import time
import unittest
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import httpx
import requests
from clickhouse_driver import Client
from requests.adapters import HTTPAdapter

support = runpy.run_path(
    str(Path(__file__).with_name("test_falcon_transport_offline.py"))
)
ROOT, definitions, module = (support[k] for k in ("ROOT", "definitions", "module"))


class AdapterTests(unittest.TestCase):
    setUp = support["TransportTests"].setUp
    install = support["TransportTests"].install
    scope = support["TransportTests"].scope

    def serving(self):
        names = {"ModelServingClient", "_probe", "serving_available"}
        ns = definitions(
            "agentic_eval/core/embeddings/serving_client.py",
            names,
            {
                "Any": Any,
                "Image": SimpleNamespace(Image=object),
                "requests": requests,
                "time": time,
                "logger": Mock(),
                "SERVING_PROBE_TIMEOUT_SECONDS": 2,
                "SERVING_PROBE_TTL_SECONDS": 30,
                "_probe_cache": {},
                "_probe_lock": threading.Lock(),
                "ServingUnavailableError": RuntimeError,
            },
        )
        client = ns["ModelServingClient"].__new__(ns["ModelServingClient"])
        client.base_url, client.default_timeout, client.max_retries = (
            "https://offline.invalid",
            120,
            3,
        )
        client.session = requests.Session()
        client.session.mount("https://", HTTPAdapter(max_retries=3))
        self.addCleanup(client.session.close)
        return client

    def test_real_probe_then_post_and_adapter_retries(self):
        for reason in ("revoked", "deadline", "allowed", "fallback"):
            with self.subTest(reason=reason):
                self.active = True
                self.guard = self.Guard("p", "u", "o", "w", 2)
                client = self.serving()
                sent = []

                def wire(adapter, request, *, sent=sent, reason=reason, **kw):
                    sent.append((request.method, kw["timeout"]))
                    self.assertEqual(adapter.max_retries.total, 0)
                    response = requests.Response()
                    response.status_code = 404 if reason == "fallback" else 200
                    response._content = b'{"embeddings": [[1.0]]}'
                    response.url = request.url
                    if request.method == "GET":
                        if reason in ("revoked", "fallback"):
                            self.active = False
                        elif reason == "deadline":
                            self.guard.deadline = 0
                    return response

                with (
                    self.scope(),
                    patch("requests.sessions.get_netrc_auth", return_value=None),
                    patch.object(HTTPAdapter, "send", wire),
                ):
                    if reason != "allowed":
                        with self.assertRaises(self.Aborted):
                            client.embed_text("trace")
                        self.assertEqual([s[0] for s in sent], ["GET"])
                    else:
                        self.assertEqual(client.embed_text("trace"), [1.0])
                        self.assertEqual([s[0] for s in sent], ["GET", "POST"])
                        self.assertTrue(all(0 < s[1] <= 2 for s in sent))
                    self.assertEqual(
                        client.session.get_adapter(client.base_url).max_retries.total, 3
                    )

    def test_redirect_is_separately_admitted(self):
        sent = []

        def wire(adapter, request, **kw):
            sent.append(request.url)
            self.active = False
            response = requests.Response()
            response.status_code = 307
            response.headers["Location"] = "https://offline.invalid/redirect"
            response._content = b""
            response.url = request.url
            return response

        with (
            self.scope(),
            patch("requests.sessions.get_netrc_auth", return_value=None),
            patch.object(HTTPAdapter, "send", wire),
        ):
            with self.assertRaises(self.Aborted):
                self.transport.requests_send(
                    None, "get", "https://offline.invalid/health", timeout=120
                )
        self.assertEqual(len(sent), 1)

    def managed_stream(self, on_enter=None, allowed=True):
        sends = []
        original = httpx.AsyncClient

        def wire(request):
            sends.append(request)
            self.assertTrue(
                all(0 < n <= 2 for n in request.extensions["timeout"].values())
            )
            return httpx.Response(
                200, content='data: {"choices": []}\n\ndata: [DONE]\n\n'
            )

        class ClientWithWait(original):
            async def __aenter__(client):
                value = await super().__aenter__()
                if on_enter:
                    on_enter()
                return value

        def client(**kw):
            return ClientWithWait(
                **kw, transport=httpx.MockTransport(wire), trust_env=False
            )

        ns = definitions(
            "ee/licensing/activation_client.py",
            {"dispatch_managed_stream"},
            {
                "AsyncIterator": AsyncIterator,
                "Callable": Callable,
                "json": json,
                "_raise_for_managed_status": lambda *a: None,
            },
        )

        async def consume():
            return [
                x
                async for x in ns["dispatch_managed_stream"](
                    url="https://offline.invalid",
                    api_key="synthetic",
                    json_body={},
                    timeout=300,
                    on_unauthorized=lambda: None,
                )
            ]

        with self.scope(), patch.object(httpx, "AsyncClient", client):
            if allowed:
                asyncio.run(consume())
                self.assertEqual(len(sends), 1)
            else:
                with self.assertRaises(self.Aborted):
                    asyncio.run(consume())
                self.assertEqual(sends, [])

    def test_managed_stream_rechecks_after_client_wait(self):
        self.managed_stream(lambda: setattr(self, "active", False), allowed=False)

    def test_managed_stream_caps_timeout_at_send(self):
        self.managed_stream()

    def test_managed_sync_refuses_revoked_and_caps_timeout(self):
        ns = definitions(
            "ee/licensing/activation_client.py",
            {"dispatch_managed_request"},
            {
                "Callable": Callable,
                "_raise_for_managed_status": lambda *a: None,
            },
        )
        post = Mock(return_value=httpx.Response(200, json={}))
        with self.scope(), patch.object(httpx.HTTPTransport, "handle_request", post):
            ns["dispatch_managed_request"](
                url="https://offline.invalid",
                api_key="synthetic",
                json_body={},
                timeout=300,
                on_unauthorized=lambda: None,
            )
            self.assertTrue(
                all(
                    t <= 2
                    for t in post.call_args.args[0].extensions["timeout"].values()
                )
            )
            self.active = False
            with self.assertRaises(self.Aborted):
                ns["dispatch_managed_request"](
                    url="https://offline.invalid",
                    api_key="synthetic",
                    json_body={},
                    timeout=300,
                    on_unauthorized=lambda: None,
                )
            self.assertEqual(post.call_count, 1)

    def test_native_connect_wait_and_insert_blocks_are_rechecked(self):
        client = Client(host="offline.invalid")
        connection = client.connection
        sends = []

        def send_query(*args, **kw):
            sends.append("query")

        def send_data(*args, **kw):
            sends.append("data")

        connection.send_query = send_query
        connection.send_data = send_data

        def connect():
            connection.connected = True
            self.active = False

        connection.connect = connect
        with self.scope():
            self.transport.guard_native_client(client)
            self.assertTrue(connection.disable_reconnect)
            with self.assertRaises(self.Aborted):
                connection.send_query("INSERT")
            self.assertEqual(sends, [])
            with self.assertRaises(self.Aborted):
                connection.send_data(None)
            self.assertEqual(sends, [])

    def test_requests_rechecks_after_tcp_connect_before_wire_bytes(self):
        from urllib3.connection import HTTPConnection

        writes = []

        def connect(connection):
            connection.sock = SimpleNamespace(
                settimeout=lambda t: None, close=lambda: None, sendall=writes.append
            )
            self.active = False

        with (
            self.scope(),
            patch("requests.sessions.get_netrc_auth", return_value=None),
            patch.object(HTTPConnection, "connect", connect),
            patch.dict("os.environ", {"NO_PROXY": "*"}),
        ):
            with self.assertRaises(self.Aborted):
                self.transport.requests_send(
                    None, "get", "http://offline.invalid/health", timeout=120
                )
        self.assertEqual(writes, [])

    def test_httpcore_rechecks_after_connect_and_before_body(self):
        from httpcore._backends.mock import AsyncMockBackend, AsyncMockStream

        for phase in ("connect", "body"):
            self.active = True
            self.guard = self.Guard("p", "u", "o", "w", 2)
            writes = []
            owner = self

            class Stream(AsyncMockStream):
                async def write(
                    stream, buffer, timeout=None, *, owner=owner, writes=writes
                ):
                    owner.assertLessEqual(timeout, 2)
                    writes.append(buffer)
                    owner.active = False

            class Backend(AsyncMockBackend):
                async def connect_tcp(
                    backend, *a, phase=phase, owner=owner, stream_class=Stream, **kw
                ):
                    if phase == "connect":
                        owner.active = False
                    return stream_class([])

            transport = httpx.AsyncHTTPTransport(retries=0)
            transport._pool._network_backend = Backend([])

            async def invoke(transport=transport):
                async with httpx.AsyncClient(
                    transport=transport,
                    trust_env=False,
                    **self.transport.async_client_options(),
                ) as client:
                    await client.post(
                        "http://offline.invalid",
                        json={"trace": "synthetic"},
                        timeout=300,
                    )

            with self.scope():
                with self.assertRaises(self.Aborted):
                    asyncio.run(invoke())
            self.assertEqual(len(writes), 0 if phase == "connect" else 1)

    def test_async_scope_does_not_leak_to_interactive_sibling(self):
        with self.scope(False):

            async def background():
                with self.transport.analysis_transport_scope(self.guard):
                    await asyncio.sleep(0)
                    return self.transport.current_guard()

            async def interactive():
                await asyncio.sleep(0)
                return self.transport.current_guard()

            async def both():
                return await asyncio.gather(background(), interactive())

            self.assertEqual(asyncio.run(both()), [self.guard, None])


if __name__ == "__main__":
    unittest.main()
