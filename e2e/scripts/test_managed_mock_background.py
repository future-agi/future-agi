"""Socket-free tests. Never import Django/application modules or start a worker."""
import importlib.util
import ast
import copy
import os
from pathlib import Path
import socket
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("background_probe", ROOT / "e2e/lib/managed-mock-background.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
SOURCE = (ROOT / "futureagi/ee/usage/services/gateway_llm_client.py").read_text()
ENV = {"AGENTCC_INTERNAL_API_KEY": "local-dev-only-shared-secret-replace-me",
       "AGENTCC_INTERNAL_URL": "http://agentcc-gateway:8080"}


class BackgroundSafety(unittest.TestCase):
    def describe_case(self, *, fail_rpc=None, namespace_info=None, pollers=None, close_error=False):
        calls = []
        namespace_info = namespace_info if namespace_info is not None else SimpleNamespace(
            name="default", state=1, id="namespace-id")
        good = SimpleNamespace(identity="13@owned", last_access_time=SimpleNamespace(seconds=1000))

        def rpc(name, timeout, wait_for_ready):
            self.assertEqual(timeout, 5)
            self.assertIs(wait_for_ready, False)
            calls.append(name)
            if fail_rpc == name:
                raise TimeoutError("bounded read deadline")

        def describe_namespace(request, *, timeout, wait_for_ready):
            self.assertEqual(vars(request), {"namespace": "default"})
            rpc("namespace", timeout, wait_for_ready)
            return SimpleNamespace(namespace_info=namespace_info)

        def describe_task_queue(request, *, timeout, wait_for_ready):
            self.assertEqual(request.namespace, "default")
            self.assertEqual(vars(request.task_queue), {"name": "agent_compass"})
            rpc(request.task_queue_type, timeout, wait_for_ready)
            return SimpleNamespace(pollers=(pollers or {}).get(request.task_queue_type, [good]))

        class Channel:
            def __enter__(self):
                calls.append("enter")
                return self

            def __exit__(self, *_):
                calls.append("close")
                if close_error:
                    raise RuntimeError("close failed")
                return False

        channel = Channel()

        def insecure_channel(address, *, options):
            self.assertEqual(address, "172.18.0.10:7233")
            self.assertEqual(options, (("grpc.enable_retries", 0), ("grpc.enable_http_proxy", 0)))
            return channel

        def stub(selected):
            self.assertIs(selected, channel)
            return SimpleNamespace(DescribeNamespace=describe_namespace, DescribeTaskQueue=describe_task_queue)

        def forbidden(*_, **__):
            self.fail("the observer must not construct an SDK Client or Runtime")

        # No generic **kwargs on RPC doubles: exact gRPC deadlines, not SDK
        # retry/timedelta arguments. Methods not present here cannot be invoked.
        modules = {"grpc": SimpleNamespace(insecure_channel=insecure_channel),
            "temporalio.client": SimpleNamespace(Client=SimpleNamespace(connect=forbidden)),
            "temporalio.runtime": SimpleNamespace(Runtime=forbidden),
            "temporalio.api.taskqueue.v1": SimpleNamespace(TaskQueue=SimpleNamespace),
            "temporalio.api.workflowservice.v1": SimpleNamespace(
                DescribeNamespaceRequest=SimpleNamespace, DescribeTaskQueueRequest=SimpleNamespace),
            "temporalio.api.workflowservice.v1.service_pb2_grpc": SimpleNamespace(WorkflowServiceStub=stub)}
        error = result = None
        try:
            with patch.dict(sys.modules, modules), patch.object(probe.time, "time", return_value=1000):
                result = probe.describe_worker("172.18.0.10", "13@owned")
        except (RuntimeError, TimeoutError) as caught:
            error = caught
        return result, calls, error

    def test_describe_worker_closes_exact_read_only_rpc_channel_before_receipt(self):
        result, calls, error = self.describe_case()
        self.assertIsNone(error)
        self.assertEqual(calls, ["enter", "namespace", 1, 2, "close"])
        self.assertEqual(result, {"namespaceId": "namespace-id", "queue": "agent_compass", "pollerIdentity": "13@owned"})

    def test_describe_rpc_deadlines_fail_closed_and_close_without_later_calls(self):
        for stage, expected in (("namespace", ["enter", "namespace", "close"]),
                                (1, ["enter", "namespace", 1, "close"]),
                                (2, ["enter", "namespace", 1, 2, "close"])):
            with self.subTest(stage=stage):
                result, calls, error = self.describe_case(fail_rpc=stage)
                self.assertIsNone(result)
                self.assertIsInstance(error, TimeoutError)
                self.assertEqual(calls, expected)

    def test_invalid_namespace_closes_before_any_queue_call(self):
        for name, state, namespace_id in (("other", 1, "id"), ("", 1, "id"),
                                           ("default", 2, "id"), ("default", 1, "")):
            with self.subTest(name=name, state=state, namespace_id=namespace_id):
                result, calls, error = self.describe_case(namespace_info=SimpleNamespace(
                    name=name, state=state, id=namespace_id))
                self.assertIsNone(result)
                self.assertRegex(str(error), "namespace not registered")
                self.assertEqual(calls, ["enter", "namespace", "close"])

    def test_either_queue_rejects_missing_duplicate_foreign_stale_or_future_pollers(self):
        good = SimpleNamespace(identity="13@owned", last_access_time=SimpleNamespace(seconds=1000))
        invalid = [[], [good, good], [SimpleNamespace(identity="other", last_access_time=good.last_access_time)],
                   [SimpleNamespace(identity=good.identity, last_access_time=SimpleNamespace(seconds=879))],
                   [SimpleNamespace(identity=good.identity, last_access_time=SimpleNamespace(seconds=1006))]]
        for queue in (1, 2):
            for pollers in invalid:
                with self.subTest(queue=queue, pollers=pollers):
                    result, calls, error = self.describe_case(pollers={queue: pollers})
                    self.assertIsNone(result)
                    self.assertRegex(str(error), "poller")
                    self.assertEqual(calls, ["enter", "namespace", *range(1, queue + 1), "close"])

    def test_channel_close_failure_never_returns_success(self):
        result, calls, error = self.describe_case(close_error=True)
        self.assertIsNone(result)
        self.assertRegex(str(error), "close failed")
        self.assertEqual(calls, ["enter", "namespace", 1, 2, "close"])

    def test_actual_serving_client_scalar_unwrap_and_batch_contract(self):
        source = (ROOT / "futureagi/agentic_eval/core/embeddings/serving_client.py").read_text()
        cls = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "ModelServingClient")
        cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef)
                    and node.name in {"_make_request", "embed_text", "embed_text_batch"}]
        calls = []
        noop = lambda *args, **kwargs: None
        scope = {"Any": object, "time": time, "logger": SimpleNamespace(debug=noop, error=noop)}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), "serving-client-contract", "exec"), scope)
        client = scope["ModelServingClient"]()
        client.base_url, client.default_timeout = "http://mock-llm:8080", 120

        def post(url, json, timeout):
            calls.append((url, json, timeout))
            body = {"embeddings": [[0.125] * 8 for _ in json["text"]]}
            return SimpleNamespace(status_code=200, json=lambda: copy.deepcopy(body))

        client.session = SimpleNamespace(post=post)
        self.assertEqual(client.embed_text("one"), [0.125] * 8)
        self.assertEqual(client.embed_text_batch(["one", "two"]), [[0.125] * 8, [0.125] * 8])
        self.assertEqual(calls, [("http://mock-llm:8080/model/v1/embed", {"text": texts, "input_type": "text"}, 120)
                                 for texts in (["one"], ["one", "two"])])

    def test_process_environment_rejects_missing_changed_and_hidden_overrides(self):
        probe.validate_process_environment({**ENV, "PWD": "/app/backend"}, ENV)
        for actual in ({}, {**ENV, "AGENTCC_INTERNAL_URL": "http://other"},
                       {**ENV, "https_proxy": "http://other"}, {**ENV, "OPENAI_API_KEY": "hidden"},
                       {**ENV, "PYTHONPATH": "/tmp/other"}):
            with self.subTest(actual_keys=list(actual)), self.assertRaisesRegex(RuntimeError, "STOP:"):
                probe.validate_process_environment(actual, ENV)

    def test_poller_identity_cardinality_and_freshness(self):
        good = SimpleNamespace(identity="13@owned", last_access_time=SimpleNamespace(seconds=950))
        probe.validate_pollers([good], "13@owned", 1000)
        for pollers, identity, now in (([], "13@owned", 1000), ([good, good], "13@owned", 1000),
                                       ([good], "13@other", 1000), ([good], "13@owned", 1200)):
            with self.subTest(identity=identity, now=now), self.assertRaisesRegex(RuntimeError, "poller"):
                probe.validate_pollers(pollers, identity, now)

    def test_real_factory_isolated_constructor_success_and_failure(self):
        created = []

        def constructor(**kwargs):
            created.append(kwargs)
            return SimpleNamespace(base_url=kwargs["base_url"] + "/", api_key=kwargs["api_key"], close=lambda: None)

        # SDK double tests the probe, not installed-SDK compatibility. The actual
        # installed OpenAI constructor remains a mandatory managed-runtime check.
        with patch.dict(os.environ, ENV, clear=True), patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=constructor)}):
            probe.construct_gateway(SOURCE)
        self.assertEqual(created, [{"api_key": ENV["AGENTCC_INTERNAL_API_KEY"],
                                    "base_url": "http://agentcc-gateway:8080/v1", "timeout": 300.0, "max_retries": 2}])
        with patch.dict(os.environ, ENV, clear=True), patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=lambda **_: None)}):
            with self.assertRaisesRegex(RuntimeError, "constructor failed"):
                probe.construct_gateway(SOURCE)

    def test_constructor_cannot_hide_a_caught_dns_attempt(self):
        def unsafe(**kwargs):
            try:
                socket.getaddrinfo("provider.invalid", 443)
            except RuntimeError:
                pass
            return SimpleNamespace(base_url=kwargs["base_url"] + "/", api_key=kwargs["api_key"], close=lambda: None)

        with patch.dict(os.environ, ENV, clear=True), patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=unsafe)}):
            with self.assertRaisesRegex(RuntimeError, "construction/cache unsafe"):
                probe.construct_gateway(SOURCE)


if __name__ == "__main__":
    unittest.main()
