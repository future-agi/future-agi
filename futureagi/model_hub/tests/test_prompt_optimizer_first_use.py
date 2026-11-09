"""No-database first-use/lifetime tests of the real optimizer source.

These deliberately fake the LLM boundary and unrelated ORM/evaluation imports.
They prove helper/instance behavior, NOT licensing, activation or import safety;
the separate fresh-process probe never installs these doubles.
"""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


BACKEND = Path(__file__).resolve().parents[2]
AGENT = BACKEND / "ee/agenthub/prompt_optimizer_agent"
MODEL = SimpleNamespace(model_name="fixture-model", temperature=0.2,
                        max_tokens=32, provider="fixture-provider")
CHAT = [{"role": "user", "content": "old", "variables": {"name": "Ada"},
         "prompt_template": "Hello {name}"}]
ROW = {"user_chat": "old", "model_chat": "old answer", "variables": {"name": "Ada"},
       "prompt_template": "Hello {name}", "metadata": {}}


def module(name, **attributes):
    result = ModuleType(name)
    result.__dict__.update(attributes)
    return result


class Client:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []

    def _get_completion_content(self, messages):
        self.calls.append(messages)
        return "fixture completion"


def load_optimizer(factory):
    """Load unchanged product bytes with explicit unit-test-only collaborators."""
    import numpy
    metrics = Mock()
    metrics.objects.filter.return_value.select_related.return_value = []
    package = "_prompt_optimizer_unit"
    modules = {
        package: module(package, __path__=[str(AGENT)]),
        "numpy": numpy,
        "tfc.telemetry": module("tfc.telemetry", wrap_for_thread=lambda fn: fn),
        "tqdm": module("tqdm", tqdm=lambda rows: rows),
        "structlog": module("structlog", get_logger=lambda *a: Mock()),
        "agentic_eval.core.llm.llm": module("agentic_eval.core.llm.llm", LLM=factory),
        "agentic_eval.core.utils.model_config": module(
            "agentic_eval.core.utils.model_config",
            ModelConfigs=SimpleNamespace(CLAUDE_4_5_SONNET_BEDROCK_ARN=MODEL)),
        "agentic_eval.core_evals.fi_evals": module("agentic_eval.core_evals.fi_evals", __all__=[]),
        "model_hub.models.develop_dataset": module("model_hub.models.develop_dataset", Column=Mock()),
        "model_hub.models.evals_metric": module("model_hub.models.evals_metric", UserEvalMetric=metrics),
        "model_hub.views.eval_runner": module("model_hub.views.eval_runner", EvaluationRunner=Mock()),
        "ee.usage.utils.usage_entries": module("ee.usage.utils.usage_entries",
            APICallStatusChoices=Mock(), APICallTypeChoices=Mock(), log_api_call=Mock()),
    }
    name = package + ".agent_task_v2"
    spec = importlib.util.spec_from_file_location(name, AGENT / "agent_task_v2.py")
    result = importlib.util.module_from_spec(spec)
    modules[name] = result
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(result)
    return result, metrics


class PromptOptimizerFirstUseTests(unittest.TestCase):
    def setUp(self):
        self.clients = []
        self.lock = threading.Lock()

        def construct(**kwargs):
            client = Client(**kwargs)
            with self.lock:
                self.clients.append(client)
            return client

        self.factory = Mock(side_effect=construct)
        self.agent, self.metrics = load_optimizer(self.factory)
        # This suite tests first use, not import safety. The real-import suite
        # separately diagnoses the base's eager constructor without a mock.
        self.clients.clear()
        self.factory.reset_mock()

    def assert_default_configuration(self, client):
        self.assertEqual(client.kwargs, dict(model_name=MODEL.model_name,
            temperature=MODEL.temperature, max_tokens=MODEL.max_tokens, provider=MODEL.provider))

    def test_injected_chat_client_is_reused_without_construction(self):
        client = Client()
        result = self.agent.get_updated_chat(CHAT, "Welcome {name}", client=client)
        self.assertEqual(result, [{"role": "user", "content": "Welcome Ada"},
                                  {"role": "assistant", "content": "fixture completion"}])
        self.assertEqual(client.calls, [[{"role": "user", "content": "Welcome Ada"}]])
        self.factory.assert_not_called()

    def test_injected_gradient_client_makes_two_completions(self):
        client = Client()
        result = self.agent.TextGradientDescent().step_v3("prompt", 1, "criteria", client=client)
        self.assertEqual(result, "fixture completion")
        self.assertEqual(len(client.calls), 2)
        self.assertIn("fixture completion", client.calls[1][0]["content"])
        self.factory.assert_not_called()

    def test_default_chat_constructs_per_invocation(self):
        for _ in range(2):
            result = self.agent.get_updated_chat(CHAT, "Welcome {name}")
            self.assertEqual(result[-1]["content"], "fixture completion")
        self.assertEqual(self.factory.call_count, 2)
        self.assertIsNot(self.clients[0], self.clients[1])
        for client in self.clients:
            self.assert_default_configuration(client)
            self.assertEqual(len(client.calls), 1)

    def test_default_gradient_reuses_one_client_for_both_completions(self):
        for _ in range(2):
            result = self.agent.TextGradientDescent().step_v3("prompt", 1, "criteria")
            self.assertEqual(result, "fixture completion")
        self.assertEqual(self.factory.call_count, 2)
        for client in self.clients:
            self.assert_default_configuration(client)
            self.assertEqual(len(client.calls), 2)

    def optimizer(self, data=None):
        relation = SimpleNamespace(all=lambda: [])
        record = SimpleNamespace(user_eval_template_ids=relation)
        return self.agent.PromptOptimizer("Hello {name}", data, optimizer=record)

    def test_instance_owns_and_reuses_client_across_rows(self):
        optimizer = self.optimizer()
        for _ in range(2):
            self.assertEqual(optimizer.process_row_data(ROW, "Welcome {name}"), ("", 0.0))
        self.factory.assert_called_once()
        self.assertIs(optimizer.client, self.clients[0])
        self.assertEqual(len(optimizer.client.calls), 2)
        self.assertTrue(optimizer.client.kwargs["optimizer"])

    def test_real_optimization_loop_reuses_instance_client(self):
        import pandas as pd
        optimizer = self.optimizer(pd.DataFrame([dict(ROW) for _ in range(10)]))
        # Bound one iteration while executing the real threaded mini-batch,
        # process_row_data, get_updated_chat and gradient/follow-up call paths.
        with patch.object(optimizer, "_calculate_score", side_effect=[0.0, 0.0, 1.0]):
            result = optimizer.get_optimized_prompt(k=1)
        self.assertEqual(result, [{"iteration_num": 1, "train_score": 1.0,
                                  "instruction_template": "fixture completion"}])
        self.factory.assert_called_once()
        self.assertEqual(len(optimizer.client.calls), 8)

    def test_constructor_failure_propagates_and_next_helper_use_retries(self):
        self.factory.side_effect = [RuntimeError("fixture constructor failure"), Client()]
        with self.assertRaisesRegex(RuntimeError, "fixture constructor failure"):
            self.agent.get_updated_chat(CHAT, "Welcome {name}")
        result = self.agent.get_updated_chat(CHAT, "Welcome {name}")
        self.assertEqual(result[-1]["content"], "fixture completion")
        self.assertEqual(self.factory.call_count, 2)

    def test_failed_instance_does_not_prevent_a_later_instance(self):
        client = Client()
        self.factory.side_effect = [RuntimeError("fixture instance failure"), client]
        with self.assertRaisesRegex(RuntimeError, "fixture instance failure"):
            self.optimizer()
        optimizer = self.optimizer()
        self.assertIs(optimizer.client, client)
        self.assertEqual(optimizer.process_row_data(ROW, None), ("", 0.0))
        self.assertEqual(self.factory.call_count, 2)

    def test_concurrent_helpers_have_independent_clients(self):
        barrier = threading.Barrier(2)

        def use():
            barrier.wait(timeout=5)
            return self.agent.get_updated_chat(CHAT, "Welcome {name}")

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(use) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(len({id(client) for client in self.clients}), 2)
        self.assertEqual([len(client.calls) for client in self.clients], [1, 1])
        self.assertTrue(all(result[-1]["content"] == "fixture completion" for result in results))

    def test_concurrent_instances_have_independent_clients(self):
        barrier = threading.Barrier(2)

        def use():
            barrier.wait(timeout=5)
            optimizer = self.optimizer()
            self.assertEqual(optimizer.process_row_data(ROW, None), ("", 0.0))
            return optimizer

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(use) for _ in range(2)]
            optimizers = [future.result(timeout=10) for future in futures]
        self.assertEqual(self.factory.call_count, 2)
        self.assertIsNot(optimizers[0].client, optimizers[1].client)
        self.assertEqual([len(optimizer.client.calls) for optimizer in optimizers], [1, 1])
