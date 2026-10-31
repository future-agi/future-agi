"""Readiness (deploy/update/readiness.py): bin/install's readiness loop, without Docker.

A fake installation serves scripted container listings and app logs, a fake
clock advances only when the loop sleeps, and a fake probe answers per URL
from a schedule, so every wait runs instantly and deterministically.
"""

from __future__ import annotations

import http.client
import http.server
import io
import os
import re
import shlex
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update import readiness as readiness_module  # noqa: E402
from deploy.update.install import CommandResult, Installation  # noqa: E402
from deploy.update.readiness import NotReady, Readiness, bounded, http_ok, standalone_settings  # noqa: E402

START = 1000.0
BACKEND = "http://localhost:8000/health/"
FRONTEND = "http://localhost:3000/"


def app(id="c-new", status="running", restarts=0, exit_code=0, started_at="2026-10-19T00:00:00Z", service="app"):
    return {"id": id, "service": service, "image_id": "sha256:" + "a" * 64, "image": "futureagi/standalone:v1.1.0",
            "status": status, "exit_code": exit_code, "restart_count": restarts, "health": "none",
            "started_at": started_at, "command": []}


class Clock:
    def __init__(self):
        self.now = START
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    @property
    def elapsed(self):
        return self.now - START


class FakeInstallation:
    """containers() and `compose logs` follow scripts indexed by elapsed time."""

    def __init__(self, clock, env=None):
        self.clock = clock
        self.env = dict(env or {})
        self.listing = lambda t: [app()]   # elapsed seconds -> containers
        self.applied = lambda t: 0         # elapsed seconds -> "Applying " lines in the app log
        self.calls = []
        self.hint = "docker compose"       # what compose_hint() answers

    def compose_hint(self):
        return self.hint

    def containers(self):
        self.calls.append(("containers",))
        return [dict(c) for c in self.listing(self.clock.elapsed)]

    def compose_run(self, *args, check=True, stream=False, timeout=None):
        self.calls.append(("compose", args, check))
        lines = "".join(f"app-1  |   Applying app.{n:04d}_migration... OK\n" for n in range(self.applied(self.clock.elapsed)))
        return CommandResult(["docker", "compose", *args], 0, "app-1  | starting\n" + lines, "")


class Probe:
    """Answers each URL from a predicate on elapsed seconds (default: always up)."""

    def __init__(self, clock):
        self.clock = clock
        self.rules = {}
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return self.rules.get(url, lambda t: True)(self.clock.elapsed)


class ReadinessCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.inst = FakeInstallation(self.clock)
        self.probe = Probe(self.clock)
        self.out = io.StringIO()
        self.ready = Readiness(self.inst, self.out, probe=self.probe, clock=self.clock, sleep=self.clock.sleep,
                               interval=5.0)

    def wait(self, previous_ids=()):
        return self.ready.wait_standalone(previous_ids)

    def not_ready(self, previous_ids=()):
        with self.assertRaises(NotReady) as ctx:
            self.wait(previous_ids)
        return str(ctx.exception)


# ------------------------------------------------------------------ bounded --

class AutomaticReadinessTests(unittest.TestCase):
    """Synthetic guard samples, NOT product or large-volume qualification."""
    def setUp(self):
        from dataclasses import replace
        from deploy.update.contracts import AutomationPolicy, AutomaticUpdateEstimate
        from deploy.update.readiness import AutomaticReadiness, RuntimeSample
        self.clock = Clock()
        self.policy = AutomationPolicy(stability_period_seconds=10, migration_timeout_seconds=100,
            migration_stall_timeout_seconds=15, wait_timeout_seconds=60, disk_watermark_reserve_bytes=10)
        self.estimate = AutomaticUpdateEstimate(1, 30, 1, 0, 100, 100, 100, 100, 100, 200, 30, 15)
        self.sample_type = RuntimeSample
        self.waiter = AutomaticReadiness(clock=self.clock, sleep=self.clock.sleep, interval=5)
        self.observe = lambda t: RuntimeSample('ready', '', '', 1000, {'a': (True, True, 0, 'start')})

    def wait(self, *, previous=False):
        return self.waiter.wait(lambda: self.observe(self.clock.elapsed), policy=self.policy,
            estimate=self.estimate, deadline=START + 200, previous=previous, expected_ids={'a'})

    def failure(self, reason):
        from deploy.update.readiness import AutomaticNotReady
        with self.assertRaises(AutomaticNotReady) as caught:
            self.wait()
        self.assertEqual(caught.exception.reason_code.value, reason)

    def test_health_and_every_restart_count_remain_stable_for_the_full_period(self):
        self.wait()
        self.assertEqual(self.clock.elapsed, 10)
        for change in ('health', 'restart', 'replacement', 'started'):
            with self.subTest(change=change):
                self.setUp()
                def observe(t):
                    rows = {'a': (True, not(t >= 5 and change == 'health'),
                        int(t >= 5 and change == 'restart'), 'new' if t >= 5 and change == 'started' else 'start')}
                    if t >= 5 and change == 'replacement':
                        rows = {'b': (True, True, 0, 'start')}
                    return self.sample_type('ready', '', '', 1000, rows)
                self.observe = observe
                self.failure('readiness_failed')

    def test_unchanged_phase_name_and_pg_views_stall_at_the_bounded_timeout(self):
        self.observe = lambda t: self.sample_type('migrating', 'tfc.0002', 'fixed-view', 1000,
                                                  {'a': (True, False, 0, 'start')})
        self.failure('migration_stalled')
        self.assertEqual(self.clock.elapsed, 15)

    def test_each_real_progress_source_resets_the_stall_timer_but_not_the_hard_deadline(self):
        for source in ('phase', 'migration', 'pg'):
            with self.subTest(source=source):
                self.setUp()
                self.observe = lambda t: self.sample_type('p' + str(t) if source == 'phase' else 'migrating',
                    str(t) if source == 'migration' else 'tfc.0002', str(t) if source == 'pg' else 'fixed',
                    1000, {'a': (True, False, 0, 'start')})
                self.failure('migration_exceeds_window')
                self.assertEqual(self.clock.elapsed, 30)  # estimate already includes 2x; NOT 60

    def test_migration_completion_gets_a_separate_startup_budget(self):
        self.observe = lambda t: self.sample_type('migrating' if t < 25 else 'api', str(t), str(t), 1000,
                                                  {'a': (True, t >= 70, 0, 'start')})
        self.wait()
        self.assertEqual(self.clock.elapsed, 80)

    def test_disk_watermark_is_independent_of_progress_and_health(self):
        self.observe = lambda t: self.sample_type('ready', '', '', 99, {'a': (True, True, 0, 'start')})
        self.failure('disk_watermark_abort')
        self.assertEqual(self.clock.elapsed, 0)

    def test_missing_estimates_sizes_and_ids_fail_closed(self):
        self.estimate = None
        self.failure('restore_capacity_unknown')
        self.setUp()
        self.observe = lambda t: self.sample_type('ready', '', '', None, {'a': (True, True, 0, 'start')})
        self.failure('restore_capacity_unknown')

    def test_slow_probe_cannot_cross_a_deadline_and_succeed(self):
        def slow(t):
            self.clock.now += 200
            return self.sample_type('ready', '', '', 1000, {'a': (True, True, 0, 'start')})
        self.observe = slow
        self.failure('readiness_failed')

    def test_late_ready_or_healthy_sample_cannot_erase_migration_deadline(self):
        # Reviewer's slow-observation reproduction, plus both independent ways
        # completion can be inferred and the exact deadline boundary.
        for phase, healthy in (('ready', False), ('migrating', True), ('api', True)):
            for arrived in (30, 31):
                with self.subTest(phase=phase, healthy=healthy, arrived=arrived):
                    self.setUp()
                    calls = []
                    def observe(t):
                        calls.append(t)
                        if len(calls) == 1:
                            return self.sample_type('migrating', 'tfc.0002', 'fixed', 1000,
                                                    {'a': (True, False, 0, 'start')})
                        self.clock.now = START + arrived
                        return self.sample_type(phase, '', '', 1000, {'a': (True, healthy, 0, 'start')})
                    self.observe = observe
                    self.failure('migration_exceeds_window')
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(self.clock.elapsed, arrived)

    def test_zero_plan_has_no_hidden_migration_allocation(self):
        from dataclasses import replace
        self.estimate = replace(self.estimate, migration_seconds=0, migration_deadline_seconds=0)
        self.observe = lambda t: self.sample_type('migrating', 'tfc.0002', 'pg', 1000,
                                                  {'a': (True, False, 0, 'start')})
        self.failure('readiness_failed')
        self.assertEqual(self.clock.elapsed, 60)

    def test_source_start_reconciliation_has_no_migration_or_reextract_authority(self):
        self.observe = lambda t: self.sample_type('ready', '', '', 1000, {'a': (True, True, 3, 'start')})
        self.wait(previous=True)
        self.assertEqual(self.clock.elapsed, 10)


class BoundedTests(unittest.TestCase):
    def test_default_when_missing_or_empty(self):
        self.assertEqual(bounded({}, "K", 600, 60, 1800), 600)
        self.assertEqual(bounded({"K": ""}, "K", 600, 60, 1800), 600)

    def test_bounds_are_inclusive(self):
        self.assertEqual(bounded({"K": "60"}, "K", 600, 60, 1800), 60)
        self.assertEqual(bounded({"K": "1800"}, "K", 600, 60, 1800), 1800)
        self.assertEqual(bounded({"K": "0600"}, "K", 600, 60, 1800), 600)

    def test_out_of_bounds(self):
        for raw in ("59", "1801", "0", "99999999999999999999"):
            with self.subTest(raw=raw):
                with self.assertRaises(NotReady) as ctx:
                    bounded({"K": raw}, "K", 600, 60, 1800)
                self.assertEqual(str(ctx.exception), f"K must be an integer in [60,1800] (got {raw!r})")

    def test_non_integers(self):
        for raw in ("ten", "1.5", "-60", "+60", " 60", "60 ", "6e2", "60s", "60\n", "٦٠", "0x3c"):
            with self.subTest(raw=raw):
                with self.assertRaises(NotReady) as ctx:
                    bounded({"K": raw}, "K", 600, 60, 1800)
                self.assertIn("K must be an integer in [60,1800]", str(ctx.exception))

    def test_a_bad_default_is_reported_too(self):
        with self.assertRaises(NotReady):
            bounded({}, "K", 5, 60, 1800)

    def test_standalone_settings(self):
        self.assertEqual(standalone_settings({}), (600, 15, 3600))
        self.assertEqual(standalone_settings({"INSTALL_READY_TIMEOUT_SECONDS": "120", "INSTALL_STABILITY_SECONDS": "5",
                                              "INSTALL_READY_MAX_SECONDS": "7200"}), (120, 5, 7200))
        for key, bad in (("INSTALL_READY_TIMEOUT_SECONDS", "1801"), ("INSTALL_STABILITY_SECONDS", "4"),
                         ("INSTALL_READY_MAX_SECONDS", "299")):
            with self.subTest(key=key):
                with self.assertRaises(NotReady) as ctx:
                    standalone_settings({key: bad})
                self.assertTrue(str(ctx.exception).startswith(f"{key} must be an integer in ["))

    def test_standalone_settings_take_bin_installs_defaults_and_bounds(self):
        # The same `bounded_env_int KEY DEFAULT MIN MAX` lines bin/install runs.
        lines = re.findall(r"^bounded_env_int (INSTALL_[A-Z_]+) ([0-9]+) ([0-9]+) ([0-9]+)$",
                           (ROOT / "bin" / "install").read_text(encoding="utf-8"), re.MULTILINE)
        keys = ("INSTALL_READY_TIMEOUT_SECONDS", "INSTALL_STABILITY_SECONDS", "INSTALL_READY_MAX_SECONDS")
        self.assertEqual([line[0] for line in lines], list(keys))
        self.assertEqual(standalone_settings({}), tuple(int(line[1]) for line in lines))
        for index, (key, _, minimum, maximum) in enumerate(lines):
            for raw in (minimum, maximum):
                with self.subTest(key=key, raw=raw):
                    self.assertEqual(standalone_settings({key: raw})[index], int(raw))
            for raw in (str(int(minimum) - 1), str(int(maximum) + 1)):
                with self.subTest(key=key, raw=raw):
                    with self.assertRaises(NotReady) as ctx:
                        standalone_settings({key: raw})
                    self.assertEqual(str(ctx.exception), f"{key} must be an integer in [{minimum},{maximum}] "
                                                         f"(got {raw!r})")


# ------------------------------------------------------------------ http_ok --

class FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class HttpOkTests(unittest.TestCase):
    def opener(self, result):
        opener = mock.Mock()
        if isinstance(result, BaseException):
            opener.open.side_effect = result
        else:
            opener.open.return_value = FakeResponse(result)
        patcher = mock.patch.object(readiness_module, "build_opener", return_value=opener)
        build = patcher.start()
        self.addCleanup(patcher.stop)
        return build, opener

    def test_bypasses_every_proxy(self):
        build, opener = self.opener(200)
        self.assertTrue(http_ok("http://localhost:8000/health/"))
        (handler,), _ = build.call_args
        self.assertIsInstance(handler, urllib.request.ProxyHandler)
        self.assertEqual(handler.proxies, {})
        opener.open.assert_called_once_with("http://localhost:8000/health/", timeout=3.0)

    def test_timeout_is_passed(self):
        _, opener = self.opener(200)
        http_ok("http://localhost:1/", timeout=0.5)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 0.5)

    def test_status_codes(self):
        for status, ok in ((200, True), (204, True), (301, True), (399, True), (400, False), (404, False),
                           (500, False), (199, False)):
            with self.subTest(status=status):
                self.opener(status)
                self.assertIs(http_ok("http://localhost:8000/health/"), ok)

    def test_errors_are_not_ready(self):
        errors = (
            HTTPError("http://localhost:8000/health/", 503, "Service Unavailable", {}, io.BytesIO(b"down")),
            URLError(ConnectionRefusedError(61, "Connection refused")),
            ConnectionResetError(54, "Connection reset by peer"),
            socket.timeout("timed out"),
            OSError("network is unreachable"),
            ValueError("unknown url type"),
            http.client.BadStatusLine("garbage"),
            http.client.RemoteDisconnected("Remote end closed connection without response"),
            http.client.IncompleteRead(b""),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.opener(error)
                self.assertIs(http_ok("http://localhost:8000/health/"), False)

    def test_an_error_answer_is_closed(self):
        error = HTTPError("http://localhost:8000/health/", 500, "Internal Server Error", {}, io.BytesIO(b"boom"))
        self.opener(error)
        with mock.patch.object(error, "close") as close:
            self.assertFalse(http_ok("http://localhost:8000/health/"))
        close.assert_called_once_with()
        error.close()

    def test_an_error_answer_that_cannot_be_closed(self):
        error = HTTPError("http://localhost:8000/health/", 502, "Bad Gateway", {}, io.BytesIO())
        self.opener(error)
        with mock.patch.object(error, "close", side_effect=KeyError("file")):
            self.assertFalse(http_ok("http://localhost:8000/health/"))
        error.close()

    def test_real_server_is_reached_although_a_proxy_is_exported(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200 if self.path == "/health/" else 404)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        # A proxy that refuses everything: a probe that used it could not succeed.
        proxy = {"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "no_proxy": "",
                 "NO_PROXY": ""}
        with mock.patch.dict(os.environ, proxy):
            self.assertTrue(http_ok(f"http://127.0.0.1:{port}/health/"))
            self.assertFalse(http_ok(f"http://127.0.0.1:{port}/missing"))

    def test_nothing_listening(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.assertFalse(http_ok(f"http://127.0.0.1:{port}/health/", timeout=1.0))


# ------------------------------------------------------- wait_standalone: ok --

class WaitStandaloneSuccessTests(ReadinessCase):
    def test_returns_after_the_stability_window(self):
        self.wait()
        self.assertEqual(self.clock.elapsed, 15)
        self.assertEqual(self.clock.sleeps, [5.0, 5.0, 5.0])
        self.assertIn(BACKEND, self.probe.urls)
        self.assertIn(FRONTEND, self.probe.urls)

    def test_custom_stability_window(self):
        self.inst.env["INSTALL_STABILITY_SECONDS"] = "30"
        self.wait()
        self.assertEqual(self.clock.elapsed, 30)

    def test_waits_for_the_api_and_the_ui(self):
        self.probe.rules[BACKEND] = lambda t: t >= 40
        self.probe.rules[FRONTEND] = lambda t: t >= 60
        self.wait()
        self.assertEqual(self.clock.elapsed, 75)

    def test_uses_the_ports_from_env(self):
        self.inst.env.update(BACKEND_PORT="18000", FRONTEND_PORT="13000")
        self.wait()
        self.assertEqual(set(self.probe.urls), {"http://localhost:18000/health/", "http://localhost:13000/"})

    def test_waits_for_the_container_to_run(self):
        self.inst.listing = lambda t: [app(status="created" if t < 20 else "running")]
        self.wait()
        self.assertEqual(self.clock.elapsed, 35)

    def test_waits_for_the_container_to_exist(self):
        self.inst.listing = lambda t: [] if t < 10 else [app()]
        self.wait()
        self.assertEqual(self.clock.elapsed, 25)

    def test_other_services_are_ignored(self):
        self.inst.listing = lambda t: [app(id="c-db", service="postgres", status="exited", exit_code=1,
                                           restarts=9), app()]
        self.wait()
        self.assertEqual(self.clock.elapsed, 15)

    def test_bad_settings_fail_before_anything_runs(self):
        self.inst.env["INSTALL_READY_MAX_SECONDS"] = "10"
        message = self.not_ready()
        self.assertEqual(message, "INSTALL_READY_MAX_SECONDS must be an integer in [300,7200] (got '10')")
        self.assertEqual(self.inst.calls, [])
        self.assertEqual(self.probe.urls, [])

    def test_reads_the_migration_count_from_the_app_logs(self):
        self.wait()
        self.assertEqual(self.inst.calls[0], ("compose", ("logs", "--no-color", "--tail", "2000", "app"), False))


class StabilityTests(ReadinessCase):
    def test_a_new_start_time_restarts_the_window(self):
        self.inst.listing = lambda t: [app(started_at="A" if t < 10 else "B")]
        self.wait()
        self.assertEqual(self.clock.elapsed, 25)

    def test_a_replaced_container_restarts_the_window(self):
        self.inst.listing = lambda t: [app(id="c-1" if t < 10 else "c-2")]
        self.wait()
        self.assertEqual(self.clock.elapsed, 25)

    def test_a_single_restart_restarts_the_window(self):
        self.inst.listing = lambda t: [app(restarts=0 if t < 10 else 1, started_at="A" if t < 10 else "B")]
        self.wait()
        self.assertEqual(self.clock.elapsed, 25)

    def test_a_failed_probe_restarts_the_window(self):
        self.probe.rules[BACKEND] = lambda t: t != 10
        self.wait()
        self.assertEqual(self.clock.elapsed, 30)

    def test_a_flapping_ui_never_settles(self):
        self.inst.env["INSTALL_READY_TIMEOUT_SECONDS"] = "60"
        self.probe.rules[FRONTEND] = lambda t: t % 10 != 0  # down every 10s, at the deadline too
        message = self.not_ready()
        self.assertIn("still waiting on the UI on port 3000", message)


class MigrationWindowTests(ReadinessCase):
    def setUp(self):
        super().setUp()
        self.inst.env.update(INSTALL_READY_TIMEOUT_SECONDS="60", INSTALL_READY_MAX_SECONDS="300")
        self.probe.rules[BACKEND] = lambda t: False

    def test_without_migrations_the_deadline_is_the_ready_timeout(self):
        self.inst.applied = lambda t: 3
        message = self.not_ready()
        self.assertEqual(self.clock.elapsed, 60)
        self.assertNotIn("migrations in progress", self.out.getvalue())
        self.assertIn("still waiting on the API (/health/ on port 8000)", message)

    def test_climbing_migrations_extend_up_to_the_ready_max(self):
        self.inst.applied = lambda t: int(t) + 1
        self.not_ready()
        self.assertEqual(self.clock.elapsed, 300)
        self.assertIn("  migrations in progress (6 applied), extending the readiness window\n", self.out.getvalue())

    def test_extension_is_from_the_last_progress(self):
        # Progress until 100s, then none: the deadline is 100 + 60.
        self.inst.applied = lambda t: min(int(t), 100)
        self.not_ready()
        self.assertEqual(self.clock.elapsed, 160)

    def test_the_api_answering_after_migrations_succeeds(self):
        self.inst.applied = lambda t: min(int(t), 200)
        self.probe.rules[BACKEND] = lambda t: t >= 205
        self.wait()
        self.assertEqual(self.clock.elapsed, 220)

    def test_migrations_are_only_counted_while_the_api_is_down(self):
        self.probe.rules[BACKEND] = lambda t: True
        self.inst.applied = lambda t: int(t)
        self.wait()
        self.assertEqual([c for c in self.inst.calls if c[0] == "compose"],
                         [("compose", ("logs", "--no-color", "--tail", "2000", "app"), False)])


# ------------------------------------------------------------- crash loops --

class CrashLoopTests(ReadinessCase):
    def test_container_from_before_counts_from_the_first_look(self):
        self.inst.listing = lambda t: [app(id="c-old", restarts=5)]
        self.wait(previous_ids=["c-old"])
        self.assertEqual(self.clock.elapsed, 15)

    def test_container_from_before_restarting_twice_is_a_crash_loop(self):
        self.inst.listing = lambda t: [app(id="c-old", restarts=5 if t < 10 else 7, started_at=str(t))]
        message = self.not_ready(previous_ids=["c-old"])
        self.assertEqual(message, "the app container keeps restarting (2 restarts during this update)")
        self.assertEqual(self.clock.elapsed, 10)

    def test_container_created_by_the_rollout_counts_every_restart(self):
        self.inst.listing = lambda t: [app(id="c-new", restarts=2)]
        message = self.not_ready(previous_ids=["c-old"])
        self.assertEqual(message, "the app container keeps restarting (2 restarts during this update)")
        self.assertEqual(self.clock.elapsed, 0)

    def test_without_previous_ids_every_container_is_new(self):
        self.inst.listing = lambda t: [app(id="c-any", restarts=3, exit_code=0)]
        self.assertIn("keeps restarting (3 restarts during this update)", self.not_ready())

    def test_one_restart_of_a_new_container_is_tolerated(self):
        self.inst.listing = lambda t: [app(id="c-new", restarts=1)]
        self.wait(previous_ids=["c-old"])
        self.assertEqual(self.clock.elapsed, 15)

    def test_last_exit_code_is_reported(self):
        self.inst.listing = lambda t: [app(id="c-new", status="restarting", restarts=2, exit_code=137)]
        self.assertEqual(self.not_ready(),
                         "the app container keeps restarting (2 restarts during this update, last exit code 137)")

    def test_restarting_status_after_two_restarts_is_a_crash_loop_at_once(self):
        self.inst.listing = lambda t: [app(id="c-old", status="restarting", restarts=2, exit_code=1)]
        message = self.not_ready(previous_ids=["c-old"])
        self.assertEqual(message, "the app container keeps restarting (Docker is restarting it again after "
                                  "2 restarts, last exit code 1)")
        self.assertEqual(self.clock.elapsed, 0)

    def test_restarting_status_after_one_restart_is_waited_on(self):
        self.inst.listing = lambda t: [app(id="c-old", status="restarting" if t < 10 else "running", restarts=1)]
        self.wait(previous_ids=["c-old"])
        self.assertEqual(self.clock.elapsed, 25)

    def test_exited_or_dead_is_fatal(self):
        for status in ("exited", "dead"):
            with self.subTest(status=status):
                self.setUp()
                self.inst.listing = lambda t, s=status: [app(status=s, exit_code=3)]
                self.assertEqual(self.not_ready(), f"the app container stopped (status={status} exit_code=3)")
                self.assertEqual(self.clock.elapsed, 0)

    def test_a_reset_restart_count_is_not_negative(self):
        self.inst.listing = lambda t: [app(id="c-old", restarts=6 if t < 10 else 0, started_at="A" if t < 10 else "B")]
        self.wait(previous_ids=["c-old"])
        self.assertEqual(self.clock.elapsed, 25)

    def test_a_new_container_is_watched_rather_than_an_old_one(self):
        # docker lists the newest first; an old exited container of the
        # service must not be mistaken for the new one.
        self.inst.listing = lambda t: [app(id="c-old", status="exited", exit_code=137), app(id="c-new")]
        self.wait(previous_ids=["c-old"])
        self.assertEqual(self.clock.elapsed, 15)

    def test_unknown_restart_count_counts_as_none(self):
        self.inst.listing = lambda t: [app(restarts=None)]
        self.wait()
        self.assertEqual(self.clock.elapsed, 15)


# ------------------------------------------------------------------ timeouts --

class TimeoutMessageTests(ReadinessCase):
    def setUp(self):
        super().setUp()
        self.inst.env["INSTALL_READY_TIMEOUT_SECONDS"] = "60"

    def test_still_starting(self):
        self.probe.rules[BACKEND] = lambda t: False
        message = self.not_ready()
        self.assertEqual(self.clock.elapsed, 60)
        self.assertEqual(message, "the stack did not become ready in time, still waiting on the API (/health/ on port "
                                  "8000). It may still be starting: watch `docker compose logs -f app`, or raise "
                                  "INSTALL_READY_MAX_SECONDS (up to 7200) in .env")

    def test_still_waiting_on_the_ui(self):
        self.probe.rules[FRONTEND] = lambda t: False
        self.assertIn("still waiting on the UI on port 3000. It may still be starting", self.not_ready())

    def test_still_waiting_on_the_container(self):
        self.inst.listing = lambda t: [app(status="created")]
        self.assertIn("still waiting on the app container to run.", self.not_ready())

    def test_missing_app_container(self):
        self.inst.listing = lambda t: [app(service="postgres")]
        message = self.not_ready()
        self.assertIn("still waiting on the app container to exist. It may still be starting", message)

    def test_default_deadline_is_ten_minutes(self):
        del self.inst.env["INSTALL_READY_TIMEOUT_SECONDS"]
        self.probe.rules[BACKEND] = lambda t: False
        self.not_ready()
        self.assertEqual(self.clock.elapsed, 600)

    def test_a_container_that_restarted_keeps_crashing(self):
        self.inst.listing = lambda t: [app(id="c-new", restarts=1)]
        self.probe.rules[BACKEND] = lambda t: False
        message = self.not_ready(previous_ids=["c-old"])
        self.assertEqual(message, "the stack did not become ready in time: the app container keeps crashing "
                                  "(1 restart). Its log says why: `docker compose logs app`")
        self.assertNotIn("may still be starting", message)

    def test_a_container_with_a_failed_exit_keeps_crashing(self):
        self.inst.listing = lambda t: [app(id="c-old", status="restarting", restarts=1, exit_code=3)]
        message = self.not_ready(previous_ids=["c-old"])
        self.assertIn("the app container keeps crashing (exit code 3, 0 restarts)", message)

    def test_exit_code_and_restarts_are_both_reported(self):
        self.inst.listing = lambda t: [app(id="c-new", status="restarting", restarts=1, exit_code=2)]
        self.assertIn("keeps crashing (exit code 2, 1 restart)", self.not_ready())

    def test_restarts_before_the_update_do_not_count(self):
        self.inst.listing = lambda t: [app(id="c-old", restarts=4)]
        self.probe.rules[BACKEND] = lambda t: False
        self.assertIn("It may still be starting", self.not_ready(previous_ids=["c-old"]))


# --------------------------------------------------------------- wait_api --

class WaitApiTests(ReadinessCase):
    def test_returns_once_the_api_answers(self):
        self.probe.rules[BACKEND] = lambda t: t >= 20
        self.ready.wait_api()
        self.assertEqual(self.clock.elapsed, 20)
        self.assertEqual(set(self.probe.urls), {BACKEND})

    def test_returns_at_once_when_it_answers(self):
        self.ready.wait_api()
        self.assertEqual(self.clock.sleeps, [])

    def test_times_out(self):
        self.probe.rules[BACKEND] = lambda t: False
        with self.assertRaises(NotReady) as ctx:
            self.ready.wait_api()
        self.assertEqual(str(ctx.exception), "the API did not answer /health/ on port 8000 within 180s")
        self.assertEqual(self.clock.elapsed, 180)

    def test_custom_port_and_timeout(self):
        self.inst.env["BACKEND_PORT"] = "18000"
        self.probe.rules["http://localhost:18000/health/"] = lambda t: False
        with self.assertRaises(NotReady) as ctx:
            self.ready.wait_api(seconds=30)
        self.assertEqual(str(ctx.exception), "the API did not answer /health/ on port 18000 within 30s")
        self.assertEqual(self.clock.elapsed, 30)

    def test_does_not_look_at_containers(self):
        self.ready.wait_api()
        self.assertEqual(self.inst.calls, [])


class DefaultsTests(unittest.TestCase):
    def test_defaults(self):
        inst = FakeInstallation(Clock())
        ready = Readiness(inst, io.StringIO())
        self.assertIs(ready.probe, http_ok)
        self.assertEqual(ready.interval, 5.0)


# ------------------------------------------------------- round 2 fixes --

class OneOffContainerTests(ReadinessCase):
    """A `docker compose run app ...` container is not the app service's container."""

    def test_a_one_off_app_container_is_never_watched(self):
        # Docker lists the newest first: the one-off would be picked as "created by the rollout".
        self.inst.listing = lambda t: [dict(app(id="c-run", status="exited", exit_code=1), oneoff=True),
                                       app(id="c-new")]
        self.wait(previous_ids=["c-old"])
        self.assertEqual(self.clock.elapsed, 15)

    def test_only_one_off_containers_means_no_app_container_yet(self):
        self.inst.env["INSTALL_READY_TIMEOUT_SECONDS"] = "60"
        self.inst.listing = lambda t: [dict(app(id="c-run"), oneoff=True)]
        self.assertIn("still waiting on the app container to exist", self.not_ready())


class HardeningNewestContainerTests(ReadinessCase):
    def test_of_two_containers_the_rollout_created_the_newest_is_watched(self):
        # Docker lists the newest first; an older one that exited (a replaced attempt) is not the app.
        self.inst.listing = lambda t: [app(id="c-newer"), app(id="c-new", status="exited", exit_code=1)]
        self.wait(previous_ids=["c-old"])
        self.assertEqual(self.clock.elapsed, 15)
        self.inst.listing = lambda t: [app(id="c-newer", status="exited", exit_code=3), app(id="c-new")]
        self.assertEqual(self.not_ready(previous_ids=["c-old"]),
                         "the app container stopped (status=exited exit_code=3)")


class ComposeHintMessageTests(ReadinessCase):
    """The commands in readiness failures are the ones that inspect this install."""

    def setUp(self):
        super().setUp()
        self.inst.env["INSTALL_READY_TIMEOUT_SECONDS"] = "60"
        self.inst.hint = "/opt/compose/bin/docker-compose"

    def test_still_starting_names_the_install_compose_command(self):
        self.probe.rules[BACKEND] = lambda t: False
        self.assertIn("watch `/opt/compose/bin/docker-compose logs -f app`", self.not_ready())

    def test_crash_loop_names_it_too(self):
        self.inst.listing = lambda t: [app(id="c-new", restarts=1)]
        self.probe.rules[BACKEND] = lambda t: False
        self.assertIn("Its log says why: `/opt/compose/bin/docker-compose logs app`",
                      self.not_ready(previous_ids=["c-old"]))



# ------------------------------------------------------------------ round 4 --

class CheckoutInstallation(FakeInstallation):
    """The fake, printing its Compose command the way Installation does (cd_prefix and all)."""

    def __init__(self, clock, root, caller_cwd):
        super().__init__(clock)
        self.root = Path(root).resolve()
        self.caller_cwd = Path(caller_cwd)
        self.compose = ["docker", "compose"]
        self.production = False
        self.environ = {"PATH": os.defpath}
        self.env_file = self.root / ".env"

    def compose_hint(self):
        return Installation.compose_hint(self)

    def cd_prefix(self):
        return Installation.cd_prefix(self)

    def relative(self, path):
        return Installation.relative(self, path)


class Round4CommandsPastedElsewhereTests(ReadinessCase):
    """F2 (S15): the Compose commands readiness prints work pasted where bin/update was run."""

    def use_checkout(self, caller_cwd):
        self.inst = CheckoutInstallation(self.clock, self.root, caller_cwd)
        self.ready = Readiness(self.inst, self.out, probe=self.probe, clock=self.clock, sleep=self.clock.sleep,
                               interval=5.0)

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "future agi"
        self.root.mkdir()

    def backticked(self, text):
        return shlex.split(text[text.index("`") + 1:text.rindex("`")])

    def test_run_elsewhere_the_log_command_starts_in_the_checkout(self):
        self.use_checkout(self.root.parent)
        self.inst.listing = lambda t: [app(restarts=1, exit_code=3)]
        self.probe.rules[BACKEND] = lambda t: False
        text = self.not_ready(previous_ids=["c-new"])
        self.assertEqual(self.backticked(text), ["cd", str(self.root), "&&", "docker", "compose", "logs", "app"])

    def test_the_follow_command_starts_in_the_checkout_only_when_run_elsewhere(self):
        self.use_checkout(self.root.parent)
        self.probe.rules[BACKEND] = lambda t: False
        text = self.not_ready()
        self.assertEqual(self.backticked(text), ["cd", str(self.root), "&&", "docker", "compose", "logs", "-f", "app"])
        # Run in the checkout itself: the command as before.
        self.clock.now = START
        self.use_checkout(self.root)
        self.assertEqual(self.backticked(self.not_ready()), ["docker", "compose", "logs", "-f", "app"])


if __name__ == "__main__":
    unittest.main()
