"""Native Bedrock worker using real model configs and cached Judge payloads."""

import ast
import io
import json
import os
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
p = runpy.run_path(str(HERE / "test_analysis_provider_attempts_offline.py"))
f = p["fixture"]


class RealConfigs(p["ProviderAttempts"]):
    def native(self, failure=None, wire_mutator=None, client_factory=None):
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
        self.configs = configs
        original_clients = f["clients"]
        original_definition = p["original_definition"]
        requests, client_options = [], []
        module = p["j"]["module"]
        native_send = {}

        def initialize(service, **kwargs):
            self.assertEqual(service, "bedrock-runtime")
            client_options.append(kwargs)
            self.assertEqual(kwargs["config"].retries["total_max_attempts"], 1)

            def invoke_model(**request):
                requests.append(request)
                body = json.loads(request["body"])
                raw = native_send["call"](
                    provider="bedrock", payload={**body, "model": request["modelId"]}
                )
                wire = (
                    wire_mutator(raw) if wire_mutator is not None else json.dumps(raw)
                )
                return {"body": io.BytesIO(wire.encode())}

            if client_factory is not None:
                return client_factory(service, kwargs, invoke_model)
            return f["bedrock_double"](invoke_model=invoke_model)

        def clients(send):
            base, ns, evidence = original_clients(send)
            native_send["call"] = ns["direct_completion"]
            falcon = f["compile_definitions"](
                ROOT / "ee/falcon_ai/llm_client.py",
                {"json": json},
                names={"FalconLLMClient"},
            )
            provider = f["compile_definitions"](
                ROOT / "ee/agenthub/traceerroragent/background_provider.py",
                {
                    "json": json,
                    "os": SimpleNamespace(environ={}),
                    "SimpleNamespace": SimpleNamespace,
                    "FalconLLMClient": falcon["FalconLLMClient"],
                    "IncompleteAnalysis": evidence["IncompleteAnalysis"],
                },
            )
            ns["direct_completion"] = provider["direct_completion"]
            if "provider_dialect" in provider:
                ns["provider_dialect"] = provider["provider_dialect"]
            # Execute the real shared payload builder, not the permissive fixture.
            payload_ns = {
                "ModelConfigs": configs["ModelConfigs"],
                "litellm": ns["litellm"],
            }
            base._prepare_completion_payload = f["compile_definitions"](
                f["LLM_PATH"], payload_ns, "LLM", {"_prepare_completion_payload"}
            )["_prepare_completion_payload"]
            init = base.__init__

            def with_region(self, **kwargs):
                init(self, **kwargs)
                self.provider_api_keys = {
                    "aws_bedrock_anthropic": {"aws_region": "eu-west-1"}
                }

            base.__init__ = with_region
            return base, ns, evidence

        def definition(path, name, ns, parent=None):
            if name == "__init__" and parent in {"JudgeAgent", "ChauffeurAgent"}:
                ns["ModelConfigs"] = configs["ModelConfigs"]
            if name == "run" and parent == "JudgeAgent":
                tree = ast.parse((ROOT / path).read_text())
                ns["JUDGE_TOOLS"] = next(
                    ast.literal_eval(node.value)
                    for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "JUDGE_TOOLS"
                        for target in node.targets
                    )
                )
            return original_definition(path, name, ns, parent)

        with (
            patch.dict(f, {"clients": clients}),
            patch.dict(
                p["ProviderAttempts"].replay.__globals__,
                {"original_definition": definition},
            ),
            patch.dict("sys.modules", {"boto3": module("boto3", client=initialize)}),
        ):
            result = self.replay(failure)
        return result, requests, client_options

    def test_real_configs_native_success(self):
        (result, _, cache, attempts, witnesses, _), requests, options = self.native()
        self.assertTrue(result["success"], result)
        self.assertEqual(attempts, ["chauffeur", "judge", "judge"])
        self.assertEqual(len(witnesses[0].calls), len(requests))
        self.assertTrue(witnesses[0].satisfied)
        self.assertEqual(len(self.patterns), 1)
        cache.clear.assert_called_once()
        self.assertEqual(
            [r["modelId"] for r in requests],
            ["offline-haiku", "offline-sonnet", "offline-sonnet"],
        )
        self.assertTrue(all(o["region_name"] == "eu-west-1" for o in options))
        judge = json.loads(requests[1]["body"])
        self.assertIsInstance(judge["system"], list)
        self.assertEqual(judge["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertTrue(judge["system"][0]["text"])
        self.assertTrue(judge["tools"])
        self.assertEqual(
            judge["max_tokens"],
            min(
                16000,
                self.configs["ModelConfigs"].get_max_tokens(
                    self.configs["ModelConfigs"].SONNET_4_5_BEDROCK_ARN.model_name
                ),
            ),
        )

    def test_real_configs_distinct_generation_recovers(self):
        (failed, _, cache, _, witnesses, _), _, _ = self.native("judge")
        self.assertFalse(failed["success"])
        state = self.state.get_state(self.trace)
        first_generation = state.generation
        self.assertEqual(state.status, "aborted")
        self.assertEqual(self.parents, [])
        self.assertEqual(self.patterns, [])
        cache.clear.assert_not_called()
        self.assertTrue(witnesses[0].failed)
        (result, _, _, _, recovered, _), requests, _ = self.native()
        self.assertTrue(result["success"], result)
        self.assertNotEqual(
            self.state.get_state(self.trace).generation, first_generation
        )
        self.assertEqual(len(self.patterns), 1)
        self.assertTrue(recovered[0].satisfied)
        self.assertIsNot(recovered[0], witnesses[0])
        self.assertEqual(len(requests), 3)


class ProviderConfigCensus(unittest.TestCase):
    def test_all_real_config_spellings_are_explicitly_classified(self):
        with patch.dict(os.environ, {}, clear=True):
            config = runpy.run_path(
                str(ROOT / "agentic_eval/core/utils/model_config.py")
            )
        provider = f["compile_definitions"](
            ROOT / "ee/agenthub/traceerroragent/background_provider.py",
            {"IncompleteAnalysis": RuntimeError},
        )
        routes = provider["PROVIDER_ROUTES"]
        self.assertIsInstance(routes, tuple)
        expected = {member.value for member in config["LiteLlmProvider"]} | {"bedrock"}
        self.assertEqual({name for name, _ in routes}, expected)
        configured = {
            value.provider
            for value in vars(config["ModelConfigs"]).values()
            if isinstance(value, config["ModelConfig"])
        }
        self.assertLessEqual(configured, expected)
        for name, dialect in routes:
            self.assertEqual(provider["provider_dialect"](name, direct=False), "openai")
            if dialect is None:
                with self.assertRaises(RuntimeError):
                    provider["provider_dialect"](name)
            else:
                self.assertEqual(provider["provider_dialect"](name), dialect)
        for direct in (True, False):
            with self.assertRaises(RuntimeError):
                provider["provider_dialect"]("unregistered-provider", direct=direct)


class InstalledBedrock(RealConfigs):
    def installed(self, **kwargs):
        fixture = runpy.run_path(str(HERE / "analysis_bedrock_fixture.py"))
        observer = fixture["InstalledBedrockHTTP"](self, **kwargs)
        result = self.native(client_factory=observer.factory)
        return result, observer

    def test_error_body_is_not_consumed_or_published(self):
        for status in (400, 401, 429, 500, 503):
            for error_at in (1, 2):
                with self.subTest(status=status, error_at=error_at):
                    (result, requests, _), observer = self.installed(
                        status=status, error_at=error_at
                    )
                    outcome, _, cache, attempts, witnesses, _ = result
                    self.assertFalse(outcome["success"], outcome)
                    state = self.state.get_state(self.trace)
                    self.assertEqual(state.status, "aborted")
                    self.assertIsNone(state.result_analysis_id)
                    self.assertEqual(self.parents, [])
                    self.assertEqual(self.children, [])
                    self.assertEqual(self.patterns, [])
                    cache.clear.assert_not_called()
                    witness = witnesses[0]
                    self.assertTrue(witness.failed)
                    self.assertFalse(witness.satisfied)
                    self.assertTrue(witness.calls[-1]["exception"])
                    self.assertEqual(len(witness.calls), error_at)
                    self.assertEqual(len(requests), error_at)
                    self.assertEqual(len(attempts), error_at)
                    self.assertEqual(len(observer.requests), error_at)
                    _, ns, _ = f["clients"](lambda *a: None)
                    adapter = ns["BackgroundLLM"].__new__(ns["BackgroundLLM"])
                    adapter._witness = witness
                    retries = []
                    with self.assertRaises(RuntimeError):
                        adapter._attempt(
                            lambda retries=retries: retries.append("forbidden retry")
                        )
                    self.assertEqual(retries, [])
                    self.assertEqual(len(witness.calls), error_at)
                    self.assertGreater(observer.sdk_error_decodes, 0)
                    self.assertEqual(observer.error_field_reads, [])
                    self.assertNotIn(
                        observer.error_wire.decode(), observer.strict_wires
                    )
                    self.assertNotIn("second", json.dumps(outcome))
                    self.assertTrue(
                        all(
                            wire in observer.strict_wires
                            for wire in observer.success_wires
                        )
                    )
                    # Release parser observers between subcases as well as at exit.
                    observer.close()

    def test_healthy_success_body_is_strict_and_completes(self):
        (result, requests, _), observer = self.installed()
        outcome, _, cache, attempts, witnesses, _ = result
        self.assertTrue(outcome["success"], outcome)
        self.assertEqual(self.state.get_state(self.trace).status, "completed")
        self.assertTrue(witnesses[0].satisfied)
        self.assertEqual(len(requests), 3)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(self.parents), 1)
        self.assertEqual(len(self.children), 1)
        self.assertEqual(len(self.patterns), 1)
        cache.clear.assert_called_once()
        self.assertEqual(observer.sdk_error_decodes, 0)
        self.assertEqual(observer.error_field_reads, [])
        self.assertEqual(len(observer.success_wires), 3)
        self.assertTrue(all(w in observer.strict_wires for w in observer.success_wires))
        self.assertEqual(observer.events[:2], ["http-response", "strict-original"])

    def test_duplicate_success_body_still_refused(self):
        fixture = runpy.run_path(str(HERE / "analysis_bedrock_fixture.py"))
        observer = fixture["InstalledBedrockHTTP"](self)
        (outcome, _, cache, _, witnesses, _), requests, _ = self.native(
            client_factory=observer.factory,
            wire_mutator=lambda raw: json.dumps(raw).replace(
                '"stop_reason": ', '"stop_reason": "max_tokens", "stop_reason": '
            ),
        )
        self.assertFalse(outcome["success"], outcome)
        self.assertTrue(witnesses[0].failed)
        self.assertFalse(witnesses[0].satisfied)
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.parents, [])
        self.assertEqual(self.children, [])
        self.assertEqual(self.patterns, [])
        cache.clear.assert_not_called()
        self.assertEqual(observer.sdk_error_decodes, 0)
        self.assertIn(observer.success_wires[0], observer.strict_wires)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        [
            loader.loadTestsFromTestCase(RealConfigs),
            loader.loadTestsFromTestCase(ProviderConfigCensus),
        ]
        + [
            InstalledBedrock(name)
            for name in InstalledBedrock.__dict__
            if name.startswith("test_")
        ]
    )


if __name__ == "__main__":
    unittest.main()
