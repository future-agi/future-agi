"""Real child-process tests; no Docker and no inherited terminal/environment."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ProcessTests(unittest.TestCase):
    def supervisor(self, **kwargs):
        from deploy.update.processes import ProcessSupervisor
        from deploy.update.contracts import AutomationPolicy
        return ProcessSupervisor(AutomationPolicy(), docker_context='test-context',
                                 environment={'PATH': os.defpath, 'HOME': str(Path.home()),
                                              'DOCKER_HOST': 'evil', 'PYTHONPATH': '/evil',
                                              'SECRET_TOKEN': 'not inherited'},
                                 grace_seconds=0.1, **kwargs)

    def test_noninteractive_clean_environment_and_registered_context(self):
        runner = self.supervisor()
        result = runner([sys.executable, '-I', '-c',
                         'import os,sys,json; print(json.dumps([sys.stdin.read(),os.isatty(0),dict(os.environ)]))'],
                        capture_output=True, text=True)
        stdin, tty, env = json.loads(result.stdout)
        self.assertEqual(stdin, '')
        self.assertFalse(tty)
        self.assertEqual(env['DOCKER_CONTEXT'], 'test-context')
        for key in ('SECRET_TOKEN', 'DOCKER_HOST', 'PYTHONPATH'):
            self.assertNotIn(key, env)

    def test_bounded_output_and_sanitized_exception(self):
        from deploy.update.processes import ProcessError
        from deploy.update.contracts import ReasonCode
        runner = self.supervisor(output_limit=1024)
        with self.assertRaises(ProcessError) as caught:
            runner([sys.executable, '-I', '-c', 'print("secret"*100000)'])
        self.assertEqual(caught.exception.reason_code, ReasonCode.PROCESS_FAILED)
        self.assertNotIn('secret', str(caught.exception))

    def test_timeout_kills_group_including_sigterm_ignoring_descendant(self):
        from deploy.update.processes import ProcessError
        from deploy.update.contracts import ReasonCode
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'survived'
            code = ('import os,signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                    'pid=os.fork(); time.sleep(0.6); pathlib.Path(' + repr(str(marker)) + ').write_text("alive"); '
                    'time.sleep(10)')
            runner = self.supervisor()
            started = time.monotonic()
            with self.assertRaises(ProcessError) as caught:
                runner([sys.executable, '-I', '-c', code], timeout=0.15)
            self.assertEqual(caught.exception.reason_code, ReasonCode.OPERATION_TIMEOUT)
            self.assertLess(time.monotonic() - started, 2)
            time.sleep(0.7)
            self.assertFalse(marker.exists())
            self.assertTrue(runner.uncertain)

    def test_successful_leader_cannot_leave_background_children(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'survived'
            code = ('import os,time,pathlib; pid=os.fork(); '
                    'os._exit(0) if pid else None; time.sleep(0.6); '
                    'pathlib.Path(' + repr(str(marker)) + ').write_text("alive")')
            runner = self.supervisor()
            runner([sys.executable, '-I', '-c', code], timeout=2)
            time.sleep(0.7)
            self.assertFalse(marker.exists())

    def test_sigterm_cleans_owned_descendants_before_propagating(self):
        import signal
        import threading
        from deploy.update.apply import Interrupted
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'survived'
            code = ('import os,time,pathlib; os.fork(); time.sleep(0.6); '
                    'pathlib.Path(' + repr(str(marker)) + ').write_text("alive"); time.sleep(10)')
            timer = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM))
            previous = signal.getsignal(signal.SIGTERM)
            runner = self.supervisor()
            timer.start()
            try:
                with self.assertRaises(Interrupted):
                    runner([sys.executable, '-I', '-c', code], timeout=3)
            finally:
                timer.cancel()
                timer.join()
            self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
            self.assertTrue(runner.uncertain)
            time.sleep(0.7)
            self.assertFalse(marker.exists())

    def test_probe_pull_wait_and_backup_budgets_come_from_policy(self):
        runner = self.supervisor()
        self.assertEqual(runner._budget(['docker', '--context', 'test-context', 'compose', 'pull'], None),
                         runner.policy.pull_timeout_seconds)
        self.assertEqual(runner._budget(['docker', 'compose', 'config'], None),
                         runner.policy.probe_timeout_seconds)
        self.assertEqual(runner._budget(['docker', 'compose', 'up', '-d'], None),
                         runner.policy.wait_timeout_seconds)
        self.assertEqual(runner._budget(['/usr/bin/backup'], runner.policy.backup_timeout_seconds),
                         runner.policy.backup_timeout_seconds)
        self.assertEqual(runner._budget(['docker', 'compose', 'pull'], 0.1), 0.1)

    def test_total_budget_and_context_override_fail_closed(self):
        from deploy.update.processes import ProcessError
        runner = self.supervisor()
        with self.assertRaises(ProcessError):
            runner(['docker', '--context', 'foreign', 'ps'])
        runner.deadline = time.monotonic() - 1
        with self.assertRaises(ProcessError):
            runner([sys.executable, '-I', '-c', 'raise AssertionError()'])
