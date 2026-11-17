"""GET /ready: the code executor reads not ready while a trivial Python eval cannot
run in its sandbox, so a roll onto hosts where the sandbox cannot start stops by
itself. /health keeps checking only the egress policy."""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

EXECUTOR = Path(__file__).resolve().parents[1] / "code-executor"
NOT_READY = "503 Service Unavailable"
PENDING_SENTENCE = "The code sandbox's self-test has not finished yet."
PENDING = (NOT_READY, {"status": "error", "data": PENDING_SENTENCE})
PASSED = {"status": "success", "data": {"result": 1.0, "reason": "bool"}}
# The customer run's default limit (DEFAULT_TIMEOUT), which is also the patience.
RUN_LIMIT = 30
SELF_TEST = ("def evaluate():\n    return True\n", {}, RUN_LIMIT)
NSJAIL_STDERR = "[F][1] runChild():486 Launching child process failed"


class StopLoop(Exception):
    """Ends the self-test loop in a test."""


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_server():
    """server.py with falcon stubbed. patch.dict drops each module first imported
    under it. json is imported at the top of this file so that it stays: C json
    looks json.decoder up in sys.modules to report a parse error."""
    guard = load_module("egress_guard", EXECUTOR / "egress_guard.py")
    falcon = SimpleNamespace(App=mock.Mock, HTTP_503=NOT_READY)
    with mock.patch.dict(sys.modules, {"falcon": falcon, "egress_guard": guard}):
        return load_module("executor_server_ready_under_test", EXECUTOR / "server.py")


class ReadyTestCase(unittest.TestCase):
    """No real sandbox, loop thread or clock: the jailed run is a fake that records
    its calls, and time stands still until a test moves it."""

    def setUp(self):
        self.server = load_server()
        directory = tempfile.TemporaryDirectory(prefix="executor-ready-")
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.now = 1000.0
        self.runs = []
        self.result = PASSED
        self.real_jail = self.server._execute_python_nsjail
        self.server.SELF_TEST_DIR = self.directory
        self.server.NSJAIL_AVAILABLE = True
        self.server._execute_python_nsjail = self.jail
        self.server._prepare_network = lambda: None
        self.server.threading = mock.MagicMock()
        self.server.time = SimpleNamespace(monotonic=lambda: self.now)

    def jail(self, code, input_data, timeout):
        self.runs.append((code, input_data, timeout))
        return self.result

    def ready(self):
        resp = SimpleNamespace(status="200 OK", media=None)
        self.server.ReadyResource().on_get(None, resp)
        return resp.status, resp.media

    def start_blocked_self_test(self):
        """Start a self-test whose run waits until the test sets self.release."""
        self.release = threading.Event()
        entered = threading.Event()

        def blocked(code, input_data, timeout):
            entered.set()
            self.release.wait(10)
            return self.jail(code, input_data, timeout)

        self.server._execute_python_nsjail = blocked
        thread = threading.Thread(target=self.server._run_self_test_when_due)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(self.release.set)
        self.assertTrue(entered.wait(10))
        return thread


class ReadyTests(ReadyTestCase):
    def test_not_ready_before_the_first_self_test_finishes(self):
        self.assertEqual(self.ready(), PENDING)

    def test_ready_once_a_self_test_passed(self):
        self.server._run_self_test_when_due()
        self.assertEqual(self.runs, [SELF_TEST])
        self.assertEqual(self.ready(), ("200 OK", {"status": "ok"}))

    def test_a_failed_self_test_reads_not_ready_at_once_with_the_runs_sentence(self):
        server = self.server
        server._run_self_test_when_due()
        self.assertEqual(self.ready()[0], "200 OK")
        self.now += server.SELF_TEST_INTERVAL
        server._execute_python_nsjail = self.real_jail
        server.NSJAIL_PATH = "/synthetic/nsjail"
        server.UID_MAP_PATH = os.path.join(self.directory, "uid_map")
        Path(server.UID_MAP_PATH).write_text("0 100000 65536\n")
        commands = []

        def nsjail(argv, **kwargs):
            commands.append(argv)
            kwargs["stderr"].write(NSJAIL_STDERR.encode())
            return SimpleNamespace(returncode=255)

        with (
            mock.patch.object(server, "_write_run_script", return_value="/x/eval.py"),
            mock.patch.object(server.subprocess, "run", side_effect=nsjail),
        ):
            server._run_self_test_when_due()
        status, body = self.ready()
        sentence = server._sandbox_unavailable()
        self.assertEqual(status, NOT_READY)
        self.assertEqual(body, {"status": "error", "data": sentence})
        self.assertIn("userns-remap", sentence)
        self.assertEqual([argv[0] for argv in commands], ["/synthetic/nsjail"])

    def test_a_running_self_test_keeps_ready_for_the_run_limit_then_flips(self):
        self.server._run_self_test_when_due()
        self.now += self.server.SELF_TEST_INTERVAL
        self.start_blocked_self_test()
        self.now += RUN_LIMIT
        self.assertEqual(self.ready()[0], "200 OK")
        self.now += 1
        self.assertEqual(self.ready(), PENDING)

    def test_a_result_with_no_self_test_after_it_goes_stale(self):
        self.server._run_self_test_when_due()
        self.now += self.server.SELF_TEST_INTERVAL + RUN_LIMIT
        self.assertEqual(self.ready()[0], "200 OK")
        self.now += 1
        self.assertEqual(self.ready(), PENDING)

    def test_ready_never_runs_the_sandbox_itself(self):
        self.server._run_self_test_when_due()
        with (
            mock.patch.object(self.server.subprocess, "run") as run,
            mock.patch.object(self.server.subprocess, "Popen") as popen,
        ):
            for _ in range(5):
                self.assertEqual(self.ready()[0], "200 OK")
            self.now += 1000
            self.assertEqual(self.ready(), PENDING)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual((run.call_count, popen.call_count), (0, 0))

    def test_one_self_test_per_interval_across_concurrent_workers(self):
        first = self.start_blocked_self_test()
        waits = []

        def worker():
            waits.append(self.server._run_self_test_when_due())

        others = [threading.Thread(target=worker) for _ in range(3)]
        for thread in others:
            thread.start()
        for thread in others:
            thread.join(10)
        self.assertEqual(waits, [self.server.SELF_TEST_INTERVAL] * 3)
        self.release.set()
        first.join(10)
        self.assertEqual(self.runs, [SELF_TEST])
        self.server._execute_python_nsjail = self.jail
        self.now += self.server.SELF_TEST_INTERVAL - 1
        self.assertEqual(self.server._run_self_test_when_due(), 1)
        self.assertEqual(len(self.runs), 1)
        self.now += 1
        self.server._run_self_test_when_due()
        self.assertEqual(len(self.runs), 2)

    def test_a_corrupt_result_reads_not_ready_until_the_next_self_test(self):
        result = json.dumps({"failure": None, "finished_at": self.now})
        path = Path(self.directory, self.server.SELF_TEST_STATE)
        path.write_text(result[: len(result) // 2])
        self.assertEqual(self.ready(), PENDING)
        self.server._run_self_test_when_due()
        self.assertEqual(self.ready(), ("200 OK", {"status": "ok"}))

    def test_a_result_another_container_left_in_tmp_does_not_count(self):
        self.server._run_self_test_when_due()
        with mock.patch.object(self.server, "_container", return_value="restarted"):
            self.assertEqual(self.ready(), PENDING)
            self.server._run_self_test_when_due()
            self.assertEqual(len(self.runs), 2)
            self.assertEqual(self.ready()[0], "200 OK")

    def test_a_container_is_known_by_when_its_pid_1_started(self):
        self.server.INIT_STAT_PATH = os.path.join(self.directory, "stat")
        fields = " ".join(f"f{number}" for number in range(3, 53))
        Path(self.server.INIT_STAT_PATH).write_text(f"1 (gunicorn: (x)) {fields}\n")
        self.assertEqual(self.server._container(), "f22")

    def test_each_worker_starts_one_self_test_loop_in_a_daemon_thread(self):
        resource = self.server.ReadyResource()
        for _ in range(3):
            resource.on_get(None, SimpleNamespace(status="200 OK", media=None))
        thread = self.server.threading.Thread
        self.assertEqual(thread.call_count, 1)
        self.assertIs(thread.call_args.kwargs["target"], self.server._self_test_loop)
        self.assertTrue(thread.call_args.kwargs["daemon"])
        thread.return_value.start.assert_called_once_with()

    def test_ready_needs_the_egress_policy(self):
        self.server._run_self_test_when_due()
        refusal = {"status": "error", "error_code": "EGRESS_POLICY_UNAVAILABLE"}
        self.server._prepare_network = lambda: refusal
        self.assertEqual(self.ready(), (NOT_READY, refusal))

    def test_the_loop_logs_an_error_and_tries_again_after_the_interval(self):
        outcomes = [OSError("read-only file system"), 4.0]
        waits = []

        def run_when_due():
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        def sleep(seconds):
            waits.append(seconds)
            if len(waits) == 2:
                raise StopLoop

        self.server._run_self_test_when_due = run_when_due
        self.server.time = SimpleNamespace(monotonic=lambda: self.now, sleep=sleep)
        with self.assertLogs(level="ERROR") as logs, self.assertRaises(StopLoop):
            self.server._self_test_loop()
        self.assertEqual(waits, [self.server.SELF_TEST_INTERVAL, 4.0])
        self.assertIsInstance(logs.records[0].exc_info[1], OSError)


class HealthTests(ReadyTestCase):
    def test_health_answers_as_before_while_the_self_test_fails(self):
        self.result = {"status": "error", "data": "The sandbox could not start."}
        self.server._run_self_test_when_due()
        self.assertEqual(self.ready()[0], NOT_READY)
        resp = SimpleNamespace(status="200 OK", media=None)
        self.server.HealthResource().on_get(None, resp)
        self.assertEqual(resp.status, "200 OK")
        self.assertEqual(resp.media["status"], "ok")
        self.assertEqual(
            set(resp.media), {"status", "nsjail", "python", "node", "egress_ports"}
        )


if __name__ == "__main__":
    unittest.main()
