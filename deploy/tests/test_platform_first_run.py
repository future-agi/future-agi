"""First-run experience of the standalone app container (deploy/platform):
the boot summary, the boot phase the starting page shows, the JSON 503 the
API port answers while the bootstrap runs, the waiter that switches the UI to
the app, and the nginx wiring that serves the starting page until then.

bootstrap.py is loaded from its file; nothing here imports Django or needs a
running stack."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = ROOT / "deploy" / "platform"

_spec = importlib.util.spec_from_file_location(
    "platform_bootstrap", PLATFORM / "bin" / "bootstrap.py"
)
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)

# Values that must never reach a log line.
SECRETS = {
    "OPENAI_API_KEY": "sk-openai-secret-value",
    "ANTHROPIC_API_KEY": "sk-ant-secret-value",
    "AWS_ACCESS_KEY_ID": "AKIASECRETVALUE",
    "AWS_SECRET_ACCESS_KEY": "aws-secret-value",
    "MAILGUN_API_KEY": "mailgun-secret-value",
    "EE_LICENSE_KEY": "license-secret-value",
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class RunDir(unittest.TestCase):
    """Points the module's /run/futureagi paths at a temporary directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        run = Path(self._tmp.name) / "futureagi"
        for name, value in (
            ("RUN_DIR", run),
            ("STATUS_FILE", run / "status.json"),
            ("UI_READY", run / "ready"),
            ("SUMMARY_SHOWN", run / "summary-shown"),
            ("VERTEX_KEY", Path(self._tmp.name) / "vertex.json"),
        ):
            patcher = mock.patch.object(bootstrap, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.run_dir = run

    def status(self) -> dict:
        return json.loads((self.run_dir / "status.json").read_text())


class BootSummaryTest(RunDir):
    def summary(self, **env) -> str:
        with mock.patch.dict(os.environ, env, clear=True):
            return "\n".join(bootstrap.boot_summary())

    def row(self, label: str, **env) -> str:
        """The value of one row, whatever the label column's width."""
        match = re.search(rf"^  {label} +(.*)$", self.summary(**env), re.M)
        self.assertIsNotNone(match, label)
        return match.group(1)

    def test_names_the_setup_version_and_urls(self):
        text = self.summary(FUTURE_AGI_VERSION="v1.8.0", FRONTEND_PORT="3300")

        self.assertIn("Future AGI · Standalone setup · version v1.8.0", text)
        self.assertIn("http://localhost:3300", text)
        self.assertIn("http://localhost:8000", text)
        self.assertIn("http://localhost:4318", text)
        self.assertIn("docs/configuration.md", text)

    def test_traces_name_the_url_sdks_send_to(self):
        self.assertTrue(self.row("Traces").startswith("http://localhost:4318 "))
        self.assertTrue(
            self.row("Traces", FI_COLLECTOR_OTLP_HTTP_PORT="4400").startswith(
                "http://localhost:4400 "
            )
        )
        self.assertTrue(
            self.row(
                "Traces", FI_COLLECTOR_PUBLIC_URL="https://otel.example.com/"
            ).startswith("https://otel.example.com ")
        )

    def test_public_urls_win_over_ports(self):
        text = self.summary(
            FRONTEND_URL="https://ai.example.com/",
            VITE_HOST_API="https://api.example.com",
        )

        self.assertIn("https://ai.example.com\n", text + "\n")
        self.assertIn("https://api.example.com", text)

    def test_an_unset_version_says_so(self):
        self.assertIn("version not pinned", self.summary(FUTURE_AGI_VERSION="unknown"))

    def test_lists_llm_providers_by_name_only(self):
        text = self.summary(**SECRETS)

        self.assertIn("OpenAI, Anthropic, AWS Bedrock", text)
        for value in SECRETS.values():
            self.assertNotIn(value, text)

    def test_a_partial_key_pair_or_a_placeholder_does_not_count(self):
        text = self.summary(AWS_ACCESS_KEY_ID="AKIA", OPENAI_API_KEY="CHANGEME-set-me")

        self.assertNotIn("Bedrock", text)
        self.assertNotIn("OpenAI", text)
        self.assertIn("none in .env", text)

    def test_a_mounted_vertex_key_counts(self):
        bootstrap.VERTEX_KEY.write_text("{}")

        self.assertIn("Vertex AI", self.summary())

    def test_email(self):
        self.assertTrue(self.row("Email").startswith("off"))
        self.assertEqual(
            self.row(
                "Email", MAILGUN_API_KEY="k", MAILGUN_SENDER_DOMAIN="mg.example.com"
            ),
            "Mailgun (mg.example.com)",
        )
        # settings.py honours an explicit EMAIL_BACKEND over Mailgun, and like
        # tfc.utils.email, the console and dummy backends deliver nothing.
        for backend in ("console", "dummy"):
            env = {
                "MAILGUN_API_KEY": "k",
                "EMAIL_BACKEND": f"django.core.mail.backends.{backend}.EmailBackend",
            }
            self.assertTrue(self.row("Email", **env).startswith("off"), backend)
            self.assertNotEqual(self.row("Password reset", **env), "by email")
        self.assertEqual(
            self.row(
                "Email", EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"
            ),
            "EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend",
        )

    def test_telemetry(self):
        self.assertTrue(self.row("Telemetry").startswith("on"))
        # What the opt-out still sends, as docs/telemetry.md lists it.
        self.assertEqual(
            self.row("Telemetry", FUTURE_AGI_TELEMETRY_DISABLED="true"),
            "off: one registration ping (instance id, version, deployment type, "
            "timestamp) only",
        )

    def test_password_reset(self):
        self.assertIn(
            "docker compose exec app python manage.py reset_password",
            self.row("Password reset"),
        )
        self.assertEqual(
            self.row(
                "Password reset", MAILGUN_API_KEY="k", MAILGUN_SENDER_DOMAIN="mg.x"
            ),
            "by email",
        )
        # An .env made from an older .env.example turned the unsafe mode on.
        for value in ("true", "TRUE"):
            unsafe = self.row("Password reset", OSS_RETURN_PASSWORD_RESET_LINK=value)
            self.assertTrue(unsafe.startswith("UNSAFE:"), value)
            self.assertIn("take over any account", unsafe)
        for value in ("false", "1"):
            self.assertFalse(
                self.row(
                    "Password reset", OSS_RETURN_PASSWORD_RESET_LINK=value
                ).startswith("UNSAFE:"),
                value,
            )

    def test_hosted_service_hooks_are_named_only_when_set(self):
        self.assertEqual(self.row("Other hooks"), "none")
        text = self.summary(
            HUBSPOT_API_TOKEN="pat-secret", SENTRY_DSN="https://k@s.io/1"
        )
        self.assertEqual(
            self.row(
                "Other hooks",
                HUBSPOT_API_TOKEN="pat-secret",
                SENTRY_DSN="https://k@s.io/1",
            ),
            "HubSpot, Sentry",
        )
        self.assertNotIn("pat-secret", text)

    def test_code_evals_sandbox_and_edition(self):
        self.assertIn("built-in sandbox", self.summary())
        self.assertIn("nsjail", self.summary(COMPOSE_PROFILES="ml,sandbox"))
        self.assertIn("Open source", self.summary())
        self.assertIn("Enterprise", self.summary(EE_LICENSE_KEY="x"))

    def test_model_serving(self):
        with mock.patch.object(
            bootstrap.socket, "getaddrinfo", side_effect=socket.gaierror
        ):
            self.assertIn(
                "off (`docker compose --profile ml up -d`",
                self.summary(MODEL_SERVING_URL="http://serving:8080"),
            )
        with mock.patch.object(bootstrap.socket, "getaddrinfo", return_value=[]):
            self.assertEqual(
                self.row("Model serving", MODEL_SERVING_URL="http://serving:8080"),
                "on",
            )
        self.assertTrue(
            self.row("Model serving", MODEL_SERVING_URL="").startswith("off")
        )

    def test_the_dev_setup_is_marked(self):
        # docker-compose.dev.yml sets FI_DEV_RELOAD; bin/start reads it.
        self.assertIn("(dev: hot reload)", self.summary(FI_DEV_RELOAD="true"))
        self.assertNotIn("(dev: hot reload)", self.summary(GRANIAN_RELOAD="true"))
        self.assertNotIn("(dev: hot reload)", self.summary())

    def test_printed_once_per_boot(self):
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            contextlib.redirect_stdout(out),
        ):
            bootstrap.show_summary_once()
            bootstrap.show_summary_once()

        self.assertEqual(out.getvalue().count("Standalone setup"), 1)
        self.assertTrue(
            all(line.startswith("[bootstrap] ") for line in out.getvalue().splitlines())
        )

    def test_a_broken_summary_never_blocks_the_boot(self):
        out = io.StringIO()
        with (
            mock.patch.object(
                bootstrap, "boot_summary", side_effect=RuntimeError("boom")
            ),
            contextlib.redirect_stdout(out),
        ):
            bootstrap.show_summary_once()

        self.assertIn("summary unavailable", out.getvalue())


class PhaseTest(RunDir):
    def test_every_phase_is_written_for_the_starting_page(self):
        for phase in bootstrap.PHASES:
            bootstrap.set_phase(phase)
            status = self.status()
            self.assertEqual(status["phase"], phase)
            self.assertEqual(status["message"], bootstrap.PHASES[phase])
            self.assertEqual(
                status["status"], "ready" if phase == "ready" else "starting"
            )
        self.assertEqual((self.run_dir / "status.json").stat().st_mode & 0o777, 0o644)

    def test_the_migration_phase_tells_a_first_boot_to_wait(self):
        self.assertIn("first boot takes a few minutes", bootstrap.PHASES["migrating"])

    def test_an_unwritable_run_dir_is_not_fatal(self):
        with mock.patch.object(
            bootstrap, "RUN_DIR", Path("/proc/nonexistent/futureagi")
        ):
            bootstrap.write_status("waiting")


class PlaceholderTest(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.server = bootstrap.start_placeholder(self.port)
        self.assertIsNotNone(self.server)
        self.addCleanup(self._stop)

    def _stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def request(self, method="GET", path="/health/"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method=method
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(req, timeout=5)
        return caught.exception

    def test_answers_json_503_starting_with_the_phase(self):
        with mock.patch.object(bootstrap, "_phase", "migrating"):
            error = self.request()

        self.assertEqual(error.code, 503)
        self.assertEqual(error.headers["Content-Type"], "application/json")
        self.assertEqual(error.headers["Retry-After"], "5")
        body = json.loads(error.read())
        self.assertEqual(body["status"], "starting")
        self.assertEqual(body["phase"], "migrating")
        self.assertIn("Migrating the database", body["message"])

    def test_every_method_and_path(self):
        for method, path in (
            ("POST", "/tracer/v1/traces"),
            ("HEAD", "/"),
            ("PUT", "/x"),
        ):
            self.assertEqual(self.request(method, path).code, 503)

    def _handle_error_output(self, error):
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            try:
                raise error
            except type(error):
                self.server.handle_error(None, ("127.0.0.1", 1))
        return stderr.getvalue()

    def test_a_client_that_hangs_up_leaves_no_traceback(self):
        for error in (BrokenPipeError(32, "Broken pipe"), ConnectionResetError()):
            self.assertEqual(self._handle_error_output(error), "")

    def test_other_handler_errors_are_still_reported(self):
        self.assertIn("ValueError", self._handle_error_output(ValueError("bug")))

    def test_a_taken_port_is_left_alone(self):
        self.assertIsNone(bootstrap.start_placeholder(self.port))

    def test_stopping_frees_the_port_for_the_api(self):
        with mock.patch.object(bootstrap, "_placeholder", self.server):
            bootstrap.stop_placeholder()
            self.assertIsNone(bootstrap._placeholder)
        self.server = None
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.port))


class _Health(BaseHTTPRequestHandler):
    healthy_after = 0

    def do_GET(self):
        type(self).healthy_after -= 1
        self.send_response(200 if type(self).healthy_after < 0 else 503)
        self.end_headers()

    def log_message(self, *args):
        pass


class WaitForApiTest(RunDir):
    def serve_health(self, healthy_after: int) -> None:
        _Health.healthy_after = healthy_after
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        patcher = mock.patch.object(
            bootstrap,
            "HEALTH_URL",
            f"http://127.0.0.1:{server.server_address[1]}/health/",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_switches_the_ui_and_prints_the_ready_line(self):
        self.serve_health(healthy_after=2)
        out = io.StringIO()
        env = {"FRONTEND_PORT": "3000"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(out),
        ):
            started = bootstrap.time.time() - 95
            self.assertEqual(bootstrap.wait_for_api(started, poll_seconds=0.01), 0)

        self.assertTrue((self.run_dir / "ready").exists())
        self.assertEqual(self.status()["phase"], "ready")
        line = out.getvalue().strip().splitlines()[-1]
        self.assertRegex(
            line,
            r"^\[bootstrap\] Future AGI is ready in 1m 3[5-6]s\. Open http://localhost:3000$",
        )

    def test_the_boot_start_comes_from_bin_start(self):
        with mock.patch.dict(os.environ, {"FI_BOOT_STARTED_AT": "1000"}):
            self.assertEqual(bootstrap.boot_started_at(5000.0), 1000.0)
        with mock.patch.dict(os.environ, {"FI_BOOT_STARTED_AT": "junk"}):
            self.assertEqual(bootstrap.boot_started_at(5000.0), 5000.0)

    def test_hand_over_reexecutes_as_the_waiter(self):
        """The Django process must not stay in memory next to the API."""
        with mock.patch.object(bootstrap.os, "execv") as execv:
            with (
                self.assertRaises(SystemExit),
                mock.patch.object(bootstrap, "wait_for_api", return_value=0),
            ):
                bootstrap.hand_over(1234.5)

        executable, argv = execv.call_args.args
        self.assertEqual(argv[0], executable)
        self.assertTrue(argv[1].endswith("deploy/platform/bin/bootstrap.py"))
        self.assertEqual(argv[2:], ["--wait-for-api", "1234.5"])

    def test_hand_over_waits_in_process_when_exec_fails(self):
        out = io.StringIO()
        with (
            mock.patch.object(bootstrap.os, "execv", side_effect=OSError("no exec")),
            mock.patch.object(bootstrap, "wait_for_api", return_value=0) as wait,
            contextlib.redirect_stdout(out),
        ):
            with self.assertRaises(SystemExit) as exit_:
                bootstrap.hand_over(1234.5)

        self.assertEqual(exit_.exception.code, 0)
        wait.assert_called_once_with(1234.5)
        self.assertIn("re-exec failed", out.getvalue())

    def test_the_module_loads_without_django(self):
        """Waiter mode re-imports this file; only the bootstrap proper may
        pull Django in."""
        import subprocess
        import sys

        code = (
            "import importlib.util, sys;"
            f"s = importlib.util.spec_from_file_location('b', {str(PLATFORM / 'bin' / 'bootstrap.py')!r});"
            "m = importlib.util.module_from_spec(s); s.loader.exec_module(m);"
            "print(any(n == 'django' or n.startswith('django.') for n in sys.modules))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        self.assertEqual(result.stdout.strip(), "False")

    def test_durations(self):
        self.assertEqual(bootstrap.format_duration(42.4), "42s")
        self.assertEqual(bootstrap.format_duration(125), "2m 05s")


class NginxStartingPageTest(unittest.TestCase):
    conf = (PLATFORM / "nginx.conf").read_text()
    page = (PLATFORM / "starting.html").read_text()

    def test_page_locations_answer_503_until_the_app_is_up(self):
        gate = "if (!-f /run/futureagi/ready) { return 503; }"
        for location in ("location / {", "location ~* \\.html$ {"):
            block = self.conf.split(location, 1)[1].split("}", 1)[0] + "}"
            self.assertIn(gate, block, location)
        self.assertIn("error_page 503 /starting.html;", self.conf)

    def test_assets_and_the_status_file_are_never_gated(self):
        for location in (
            "location = /config.js {",
            "location /assets/ {",
            "location = /starting-status.json {",
        ):
            block = self.conf.split(location, 1)[1].split("}", 1)[0]
            self.assertNotIn("return 503", block, location)
        self.assertIn("alias /run/futureagi/status.json;", self.conf)

    def test_the_marker_and_status_paths_match_the_bootstrap(self):
        self.assertEqual(str(bootstrap.UI_READY), "/run/futureagi/ready")
        self.assertEqual(str(bootstrap.STATUS_FILE), "/run/futureagi/status.json")
        start = (PLATFORM / "bin" / "start").read_text()
        self.assertIn("rm -rf /run/futureagi", start)
        self.assertIn("install -d -m 0755 /run/futureagi", start)
        self.assertIn("export FI_BOOT_STARTED_AT", start)

    def test_the_page_is_self_contained(self):
        # Nothing from the network: an offline install must render it.
        self.assertIsNone(re.search(r"""(src|href)=["']https?://""", self.page))
        self.assertNotIn('<link rel="stylesheet"', self.page)
        self.assertIn("/starting-status.json", self.page)
        self.assertIn('http-equiv="refresh"', self.page)
        self.assertIn("Future AGI is starting", self.page)
        self.assertIn("docker compose logs -f app", self.page)

    def test_every_file_nginx_reads_from_etc_nginx_is_in_the_image(self):
        """A file nginx.conf names but the Dockerfile never copies falls back
        silently (the starting page became a plain-text 503)."""
        directives = "\n".join(line.split("#", 1)[0] for line in self.conf.splitlines())
        paths = set(re.findall(r"/etc/nginx/[\w.-]+", directives))
        for block in re.findall(r"root /etc/nginx;([^}]*)", directives):
            for entries in re.findall(r"try_files ([^;]+);", block):
                paths.update(
                    f"/etc/nginx{entry}"
                    for entry in entries.split()
                    if entry.startswith("/")
                )
        # Shipped by the nginx package.
        paths.discard("/etc/nginx/mime.types")
        self.assertIn("/etc/nginx/starting.html", paths)

        dockerfile = (PLATFORM / "Dockerfile").read_text()
        copied = set(re.findall(r"^COPY [^-\s]\S* (/etc/nginx/\S+)$", dockerfile, re.M))
        # A Windows checkout's CRLF line endings are stripped from each one.
        crlf = dockerfile.split("RUN sed -i 's/\\r$//'", 1)[1].split("&&", 1)[0]
        for path in sorted(paths):
            self.assertIn(path, copied)
            self.assertIn(path, crlf.split())

    def test_the_web_program_starts_first(self):
        conf = (PLATFORM / "supervisord.conf").read_text()
        web = conf.split("[program:web]", 1)[1].split("[", 1)[0]
        self.assertIn("priority=10", web)


if __name__ == "__main__":
    unittest.main()
