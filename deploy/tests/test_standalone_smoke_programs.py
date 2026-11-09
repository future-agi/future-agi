"""Exercise the standalone CI supervisor smoke block without Docker or services.

Run with: python3 -I -B deploy/tests/test_standalone_smoke_programs.py
The docker shell stub supplies fixture status/config; the workflow's actual awk
and shell logic run unchanged, with an explicit, credential-free environment.
"""

import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/standalone-ci.yml"
SUPERVISOR = ROOT / "deploy/standalone/supervisord.conf"
CONFIG = """\
[program:api]
autostart=true
[program:sandbox]
autostart=false
autorestart=false
[program:bootstrap]
autostart=true
"""


def smoke(status, config=CONFIG, *, config_rc=0, status_rc=0):
    source = WORKFLOW.read_text()
    start = source.index("          # A program that went FATAL")
    marker = '          pass "every supervised program is running"'
    end = source.index(marker, start) + len(marker)
    block = textwrap.dedent(source[start:end])
    # Do not source the workflow's service/login helpers. Only these two exact
    # docker commands are supported, and neither invokes Docker.
    harness = r'''
set -euo pipefail
pass() { echo "PASS  $*"; }
fail() { echo "::error::$*"; exit 1; }
docker() {
  if [ "$*" = "compose exec -T app supervisorctl -c /etc/futureagi/supervisord.conf status" ]; then
    printf '%s\n' "$FIXTURE_STATUS"
    return "$FIXTURE_STATUS_RC"
  elif [ "$#" -eq 7 ] && [ "$1 $2 $3 $4 $5" = "compose exec -T app awk" ] && [ "$7" = /etc/futureagi/supervisord.conf ]; then
    if [ "$FIXTURE_CONFIG_RC" -ne 0 ]; then return "$FIXTURE_CONFIG_RC"; fi
    command awk "$6" "$FIXTURE_CONFIG"
  else
    echo "unexpected docker command" >&2
    return 97
  fi
}
'''
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "supervisord.conf"
        path.write_text(config)
        return subprocess.run(
            ["/bin/bash", "--noprofile", "--norc"],
            input=harness + block + "\n",
            env={
                "PATH": "/usr/bin:/bin",
                "FIXTURE_CONFIG": str(path),
                "FIXTURE_STATUS": status,
                "FIXTURE_CONFIG_RC": str(config_rc),
                "FIXTURE_STATUS_RC": str(status_rc),
            },
            text=True,
            capture_output=True,
            timeout=5,
        )


class StandaloneSmokePrograms(unittest.TestCase):
    def assert_pass(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS  every supervised program is running", result.stdout)

    def assert_down(self, result, name):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::error::supervised programs not running:", result.stdout)
        self.assertIn(name, result.stdout)
        self.assertNotIn("PASS  every supervised program is running", result.stdout)

    def test_sandbox_stopped_and_api_running_pass(self):
        result = smoke("api RUNNING pid 12\nsandbox STOPPED Not started")
        self.assert_pass(result)
        self.assertIn("Accepted STOPPED (autostart=false): sandbox", result.stderr)

    def test_shipped_configuration_accepts_stopped_sandbox(self):
        self.assert_pass(smoke("api RUNNING pid 12\nsandbox STOPPED Not started", SUPERVISOR.read_text()))

    def test_api_stopped_still_fails(self):
        self.assert_down(smoke("api STOPPED Not started\nsandbox STOPPED Not started"), "api")

    def test_sandbox_fatal_backoff_and_starting_still_fail(self):
        for state in ("FATAL", "BACKOFF", "STARTING", "STOPPING", "UNKNOWN"):
            with self.subTest(state=state):
                self.assert_down(smoke(f"api RUNNING pid 12\nsandbox {state} trouble"), "sandbox")

    def test_bootstrap_exited_still_passes(self):
        self.assert_pass(smoke("api RUNNING pid 12\nbootstrap EXITED completed"))

    def test_manually_started_sandbox_passes(self):
        self.assert_pass(smoke("api RUNNING pid 12\nsandbox RUNNING pid 13"))

    def test_all_running_does_not_need_any_stopped_allowance(self):
        self.assert_pass(smoke("api RUNNING pid 12", "[program:api]\nautostart=true\n"))

    def test_unknown_and_similar_names_stopped_fail(self):
        for name in ("worker", "sandbox-extra", "group:sandbox"):
            with self.subTest(name=name):
                self.assert_down(smoke(f"api RUNNING pid 12\n{name} STOPPED Not started"), name)

    def test_names_come_from_config_not_a_sandbox_exception(self):
        config = CONFIG.replace("program:sandbox", "program:optional-worker")
        result = smoke("api RUNNING pid 12\noptional-worker STOPPED Not started", config)
        self.assert_pass(result)
        self.assertIn("Accepted STOPPED (autostart=false): optional-worker", result.stderr)
        self.assert_down(smoke("sandbox STOPPED Not started", config), "sandbox")

    def test_whitespace_comments_and_multiple_optional_programs(self):
        config = """\
[program:api]
autostart=true
  [program:first]
  autostart = false ; intentionally disabled
[program:second]
autostart=false
"""
        result = smoke("api RUNNING pid 12\nfirst STOPPED Not started\nsecond STOPPED Not started", config)
        self.assert_pass(result)
        for name in ("first", "second"):
            self.assertIn(f"Accepted STOPPED (autostart=false): {name}", result.stderr)

    def test_other_sections_and_commented_setting_do_not_allow_stopped(self):
        configs = (
            "[program:api]\n; autostart=false\n",
            "[program:api]\nautostart=true\n[group:optional]\nautostart=false\n",
            "[program:api]\nautostart=true\n[eventlistener:optional]\nautostart=false\n",
        )
        for config in configs:
            with self.subTest(config=config):
                self.assert_down(smoke("api STOPPED Not started", config), "api")

    def test_empty_status_still_fails(self):
        self.assert_down(smoke(""), "no status")

    def test_status_nonzero_with_valid_output_is_handled_as_before(self):
        self.assert_pass(smoke("api RUNNING pid 12\nsandbox STOPPED Not started", status_rc=3))

    def test_unreadable_configuration_fails_closed(self):
        result = smoke("api RUNNING pid 12\nsandbox STOPPED Not started", config_rc=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PASS  every supervised program is running", result.stdout)


if __name__ == "__main__":
    unittest.main()
