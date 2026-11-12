"""Installed HTTPX/httpcore/OpenAI/botocore against in-process HTTP servers.

No real socket, DNS, credential/config file or provider egress is permitted.
The server sees protocol bytes, not mocked high-level post/invoke results.
"""

import datetime as dt
import io
import json
import os
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import botocore.awsrequest
import botocore.credentials
import botocore.session
import httpx
import openai
from httpcore._backends.mock import MockBackend, MockStream
from httpcore._sync.connection_pool import PoolRequest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
b = runpy.run_path(str(HERE / "test_falcon_background_boundaries_offline.py"))
f = runpy.run_path(str(HERE / "analysis_llm_fixture.py"))
g = runpy.run_path(str(HERE / "analysis_gateway_fixture.py"))


class WitnessWire(unittest.TestCase):
    setUp = b["BoundaryTests"].setUp
    install = b["BoundaryTests"].install

    def exercise(self, route, phase=None, *, duplicate=False):
        self.install()
        from tracer.services.analysis_result_completion import (
            CompletionWitness,
            IncompleteAnalysis,
        )
        from tracer.services.background_transport import analysis_transport_scope

        with patch.dict(
            os.environ,
            {
                "BEDROCK_HAIKU_ARN": "bedrock/offline-haiku",
                "BEDROCK_SONNET_ARN": "bedrock/offline-sonnet",
            },
            clear=True,
        ):
            configs = runpy.run_path(
                str(ROOT / "agentic_eval/core/utils/model_config.py")
            )
        config = getattr(
            configs["ModelConfigs"],
            {
                "openai": "OPENAI_GPT_5_1",
                "anthropic": "CLAUDE_3_5_SONNET",
                "bedrock": "HAIKU_4_5_BEDROCK_ARN",
                "managed": "OPENAI_GPT_5_1",
                "sdk": "SONNET_4_5_BEDROCK_ARN",
            }[route],
        )
        base, adapter, _ = f["clients"](lambda *args: None)
        adapter["IncompleteAnalysis"] = IncompleteAnalysis
        base._prepare_completion_payload = f["compile_definitions"](
            f["LLM_PATH"],
            {"ModelConfigs": configs["ModelConfigs"], "litellm": adapter["litellm"]},
            "LLM",
            {"_prepare_completion_payload"},
        )["_prepare_completion_payload"]
        witness = CompletionWitness(10)
        client = adapter["BackgroundLLM"](
            witness=witness,
            provider=config.provider,
            model_name=config.model_name,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
        payload = client._prepare_completion_payload(
            [
                {"role": "user", "content": "WIRE_PAYLOAD_SENTINEL"},
            ],
            None,
        )
        dialect = route if route in {"bedrock", "anthropic"} else "openai"
        data = (
            {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "complete"}],
            }
            if dialect != "openai"
            else {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "complete"}}
                ]
            }
        )
        wire = json.dumps(data).encode()
        if duplicate:
            wire = wire.replace(
                b'"stop_reason":', b'"stop_reason":"max_tokens","stop_reason":'
            )
            wire = wire.replace(
                b'"finish_reason":', b'"finish_reason":"length","finish_reason":'
            )
        status = b"307 Temporary Redirect" if phase == "redirect" else b"200 OK"
        response = (
            b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\n"
            b"Location: /forbidden-hop\r\nContent-Length: "
            + str(len(wire)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + wire
        )
        writes, events, errors = [], [], []
        owner = self

        def revoke(point):
            events.append(point)
            if point == phase:
                owner.active = False

        class Stream(MockStream):
            def write(stream, buffer, timeout=None):
                writes.append(bytes(buffer))
                if buffer.startswith(b"POST "):
                    revoke("body")
                return super().write(buffer, timeout)

            def start_tls(stream, *args, **kwargs):
                revoke("tls")
                return stream

        class Server(MockBackend):
            def connect_tcp(backend, *args, **kwargs):
                revoke("connect")
                return Stream([response])

        original_init = httpx.HTTPTransport.__init__

        def transport_init(transport, *args, **kwargs):
            original_init(transport, *args, **kwargs)
            transport._pool._network_backend = Server([])

        original_wait = PoolRequest.wait_for_connection

        def wait(request, *args, **kwargs):
            connection = original_wait(request, *args, **kwargs)
            revoke("pool")
            return connection

        # Inject after connect/TLS completes but before header bytes leave.
        from httpcore._trace import Trace

        original_trace = Trace.trace

        def trace(event, name, info):
            if name.endswith("send_request_headers.started"):
                revoke("headers")
            return original_trace(event, name, info)

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
        modules, retained = {}, None

        def refresh():
            revoke("refresh")
            return {
                "access_key": "synthetic",
                "secret_key": "synthetic",
                "token": "synthetic",
                "expiry_time": (
                    dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
                ).isoformat(),
            }

        def factory(service, **options):
            session = botocore.session.Session()
            session._config = {"profiles": {}}
            actual = session.create_client(
                service,
                region_name="us-west-2",
                endpoint_url=("https" if phase == "tls" else "http")
                + "://offline.invalid",
                aws_access_key_id="synthetic",
                aws_secret_access_key="synthetic",
                config=options["config"],
            )
            self.addCleanup(actual.close)
            actual._request_signer._credentials = (
                botocore.credentials.RefreshableCredentials(
                    access_key="synthetic",
                    secret_key="synthetic",
                    token="synthetic",
                    expiry_time=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1),
                    refresh_using=refresh,
                    method="synthetic",
                )
            )
            # Registered before the product's admission: signing has completed.
            actual.meta.events.register(
                "before-send.bedrock-runtime.InvokeModel", lambda **kw: revoke("sign")
            )
            return actual

        def connect(connection):
            revoke(
                "tls"
                if isinstance(connection, botocore.awsrequest.AWSHTTPSConnection)
                else "connect"
            )

            def sendall(data):
                writes.append(bytes(data))
                if bytes(data).startswith(b"POST "):
                    revoke("body")

            connection.sock = SimpleNamespace(
                sendall=sendall,
                makefile=lambda *a, **k: io.BytesIO(response),
                settimeout=lambda *a: None,
                close=lambda: None,
                shutdown=lambda *a: None,
            )
            connection.is_verified = True

        from urllib3.connectionpool import HTTPConnectionPool

        original_get_conn = HTTPConnectionPool._get_conn
        original_output = botocore.awsrequest.AWSConnection._send_output

        def get_conn(pool, *args, **kwargs):
            connection = original_get_conn(pool, *args, **kwargs)
            revoke("pool")
            return connection

        def output(connection, *args, **kwargs):
            revoke("headers")
            return original_output(connection, *args, **kwargs)

        with (
            patch.dict(
                os.environ,
                {"HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "", "NO_PROXY": "*"},
            ),
            patch.object(httpx.HTTPTransport, "__init__", transport_init),
            patch.object(PoolRequest, "wait_for_connection", wait),
            patch.object(Trace, "trace", trace),
            patch.object(botocore.awsrequest.AWSHTTPConnection, "connect", connect),
            patch.object(botocore.awsrequest.AWSHTTPSConnection, "connect", connect),
            patch.object(HTTPConnectionPool, "_get_conn", get_conn),
            patch.object(botocore.awsrequest.AWSConnection, "_send_output", output),
        ):
            if route in {"openai", "anthropic", "bedrock"}:
                send, kwargs = (
                    provider["direct_completion"],
                    {
                        "provider": route,
                        "payload": payload,
                        "timeout": 1,
                        "api_key": "synthetic",
                        "api_url": "https://offline.invalid",
                    },
                )
                if route == "bedrock":
                    modules["boto3"] = b["module"]("boto3", client=factory)
            elif route == "managed":
                modules.update(g["managed_modules"](httpx.post))
                send, kwargs = (
                    gateway["managed_completion"],
                    {"payload": {**payload, "model": "falcon_ai"}},
                )
            else:
                retained = openai.OpenAI(
                    api_key="synthetic",
                    base_url="https://offline.invalid/v1",
                    max_retries=3,
                    http_client=httpx.Client(follow_redirects=True, trust_env=False),
                )
                self.addCleanup(retained.close)
                send, kwargs = (
                    gateway["sdk_completion"],
                    dict(client=client._sdk(retained), **payload),
                )
            with (
                patch.dict("sys.modules", modules),
                analysis_transport_scope(self.guard),
            ):
                try:
                    client._attempt(send, raw_provider=dialect, **kwargs)
                except BaseException as exc:
                    errors.append(exc)
        payload_writes = [w for w in writes if b"WIRE_PAYLOAD_SENTINEL" in w]
        rejected = phase is not None or duplicate
        if rejected:
            self.assertTrue(errors, (route, phase, events))
            self.assertTrue(witness.failed)
            self.assertFalse(witness.satisfied)
            with self.assertRaises(IncompleteAnalysis):
                witness.require()
            self.active = True
            with self.assertRaises(IncompleteAnalysis):
                client._attempt(lambda: self.fail("sticky failure sent again"))
            if duplicate:
                self.assertEqual(len(payload_writes), 1)
                self.assertIsInstance(errors[0], ValueError)
            if phase not in {None, "redirect"}:
                self.assertIn(phase, events)
                self.assertIsInstance(errors[0], self.Aborted)
                self.assertEqual(self.guard.code, "analysis_access_revoked")
                self.assertEqual(payload_writes, [], (route, phase, writes))
                self.assertEqual(
                    len([w for w in writes if w]), 1 if phase == "body" else 0
                )
            if phase == "redirect":
                self.assertEqual(len(payload_writes), 1)  # first admitted request only
                self.assertFalse(any(b"forbidden-hop" in w for w in writes))
        else:
            self.assertEqual(errors, [])
            witness.require()
            self.assertTrue(witness.satisfied)
            self.assertEqual(len(payload_writes), 1)
        if retained is not None:
            self.assertEqual(retained.max_retries, 3)
            self.assertTrue(retained._client.follow_redirects)
            self.assertNotIn("_make_status_error_from_response", vars(retained))
        print(
            json.dumps(
                {
                    "route": route,
                    "phase": phase,
                    "duplicate": duplicate,
                    "payloadWrites": len(payload_writes),
                    "writes": len(writes),
                    "failed": witness.failed,
                    "code": self.guard.code,
                }
            ),
            flush=True,
        )

    def test_sdk_copy_cannot_ignore_private_transport(self):
        self.install()
        from tracer.services.analysis_result_completion import (
            CompletionWitness,
            IncompleteAnalysis,
        )
        from tracer.services.background_transport import analysis_transport_scope

        _, adapter, _ = f["clients"](lambda *args: None)
        adapter["IncompleteAnalysis"] = IncompleteAnalysis
        witness = CompletionWitness(10)
        client = adapter["BackgroundLLM"](
            witness=witness,
            provider="openai",
            model_name="offline",
            temperature=0,
            max_tokens=10,
        )
        sends = []
        retained = SimpleNamespace(
            with_options=lambda **kwargs: SimpleNamespace(
                _client=object(),
                max_retries=0,
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        with_raw_response=SimpleNamespace(
                            create=lambda **kw: sends.append(kw)
                        )
                    )
                ),
            )
        )
        gateway = runpy.run_path(
            str(ROOT / "ee/agenthub/traceerroragent/background_gateway.py")
        )
        with (
            analysis_transport_scope(self.guard),
            self.assertRaises(IncompleteAnalysis),
        ):
            client._attempt(
                gateway["sdk_completion"], client=retained, model="offline", messages=[]
            )
        self.assertEqual(sends, [])
        self.assertTrue(witness.failed)
        self.assertFalse(witness.satisfied)
        self.assertNotIn("_make_status_error_from_response", vars(retained))

    def test_census_refuses_api_adjacent_only_mutants(self):
        census = runpy.run_path(str(HERE / "falcon_send_census.py"))
        admission = runpy.run_path(str(HERE / "falcon_send_admission.py"))
        sources, _ = census["load_sources"]()
        prefix = "ee.agenthub.traceerroragent."
        for module, needle in (
            (prefix + "background_provider", ", **sync_client_options()"),
            (prefix + "background_gateway", ", **sync_client_options()"),
            (prefix + "background_provider", "guard_bedrock(client)"),
            (prefix + "background_wire", "before_send()"),
            (prefix + "background_gateway", "follow_redirects=False"),
        ):
            with self.subTest(module=module, removal=needle):
                replacement = (
                    "follow_redirects=True"
                    if needle == "follow_redirects=False"
                    else (
                        "pass"
                        if needle in {"guard_bedrock(client)", "before_send()"}
                        else ""
                    )
                )
                changed = {
                    **sources,
                    module: sources[module].replace(needle, replacement),
                }
                self.assertNotEqual(changed[module], sources[module])
                _, errors = census["audit"](
                    changed,
                    admission["admission"],
                    admission["EXCEPTIONS"],
                    admission["EXCEPTION_COUNTS"],
                )
                self.assertTrue(errors, (module, needle))

    def test_httpx_wire_refusal_matrix(self):
        for route in ("openai", "anthropic", "managed", "sdk"):
            for phase in ("pool", "connect", "tls", "headers", "body"):
                with self.subTest(route=route, phase=phase):
                    self.setUp()
                    self.exercise(route, phase)

    def test_bedrock_wire_refusal_matrix(self):
        for phase in ("refresh", "sign", "pool", "connect", "tls", "headers", "body"):
            with self.subTest(phase=phase):
                self.setUp()
                self.exercise("bedrock", phase)

    def test_no_route_follows_307(self):
        for route in ("openai", "anthropic", "managed", "sdk", "bedrock"):
            with self.subTest(route=route):
                self.setUp()
                self.exercise(route, "redirect")

    def test_real_configs_healthy_and_original_bytes(self):
        for route in ("openai", "anthropic", "managed", "sdk", "bedrock"):
            for duplicate in (False, True):
                with self.subTest(route=route, duplicate=duplicate):
                    self.setUp()
                    self.exercise(route, duplicate=duplicate)


if __name__ == "__main__":
    unittest.main()
