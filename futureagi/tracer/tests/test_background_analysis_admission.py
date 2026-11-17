"""DB-free production-body regressions; run via test_background_analysis_switch.py."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from tracer.tests import test_background_analysis_switch as base
from tracer.tests.test_background_analysis_switch import fake_module, source_nodes

TASKS = "tracer/tasks/error_analysis.py"
COMMAND = "tfc/management/commands/debug_temporal.py"
ANALYSIS_ACTIVITIES = (
    "check_and_process_trace_errors",
    "run_deep_analysis_on_demand",
    "analyze_traces_on_demand",
)


class BackgroundAnalysisAdmissionTests(unittest.TestCase):
    setUp = base.BackgroundAnalysisSwitchTests.setUp
    off = base.BackgroundAnalysisSwitchTests.off

    def scheduler(self):
        self.db = Mock()
        self.db.ensure_all_projects_have_tasks.return_value = 0
        self.db_factory = Mock(return_value=self.db)
        self.status = SimpleNamespace(RUNNING="running", WAITING="waiting")
        self.legacy_batch = Mock()
        self.close_connections = Mock()
        self.log = Mock()
        self.clock = Mock(return_value="now")
        patch.dict(
            sys.modules,
            {
                "tracer.models.trace_error_analysis_task": fake_module(
                    "tracer.models.trace_error_analysis_task",
                    TraceErrorTaskStatus=self.status,
                ),
                "tracer.queries.error_analysis": fake_module(
                    "tracer.queries.error_analysis",
                    TraceErrorAnalysisDB=self.db_factory,
                ),
            },
        ).start()
        ns = {
            "temporal_activity": lambda **kwargs: lambda fn: fn,
            "close_old_connections": self.close_connections,
            "process_trace_error_batch": self.legacy_batch,
            "logger": self.log,
            "timezone": SimpleNamespace(now=self.clock),
        }
        source_nodes(TASKS, ["check_and_process_trace_errors"], ns)
        return ns["check_and_process_trace_errors"]

    def test_scheduler_off_has_no_db_reads_writes_or_dispatch_on_retries(self):
        run = self.scheduler()
        self.off()
        for _ in range(3):
            self.assertIsNone(run())
        self.db_factory.assert_not_called()
        self.assertEqual(self.db.mock_calls, [])
        self.assertEqual(self.legacy_batch.mock_calls, [])
        self.close_connections.assert_not_called()
        self.clock.assert_not_called()
        self.assertEqual(self.log.info.call_count, 3)
        for entry in self.log.info.call_args_list:
            self.assertIn(self.gate.SETTING, entry.args[0])
        self.assertEqual(len(self.log.mock_calls), 3)

    def test_scheduler_default_on_preserves_sampling_running_skip_and_batch_size(self):
        run = self.scheduler()
        running = SimpleNamespace(id="running", status=self.status.RUNNING)
        waiting = SimpleNamespace(
            id=123, status=self.status.WAITING, project=SimpleNamespace(name="Project")
        )
        empty = SimpleNamespace(id="empty", status=self.status.WAITING)

        class Tasks(list):
            def count(self):
                return len(self)

        traces = [f"trace-{i}" for i in range(13)]
        self.db.get_active_tasks.return_value = Tasks([running, waiting, empty])
        self.db.get_traces_to_process_for_task.side_effect = [traces, []]
        self.assertIsNone(run())
        self.close_connections.assert_called_once_with()
        self.db.ensure_all_projects_have_tasks.assert_called_once_with()
        self.assertEqual(
            self.db.get_traces_to_process_for_task.call_args_list,
            [call(waiting, days_back=1), call(empty, days_back=1)],
        )
        self.db.update_task_status.assert_called_once_with(
            waiting, self.status.RUNNING, last_run_at="now"
        )
        self.legacy_batch.delay.assert_called_once_with("123", traces[:10])
        self.log.exception.assert_not_called()

    def command(self):
        class CommandError(Exception):
            """Django's command handler maps this exception to a nonzero exit."""

        # Run immediate mock coroutines without creating an event loop/socket.
        def run_immediate(coroutine):
            try:
                coroutine.send(None)
            except StopIteration as done:
                return done.value
            self.fail("unexpected async I/O suspension")

        async def get_client():
            self.client_called()
            return SimpleNamespace(namespace="test")

        async def get_status(workflow_id):
            self.status_called(workflow_id)
            return "RUNNING"

        self.start = Mock(return_value="workflow-id")
        self.client_called = Mock()
        self.status_called = Mock()
        self.import_activities = Mock()
        patch.dict(
            sys.modules,
            {
                "django.core.management.base": fake_module(
                    "django.core.management.base", CommandError=CommandError
                ),
                "asyncio": fake_module("asyncio", run=run_immediate),
                "tfc.temporal": fake_module(
                    "tfc.temporal",
                    TEMPORAL_HOST="mock",
                    TEMPORAL_NAMESPACE="test",
                    get_client=get_client,
                    get_workflow_status_async=get_status,
                ),
                "tfc.temporal.drop_in.decorator": fake_module(
                    "tfc.temporal.drop_in.decorator",
                    _ACTIVITY_REGISTRY={},
                    _ACTIVITY_WRAPPERS={},
                    get_temporal_activities=lambda: [],
                ),
                "tfc.temporal.common.registry": fake_module(
                    "tfc.temporal.common.registry",
                    _import_temporal_activity_modules=self.import_activities,
                    get_registry_info=lambda: {},
                ),
                "tfc.temporal.drop_in.runner": fake_module(
                    "tfc.temporal.drop_in.runner", start_activity=self.start
                ),
            },
        ).start()
        patch("time.sleep", side_effect=lambda _: None).start()
        ns = {}
        source_nodes(COMMAND, ["handle"], ns, "Command")
        command = SimpleNamespace(
            stdout=Mock(),
            style=SimpleNamespace(SUCCESS=str, ERROR=str, WARNING=str),
        )
        return ns["handle"], command, CommandError

    def test_debug_off_refuses_all_three_before_any_connection_or_workflow(self):
        handle, command, error = self.command()
        self.off()
        for activity in ANALYSIS_ACTIVITIES:
            with self.subTest(activity=activity), self.assertRaises(error) as caught:
                handle(command, test_activity=activity, queue="agent_compass")
            self.assertEqual(
                str(caught.exception), self.gate.BACKGROUND_ANALYSIS_UNAVAILABLE_MESSAGE
            )
        self.start.assert_not_called()
        self.client_called.assert_not_called()
        self.import_activities.assert_not_called()
        command.stdout.write.assert_not_called()

    def test_debug_off_preserves_unrelated_activity_dispatch(self):
        handle, command, _ = self.command()
        self.off()
        handle(command, test_activity="run_all_prompts_task", queue="tasks_l")
        self.start.assert_called_once_with(
            "run_all_prompts_task", args=(), kwargs={}, queue="tasks_l"
        )
        self.status_called.assert_called_once_with("workflow-id")
        self.client_called.assert_called_once_with()

    def test_debug_default_on_preserves_analysis_dispatch_arguments(self):
        handle, command, _ = self.command()
        for activity in ANALYSIS_ACTIVITIES:
            handle(command, test_activity=activity, queue="agent_compass")
        self.assertEqual(
            self.start.call_args_list,
            [
                call(name, args=(), kwargs={}, queue="agent_compass")
                for name in ANALYSIS_ACTIVITIES
            ],
        )
        self.assertEqual(self.status_called.call_count, 3)

    def test_debug_off_without_test_activity_keeps_diagnostics(self):
        handle, command, _ = self.command()
        self.off()
        handle(command, test_activity=None, queue="tasks_l")
        self.start.assert_not_called()
        self.client_called.assert_called_once_with()
        self.import_activities.assert_called_once_with()
