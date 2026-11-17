"""Real admission + raw transports + witness; synthetic wire, no credentials/I/O."""

import io
import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
b = runpy.run_path(str(HERE / "test_falcon_background_boundaries_offline.py"))
f = runpy.run_path(str(HERE / "analysis_llm_fixture.py"))
g = runpy.run_path(str(HERE / "analysis_gateway_fixture.py"))


class RevocationWitness(unittest.TestCase):
    setUp = b["BoundaryTests"].setUp
    install = b["BoundaryTests"].install

    def exercise(self, route, revoke):
        self.install()
        from tracer.services.analysis_result_completion import (
            CompletionWitness,
            IncompleteAnalysis,
        )
        from tracer.services.background_transport import analysis_transport_scope

        _, adapter, _ = f["clients"](lambda *a: None)
        adapter["IncompleteAnalysis"] = IncompleteAnalysis
        witness = CompletionWitness(10)
        client = adapter["BackgroundLLM"](
            witness=witness,
            provider="openai",
            model_name="offline",
            temperature=0,
            max_tokens=10,
        )
        sent, published = [], []
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

        def http_send(request):
            sent.append(route)
            return httpx.Response(200, content=wire, request=request)

        raw_http = SimpleNamespace(
            Client=lambda **kw: httpx.Client(
                transport=httpx.MockTransport(http_send), **kw
            )
        )
        falcon = f["compile_definitions"](
            ROOT / "ee/falcon_ai/llm_client.py", {}, names={"FalconLLMClient"}
        )
        provider = f["compile_definitions"](
            ROOT / "ee/agenthub/traceerroragent/background_provider.py",
            {
                "json": json,
                "SimpleNamespace": SimpleNamespace,
                "FalconLLMClient": falcon["FalconLLMClient"],
                "IncompleteAnalysis": IncompleteAnalysis,
                "httpx": raw_http,
                "os": SimpleNamespace(environ={}),
            },
        )
        payload = {
            "model": "offline",
            "messages": [{"role": "user", "content": "synthetic"}],
            "max_tokens": 10,
        }
        modules = {}
        gateway = runpy.run_path(
            str(ROOT / "ee/agenthub/traceerroragent/background_gateway.py")
        )
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

                def invoke(**kwargs):
                    sent.append(route)
                    return {"body": io.BytesIO(wire)}

                modules["boto3"] = b["module"](
                    "boto3",
                    client=lambda *a, **k: f["bedrock_double"](invoke_model=invoke),
                )
                modules["botocore.config"] = b["module"]("config", Config=lambda **k: k)
        elif route == "managed":

            def post(url, **kwargs):
                return http_send(httpx.Request("POST", url))

            modules.update(g["managed_modules"](post))
            send, kwargs = (
                gateway["managed_completion"],
                {"payload": {**payload, "model": "falcon_ai"}},
            )
        else:

            def create(**kwargs):
                return SimpleNamespace(
                    http_response=http_send(
                        httpx.Request("POST", "https://offline.invalid")
                    )
                )

            scoped = f["ScopedGateway"](
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        with_raw_response=SimpleNamespace(create=create)
                    )
                )
            )
            send, kwargs = gateway["sdk_completion"], dict(client=scoped, **payload)

        transport_send = send

        def dispatch(**kwargs):
            if revoke == "boundary":
                self.active = False  # after outer admission, before raw send
            return transport_send(**kwargs)

        with patch.dict("sys.modules", modules), analysis_transport_scope(self.guard):

            def attempt():
                return client._attempt(dispatch, raw_provider=dialect, **kwargs)

            if revoke == "before":
                self.active = False
            elif revoke == "between":
                attempt()
                self.assertTrue(witness.satisfied)
                self.active = False
            if revoke:
                with self.assertRaises(self.Aborted):
                    attempt()
                self.assertEqual(self.guard.code, "analysis_access_revoked")
                self.assertTrue(witness.failed)
                self.assertFalse(witness.satisfied)
                self.assertTrue(witness.calls[-1]["exception"])
                with self.assertRaises(IncompleteAnalysis):
                    witness.require()
                    published.append("result")
                self.active = True
                with self.assertRaises(IncompleteAnalysis):
                    attempt()
                self.assertEqual(sent, [route] if revoke == "between" else [])
                self.assertEqual(published, [])
            else:
                attempt()
                witness.require()
                published.append("result")
                self.assertFalse(witness.failed)
                self.assertEqual(
                    witness.calls[-1]["raw_finish"],
                    "end_turn" if dialect != "openai" else "stop",
                )
                self.assertEqual(sent, [route])
                self.assertEqual(published, ["result"])

    def test_each_transport_revokes_before_send_without_publication(self):
        for route in ("openai", "anthropic", "bedrock", "managed", "sdk"):
            with self.subTest(route=route):
                self.setUp()
                self.exercise(route, "before")

    def test_each_raw_boundary_rechecks_after_outer_admission(self):
        for route in ("openai", "anthropic", "bedrock", "managed", "sdk"):
            with self.subTest(route=route):
                self.setUp()
                self.exercise(route, "boundary")

    def test_each_transport_revokes_between_attempts(self):
        for route in ("openai", "anthropic", "bedrock", "managed", "sdk"):
            with self.subTest(route=route):
                self.setUp()
                self.exercise(route, "between")

    def test_each_transport_healthy_witness_completes(self):
        for route in ("openai", "anthropic", "bedrock", "managed", "sdk"):
            with self.subTest(route=route):
                self.setUp()
                self.exercise(route, None)

    def test_managed_explicit_deadline_is_not_widened_to_guard_budget(self):
        import time

        self.install()
        from tracer.services.background_transport import analysis_transport_scope

        _, adapter, evidence = f["clients"](lambda *a: None)
        witness = evidence["CompletionWitness"](10)
        client = adapter["BackgroundLLM"](
            witness=witness,
            provider="openai",
            model_name="falcon_ai",
            temperature=0,
            max_tokens=10,
        )
        client._requires_managed_transport = lambda model: True
        timeouts = []

        def post(url, **kwargs):
            timeouts.append(kwargs["timeout"])
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": "complete"}}
                    ]
                },
                request=httpx.Request("POST", url),
            )

        with (
            patch.dict("sys.modules", g["managed_modules"](post)),
            analysis_transport_scope(self.guard),
        ):
            client._try_gateway_completion(
                {"model": "falcon_ai"}, deadline_monotonic=time.monotonic() + 0.5
            )
            self.assertGreater(timeouts[0], 0)
            self.assertLessEqual(timeouts[0], 0.5)
            self.assertTrue(witness.satisfied)
            with self.assertRaises(RuntimeError):
                client._try_gateway_completion(
                    {"model": "falcon_ai"}, deadline_monotonic=time.monotonic() - 1
                )
            self.assertEqual(len(timeouts), 1)
            self.assertTrue(witness.failed)
            self.assertFalse(witness.satisfied)

    def test_census_proves_each_wire_gate_and_rejects_removed_gate(self):
        import ast

        census = runpy.run_path(str(HERE / "falcon_send_census.py"))
        admission = runpy.run_path(str(HERE / "falcon_send_admission.py"))
        sources, _ = census["load_sources"]()
        for module, owner, call in admission["WITNESSED_SENDS"]:
            with self.subTest(module=module, call=call):
                matches = [
                    s
                    for s in census["census"](sources[module])
                    if s["owner"] == owner and s["call"] == call
                ]
                self.assertEqual(len(matches), 1)
                site = matches[0]
                self.assertIn("GUARDED", admission["admission"](module, site, sources))
                # Remove just THIS wire's immediately preceding admission, not
                # all earlier checks. An old successful check must not suffice.
                for node, parent in census["ancestors"](site):
                    statements = next(
                        (
                            v
                            for v in vars(parent).values()
                            if isinstance(v, list) and node in v
                        ),
                        None,
                    )
                    if statements is not None:
                        statements[statements.index(node) - 1] = ast.Pass()
                        break
                changed = ast.unparse(site["tree"])
                candidate = next(
                    s
                    for s in census["census"](changed)
                    if s["owner"] == owner and s["call"] == call
                )
                self.assertIsNone(
                    admission["admission"](
                        module, candidate, {**sources, module: changed}
                    )
                )

    def test_admission_consumes_no_response_body(self):
        self.install()
        # Admission has only the guard/timeout, not a response argument. A body
        # access tripwire stays untouched on both healthy and denied admission.
        from tracer.services.background_transport import (
            analysis_transport_scope,
            before_send,
        )

        class Response:
            @property
            def content(self):
                raise AssertionError("admission consumed response")

        response = Response()
        with analysis_transport_scope(self.guard):
            self.assertGreater(before_send(1), 0)
            self.active = False
            with self.assertRaises(self.Aborted):
                before_send(1)
        self.assertIsInstance(response, Response)


if __name__ == "__main__":
    unittest.main()
