"""Original success bytes through installed clients and loopback HTTP servers.

Only transport/auth configuration is synthetic; decoding, witnessing and the
Chauffeur typed-report seal are the product definitions. No external egress.
"""

import json
import os
import runpy
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer
from types import SimpleNamespace
from unittest.mock import patch

import botocore.session
import httpx
import openai

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
b = runpy.run_path(str(HERE / "test_falcon_background_boundaries_offline.py"))
f = runpy.run_path(str(HERE / "analysis_llm_fixture.py"))
g = runpy.run_path(str(HERE / "analysis_gateway_fixture.py"))
CONNECT = socket.socket.connect
GETADDRINFO = socket.getaddrinfo
CREATE_CONNECTION = socket.create_connection
MALFORMED = {"ff": b"\xff", "truncated": b"\xe2\x82", "surrogate": b"\xed\xa0\x80"}
UNICODE = "Answered café 東京 😀 �"


def deny(*args, **kwargs):
    raise AssertionError("UTF-8 regression forbids external egress")


def loopback_connect(sock, address):
    if sock.family != socket.AF_INET or address[0] != "127.0.0.1":
        return deny()
    return CONNECT(sock, address)


def loopback_lookup(host, *args, **kwargs):
    if host != "127.0.0.1":
        return deny()
    return GETADDRINFO(host, *args, **kwargs)


class StrictNativeUTF8(unittest.TestCase):
    install = b["BoundaryTests"].install

    def setUp(self):
        b["BoundaryTests"].setUp(self)
        for target, value in (
            ("socket.socket.connect", loopback_connect),
            ("socket.getaddrinfo", loopback_lookup),
            ("socket.create_connection", CREATE_CONNECTION),
            ("socket.socket.connect_ex", deny),
            ("socket.socket.sendto", deny),
        ):
            guard = patch(target, value)
            guard.start()
            self.addCleanup(guard.stop)
        if hasattr(socket.socket, "sendmsg"):
            guard = patch("socket.socket.sendmsg", deny)
            guard.start()
            self.addCleanup(guard.stop)
        environment = patch.dict(
            os.environ,
            {"HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "", "NO_PROXY": "*"},
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.install()

    def exercise(self, route, variant):
        from ee.agenthub.traceerroragent.background_payload_reports import (
            chauffeur_report,
        )
        from ee.agenthub.traceerroragent.background_payloads import require_payload
        from tracer.services.analysis_result_completion import (
            CompletionWitness,
            IncompleteAnalysis,
        )
        from tracer.services.background_transport import analysis_transport_scope

        report = {
            "sub_flows": [
                {"name": "flow", "spans": ["span"], "summary": UNICODE, "flags": []}
            ],
            "trace_overview": UNICODE,
        }
        text = json.dumps(report, ensure_ascii=False)
        dialect = route if route in {"anthropic", "bedrock"} else "openai"
        data = (
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}
            if dialect != "openai"
            else {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}
        )
        wire = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if variant in MALFORMED:
            wire = wire.replace(b"Answered", b"Answ" + MALFORMED[variant] + b"ered")
        fragmented = variant == "fragmented"
        # HTTP chunk framing forces a genuine split *within* each UTF-8 sequence,
        # independent of how the operating system coalesces socket writes.
        chunks = [bytes([byte]) for byte in wire] if fragmented else [wire]
        requests, server_errors = [], []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler):
                try:
                    requests.append(
                        handler.rfile.read(int(handler.headers["Content-Length"]))
                    )
                    handler.send_response(200)
                    handler.send_header("Content-Type", "application/json")
                    handler.send_header("Connection", "close")
                    if fragmented:
                        handler.send_header("Transfer-Encoding", "chunked")
                    else:
                        handler.send_header("Content-Length", str(len(wire)))
                    handler.end_headers()
                    for chunk in chunks:
                        handler.wfile.write(
                            b"1\r\n" + chunk + b"\r\n" if fragmented else chunk
                        )
                        handler.wfile.flush()
                    if fragmented:
                        handler.wfile.write(b"0\r\n\r\n")
                        handler.wfile.flush()
                except Exception as exc:
                    server_errors.append(type(exc).__name__)

            def log_message(handler, *args):
                pass

        server = TCPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        server_thread.start()

        def close_server():
            server.shutdown()
            server_thread.join(timeout=2)
            server.server_close()

        self.addCleanup(close_server)
        endpoint = f"http://127.0.0.1:{server.server_address[1]}"
        _, adapter, _ = f["clients"](lambda *args: self.fail("legacy fallback"))
        adapter["IncompleteAnalysis"] = IncompleteAnalysis
        witness = CompletionWitness(10)
        client = adapter["BackgroundLLM"](
            witness=witness,
            provider=dialect,
            model_name="offline",
            temperature=0,
            max_tokens=100,
        )
        payload = client._prepare_completion_payload(
            [{"role": "user", "content": "synthetic request"}], None
        )
        falcon = f["compile_definitions"](
            ROOT / "ee/falcon_ai/llm_client.py", {}, names={"FalconLLMClient"}
        )
        provider = f["compile_definitions"](
            ROOT / "ee/agenthub/traceerroragent/background_provider.py",
            {
                "json": json,
                "os": SimpleNamespace(environ={}),
                "httpx": httpx,
                "SimpleNamespace": SimpleNamespace,
                "IncompleteAnalysis": IncompleteAnalysis,
                "FalconLLMClient": falcon["FalconLLMClient"],
            },
        )
        gateway = runpy.run_path(
            str(ROOT / "ee/agenthub/traceerroragent/background_gateway.py")
        )
        modules = {}
        if route in {"openai", "anthropic", "bedrock"}:
            send = provider["direct_completion"]
            kwargs = {
                "provider": route,
                "payload": payload,
                "timeout": 1,
                "api_key": "synthetic",
                "api_url": endpoint,
            }
            if route == "bedrock":

                def factory(service, **options):
                    session = botocore.session.Session()
                    session._config = {"profiles": {}}
                    actual = session.create_client(
                        service,
                        region_name="us-west-2",
                        endpoint_url=endpoint,
                        aws_access_key_id="synthetic",
                        aws_secret_access_key="synthetic",
                        config=options["config"],
                    )
                    self.addCleanup(actual.close)
                    return actual

                modules["boto3"] = b["module"]("boto3", client=factory)
        elif route == "managed":
            modules.update(g["managed_modules"](httpx.post))
            managed = modules["ee.licensing.managed_ai"]
            managed._cloud_gateway_credentials.return_value = (endpoint, "synthetic")
            send, kwargs = (
                gateway["managed_completion"],
                {"payload": {**payload, "model": "falcon_ai"}},
            )
        else:
            retained = openai.OpenAI(
                api_key="synthetic",
                base_url=endpoint + "/v1",
                max_retries=3,
                http_client=httpx.Client(trust_env=False),
            )
            self.addCleanup(retained.close)
            send, kwargs = (
                gateway["sdk_completion"],
                {"client": client._sdk(retained), **payload},
            )

        typed, errors = [], []
        with patch.dict("sys.modules", modules), analysis_transport_scope(self.guard):
            try:
                result = client._attempt(send, raw_provider=dialect, **kwargs)
                sealed = chauffeur_report(
                    result.choices[0].message.content, witness, decode=True
                )
                typed.append(require_payload(sealed, witness, "chauffeur"))
            except Exception as exc:
                errors.append(exc)
        observation = {
            "route": route,
            "variant": variant,
            "httpRequests": len(requests),
            "witnessFailed": witness.failed,
            "witnessSatisfied": witness.satisfied,
            "typedReports": len(typed),
            "alteredReport": bool(typed and typed[0] != report),
            "exceptions": [type(exc).__name__ for exc in errors],
        }
        print(json.dumps(observation), flush=True)
        self.assertEqual(server_errors, [])
        self.assertEqual(len(requests), 1, observation)
        if variant in MALFORMED:
            self.assertTrue(witness.failed, observation)
            self.assertFalse(witness.satisfied, observation)
            self.assertEqual(typed, [], observation)
            self.assertEqual(len(errors), 1, observation)
            self.assertIsInstance(errors[0], UnicodeDecodeError)
            self.assertTrue(witness.calls[-1]["exception"])
            self.assertTrue(witness.calls[-1]["ended"])
            with self.assertRaises(IncompleteAnalysis):
                witness.require()
            with self.assertRaises(IncompleteAnalysis):
                client._attempt(lambda: self.fail("sticky failure retried"))
            self.assertEqual(len(requests), 1)
        else:
            self.assertEqual(errors, [], observation)
            self.assertFalse(witness.failed)
            self.assertTrue(witness.satisfied)
            witness.require()
            self.assertEqual(typed, [report])


def regression(route, variant):
    def test(self):
        self.exercise(route, variant)

    return test


for route in ("openai", "anthropic", "managed", "sdk", "bedrock"):
    for variant in (*MALFORMED, "unicode", "fragmented"):
        setattr(StrictNativeUTF8, f"test_{route}_{variant}", regression(route, variant))


if __name__ == "__main__":
    unittest.main(verbosity=2)
