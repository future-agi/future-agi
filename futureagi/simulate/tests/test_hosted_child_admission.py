"""DB-free, socket-blocked execution of production launch/rerun boundaries.

Run directly with python -I. AST extraction avoids Django startup and .env reads;
only imports inside the extracted functions are replaced with explicit doubles.
No process, filesystem scratch directory, workflow or database is created.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

_ROOT = Path(__file__).resolve().parents[1]
_ACTIVITY = _ROOT / "temporal/activities/hosted_runner.py"
_VIEW = _ROOT / "views/run_test.py"


class ApplicationError(Exception):
    def __init__(self, message, *, type, non_retryable):
        super().__init__(message)
        self.type = type
        self.non_retryable = non_retryable


def load_functions(path, names, namespace, *, owner=None):
    tree = ast.parse(path.read_text())
    body = tree.body
    if owner:
        body = next(
            n for n in body if isinstance(n, ast.ClassDef) and n.name == owner
        ).body
    nodes = [
        n
        for n in body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names
    ]
    for node in nodes:
        node.decorator_list = []
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            *nodes,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)


def finish(coroutine):
    """All awaited dependencies are immediate doubles; no event-loop sockets."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("unexpected suspension in offline test")


class NoSockets(unittest.TestCase):
    def setUp(self):
        for name in ("socket.socket", "socket.create_connection"):
            guard = patch(name, side_effect=AssertionError("sockets forbidden"))
            guard.start()
            self.addCleanup(guard.stop)


class HostedLaunchAdmissionTests(NoSockets):
    def harness(self, settings=None, overlay=None):
        async def lines():
            for line in ():
                yield line

        proc = SimpleNamespace(stdout=lines(), wait=AsyncMock(return_value=0))
        spawn = AsyncMock(return_value=proc)
        constants = SimpleNamespace(HOSTED_RUNNER_CHAT_MODE="chat")
        imports = patch.dict("sys.modules", {"simulate.temporal.constants": constants})
        imports.start()
        self.addCleanup(imports.stop)
        ns = {
            "settings": settings if settings is not None else SimpleNamespace(),
            "ApplicationError": ApplicationError,
            "json": json,
            "contextlib": contextlib,
            "asyncio": SimpleNamespace(
                create_subprocess_exec=spawn,
                subprocess=asyncio.subprocess,
                CancelledError=asyncio.CancelledError,
            ),
            "os": SimpleNamespace(
                environ={},
                getenv=lambda key, default=None: default,
                path=os.path,
                makedirs=Mock(),
                rmdir=Mock(),
            ),
            "tempfile": SimpleNamespace(
                mkdtemp=Mock(return_value="/synthetic/scratch")
            ),
            "shutil": SimpleNamespace(rmtree=Mock()),
            "open": mock_open(),
            "_runs_base": lambda: "/synthetic/runs",
            "_child_environment": lambda job, *, origins: {},
            "_resolve_voice_secret_env": AsyncMock(return_value=overlay or {}),
            "_acquire_child_slot": AsyncMock(),
            "_child_semaphore": Mock(),
            "_acquire_did_slot": AsyncMock(),
            "_release_did_slot": AsyncMock(),
            "activity": SimpleNamespace(logger=Mock(), heartbeat=Mock()),
            "RunHostedJobOutput": lambda **kw: SimpleNamespace(**kw),
        }
        ns["CLOUD_DEPLOYMENTS"] = ("US", "EU", "DEV")
        load_functions(_ROOT.parent / "tfc/ee_loader.py", {"is_cloud_env"}, ns)
        load_functions(
            _ACTIVITY,
            {
                "run_hosted_sdk_job",
                "_check_child_launch_hold",
                "_final_child_environment",
                "_is_backend_only_secret",
                "_secret_env_ref_keys",
            },
            ns,
        )
        job = SimpleNamespace(
            mode="chat", run_seconds=30, job_json="{}", job_id="synthetic-job"
        )
        return ns, spawn, job

    def test_hold_refuses_before_any_subprocess_or_slot(self):
        ns, spawn, job = self.harness(SimpleNamespace(HOSTED_RUNNER_LAUNCH_HOLD=True))
        with self.assertRaises(ApplicationError) as caught:
            finish(ns["run_hosted_sdk_job"](job))
        self.assertEqual("hosted_runner_launch_held", caught.exception.type)
        self.assertTrue(caught.exception.non_retryable)
        spawn.assert_not_called()
        ns["_acquire_child_slot"].assert_not_called()
        ns["_acquire_did_slot"].assert_not_called()
        ns["tempfile"].mkdtemp.assert_not_called()

    def test_hold_off_by_default_and_overlay_is_sanitized_at_spawn(self):
        key = "ALK_UNSAFE_INPROCESS_CODE_ACTORS"
        ns, spawn, job = self.harness(
            overlay={key: "true", "LIVEKIT_API_KEY": "job-owned"}
        )
        finish(ns["run_hosted_sdk_job"](job))
        spawn.assert_awaited_once()
        env = spawn.call_args.kwargs["env"]
        self.assertNotIn(key, env)
        self.assertEqual("job-owned", env["LIVEKIT_API_KEY"])
        self.assertEqual("/synthetic/runs/synthetic-job", env["FI_RUN_ROOT"])

    def test_hold_rechecked_after_waiting_for_a_slot(self):
        settings = SimpleNamespace(HOSTED_RUNNER_LAUNCH_HOLD=False)
        ns, spawn, job = self.harness(settings)
        ns["_acquire_child_slot"].side_effect = lambda: setattr(
            settings, "HOSTED_RUNNER_LAUNCH_HOLD", True
        )
        with self.assertRaises(ApplicationError):
            finish(ns["run_hosted_sdk_job"](job))
        spawn.assert_not_called()
        ns["_child_semaphore"].release.assert_called_once()

    def test_setting_defaults_off_and_parses_opt_in(self):
        path = _ROOT.parent / "tfc/settings/settings.py"
        tree = ast.parse(path.read_text())
        nodes = [
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "HOSTED_RUNNER_LAUNCH_HOLD"
                for t in n.targets
            )
        ]
        self.assertEqual(1, len(nodes))
        for value, expected in (
            (None, False),
            ("false", False),
            ("true", True),
            ("1", True),
        ):
            ns = {
                "os": SimpleNamespace(
                    getenv=lambda key, default, value=value: (
                        default if value is None else value
                    )
                )
            }
            exec(
                compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), ns
            )
            self.assertIs(expected, ns["HOSTED_RUNNER_LAUNCH_HOLD"])


class HostedRerunAdmissionTests(NoSockets):
    def harness(self, enabled=False):
        execution = MagicMock()
        execution.id = "execution"
        execution.scenario_ids = ["case"]
        execution.simulator_agent_id = None
        execution.status = "completed"
        workflow = Mock(return_value="hosted-workflow")
        native = Mock(return_value={"workflow_id": "native-workflow", "merged": False})
        services = SimpleNamespace(
            resolve_run_agent_version=Mock(return_value="version"),
            resolve_runner_mode=Mock(return_value="chat"),
            agent_field_for_run=Mock(return_value="VOICE"),
        )
        imports = patch.dict(
            "sys.modules",
            {
                "simulate.services.hosted_runner": services,
                "simulate.temporal.client": SimpleNamespace(
                    start_simulation_runner_workflow=workflow,
                    rerun_call_executions=native,
                ),
            },
        )
        imports.start()
        self.addCleanup(imports.stop)
        ns = {"app_settings": SimpleNamespace(HOSTED_RUNNER_ENABLED=enabled)}
        load_functions(_VIEW, {"_dispatch_hosted_rerun"}, ns)
        return ns, execution, workflow, native

    def test_dispatch_disabled_refuses_without_hosted_or_native_workflow(self):
        ns, execution, workflow, native = self.harness()
        with self.assertRaisesRegex(
            ValueError, "Hosted simulation reruns are disabled"
        ):
            ns["_dispatch_hosted_rerun"](execution, ["call"])
        workflow.assert_not_called()
        native.assert_not_called()

    def test_enabled_dispatch_keeps_hosted_route(self):
        ns, execution, workflow, native = self.harness(enabled=True)
        self.assertEqual(
            "hosted-workflow", ns["_dispatch_hosted_rerun"](execution, ["call"])
        )
        self.assertEqual(["call"], workflow.call_args.kwargs["call_execution_ids"])
        native.assert_not_called()

    def test_both_views_refuse_before_mutation_without_native_fallback(self):
        for owner in ("CallExecutionRerunView", "TestExecutionRerunView"):
            with self.subTest(owner=owner):
                ns, execution, workflow, native = self.harness()
                queryset = MagicMock()
                queryset.__iter__.side_effect = lambda execution=execution: iter(
                    [execution]
                )
                queryset.exists.return_value = True
                queryset.select_related.return_value = queryset
                queryset.exclude.return_value = queryset
                queryset.order_by.return_value = queryset
                manager = Mock(filter=Mock(return_value=queryset))
                gm = Mock()
                gm.bad_request.side_effect = lambda message: {
                    "status": 400,
                    "message": message,
                }
                view = Mock(_gm=gm)
                request = SimpleNamespace(
                    organization="org",
                    validated_data={"rerun_type": "call_and_eval", "select_all": True},
                )
                ns.update(
                    {
                        "get_object_or_404": Mock(
                            return_value=execution
                            if owner == "CallExecutionRerunView"
                            else execution.run_test
                        ),
                        "run_test_workspace_filter": Mock(),
                        "RunTest": Mock(),
                        "TestExecution": SimpleNamespace(
                            objects=manager,
                            ExecutionStatus=SimpleNamespace(
                                PENDING="pending",
                                CANCELLING="cancelling",
                                RUNNING="running",
                            ),
                        ),
                        "CallExecution": SimpleNamespace(objects=manager),
                        "AgentDefinition": SimpleNamespace(
                            AgentTypeChoices=SimpleNamespace(TEXT="TEXT", VOICE="VOICE")
                        ),
                        "_hosted_execution_eligible": Mock(return_value=True),
                        "_repository_harness_job_id": Mock(return_value=None),
                        "_latest_repository_harness_execution": Mock(return_value=None),
                        "_voice_sim_gate_response": Mock(return_value=None),
                        "Http404": type("Http404", (Exception,), {}),
                        "traceback": Mock(),
                        "Response": Mock(),
                        "status": MagicMock(),
                        "clear_call_for_rerun": Mock(),
                        "logger": Mock(),
                    }
                )
                load_functions(
                    _VIEW,
                    {"_hosted_rerun_disabled", "_historically_hosted_execution"},
                    ns,
                )
                load_functions(_VIEW, {"post"}, ns, owner=owner)
                response = ns["post"](view, request, "synthetic-id")
                self.assertEqual(
                    {
                        "status": 400,
                        "message": "Hosted simulation reruns are disabled. Try again when the hosted runner is enabled.",
                    },
                    response,
                )
                ns["clear_call_for_rerun"].assert_not_called()
                view._prepare_call_executions_for_rerun_bulk.assert_not_called()
                workflow.assert_not_called()
                native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
