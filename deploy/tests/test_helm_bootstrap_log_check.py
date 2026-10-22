"""The upgrade proof must drain kubectl logs without masking producer failures."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("mode", ["success", "missing", "producer_failure"])
def test_upgrade_bootstrap_log_check(tmp_path, mode):
    script = (
        Path(__file__).resolve().parents[1] / "helm/futureagi/hack/kind-smoke.sh"
    ).read_text()
    start = script.index(
        'kubectl -n "$ns" logs "job/$release-bootstrap"',
        script.index('say "upgrade in place:'),
    )
    end = script.index('kubectl -n "$ns" rollout status', start)
    check = script[start:end]
    kubectl = tmp_path / "kubectl"
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
    result = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            'ns=fixture; release=fixture; admin_email=smoke@example.com; work="$1"; '
            'fail() { printf "%s\\n" "$*" >&2; exit 1; };\n' + check,
            "bootstrap-log-check",
            str(tmp_path),
        ],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "LOG_MODE": mode},
        capture_output=True,
        text=True,
        timeout=10,
    )
    if mode == "success":
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
