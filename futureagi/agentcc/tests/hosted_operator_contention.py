"""Two real operator processes; a test-only pause holds the first one's lock."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def install_pause():
    marker = os.environ.get("G18_OPERATOR_PAUSE_FILE")
    if not marker:
        return
    from agentcc.management.commands import hosted_gateway_migrate as operator

    original = operator.MigrationExecutor

    def paused_executor(*args, **kwargs):
        # The real command acquires its advisory lock before constructing this.
        held = Path(marker + ".held")
        held.write_text("synthetic operator lock acquired")
        deadline = time.monotonic() + 20
        while Path(marker).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("synthetic operator pause deadline")
            time.sleep(0.01)
        return original(*args, **kwargs)

    operator.MigrationExecutor = paused_executor


def assert_operator_exclusion(rehearsal):
    with tempfile.TemporaryDirectory(prefix="g18-operator-contention-") as directory:
        marker = Path(directory) / "pause"
        marker.touch()
        held = Path(str(marker) + ".held")
        env = rehearsal.environment(G18_OPERATOR_PAUSE_FILE=str(marker))
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agentcc.tests.hosted_process_entry",
                "hosted_gateway_migrate",
                "check",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 15
            while not held.exists():
                assert child.poll() is None, (
                    "first synthetic operator exited before holding its lock"
                )
                assert time.monotonic() < deadline, (
                    "first synthetic operator did not acquire its lock"
                )
                time.sleep(0.01)
            observed = time.monotonic()
            assert "holds the lock" in rehearsal.run("check", expected=1)
            rehearsal.timings.append(
                {
                    "injected_operator_lock_observation_seconds": round(
                        time.monotonic() - observed, 3
                    )
                }
            )
        finally:
            marker.unlink(missing_ok=True)
            try:
                stdout, stderr = child.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
                raise AssertionError(
                    "first synthetic operator failed to release its lock"
                ) from None
        for value in (rehearsal.source, rehearsal.target):
            assert value not in stdout + stderr
        assert child.returncode == 0, (
            "first synthetic operator did not complete its check"
        )
        rehearsal.run("check")  # lock released and command still usable
