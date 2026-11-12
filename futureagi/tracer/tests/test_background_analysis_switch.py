"""DB-free dispatch contract tests: python -I <this file>.

Execute the production function/class ASTs with external I/O replaced by mocks.
This deliberately avoids Django app startup, credentials, DBs and worker imports;
it is not an integration test of middleware or the Temporal worker.
"""

import ast
import dataclasses
import os
import re
import socket
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[2]
GATE = "tfc.capabilities.background_analysis"
FEED = "tracer/queries/feed.py"
TOOL = "ai_tools/tools/tracing/analyze_project_traces.py"
VIEW = "tracer/views/feed/tab_views.py"


def source_nodes(path, names, namespace, parent=None):
    """Compile unchanged bodies; strip only view decorators requiring Django."""
    tree = ast.parse((ROOT / path).read_text(), filename=path)
    body = tree.body
    if parent:
        body = next(n for n in body if getattr(n, "name", None) == parent).body
    selected = [n for n in body if getattr(n, "name", None) in names]
    assert len(selected) == len(names), (path, names)
    if parent:
        for node in selected:
            node.decorator_list = []
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            *selected,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), path, "exec"), namespace)
    return namespace


def fake_module(name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    return module


class BackgroundAnalysisSwitchTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        ).start()
        patch.object(
            socket, "create_connection", side_effect=AssertionError("network forbidden")
        ).start()
        patch.dict(os.environ, {}, clear=True).start()
        self.settings = SimpleNamespace()
        self.state = Mock()
        self.state.set_running.return_value = True
        self.analysis = Mock()
        self.analysis.objects.filter.return_value.exists.return_value = False
        self.scope = Mock(
            return_value=(SimpleNamespace(id="project", name="Project"), None)
        )
        self.reader = Mock()
        self.reader.root_ids_by_trace_ids.return_value = {"trace": "root"}
        self.get_reader = Mock()
        self.get_reader.return_value.__enter__ = Mock(return_value=self.reader)
        self.get_reader.return_value.__exit__ = Mock(return_value=False)
        self.deep_task = Mock()
        self.batch_task = Mock()
        self.modules = {
            "django.conf": fake_module("django.conf", settings=self.settings),
            "structlog": fake_module("structlog", get_logger=Mock(return_value=Mock())),
            "rest_framework.exceptions": fake_module(
                "rest_framework.exceptions", PermissionDenied=PermissionError
            ),
            "tracer.models.trace_error_analysis": fake_module(
                "tracer.models.trace_error_analysis", TraceErrorAnalysis=self.analysis
            ),
            "tracer.queries": fake_module(
                "tracer.queries", deep_analysis_state=self.state
            ),
            "tracer.services.clickhouse.v2": fake_module(
                "tracer.services.clickhouse.v2", get_reader=self.get_reader
            ),
            "tracer.services.falcon_analysis_scope": fake_module(
                "tracer.services.falcon_analysis_scope",
                resolve_analysis_scope=self.scope,
            ),
            "tracer.tasks": fake_module(
                "tracer.tasks", run_deep_analysis_on_demand=self.deep_task
            ),
            "tracer.tasks.error_analysis": fake_module(
                "tracer.tasks.error_analysis", analyze_traces_on_demand=self.batch_task
            ),
        }
        patch.dict(sys.modules, self.modules).start()
        self.gate = fake_module(GATE)
        exec(
            compile(
                (ROOT / "tfc/capabilities/background_analysis.py").read_text(),
                GATE,
                "exec",
            ),
            self.gate.__dict__,
        )
        patch.dict(sys.modules, {GATE: self.gate}).start()
        self.ns = {
            "__name__": __name__,
            "dataclass": dataclasses.dataclass,
            "field": dataclasses.field,
        }
        source_nodes(
            "tracer/types/feed_types.py",
            ["DeepAnalysisDispatchResponse", "DeepAnalysisResponse"],
            self.ns,
        )
        source_nodes("ai_tools/base.py", ["ToolResult"], self.ns)
        source_nodes("ai_tools/formatting.py", ["section", "key_value_block"], self.ns)
        self.cluster = Mock()
        self.cluster.return_value.exists.return_value = True
        self.membership = Mock(return_value=True)
        self.ns.update(
            _cluster_qs_for_access=self.cluster,
            _cluster_has_trace=self.membership,
            TraceErrorAnalysis=self.analysis,
            TraceErrorDetail=Mock(),
            deep_analysis_state=self.state,
            _build_root_causes=Mock(return_value=[]),
            _build_recommendations=Mock(return_value=[]),
        )
        source_nodes(FEED, ["dispatch_deep_analysis", "get_deep_analysis"], self.ns)
        source_nodes(TOOL, ["execute"], self.ns, "AnalyzeProjectTracesTool")
        self.context = SimpleNamespace(
            user=SimpleNamespace(pk="user"),
            organization=SimpleNamespace(pk="org"),
            workspace=SimpleNamespace(pk="workspace"),
        )
        self.params = SimpleNamespace(
            project_id="project", trace_ids=["trace"], max_traces=50
        )

    def dispatch(self, **kwargs):
        return self.ns["dispatch_deep_analysis"](
            "cluster", "trace", ["project"], **kwargs
        )

    def execute(self):
        return self.ns["execute"](None, self.params, self.context)

    def off(self):
        setattr(self.settings, self.gate.SETTING, False)

    def test_gate_defaults_on_for_absent_blank_and_garbage_environment(self):
        self.assertTrue(self.gate.background_analysis_enabled())
        for value in ("", " ", "garbage", "disabled", "2", "true", "1", "on", "yes"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {self.gate.SETTING: value}),
            ):
                self.assertTrue(self.gate.background_analysis_enabled())

    def test_explicit_off_environment_and_setting_values(self):
        for value in ("false", "0", "no", "off", " OFF ", "False"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {self.gate.SETTING: value}),
            ):
                self.assertFalse(self.gate.background_analysis_enabled())
            setattr(self.settings, self.gate.SETTING, value)
            self.assertFalse(self.gate.background_analysis_enabled())
            delattr(self.settings, self.gate.SETTING)
        self.off()
        self.assertFalse(self.gate.background_analysis_enabled())

    def test_settings_precedence_and_call_time_read(self):
        with patch.dict(os.environ, {self.gate.SETTING: "off"}):
            for value in (True, "", " ", "typo", 0, 1, [], {}):
                setattr(self.settings, self.gate.SETTING, value)
                self.assertTrue(self.gate.background_analysis_enabled(), repr(value))
            setattr(self.settings, self.gate.SETTING, None)
            self.assertFalse(self.gate.background_analysis_enabled())
            setattr(self.settings, self.gate.SETTING, True)
            self.assertTrue(self.gate.background_analysis_enabled())
        self.off()
        self.assertFalse(self.gate.background_analysis_enabled())

    def test_feed_default_on_dispatches_same_arguments_and_shape(self):
        result = self.dispatch()
        self.assertEqual(
            dataclasses.asdict(result), {"status": "running", "trace_id": "trace"}
        )
        self.deep_task.delay.assert_called_once_with("trace", False)
        self.state.set_running.assert_called_once_with("trace")
        self.cluster.assert_called_once_with("cluster", ["project"])
        self.membership.assert_called_once_with("cluster", "trace", ["project"])

    def test_feed_off_blocks_normal_and_forced_dispatch_without_state_writes(self):
        self.off()
        for has_analysis in (False, True):
            self.analysis.objects.filter.return_value.exists.return_value = has_analysis
            for force in (False, True):
                with self.subTest(has_analysis=has_analysis, force=force):
                    result = self.dispatch(force=force)
                    expected = (
                        "done"
                        if has_analysis and not force
                        else "temporarily_unavailable"
                    )
                    self.assertEqual(
                        dataclasses.asdict(result),
                        {"status": expected, "trace_id": "trace"},
                    )
        self.deep_task.delay.assert_not_called()
        self.state.set_running.assert_not_called()

    def test_feed_default_on_idempotence_and_forced_rerun_unchanged(self):
        self.state.set_running.return_value = False
        self.assertEqual(self.dispatch().status, "running")
        self.deep_task.delay.assert_not_called()
        self.assertEqual(self.dispatch(force=True).status, "running")
        self.deep_task.delay.assert_called_once_with("trace", True)
        self.deep_task.reset_mock()
        self.analysis.objects.filter.return_value.exists.return_value = True
        self.assertEqual(self.dispatch().status, "done")
        self.deep_task.delay.assert_not_called()

    def test_feed_off_preserves_not_found_and_access_checks(self):
        self.off()
        self.cluster.return_value.exists.return_value = False
        self.assertIsNone(self.dispatch())
        self.membership.assert_not_called()
        self.cluster.return_value.exists.return_value = True
        self.membership.return_value = False
        self.assertIsNone(self.dispatch())
        self.deep_task.delay.assert_not_called()

    def test_cached_get_still_returns_results_off(self):
        self.off()
        self.state.status.return_value = "done"
        self.ns[
            "TraceErrorDetail"
        ].objects.filter.return_value.order_by.return_value = []
        result = self.ns["get_deep_analysis"]("cluster", "trace", ["project"])
        self.assertEqual(
            dataclasses.asdict(result),
            {
                "status": "done",
                "trace_id": "trace",
                "root_causes": [],
                "recommendations": [],
                "immediate_fix": None,
            },
        )
        self.deep_task.delay.assert_not_called()
        self.state.set_running.assert_not_called()

    def test_tool_default_on_dispatches_batches_and_preserves_response(self):
        traces = [f"trace-{i}" for i in range(11)]
        self.params.trace_ids = traces
        self.reader.root_ids_by_trace_ids.return_value = dict.fromkeys(traces, "root")
        result = self.execute()
        principal = {
            "requesting_user_id": "user",
            "organization_id": "org",
            "workspace_id": "workspace",
        }
        self.assertEqual(
            self.batch_task.delay.call_args_list,
            [
                call("project", traces[:10], **principal),
                call("project", traces[10:], **principal),
            ],
        )
        self.assertEqual(
            result.data,
            {
                "status": "dispatched",
                "project_id": "project",
                "project_name": "Project",
                "trace_count": 11,
                "trace_ids": traces,
                "batches": 2,
            },
        )
        self.assertFalse(result.is_error)
        self.assertIsNone(result.error_code)
        self.assertIn("Trace Analysis Triggered", result.content)
        self.assertEqual(
            self.state.set_running.call_args_list, [call(t) for t in traces]
        )
        self.scope.assert_called_once_with("project", **principal)

    def test_tool_default_on_discovers_unanalyzed_traces(self):
        self.params.trace_ids = []
        self.analysis.objects.filter.return_value.values_list.return_value = ["old"]
        self.reader.recent_root_trace_ids_by_project.return_value = ["old", "new"]
        result = self.execute()
        self.assertEqual(result.data["trace_ids"], ["new"])
        self.batch_task.delay.assert_called_once_with(
            "project",
            ["new"],
            requesting_user_id="user",
            organization_id="org",
            workspace_id="workspace",
        )

    def test_tool_off_exact_error_for_specific_and_all_traces(self):
        self.off()
        for trace_ids in (["trace"], []):
            self.params.trace_ids = trace_ids
            result = self.execute()
            self.assertEqual(
                dataclasses.asdict(result),
                {
                    "content": f"**Error:** {self.gate.BACKGROUND_ANALYSIS_UNAVAILABLE_MESSAGE}",
                    "data": None,
                    "is_error": True,
                    "error_code": "TEMPORARILY_UNAVAILABLE",
                },
            )
        self.batch_task.delay.assert_not_called()
        self.state.set_running.assert_not_called()
        self.get_reader.assert_not_called()
        self.analysis.objects.filter.assert_not_called()

    def test_tool_off_preserves_scope_denial(self):
        self.off()
        self.scope.side_effect = PermissionError
        self.assertEqual(self.execute().error_code, "PERMISSION_DENIED")
        self.batch_task.delay.assert_not_called()

    def test_message_has_one_source(self):
        self.assertEqual(
            self.gate.BACKGROUND_ANALYSIS_UNAVAILABLE_MESSAGE,
            "Deep analysis isn't available right now, so no analysis was started.",
        )
        for path in (FEED, TOOL, VIEW):
            self.assertNotIn(
                self.gate.BACKGROUND_ANALYSIS_UNAVAILABLE_MESSAGE,
                (ROOT / path).read_text(),
            )

    def view_namespace(self):
        # Real error-envelope and GeneralMethods bodies, only the DRF transport is fake.
        errors_source = (ROOT / "tfc/utils/api_errors.py").read_text()
        statuses = {
            name: int(name.split("_")[1])
            for name in re.findall(r"HTTP_\d+_[A-Z_]+", errors_source)
        }
        patch.dict(
            sys.modules,
            {
                "rest_framework": fake_module(
                    "rest_framework", status=SimpleNamespace(**statuses)
                )
            },
        ).start()
        ns = {"__name__": __name__}
        exec(compile(errors_source, "api_errors.py", "exec"), ns)
        ns.update(
            Response=lambda data, status: SimpleNamespace(
                data=data, status_code=status
            ),
            HTTP_200_OK=200,
            HTTP_400_BAD_REQUEST=400,
            STATUS_KEY_NAME="status",
            RESULT_KEY_NAME="result",
            SUCCESS_STATUS_CODE=True,
            _ERROR_MESSAGE_KEYS={"message", "detail", "error"},
            _ERROR_ENVELOPE_KEYS={"message", "detail", "error", "code"},
        )
        methods = ["_error_response_body", "bad_request", "success_response"]
        source_nodes("tfc/utils/general_methods.py", methods, ns, "GeneralMethods")
        gm = type("GeneralMethods", (), {name: ns[name] for name in methods})()
        ns.update(
            _accessible_project_ids_or_response=Mock(return_value=(["project"], None)),
            feed_service=SimpleNamespace(
                dispatch_deep_analysis=Mock(
                    side_effect=lambda cluster, projects, **kw: self.ns[
                        "dispatch_deep_analysis"
                    ](cluster, project_ids=projects, **kw)
                )
            ),
            DeepAnalysisDispatchResponseSerializer=lambda result: SimpleNamespace(
                data=dataclasses.asdict(result)
            ),
            logger=Mock(),
        )
        source_nodes(VIEW, ["post"], ns, "FeedDeepAnalysisView")
        return ns, SimpleNamespace(_gm=gm)

    def test_view_off_maps_to_exact_standard_400_envelope(self):
        self.off()
        ns, view = self.view_namespace()
        response = ns["post"](
            view,
            SimpleNamespace(validated_data={"trace_id": "trace", "force": True}),
            "cluster",
        )
        message = self.gate.BACKGROUND_ANALYSIS_UNAVAILABLE_MESSAGE
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {
                "status": False,
                "type": "validation_error",
                "code": "invalid",
                "detail": message,
                "message": message,
                "error": message,
                "result": message,
            },
        )
        self.deep_task.delay.assert_not_called()
        self.state.set_running.assert_not_called()

    def test_view_default_on_and_cached_off_keep_success_envelope(self):
        ns, view = self.view_namespace()
        request = SimpleNamespace(validated_data={"trace_id": "trace"})
        response = ns["post"](view, request, "cluster")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {"status": True, "result": {"status": "running", "trace_id": "trace"}},
        )
        self.off()
        self.analysis.objects.filter.return_value.exists.return_value = True
        response = ns["post"](view, request, "cluster")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {"status": True, "result": {"status": "done", "trace_id": "trace"}},
        )


if __name__ == "__main__":
    # One DB-free process for the original 15 contracts and admission/census tests.
    import importlib.util

    sys.modules["test_background_analysis_switch"] = sys.modules[__name__]
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        BackgroundAnalysisSwitchTests
    )
    for name in (
        "test_background_analysis_admission",
        "test_background_analysis_census",
    ):
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name(f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    with (
        patch.object(socket, "socket", side_effect=AssertionError("network forbidden")),
        patch.object(
            socket, "create_connection", side_effect=AssertionError("network forbidden")
        ),
    ):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
