"""Real transport/retry definitions, synthetic I/O; python3 -I this_file.

No Django setup, credentials or provider egress. Independent regression oracles.
"""

import asyncio
import json
import random
import runpy
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

support = runpy.run_path(
    str(Path(__file__).with_name("test_falcon_background_boundaries_offline.py"))
)
ROOT, definitions, module = (support[k] for k in ("ROOT", "definitions", "module"))


class TransportTests(unittest.TestCase):
    setUp = support["BoundaryTests"].setUp
    install = support["BoundaryTests"].install

    def scope(self, guard=True):
        path = ROOT / "tracer/services/background_transport.py"
        if not path.exists():
            return nullcontext()
        if not hasattr(self, "transport"):
            self.transport = module("transport", **runpy.run_path(str(path)))
            self.modules["tracer.services.background_transport"] = self.transport
            self.install()
        return self.transport.analysis_transport_scope(self.guard if guard else None)

    def retry(self, reason, interactive=False):
        attempts = []

        class Empty(Exception):
            pass

        class HTTPError(Exception):
            pass

        async def sleep(delay):
            if reason == "revoked":
                self.active = False
            else:
                self.guard.deadline = 0

        async def stream(*args):
            attempts.append(1)
            if len(attempts) == 1:
                raise Empty()
            yield {"choices": []}

        fn = definitions(
            "ee/falcon_ai/llm_client.py",
            {"stream_with_retry"},
            {
                "asyncio": SimpleNamespace(sleep=sleep),
                "random": random,
                "logger": Mock(),
                "EmptyStreamError": Empty,
                "httpx": SimpleNamespace(
                    HTTPStatusError=HTTPError,
                    TimeoutException=TimeoutError,
                    ConnectError=ConnectionError,
                ),
                "RETRYABLE_STATUS_CODES": {429, 500},
            },
            owner="FalconLLMClient",
        )["stream_with_retry"]
        client = SimpleNamespace(stream_completion=stream)

        async def consume():
            return [c async for c in fn(client, [])]

        with self.scope(not interactive):
            if interactive:
                asyncio.run(consume())
                self.assertEqual(len(attempts), 2)
            else:
                with self.assertRaises(self.Aborted):
                    asyncio.run(consume())
                self.assertEqual(len(attempts), 1)

    def test_real_retry_revoked_during_backoff(self):
        self.retry("revoked")

    def test_real_retry_deadline_during_backoff(self):
        self.retry("deadline")

    def test_interactive_retry_is_unchanged(self):
        self.retry("revoked", interactive=True)

    def embedding(self, effect=None, interactive=False, method="_make_request"):
        sent = []
        response = SimpleNamespace(
            status_code=200, json=lambda: {"embeddings": [[1.0]]}
        )

        def post(*args, **kw):
            sent.append(kw)
            return response

        def probe(*args):
            if effect == "revoked":
                self.active = False
            if effect == "deadline":
                self.guard.deadline = 0
            return True

        requests = SimpleNamespace(
            exceptions=SimpleNamespace(
                Timeout=TimeoutError,
                ConnectionError=ConnectionError,
                RequestException=Exception,
            )
        )
        ns = definitions(
            "agentic_eval/core/embeddings/serving_client.py",
            {"ModelServingClient"},
            {
                "Any": Any,
                "Image": SimpleNamespace(Image=object),
                "requests": requests,
                "logger": Mock(),
                "time": __import__("time"),
                "serving_available": probe,
            },
        )
        client = ns["ModelServingClient"].__new__(ns["ModelServingClient"])
        client.base_url, client.default_timeout = "https://offline.invalid", 120
        client.session = SimpleNamespace(post=post)
        # The guarded session is independent of the cached interactive client.
        with self.scope(not interactive):
            if hasattr(self, "transport"):
                p = patch.object(
                    self.transport,
                    "requests_send",
                    lambda session, verb, url, **kw: post(
                        url,
                        **{**kw, "timeout": self.transport.before_send(kw["timeout"])},
                    ),
                )
            else:
                p = nullcontext()
            with p:

                def invoke():
                    return (
                        client._make_request("/embed", {"text": ["x"], "input_type": "text"})
                        if method == "_make_request"
                        else client.embed_text_batch(["x"])
                    )

                if effect and not interactive:
                    with self.assertRaises(self.Aborted):
                        invoke()
                    self.assertEqual(sent, [])
                else:
                    invoke()
                    self.assertEqual(len(sent), 1)
                    if interactive:
                        self.assertEqual(sent[0]["timeout"], 120)
                    else:
                        self.assertGreater(sent[0]["timeout"], 0)
                        self.assertLessEqual(sent[0]["timeout"], 2)

    def test_embedding_revoked_in_health_probe(self):
        self.embedding("revoked")

    def test_embedding_deadline_in_health_probe(self):
        self.embedding("deadline")

    def test_embedding_timeout_capped(self):
        self.embedding()

    def test_embedding_batch_rechecks(self):
        self.embedding("revoked", method="embed_text_batch")

    def test_embedding_interactive_no_guard_still_sends(self):
        self.embedding("revoked", interactive=True)

    def test_explore_provider_refused_using_ambient_context(self):
        sent = Mock()
        self.modules["structlog"] = module("structlog", get_logger=lambda *a: Mock())
        self.modules["litellm"] = module("litellm", completion=sent)
        self.modules["ee.licensing.managed_ai"] = module(
            "managed", chat_completion=sent
        )
        self.install()
        fn = definitions(
            "ai_tools/tools/tracing/explore_trace.py",
            {"_run_chauffeur"},
            {"logger": Mock(), "json": json, "os": SimpleNamespace(environ={})},
            owner="ExploreTraceTool",
        )["_run_chauffeur"]
        with self.scope():
            self.active = False
            with self.assertRaises(self.Aborted):
                fn(SimpleNamespace(), "trace")
        sent.assert_not_called()

    def test_run_sync_propagates_context_and_resets(self):
        with self.scope(False):
            self.assertTrue(
                hasattr(self, "transport"), "missing shared transport context"
            )
            self.assertIsNone(self.transport.current_guard())
            result = self.guard.run_sync(lambda: self.transport.current_guard())
            self.assertIs(result, self.guard)
            self.assertIsNone(self.transport.current_guard())


if __name__ == "__main__":
    unittest.main()
