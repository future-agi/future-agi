"""The upgrade proof must drain kubectl logs without masking producer failures."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BootstrapLogCheckTests(unittest.TestCase):
    def run_check(self, mode):
        script = (
            Path(__file__).resolve().parents[1] / "helm/futureagi/hack/kind-smoke.sh"
        ).read_text()
        start = script.index(
            'kubectl -n "$ns" logs "job/$release-bootstrap"',
            script.index('say "upgrade in place:'),
        )
        end = script.index('kubectl -n "$ns" rollout status', start)
        check = script[start:end]
        with tempfile.TemporaryDirectory() as directory:
            kubectl = Path(directory) / "kubectl"
            kubectl.write_text(
                f"#!{sys.executable}\n"
                "import os, signal, sys\n"
                "signal.signal(signal.SIGPIPE, signal.SIG_DFL)\n"
                "assert sys.argv[1:] == ['-n', 'fixture', 'logs', 'job/fixture-bootstrap']\n"
                "if os.environ['LOG_MODE'] != 'missing':\n"
                "    print('first admin smoke@example.com already exists: left unchanged', flush=True)\n"
                "sys.stdout.write('bootstrap detail\\n' * 100000)\n"
                "sys.stdout.flush()\n"
                "sys.exit(7 if os.environ['LOG_MODE'] == 'producer_failure' else 0)\n"
            )
            kubectl.chmod(0o700)
            return subprocess.run(
                [
                    "bash",
                    "-euo",
                    "pipefail",
                    "-c",
                    'ns=fixture; release=fixture; admin_email=smoke@example.com; work="$1"; '
                    'fail() { printf "%s\\n" "$*" >&2; exit 1; };\n' + check,
                    "bootstrap-log-check",
                    directory,
                ],
                env={
                    **os.environ,
                    "PATH": f"{directory}:{os.environ['PATH']}",
                    "LOG_MODE": mode,
                },
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

    def test_matching_large_log_succeeds(self):
        result = self.run_check("success")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_message_fails(self):
        self.assertNotEqual(self.run_check("missing").returncode, 0)

    def test_log_producer_failure_fails(self):
        self.assertEqual(self.run_check("producer_failure").returncode, 7)


if __name__ == "__main__":
    unittest.main()
