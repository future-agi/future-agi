"""Real HTTP parsers cannot discard ambiguous gateway evidence."""

import ast
import json
import os
import runpy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
g = runpy.run_path(str(HERE / "analysis_gateway_fixture.py"))
p = runpy.run_path(str(HERE / "test_analysis_provider_attempts_offline.py"))
r = runpy.run_path(str(HERE / "test_analysis_raw_providers_offline.py"))


class ManagedWire(r["RawFalcon"]):
    variant = "none"
    cloud = True

    def install(self):
        deployment = self.modules.get("ee.usage.deployment")
        if deployment is not None:
            deployment.DeploymentMode.is_cloud = lambda: self.cloud
            deployment.DeploymentMode.is_ee = lambda: True
        super().install()

    def worker(self, boundary):
        send = self._raw_managed_response
        self.wires, self.parses = [], []

        def post(url, **kwargs):
            wire = g["envelope"](send(kwargs["json"]), self.variant)
            self.wires.append(wire)
            response = httpx.Response(
                200, text=wire, request=httpx.Request("POST", url)
            )
            original = response.json

            def parsed(*a, **kw):
                self.parses.append("legacy-json")
                return original(*a, **kw)

            response.json = parsed
            return response

        self.modules.update(g["managed_modules"](post, cloud=self.cloud))
        self.modules.update(g["load_gateway"]({}))
        # Falcon's managed identity is falcon_ai; this is routing, not a provider substitute.
        self.agent_runner.agent.llm_client.model = "falcon_ai"
        return super().worker(boundary)

    def check(self, variant, cloud=True):
        self.variant, self.cloud = variant, cloud
        result, effects, marker, attempts, _ = self.replay_raw("managed", "stop")
        witness = self.agent_runner.agent.llm_client._witness
        if variant == "none":
            self.assertTrue(result["success"], result)
            self.assertTrue(witness.satisfied)
            self.assertEqual(
                effects, ["completed", "embedding", "success-log", "cluster"]
            )
            marker.complete.assert_called_once()
        else:
            self.assertTrue(self.wires)
            if variant in {"terminal", "arguments"}:
                self.assertTrue(
                    any(
                        (
                            '"finish_reason": "length", "finish_reason"'
                            if variant == "terminal"
                            else '"arguments": "{}", "arguments"'
                        )
                        in wire
                        for wire in self.wires
                    )
                )
            self.assertFalse(result["success"], result)
            self.assertTrue(witness.failed)
            self.assertFalse(witness.satisfied)
            self.assertEqual(effects, [])
            self.assertEqual(self.saved, [])
            marker.complete.assert_not_called()
            marker.abort.assert_called_once()
        self.assertEqual(len(witness.calls), len(attempts))
        self.assertEqual(
            self.parses, [], "background bypasses the lossy managed parser"
        )

    def test_managed_duplicate_terminal_aborts(self):
        self.check("terminal")

    def test_managed_duplicate_arguments_aborts(self):
        self.check("arguments")

    def test_managed_exact_reviewer_final_terminal_aborts(self):
        self.check("final-terminal")

    def test_managed_unique_body_completes(self):
        self.check("none")

    def test_selfhost_duplicate_terminal_aborts(self):
        self.check("terminal", cloud=False)

    def test_selfhost_duplicate_arguments_aborts(self):
        self.check("arguments", cloud=False)

    def test_selfhost_unique_body_completes(self):
        self.check("none", cloud=False)


class SDKWire(p["ProviderAttempts"]):
    def gateway(self, variant="none", legacy=False, status=200, opaque=False):
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
        original_clients = p["fixture"]["clients"]
        original_definition = p["original_definition"]
        self.wires, self.parses, self.configured, self.requests = [], [], [], []
        clients = []

        def make_clients(send):
            base, ns, evidence = original_clients(send)
            base._prepare_completion_payload = p["fixture"]["compile_definitions"](
                p["fixture"]["LLM_PATH"],
                {"ModelConfigs": configs["ModelConfigs"], "litellm": ns["litellm"]},
                "LLM",
                {"_prepare_completion_payload"},
            )["_prepare_completion_payload"]
            return base, ns, evidence

        def definition(path, name, namespace, parent=None):
            if name == "__init__" and parent in {"JudgeAgent", "ChauffeurAgent"}:
                namespace["ModelConfigs"] = configs["ModelConfigs"]
                self.configured.append(
                    configs["ModelConfigs"].SONNET_4_5_BEDROCK_ARN.provider
                )
            if name == "run" and parent == "JudgeAgent":
                tree = ast.parse((ROOT / path).read_text())
                namespace["JUDGE_TOOLS"] = next(
                    ast.literal_eval(n.value)
                    for n in tree.body
                    if isinstance(n, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "JUDGE_TOOLS"
                        for t in n.targets
                    )
                )
            return original_definition(path, name, namespace, parent)

        def factory(send):
            def handler(request):
                payload = json.loads(request.content)
                self.requests.append(payload)
                wire = g["envelope"](send("gateway", payload), variant)
                self.wires.append(wire)
                response = httpx.Response(
                    status, text=wire, headers={"content-type": "application/json"}
                )
                original = response.json

                def parsed(*a, **kw):
                    self.parses.append("sdk-json")
                    return original(*a, **kw)

                response.json = parsed
                return response

            class ObservedOpenAI(openai.OpenAI):
                def _make_status_error_from_response(sdk, response):
                    self.parses.append("sdk-error-json")
                    return super()._make_status_error_from_response(response)

            client = ObservedOpenAI(
                api_key="synthetic",
                base_url="https://offline.invalid/v1",
                http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                max_retries=3,
            )
            clients.append(client)
            original_init = httpx.HTTPTransport.__init__

            def init_transport(transport, *args, **kwargs):
                original_init(transport, *args, **kwargs)
                transport.handle_request = handler

            transport_patch = patch.object(
                httpx.HTTPTransport, "__init__", init_transport
            )
            transport_patch.start()
            self.addCleanup(transport_patch.stop)
            options = []

            def with_options(**kwargs):
                options.append(kwargs)
                if opaque:
                    return SimpleNamespace(
                        chat=SimpleNamespace(
                            completions=SimpleNamespace(
                                create=client.chat.completions.create
                            )
                        )
                    )
                return client.with_options(**kwargs)

            # Foreground uses .chat directly; background must select a zero-retry copy.
            return SimpleNamespace(
                with_options=with_options, options=options, chat=client.chat
            )

        gateway_modules = g["load_gateway"]({})
        gateway_module = gateway_modules[
            "ee.agenthub.traceerroragent.background_gateway"
        ]
        gateway_globals = gateway_module.sdk_completion.__globals__
        strict = gateway_globals["strict_response"]

        def success_only(response):
            self.strict_statuses.append(response.status_code)
            if response.status_code >= 300:
                self.error_decodes.append(response.status_code)
            return strict(response)

        self.error_decodes, self.strict_statuses = [], []
        gateway_globals["strict_response"] = success_only
        try:
            with (
                patch.dict(p["fixture"], {"Gateway": factory, "clients": make_clients}),
                patch.dict(
                    p["ProviderAttempts"].replay.__globals__,
                    {"original_definition": definition},
                ),
                patch.dict(sys.modules, gateway_modules),
            ):
                result = self.replay(gateway=True, legacy=legacy)
            self.assertEqual(set(self.configured), {"aws_bedrock_anthropic"})
            self.assertTrue(all(client.max_retries == 3 for client in clients))
            self.assertTrue(
                all(
                    "_make_status_error_from_response" not in vars(client)
                    for client in clients
                )
            )
            return result
        finally:
            for client in clients:
                client.close()

    def refused(self, variant, **kwargs):
        result, _, cache, attempts, witnesses, _ = self.gateway(variant, **kwargs)
        self.assertFalse(result["success"], result)
        self.assertEqual(self.state.get_state(self.trace).status, "aborted")
        self.assertIsNone(self.state.get_state(self.trace).result_analysis_id)
        self.assertTrue(witnesses[0].failed)
        self.assertFalse(witnesses[0].satisfied)
        self.assertEqual(self.parents, [])
        self.assertEqual(self.children, [])
        self.assertEqual(self.patterns, [])
        cache.clear.assert_not_called()
        self.assertEqual(self.parses, [])
        self.assertEqual(self.error_decodes, [])
        if not kwargs.get("opaque"):
            self.assertEqual(len(witnesses[0].calls), len(attempts))
            self.assertTrue(self.wires)

    def test_sdk_duplicate_terminal_aborts(self):
        self.refused("terminal")

    def test_sdk_duplicate_arguments_aborts(self):
        self.refused("arguments")

    def test_sdk_exact_reviewer_final_terminal_aborts(self):
        self.refused("final-terminal")

    def test_sdk_unique_body_completes(self):
        result, _, cache, attempts, witnesses, gateways = self.gateway()
        self.assertTrue(result["success"], result)
        self.assertEqual(self.state.get_state(self.trace).status, "completed")
        self.assertTrue(witnesses[0].satisfied)
        self.assertEqual(len(self.parents), 1)
        self.assertEqual(len(self.children), 1)
        self.assertEqual(len(self.patterns), 1)
        self.assertEqual(len(witnesses[0].calls), len(attempts))
        self.assertEqual(self.parses, [])
        self.assertEqual(self.strict_statuses, [200, 200, 200])
        self.assertTrue(
            all(o["max_retries"] == 0 for gateway in gateways for o in gateway.options)
        )
        cache.clear.assert_called_once()
        self.assertEqual(
            [v["model"] for v in self.requests],
            [
                "bedrock/offline-haiku",
                "bedrock/offline-sonnet",
                "bedrock/offline-sonnet",
            ],
        )
        system = self.requests[1]["messages"][0]["content"]
        self.assertIsInstance(system, list)
        self.assertEqual(system[0]["cache_control"], {"type": "ephemeral"})

    def test_sdk_error_body_never_parsed_or_retried(self):
        for status in (400, 401, 429, 500, 503):
            with self.subTest(status=status):
                self.refused("terminal", status=status)
                self.assertEqual(len(self.wires), 1)

    def test_sdk_opaque_client_refused_before_send(self):
        self.refused("none", opaque=True)
        self.assertEqual(self.wires, [])

    def test_sdk_distinct_generation_recovers(self):
        self.refused("arguments")
        first = self.state.get_state(self.trace).generation
        result, _, _, _, witnesses, _ = self.gateway()
        self.assertTrue(result["success"], result)
        self.assertNotEqual(self.state.get_state(self.trace).generation, first)
        self.assertTrue(witnesses[0].satisfied)
        self.assertEqual(len(self.patterns), 1)

    def test_sdk_foreground_parser_remains_unchanged(self):
        result, _, _, _, witnesses, _ = self.gateway("arguments", legacy=True)
        self.assertTrue(result["success"], result)
        self.assertEqual(witnesses, [])
        self.assertTrue(self.parses)
        self.assertEqual(len(self.patterns), 1)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        cls(name)
        for cls in (ManagedWire, SDKWire)
        for name in cls.__dict__
        if name.startswith("test_")
    )


if __name__ == "__main__":
    unittest.main()
