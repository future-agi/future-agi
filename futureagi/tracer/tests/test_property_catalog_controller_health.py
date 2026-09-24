"""Health-only tests: stdlib, no Django startup, credentials or external services.

Run: python -m unittest discover -s tracer/tests -p test_property_catalog_controller_health.py
Command bodies are executed with model/runtime boundaries replaced, following
the repository's isolated command-test pattern. No reconcile writes occur.
"""

import ast
import importlib.util
import json
import os
import signal
import tempfile
import threading
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock

TRACER = Path(__file__).resolve().parents[1]
HELPER = TRACER / "services/clickhouse/v2/property_catalog/controller_health.py"
spec = importlib.util.spec_from_file_location("isolated_controller_health", HELPER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ControllerHealth = module.ControllerHealth


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tick = 100.0
        self.stop = threading.Event()
        self.snapshots = []
        self.health = ControllerHealth(
            lambda **snapshot: self.snapshots.append(snapshot),
            stop=self.stop,
            monotonic=lambda: self.tick,
            utc_now=lambda: (
                datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=self.tick)
            ),
        )

    def test_1200_second_work_live_until_deadline_not_100_second_source_wall(self):
        self.health.progress("reconciling", timeout_seconds=1320)
        self.tick += 1201
        self.health.publish()
        self.assertTrue(self.snapshots[-1]["live"])
        self.assertFalse(self.snapshots[-1]["ready"])
        self.tick += 120
        self.health.publish()
        self.assertFalse(self.snapshots[-1]["live"])

    def test_heartbeat_and_readiness_changes_do_not_extend_deadline(self):
        self.health.progress("reconciling", timeout_seconds=20, ready=True)
        before = self.health.snapshot()["progress_at"]
        for _ in range(3):
            self.tick += 10
            self.health.publish()
        self.health.set_ready(True)
        self.assertEqual(self.health.snapshot()["progress_at"], before)
        self.assertFalse(self.health.snapshot()["live"])
        self.assertFalse(self.health.snapshot()["healthy"])

    def test_next_workspace_renews_bounded_deadline(self):
        for _ in range(3):
            self.health.progress("reconciling", timeout_seconds=1320)
            self.tick += 1200
            self.assertTrue(self.health.snapshot()["live"])
        self.tick += 121
        self.assertFalse(self.health.snapshot()["live"])

    def test_failures_and_retry_keep_liveness_without_readiness(self):
        for phase, detail in (
            ("idle", {"failed_count": 3256, "processed_count": 0}),
            ("retrying", {"cycle_error": "unavailable"}),
        ):
            self.health.progress(phase, timeout_seconds=180, ready=False, detail=detail)
            self.assertTrue(self.health.snapshot()["live"])
            self.assertFalse(self.health.snapshot()["healthy"])
        self.health.progress("idle", timeout_seconds=180, ready=True, detail={})
        self.assertTrue(self.health.snapshot()["healthy"])

    def test_shutdown_and_stop_event_clear_health(self):
        self.health.progress("idle", timeout_seconds=180, ready=True)
        self.stop.set()
        self.assertFalse(self.health.snapshot()["live"])
        self.assertFalse(self.health.snapshot()["ready"])
        self.health.__exit__()
        self.assertFalse(self.snapshots[-1]["healthy"])

    def test_background_heartbeat_and_final_shutdown_record(self):
        observed = threading.Event()
        snapshots = []

        def publish(**snapshot):
            snapshots.append(snapshot)
            if len(snapshots) >= 2:
                observed.set()

        with ControllerHealth(publish, stop=self.stop, interval_seconds=0.01):
            self.assertTrue(observed.wait(1))
            self.assertTrue(snapshots[-1]["live"])
        self.assertFalse(snapshots[-1]["live"])
        self.assertFalse(snapshots[-1]["ready"])

    def test_publisher_failure_stops_heartbeat_and_propagates(self):
        failed = threading.Event()
        calls = []

        def publish(**snapshot):
            calls.append(snapshot)
            if len(calls) == 2:
                failed.set()
                raise OSError("disk failure")

        with ControllerHealth(publish, stop=self.stop, interval_seconds=0.01) as health:
            self.assertTrue(failed.wait(1))
            health._thread.join(timeout=1)
            self.assertFalse(health.snapshot()["live"])
            with self.assertRaisesRegex(RuntimeError, "publisher failed"):
                health.progress("idle", timeout_seconds=180)
        self.assertFalse(calls[-1]["live"])

    def test_invalid_deadlines_rejected(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                self.health.progress("idle", timeout_seconds=value)


class CommandTests(unittest.TestCase):
    def setUp(self):
        source = (
            TRACER / "management/commands/ch25_property_catalog_lifecycle_controller.py"
        )
        tree = ast.parse(source.read_text())
        command = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Command"
        )
        handle = next(
            n
            for n in command.body
            if isinstance(n, ast.FunctionDef) and n.name == "handle"
        )
        functions = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef)
            and n.name
            in {
                "run_cycle",
                "_write_health",
                "_install_signal_handlers",
                "_restore_signal_handlers",
                "iso_z",
            }
        ]

        class CommandError(Exception):
            pass

        self.error = CommandError
        self.config = SimpleNamespace(
            health_file="unused",
            bootstrap_enabled=False,
            repair_expired_incomplete=False,
            scheduled_reconcile_wall_ms=1200000,
            poll_seconds=60,
            failure_backoff_seconds=30,
            workspace_ids=(),
            workspace_scope_mode="all",
        )
        self.snapshots = []
        self.healths = []

        def health_factory(publish, **kwargs):
            health = ControllerHealth(publish, **kwargs)
            original = health.progress
            health.progress = Mock(side_effect=original)
            self.healths.append(health)
            return health

        self.ns = {
            "threading": threading,
            "signal": signal,
            "ExitStack": ExitStack,
            "datetime": datetime,
            "UTC": UTC,
            "os": os,
            "tempfile": tempfile,
            "Path": Path,
            "MappingProxyType": MappingProxyType,
            "ControllerHealth": health_factory,
            "CommandError": CommandError,
            "ProductionLifecycleControllerError": type(
                "ControllerError", (Exception,), {}
            ),
            "DevRolloutError": type("RolloutError", (Exception,), {}),
            "logger": Mock(),
            "settings": object(),
            "controller_config": Mock(return_value=self.config),
            "_validate_initial_backfill_mode": Mock(),
            "discover_workspace_scopes": Mock(
                return_value=((SimpleNamespace(workspace_id="one"),), ())
            ),
            "canonical_json": lambda value, **kwargs: json.dumps(value),
            "_HEALTH_FORMAT": "futureagi.property-catalog-lifecycle-health",
            "_HEALTH_VERSION": 2,
            "CycleResult": lambda **kwargs: SimpleNamespace(**kwargs),
        }
        future = ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        )
        code = ast.fix_missing_locations(
            ast.Module(body=[future, *functions, handle], type_ignores=[])
        )
        exec(compile(code, str(source), "exec"), self.ns)
        self.actual_cycle = self.ns["run_cycle"]
        self.actual_writer = self.ns["_write_health"]
        self.ns["_write_health"] = lambda path, **snapshot: self.snapshots.append(
            snapshot
        )
        self.view = SimpleNamespace(
            stderr=Mock(), style=SimpleNamespace(ERROR=lambda text: text)
        )

    def cycle(self, failures, processed, *, stopped=False):
        def run(**kwargs):
            kwargs["on_progress"](SimpleNamespace(workspace_id="one"))
            if failures:
                kwargs["on_error"]("one", RuntimeError("expired incomplete"))
            return SimpleNamespace(
                failures=failures,
                processed=processed,
                stopped=stopped,
                as_dict=lambda **kw: {
                    "failed_count": len(failures),
                    "processed_count": len(processed),
                },
            )

        self.ns["run_cycle"] = Mock(side_effect=run)

    def test_all_failed_and_mixed_cycles_remain_unready_but_live(self):
        for processed in ((), ("other",)):
            self.cycle({str(i): "expired" for i in range(3256)}, processed)
            with self.assertRaises(self.error):
                self.ns["handle"](self.view, once=True)
            idle = next(
                s
                for s in reversed(self.snapshots)
                if s["phase"] == "idle" and s["live"]
            )
            self.assertFalse(idle["healthy"])
            self.assertFalse(idle["ready"])
            self.assertFalse(self.snapshots[-1]["live"])
            progress = self.healths[-1].progress.call_args_list
            self.assertEqual(progress[0].kwargs["timeout_seconds"], 1320)
            self.assertEqual(progress[1].kwargs["timeout_seconds"], 1320)
            self.assertFalse(self.config.bootstrap_enabled)
            self.assertFalse(self.config.repair_expired_incomplete)

    def test_success_ready_and_discovery_failure_unready(self):
        self.cycle({}, ("one",))
        self.ns["handle"](self.view, once=True)
        self.assertTrue(any(s["healthy"] for s in self.snapshots))
        self.snapshots.clear()
        self.ns["discover_workspace_scopes"].side_effect = RuntimeError(
            "db unavailable"
        )
        with self.assertRaises(self.error):
            self.ns["handle"](self.view, once=True)
        self.assertTrue(
            any(s["phase"] == "retrying" and s["live"] for s in self.snapshots)
        )
        self.assertTrue(all(not s["ready"] for s in self.snapshots))

    def test_cycle_progress_is_per_workspace_without_changing_clock_or_mode(self):
        scopes = (SimpleNamespace(workspace_id="a"), SimpleNamespace(workspace_id="b"))
        self.ns["run_workspace"] = Mock()
        progress = Mock()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        self.actual_cycle(
            scopes=scopes,
            skipped=(),
            settings_object=self.ns["settings"],
            config=self.config,
            now=now,
            status_only=True,
            stop=threading.Event(),
            on_error=Mock(),
            on_progress=progress,
        )
        self.assertEqual([c.args[0] for c in progress.call_args_list], list(scopes))
        for call in self.ns["run_workspace"].call_args_list:
            self.assertEqual(call.kwargs["now"], now)
            self.assertTrue(call.kwargs["status_only"])

    def test_signal_handler_sets_stop_and_restores_original(self):
        stop = threading.Event()
        original = signal.getsignal(signal.SIGTERM)
        handlers = self.ns["_install_signal_handlers"](stop)
        try:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            self.assertTrue(stop.is_set())
        finally:
            self.ns["_restore_signal_handlers"](handlers)
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)

    def test_atomic_v2_record_and_legacy_writer_call_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.json"
            now = datetime(2026, 1, 1, tzinfo=UTC)
            self.actual_writer(
                str(path),
                healthy=False,
                observed_at=now,
                detail={"failed_count": 3256},
                live=True,
                ready=False,
                phase="idle",
                progress_at=now,
            )
            value = json.loads(path.read_text())
            self.assertEqual(value["version"], 2)
            self.assertTrue(value["live"])
            self.assertFalse(value["ready"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            self.actual_writer(str(path), healthy=True, observed_at=now, detail={})
            value = json.loads(path.read_text())
            self.assertTrue(value["healthy"] and value["ready"] and value["live"])


if __name__ == "__main__":
    unittest.main()
