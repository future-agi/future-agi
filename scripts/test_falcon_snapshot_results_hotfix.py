"""Offline B2 history regression: real DRF JSON field and consumer methods.

No project settings or model imports; socket/database guards come from the r1
standalone bootstrap. Assertions never include fixture values in failures.
"""

import ast
import asyncio
import copy
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "snapshot_bootstrap", HERE / "test_sim_snapshot_projection.py"
)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
ROOT = bootstrap.ROOT
KEYS = bootstrap.SECRET_KEYS
TOOLS = (
    "list_agents",
    "get_agent",
    "create_agent",
    "update_agent",
    "create_agent_version",
    "list_simulation_tests",
    "get_simulation_test",
    "create_simulation_test",
)

# Load only the pure helper modules, without importing the Django application.
for module_name, relative in (
    ("simulate.utils.snapshot_projection", "simulate/utils/snapshot_projection.py"),
    ("ee.falcon_ai.snapshot_results", "ee/falcon_ai/snapshot_results.py"),
):
    path = ROOT / relative
    if path.exists():
        module = types.ModuleType(module_name)
        sys.modules[module_name] = module
        exec(compile(path.read_text(), str(path), "exec"), module.__dict__)

scope = {}
helper = sys.modules.get("ee.falcon_ai.snapshot_results")
if helper:
    scope.update(helper.__dict__)


class MessageBase(bootstrap.serializers.Serializer):
    tool_calls = bootstrap.serializers.JSONField(allow_null=True)


node = bootstrap.named("ee/falcon_ai/serializers.py", "MessageSerializer")
node.bases = [ast.Name(id="MessageBase", ctx=ast.Load())]
node.body = [n for n in node.body if isinstance(n, ast.FunctionDef)] or [ast.Pass()]
bootstrap.execute([node], dict_scope := dict(scope, MessageBase=MessageBase))
MessageSerializer = dict_scope["MessageSerializer"]


class SocketBase:
    async def send_json(self, content, close=False):
        self.sent.append(content)


async def allow_live(*args, **kwargs):
    return True


def sync_to_async(fn):
    async def wrapped(*args):
        return fn(*args)

    return wrapped


node = bootstrap.named("ee/falcon_ai/consumers.py", "FalconAIConsumer")
node.bases = [ast.Name(id="SocketBase", ctx=ast.Load())]
node.body = [
    n
    for n in node.body
    if getattr(n, "name", None) in {"_handle_reconnect", "send_json"}
]
consumer_scope = dict(
    scope,
    SocketBase=SocketBase,
    database_sync_to_async=sync_to_async,
    logger=types.SimpleNamespace(info=lambda *a, **k: None, warning=bootstrap.deny_io),
)
bootstrap.execute([node], consumer_scope)
Consumer = consumer_scope["FalconAIConsumer"]


def render(calls, replay=False):
    if not replay:
        return MessageSerializer(types.SimpleNamespace(tool_calls=calls)).data
    events = [{"type": "tool_call_result", "data": call} for call in calls]
    consumer_scope["StreamBuffer"] = lambda _: types.SimpleNamespace(
        get_status=lambda: "completed", get_all_events=lambda: events
    )
    consumer = Consumer()
    consumer.sent = []
    consumer._guard = types.SimpleNamespace(ensure_live=allow_live)
    # 2.0 adds live-access and ownership gates before reading the buffer.
    # Isolate those DB lookups here; routed tests exercise actual authentication.
    consumer._chat_access_allowed = allow_live
    consumer._owns_conversation = allow_live
    asyncio.run(consumer._handle_reconnect({"conversation_id": "fixture"}))
    return consumer.sent[1:]


def call(tool, value):
    return {
        "tool_name": tool,
        "call_id": "fixture-call",
        "result_full": value,
        "result_summary": value,
        "status": "completed",
    }


def clean(value):
    text = json.dumps(value)
    return not any(key in text for key in KEYS)


class HistoricalSnapshotTests(unittest.TestCase):
    def test_parsable_and_nested_list_results_at_both_boundaries(self):
        sensitive = dict.fromkeys(KEYS, "fixture")
        for tool in TOOLS:
            for value in (
                sensitive,
                {"agent_version": {"configuration_snapshot": sensitive}},
                [{"configuration_snapshot": {"nested": [sensitive], "keep": 7}}],
            ):
                for replay in (False, True):
                    with self.subTest(tool=tool, replay=replay):
                        stored = [call(tool, json.dumps(value))]
                        before = copy.deepcopy(stored)
                        result = render(stored, replay)
                        self.assertTrue(
                            clean(result), "historical credential field survived"
                        )
                        actual = (
                            result[0]["data"] if replay else result["tool_calls"][0]
                        )
                        expected = bootstrap.expected(value, root=False)
                        self.assertTrue(
                            json.loads(actual["result_full"]) == expected,
                            "non-secret parsed structure changed",
                        )
                        self.assertTrue(stored == before, "stored message mutated")

    def test_truncated_values_and_summary_fallback(self):
        for tool in TOOLS:
            for key in KEYS:
                for value in (
                    '{"keep":1,"' + key + '":"fixture", "tail":',
                    '{"' + key + '":"incomplete',
                    '{"' + key + '":{"nested":["incomplete',
                    "Result with 2 fields: name, " + key,
                    '{"' + key + '":false, "tail":',
                    '{"' + key + '":null, "tail":',
                    '{"' + key + '":123, "tail":',
                    '{"' + key + '":[1, {"keep":true}], "tail":',
                    '{"' + key + '":"escaped\\"text", "tail":',
                ):
                    for replay in (False, True):
                        with self.subTest(tool=tool, key=key, replay=replay):
                            stored = [call(tool, value)]
                            before = copy.deepcopy(stored)
                            self.assertTrue(
                                clean(render(stored, replay)),
                                "truncated credential survived",
                            )
                            self.assertTrue(stored == before, "stored message mutated")

    def test_non_snapshot_results_are_byte_identical(self):
        for tool in (
            "get_trace",
            "search",
            "external_connector_tool",
            "get_simulation_test_extra",
        ):
            for replay in (False, True):
                stored = [call(tool, json.dumps(dict.fromkeys(KEYS, "fixture")))]
                before = copy.deepcopy(stored)
                result = render(stored, replay)
                actual = result[0]["data"] if replay else result["tool_calls"][0]
                self.assertTrue(actual == before[0], "unrelated tool changed")
                self.assertTrue(stored == before)

    def test_secret_free_known_results_and_fields_preserved(self):
        for tool in TOOLS:
            for value in (
                '{ "keep" : [true,null,1,"café"] }',
                "plain summary",
                '{"truncated":',
                "long safe text " * 1000,
            ):
                for replay in (False, True):
                    stored = [call(tool, value)]
                    stored[0]["params"] = {"query": "unchanged"}
                    result = render(stored, replay)
                    actual = result[0]["data"] if replay else result["tool_calls"][0]
                    self.assertTrue(actual == stored[0], "secret-free result changed")

    def test_response_containers_do_not_alias_stored_results(self):
        for replay in (False, True):
            stored = [
                call(
                    "get_simulation_test",
                    {"nested": [{"api_key": "fixture", "keep": []}]},
                )
            ]
            before = copy.deepcopy(stored)
            result = render(stored, replay)
            actual = result[0]["data"] if replay else result["tool_calls"][0]
            self.assertTrue(clean(actual))
            actual["result_full"]["nested"][0]["keep"].append(1)
            self.assertTrue(stored == before, "response aliases storage")

    def test_text_recovery_keeps_safe_fragment_or_uses_fixed_placeholder(self):
        for replay in (False, True):
            for value, expected in (
                ('{"keep":1,"api_key":"fixture", "tail":', '{"keep":1, "tail":'),
                ('{"api_key":"unfinished', "[Historical simulation result omitted]"),
                ('{"api_key":"' + "x" * 9000, "[Historical simulation result omitted]"),
                ('{"api_\\u006bey":"fixture", "keep":1}', '{"keep": 1}'),
            ):
                result = render([call("get_simulation_test", value)], replay)
                actual = result[0]["data"] if replay else result["tool_calls"][0]
                self.assertTrue(
                    actual["result_full"] == expected, "redaction grammar mismatch"
                )

    def test_live_and_replayed_event_projection_match(self):
        consumer = Consumer()
        consumer.sent = []
        consumer._guard = types.SimpleNamespace(ensure_live=allow_live)
        event = {
            "type": "tool_call_result",
            "data": call("get_simulation_test", '{"api_key":"fixture"}'),
        }
        before = copy.deepcopy(event)
        asyncio.run(consumer.send_json(event))
        self.assertTrue(clean(consumer.sent), "live output bypasses boundary")
        self.assertTrue(event == before, "buffer input mutated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
