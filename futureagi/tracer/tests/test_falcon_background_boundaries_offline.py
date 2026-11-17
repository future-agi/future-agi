"""Socket-blocked, stdlib-only behavioral probes of real product definitions.

Run with python3 -I <this file>. ORM, provider and transport boundaries are
synthetic; this supplements, and does not claim, Django integration coverage.
"""

import __future__

import ast
import asyncio
import json
import runpy
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from threading import Event
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]


def no_egress(*args, **kwargs):
    raise AssertionError("Offline regression forbids network access")


def definitions(path, names, namespace, owner=None):
    if path == "agentic_eval/core/embeddings/serving_client.py" and owner is None:
        # The merged serving client validates before unwrapping. Keep its real
        # decoder and validator in this import-free transport fixture.
        names = set(names) | {"_embedding_response_body"}
        namespace["parse_embedding_response"] = runpy.run_path(
            str(ROOT / "agentic_eval/core/embeddings/validation.py")
        )["parse_embedding_response"]
    tree = ast.parse((ROOT / path).read_text())
    futures = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
    ]
    if owner:
        tree = next(node for node in tree.body if getattr(node, "name", None) == owner)
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    assert len(nodes) == len(names), (path, names)
    # Extracted definitions deliberately omit application imports. Annotations
    # must not require those dependencies on Python 3.11/3.12 (3.14 defers them).
    extracted = module(namespace.get("__name__", "offline_definitions"))
    extracted.__dict__.update(namespace)
    # dataclasses resolves ClassVar through sys.modules even with deferred
    # annotations. Register only for execution and restore any previous module.
    with patch.dict("sys.modules", {extracted.__name__: extracted}):
        exec(
            compile(
                ast.Module(body=futures + nodes, type_ignores=[]),
                path,
                "exec",
                flags=__future__.annotations.compiler_flag,
                dont_inherit=True,
            ),
            extracted.__dict__,
        )
    namespace.update(extracted.__dict__)
    return namespace


def module(name, **attrs):
    result = ModuleType(name)
    result.__dict__.update(attrs)
    return result


class Denied(Exception):
    pass


def db_async(fn):
    async def wrapped(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapped


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        for target in (
            "socket.create_connection",
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "socket.getaddrinfo",
        ):
            p = patch(target, no_egress)
            p.start()
            self.addCleanup(p.stop)
        self.active = True
        self.context = SimpleNamespace(organization=SimpleNamespace(pk="org", id="org"))

        def resolve(*args):
            if not self.active:
                raise Denied()
            return SimpleNamespace(id="project"), self.context

        self.resolve = resolve
        ns = definitions(
            "tracer/services/falcon_analysis_guard.py",
            {"AnalysisGuard", "AnalysisAborted"},
            {
                "time": time,
                "Event": Event,
                "database_sync_to_async": db_async,
                "PermissionDenied": Denied,
                "resolve_analysis_scope": resolve,
                "connector_for_execution": lambda *a: object(),
            },
        )
        self.Aborted = ns["AnalysisAborted"]
        self.Guard = ns["AnalysisGuard"]
        self.guard = self.Guard("project", "user", "org", "workspace", 2)
        self.context.background_analysis = self.guard
        self.context.user = self.context.workspace = SimpleNamespace(pk="actor")
        self.modules = {
            "tracer.services.analysis_result_binding": module(
                "binding", current_attempt=lambda: ("project", "trace", "generation")
            ),
            "tracer.services.background_transport": module(
                "transport",
                **runpy.run_path(str(ROOT / "tracer/services/background_transport.py")),
            ),
            "tracer.services.falcon_analysis_guard": module(
                "guard", AnalysisGuard=self.Guard, AnalysisAborted=self.Aborted
            ),
        }

        # Some inherited controls invoke policy/foreground helpers before the
        # application-fixture install. Supply the real inert ContextVar helper
        # throughout each test, without importing Django package initializers.
        transport_patch = patch.dict(
            "sys.modules",
            {
                "tracer.services.background_transport": self.modules[
                    "tracer.services.background_transport"
                ]
            },
        )
        transport_patch.start()
        self.addCleanup(transport_patch.stop)

    def install(self):
        import runpy

        witness = ROOT / "tracer/services/analysis_result_completion.py"
        if witness.exists():
            self.modules["tracer.services.analysis_result_completion"] = module(
                "completion", **runpy.run_path(str(witness))
            )
        if hasattr(self, "worker_agent"):
            raw = runpy.run_path(
                str(ROOT / "tracer/tests/analysis_raw_stream_fixture.py")
            )
            raw["install"](self.worker_agent, self.modules)
        for name in ("background_tools", "background_tool_validation"):
            path = ROOT / f"ee/agenthub/traceerroragent/{name}.py"
            if path.exists():
                with patch.dict("sys.modules", self.modules):
                    self.modules[f"ee.agenthub.traceerroragent.{name}"] = module(
                        name, **runpy.run_path(str(path))
                    )
        payloads = runpy.run_path(
            str(ROOT / "tracer/tests/analysis_payload_fixture.py")
        )
        self.modules = payloads["modules"](self.modules)
        p = patch.dict("sys.modules", self.modules)
        p.start()
        self.addCleanup(p.stop)

    def test_nested_mcp_refuses_credentials_and_all_retries(self):
        class HTTPError(Exception):
            def __init__(self, status):
                self.response = SimpleNamespace(status_code=status)

        connector = SimpleNamespace(
            pk="connector",
            server_url="https://offline.invalid/sse",
            transport="sse",
            name="synthetic",
            auth_type="oauth",
            discovered_tools=[{"name": "inspect"}],
            enabled_tool_names=[],
        )
        token, refresh, sends = Mock(return_value="synthetic"), Mock(), []
        proxy_ns = definitions(
            "ee/falcon_ai/mcp_proxy.py",
            {"MCPConnectorProxy"},
            {
                "asyncio": asyncio,
                "json": json,
                "time": SimpleNamespace(sleep=lambda n: None),
                "urlparse": urlparse,
                "logger": Mock(),
                "httpx": SimpleNamespace(
                    HTTPStatusError=HTTPError, TimeoutException=TimeoutError
                ),
            },
        )
        proxy = proxy_ns["MCPConnectorProxy"]
        self.modules.update(
            {
                "ee.falcon_ai.mcp_proxy": module("proxy", MCPConnectorProxy=proxy),
                "ee.falcon_ai.oauth_client": module(
                    "oauth", get_valid_access_token=token, refresh_access_token=refresh
                ),
                "ee.falcon_ai.tool_scope": module(
                    "scope",
                    current_tool_context=lambda: self.context,
                    accessible_connectors=lambda ctx: [connector],
                    connector_for_execution=lambda *a: connector,
                ),
            }
        )
        method = definitions(
            "ee/agenthub/eval_orchestrator/orchestrator.py",
            {"_tool_call_mcp"},
            {"logger": Mock()},
            owner="EvalOrchestrator",
        )["_tool_call_mcp"]
        orchestrator = SimpleNamespace(
            eval_config=SimpleNamespace(
                available_resources={"mcp_tools": [{"name": "inspect"}]}
            ),
            _resolve_organization_id=lambda: "org",
            _resources_used=[],
        )
        self.install()
        for phase in ("credentials", "timeout", "session", "notification"):
            with self.subTest(phase=phase):
                self.active = phase != "credentials"
                self.guard = self.Guard("p", "u", "o", "w", 2)
                self.context.background_analysis = self.guard
                token.reset_mock()
                sends.clear()
                proxy._session_cache.clear()

                def post(*args, phase=phase, **kwargs):
                    sends.append(kwargs["json"]["method"])
                    if phase == "notification" and len(sends) == 1:
                        raise HTTPError(400)
                    self.active = False
                    if phase == "timeout":
                        raise TimeoutError()
                    if phase == "session":
                        raise HTTPError(400)
                    return SimpleNamespace(
                        headers={"mcp-session-id": "synthetic"},
                        raise_for_status=lambda: None,
                    )

                proxy_ns["httpx"].post = post
                outcome = None
                try:
                    method(orchestrator, "inspect", {})
                except self.Aborted as exc:
                    outcome = exc.code
                self.assertEqual(outcome, "analysis_access_revoked")
                self.assertEqual(token.call_count, 0 if phase == "credentials" else 1)
                self.assertEqual(
                    len(sends), {"credentials": 0, "notification": 2}.get(phase, 1)
                )
                refresh.assert_not_called()

    def test_scores_stop_after_blocking_read(self):
        effects = []
        analysis = SimpleNamespace(
            id="analysis", total_errors=1, save=lambda **kw: effects.append("save")
        )
        query = Mock()
        query.filter.return_value = query.order_by.return_value = query
        query.first.return_value = analysis
        query.create.side_effect = lambda **kw: effects.append("create") or analysis
        project = SimpleNamespace(id="project")
        projects = Mock()
        projects.filter.return_value.first.return_value = project

        def roots(*args):
            self.active = False
            return {"trace": ("root", "project")}

        state = SimpleNamespace(clear=lambda *a: effects.append("clear"))
        self.modules.update(
            {
                "tracer.models.project": module(
                    "project", Project=SimpleNamespace(objects=projects)
                ),
                "tracer.models.trace_error_analysis": module(
                    "analysis",
                    TraceErrorAnalysis=SimpleNamespace(objects=query, drafts=query),
                ),
                "tracer.queries": module("queries", deep_analysis_state=state),
                "tracer.queries.error_analysis": module(
                    "db",
                    TraceErrorAnalysisDB=lambda: SimpleNamespace(
                        ingest_trace_error_embeddings=lambda *a, **k: effects.append(
                            "embedding"
                        )
                    ),
                ),
                "tracer.services.clickhouse.v2": module(
                    "reader",
                    get_reader=lambda: nullcontext(
                        SimpleNamespace(root_ids_by_trace_ids=roots)
                    ),
                ),
                "tracer.tasks.error_analysis": module(
                    "tasks",
                    cluster_project_errors=SimpleNamespace(
                        delay=lambda *a: effects.append("cluster")
                    ),
                ),
            }
        )
        execute = definitions(
            "ai_tools/tools/tracing/submit_trace_scores.py",
            {"_execute_owned"},
            {
                "SubmitTraceScoresInput": object,
                "ToolContext": object,
                "ToolResult": Mock(),
                "logger": Mock(),
                "key_value_block": Mock(),
                "section": Mock(),
                "format_number": str,
            },
            owner="SubmitTraceScoresTool",
        )["_execute_owned"]
        params = SimpleNamespace(
            trace_id="trace", analysis_id=None, recommended_priority="LOW"
        )
        for name in (
            "overall_score",
            "factual_grounding_score",
            "privacy_and_safety_score",
            "instruction_adherence_score",
            "optimal_plan_execution_score",
        ):
            setattr(params, name, 4)
        for name in (
            "factual_grounding_reason",
            "privacy_and_safety_reason",
            "instruction_adherence_reason",
            "optimal_plan_execution_reason",
            "insights",
        ):
            setattr(params, name, "synthetic")
        self.install()
        import runpy

        from tracer.services.analysis_result_completion import CompletionWitness

        self.guard.completion_witness = CompletionWitness(4)
        params = runpy.run_path(str(ROOT / "tracer/tests/analysis_payload_fixture.py"))[
            "parameters"
        ](params, self.guard, "submit_trace_scores")
        with self.assertRaises(self.Aborted):
            execute(None, params, self.context)
        self.assertEqual(effects, [])

    def worker(self, boundary):
        effects = []
        analysis = SimpleNamespace(
            total_errors=1, overall_score=4, recommended_priority="LOW"
        )
        query = Mock()
        query.filter.return_value = query.order_by.return_value = query

        def first():
            if hasattr(self, "finalizer_read"):
                self.finalizer_read()
            if boundary == "revoked":
                self.active = False
            elif boundary == "expired":
                self.guard.deadline = time.monotonic() - 1
            elif boundary == "stall":
                time.sleep(0.15)
            return analysis

        query.first.side_effect = first
        metadata = {}
        conversation = SimpleNamespace(id="c", pk="c", metadata=metadata)
        conversations = Mock()
        conversations.objects.create.return_value = conversation
        marker = Mock()
        marker.begin.return_value = "generation"
        marker.admit.return_value = True
        # Fixture-only legacy transition observer: durable complete/abort are
        # still the product calls. Preserve r3a's abstract clear/failure
        # assertions alongside r3b's exact generation-owned receipt assertions.
        marker.receipt = SimpleNamespace(status="running", reason_code=None)

        def complete(*args):
            marker.receipt.status = "completed"
            effects.append("completed")
            if getattr(self, "legacy_transition_control", False):
                effects.append("clear")
            return True

        def abort(project, trace, generation, code):
            marker.receipt.status = "aborted"
            marker.receipt.reason_code = code
            marker.set_failed(trace, code=code)

        marker.complete.side_effect = complete
        marker.abort.side_effect = abort

        async def stream(*args, **kwargs):
            yield {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}

        async def agent_run(**kw):
            async for _ in agent.llm_client.stream_with_retry([]):
                pass
            return {"content": "done"}

        agent = SimpleNamespace(
            run=agent_run,
            MAX_ITERATIONS=3,
            llm_client=SimpleNamespace(stream_with_retry=stream),
        )
        if hasattr(self, "agent_runner"):
            agent = getattr(self.agent_runner, "agent", agent)
            agent.run = self.agent_runner

        self.worker_agent = agent
        self.modules.update(
            {
                "ee.usage.deployment": module(
                    "deployment", DeploymentMode=SimpleNamespace(is_oss=lambda: False)
                ),
                "ee.falcon_ai.agent": module("agent", AgentLoop=lambda **kw: agent),
                "ee.falcon_ai.models": module(
                    "models", Conversation=conversations, Skill=Mock()
                ),
                "rest_framework.exceptions": module(
                    "exceptions", PermissionDenied=Denied
                ),
                "tfc.middleware.workspace_context": module(
                    "workspace",
                    set_workspace_context=lambda **kw: None,
                    workspace_context=lambda *a: nullcontext(),
                ),
                "tfc.utils.api_errors": module("errors", exception_code=str),
                "tracer.queries": module("queries", deep_analysis_state=marker),
                "tracer.queries.error_analysis": module(
                    "db",
                    TraceErrorAnalysisDB=lambda: SimpleNamespace(
                        ingest_trace_error_embeddings=lambda *a, **kw: effects.append(
                            "embedding"
                        )
                    ),
                ),
                "tracer.services.falcon_analysis_scope": module(
                    "scope", resolve_analysis_scope=self.resolve
                ),
                "tracer.models.trace_error_analysis": module(
                    "analysis",
                    TraceErrorAnalysis=SimpleNamespace(objects=query, drafts=query),
                ),
                "tracer.services.clickhouse.client": module(
                    "ch",
                    ClickHouseClient=lambda: SimpleNamespace(
                        insert=lambda *a: effects.append("success-log")
                    ),
                ),
            }
        )
        self.modules["tracer.services.falcon_analysis_guard"].AnalysisGuard = (
            lambda *a: self.guard
        )
        ns = definitions(
            "tracer/tasks/error_analysis.py",
            {"_falcon_analyze_trace_in_scope", "_falcon_analyze_single_trace"},
            {
                "background_analysis_state": marker,
                "attempt_scope": lambda *a: nullcontext(),
                "current_attempt": lambda: ("project", "trace", "generation"),
                "logger": Mock(),
                "close_old_connections": lambda: None,
                "FALCON_ANALYSIS_TIMEOUT_SECONDS": 2,
                "cluster_project_errors": SimpleNamespace(
                    delay=lambda *a: effects.append("cluster")
                ),
            },
        )
        self.install()
        result = ns["_falcon_analyze_single_trace"](
            "trace",
            "project",
            requesting_user_id="u",
            organization_id="o",
            workspace_id="w",
        )
        return result, effects, marker

    def test_finalization_rechecks_query_before_any_effect(self):
        for boundary, code in (
            ("revoked", "analysis_access_revoked"),
            ("expired", "analysis_deadline_exceeded"),
        ):
            with self.subTest(boundary=boundary):
                self.active = True
                self.guard = self.Guard("p", "u", "o", "w", 2)
                result, effects, marker = self.worker(boundary)
                self.assertFalse(result["success"], result)
                self.assertEqual(result.get("code"), code)
                self.assertEqual(effects, [])
                marker.abort.assert_called_once_with(
                    "project", "trace", "generation", code
                )
                marker.complete.assert_not_called()
                marker.set_failed.assert_called_once_with("trace", code=code)

    def test_finalization_stall_is_bounded_and_late_work_is_suppressed(self):
        self.guard.deadline = time.monotonic() + 0.04
        started = time.monotonic()
        result, effects, _ = self.worker("stall")
        self.assertLess(time.monotonic() - started, 0.12)
        self.assertEqual(result.get("code"), "analysis_deadline_exceeded")
        time.sleep(0.18)  # Let the synthetic blocked dependency return.
        self.assertEqual(effects, [])

    def test_completed_control(self):
        result, effects, _ = self.worker(None)
        self.assertTrue(result["success"], result)
        self.assertIn("success-log", effects)
        self.assertIn("completed", effects)

    def test_legacy_completed_transition_control(self):
        self.legacy_transition_control = True
        result, effects, _ = self.worker(None)
        self.assertTrue(result["success"], result)
        self.assertIn("success-log", effects)
        self.assertIn("clear", effects)

    def test_polling_preserves_aborted_reason_even_with_persisted_analysis(self):
        store = {}
        cache = SimpleNamespace(
            get=store.get,
            delete=lambda k: store.pop(k, None),
            set=lambda k, v, ttl: store.update({k: v}),
        )
        tree = ast.parse((ROOT / "tracer/queries/deep_analysis_state.py").read_text())
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        state = SimpleNamespace(
            **definitions(
                "tracer/queries/deep_analysis_state.py",
                names,
                {
                    "cache": cache,
                    "_TTL_SECONDS": 3600,
                    "background_analysis_state": SimpleNamespace(
                        get_state=lambda *a, **kw: None
                    ),
                },
            )
        )
        query = Mock()
        query.filter.return_value = query.order_by.return_value = query
        query.first.return_value = SimpleNamespace(id="persisted-before-abort")
        details = Mock()
        details.filter.return_value.order_by.return_value = []
        ns = definitions(
            "tracer/queries/feed.py",
            {"get_deep_analysis"},
            {
                "DeepAnalysisResponse": SimpleNamespace,
                "deep_analysis_state": state,
                "background_analysis_state": SimpleNamespace(
                    get_state=lambda *a, **kw: None
                ),
                "TraceErrorAnalysis": SimpleNamespace(objects=query),
                "TraceErrorDetail": SimpleNamespace(objects=details),
                "_cluster_qs_for_access": lambda *a: SimpleNamespace(
                    exists=lambda: True
                ),
                "_cluster_has_trace": lambda *a: True,
                "_build_root_causes": lambda *a: [],
                "_build_recommendations": lambda *a: [],
            },
        )
        snapshot = ModuleType("snapshot")
        snapshot.trace_snapshot = lambda *a, **kw: (
            None,
            SimpleNamespace(id="persisted-before-abort", _analysis_snapshot_details=[]),
        )
        module_patch = patch.dict(
            "sys.modules",
            {
                "tracer.services.analysis_result_snapshot": snapshot,
            },
        )
        module_patch.start()
        self.addCleanup(module_patch.stop)
        for code in ("analysis_access_revoked", "analysis_deadline_exceeded"):
            state.set_failed("trace", code=code)
            response = ns["get_deep_analysis"]("cluster", "trace", ["project"])
            self.assertEqual(response.status, "failed")
            self.assertEqual(getattr(response, "code", None), code)
        state.clear("trace")
        self.assertEqual(ns["get_deep_analysis"]("cluster", "trace").status, "done")


if __name__ == "__main__":
    unittest.main()
