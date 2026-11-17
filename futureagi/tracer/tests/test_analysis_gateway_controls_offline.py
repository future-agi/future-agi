"""Managed raw-body controls: real configs, auth parity and foreground isolation."""

import json
import os
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
g = runpy.run_path(str(HERE / "analysis_gateway_fixture.py"))
payloads = runpy.run_path(str(HERE / "analysis_payload_fixture.py"))


class GatewayControls(unittest.TestCase):
    def make(self, variant="none", *, cloud=True, status=200, opaque=False):
        with patch.dict(os.environ, {}, clear=True):
            config = runpy.run_path(
                str(ROOT / "agentic_eval/core/utils/model_config.py")
            )["ModelConfigs"]
        self.requests, self.responses = [], []
        raw = {
            "choices": [{"finish_reason": "stop", "message": {"content": "complete"}}]
        }

        def post(url, **kwargs):
            self.requests.append((url, kwargs))
            wire = g["envelope"](raw, variant)
            if opaque:
                response = SimpleNamespace(
                    status_code=200,
                    json=lambda: json.loads(wire),
                    raise_for_status=lambda: None,
                    close=lambda: None,
                )
            else:
                response = httpx.Response(
                    status, content=wire.encode(), request=httpx.Request("POST", url)
                )
                response.json = lambda: self.fail(
                    "background decoded through response.json"
                )
            self.responses.append(response)
            return response

        modules = g["managed_modules"](post, cloud=cloud)
        modules["ee.usage.deployment"].DeploymentMode.is_ee = lambda: True
        base, ns, evidence = g["f"]["clients"](
            lambda *a: self.fail("unexpected native fallback")
        )
        methods = g["f"]["compile_definitions"](
            g["f"]["LLM_PATH"],
            {"ModelConfigs": config, "litellm": ns["litellm"]},
            "LLM",
            {"_requires_managed_transport", "_prepare_completion_payload"},
        )
        base._requires_managed_transport = methods["_requires_managed_transport"]
        base._prepare_completion_payload = methods["_prepare_completion_payload"]
        modules.update(
            payloads["modules"](
                {
                    "tracer.services.analysis_result_completion": g["module"](
                        "completion", **evidence
                    )
                }
            )
        )
        isolation = patch.dict("sys.modules", modules)
        isolation.start()
        self.addCleanup(isolation.stop)
        witness = evidence["CompletionWitness"](4)
        selected = config.TURING_LARGE
        client = ns["BackgroundLLM"](
            witness=witness,
            provider=selected.provider,
            model_name=selected.model_name,
            max_tokens=selected.max_tokens,
            temperature=selected.temperature,
        )
        self.assertEqual(client.provider, "turing")
        return client, witness, modules

    def test_real_managed_model_config_completes(self):
        client, witness, _ = self.make()
        self.assertEqual(client._get_completion_content([]), "complete")
        self.assertTrue(witness.satisfied)
        self.assertEqual(len(witness.calls), len(self.requests))
        self.assertEqual(self.requests[0][1]["json"]["model"], client.model_name)
        self.assertFalse(self.requests[0][1]["follow_redirects"])

    def test_duplicate_body_is_sticky_in_shared_managed_client(self):
        client, witness, _ = self.make("terminal")
        with self.assertRaises((ValueError, RuntimeError)):
            client._get_completion_content([])
        self.assertTrue(witness.failed)
        with self.assertRaises(RuntimeError):
            client._get_completion_content([])
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(witness.satisfied)

    def test_nonfinite_and_trailing_bodies_are_not_recovered(self):
        for variant in ("constant", "trailing"):
            with self.subTest(variant=variant):
                client, witness, _ = self.make(variant)
                with self.assertRaises((ValueError, RuntimeError)):
                    client._get_completion_content([])
                self.assertTrue(witness.failed)
                self.assertEqual(len(self.requests), 1)
                self.assertTrue(self.responses[0].is_closed)

    def test_unavailable_original_bytes_fail_closed(self):
        client, witness, _ = self.make(opaque=True)
        with self.assertRaises(RuntimeError):
            client._get_completion_content([])
        self.assertTrue(witness.failed)
        self.assertFalse(witness.satisfied)
        self.assertEqual(len(self.requests), 1)

    def test_selfhost_rechecks_license_and_preserves_timeout(self):
        client, witness, modules = self.make(cloud=False)
        self.assertEqual(client._get_completion_content([]), "complete")
        self.assertTrue(witness.satisfied)
        modules[
            "ee.licensing.activation_client"
        ].require_configured_license_key.assert_called_once()
        self.assertEqual(self.requests[0][1]["timeout"], 30.0)

    def test_unauthorized_invalidates_only_selfhost_without_retry(self):
        for cloud, code in ((True, "GATEWAY_UNAUTHORIZED"), (False, "TOKEN_EXPIRED")):
            with self.subTest(cloud=cloud):
                client, witness, modules = self.make(cloud=cloud, status=401)
                with self.assertRaises(Exception) as caught:
                    client._get_completion_content([])
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(witness.failed)
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(
                    modules[
                        "ee.licensing.activation_client"
                    ].invalidate_token.call_count,
                    0 if cloud else 1,
                )
                self.assertTrue(self.responses[0].is_closed)

    def test_managed_error_content_never_reaches_evidence_decoder(self):
        for cloud in (True, False):
            for status in (302, 400, 401, 429, 500, 503):
                with self.subTest(cloud=cloud, status=status):
                    client, witness, modules = self.make(
                        "terminal", cloud=cloud, status=status
                    )
                    gateway = modules["ee.agenthub.traceerroragent.background_gateway"]
                    with patch.object(
                        gateway, "strict_response", wraps=gateway.strict_response
                    ) as strict:
                        with self.assertRaises(
                            (
                                httpx.HTTPStatusError,
                                modules[
                                    "ee.licensing.activation_client"
                                ].ManagedServiceError,
                            )
                        ):
                            client._get_completion_content([])
                        strict.assert_not_called()
                    self.assertTrue(witness.failed)
                    self.assertFalse(witness.satisfied)
                    with self.assertRaises(RuntimeError):
                        client._get_completion_content([])
                    self.assertEqual(len(self.requests), 1)
                    self.assertTrue(self.responses[0].is_closed)

    def test_removed_license_prevents_send(self):
        client, witness, modules = self.make(cloud=False)
        activation = modules["ee.licensing.activation_client"]
        activation.require_configured_license_key.side_effect = (
            activation.ManagedServiceError("NO_ENTERPRISE_LICENSE", "synthetic denial")
        )
        with self.assertRaises(activation.ManagedServiceError):
            client._get_completion_content([])
        self.assertTrue(witness.failed)
        self.assertEqual(self.requests, [])

    def test_foreground_managed_parser_unchanged(self):
        raw = {
            "choices": [{"finish_reason": "stop", "message": {"content": "complete"}}]
        }
        wire = g["envelope"](raw, "terminal")
        modules = g["managed_modules"](
            lambda url, **kw: httpx.Response(
                200, text=wire, request=httpx.Request("POST", url)
            )
        )
        with patch.dict("sys.modules", modules):
            result = modules["ee.licensing.managed_ai"].chat_completion(
                {"model": "falcon_ai", "messages": []}
            )
        self.assertEqual(result["choices"][0]["finish_reason"], "stop")


class NativeHTTPErrors(unittest.TestCase):
    def test_native_http_error_content_never_reaches_evidence_decoder(self):
        for dialect in ("openai", "anthropic"):
            for status in (302, 400, 401, 429, 500, 503):
                with self.subTest(dialect=dialect, status=status):
                    requests, reads = [], []

                    class Response(httpx.Response):
                        @property
                        def text(self, reads=reads):
                            reads.append("text")
                            return super().text

                        def json(self, reads=reads, **kwargs):
                            reads.append("json")
                            return super().json(**kwargs)

                    def handler(
                        request, requests=requests, status=status, response=Response
                    ):
                        requests.append(request)
                        return response(
                            status, content=b'{"message":"first","message":"second"}'
                        )

                    _, ns, evidence = g["f"]["clients"](lambda *a: None)
                    witness = evidence["CompletionWitness"](4)
                    adapter = ns["BackgroundLLM"].__new__(ns["BackgroundLLM"])
                    adapter._witness, adapter._key = witness, object()
                    falcon = g["f"]["compile_definitions"](
                        ROOT / "ee/falcon_ai/llm_client.py",
                        {"json": json},
                        names={"FalconLLMClient"},
                    )
                    with httpx.MockTransport(handler) as transport:
                        http = httpx.Client(transport=transport)
                        self.addCleanup(http.close)
                        provider = g["f"]["compile_definitions"](
                            ROOT / "ee/agenthub/traceerroragent/background_provider.py",
                            {
                                "json": json,
                                "os": SimpleNamespace(environ={}),
                                "FalconLLMClient": falcon["FalconLLMClient"],
                                "IncompleteAnalysis": evidence["IncompleteAnalysis"],
                                "httpx": SimpleNamespace(
                                    Client=lambda http=http, **kw: http
                                ),
                            },
                        )
                        modules = payloads["modules"](
                            {
                                "tracer.services.analysis_result_completion": g[
                                    "module"
                                ]("completion", **evidence)
                            }
                        )
                        with patch.dict("sys.modules", modules):
                            with self.assertRaises(httpx.HTTPStatusError):
                                adapter._attempt(
                                    provider["direct_completion"],
                                    raw_provider=dialect,
                                    provider=dialect,
                                    timeout=1,
                                    api_key="synthetic",
                                    api_url="https://offline.invalid",
                                    payload={
                                        "model": "offline",
                                        "max_tokens": 8,
                                        "messages": [],
                                    },
                                )
                    self.assertTrue(witness.failed)
                    self.assertFalse(witness.satisfied)
                    self.assertTrue(witness.calls[0]["exception"])
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(reads, [])
                    with self.assertRaises(RuntimeError):
                        adapter._attempt(
                            lambda requests=requests: requests.append("forbidden retry")
                        )
                    self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
