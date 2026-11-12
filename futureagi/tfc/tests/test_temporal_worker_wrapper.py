"""bin/temporal-worker hands the worker's own exit status to its supervisor.

On the prod/staging entrypoint path the container runs
``exec ./bin/temporal-worker``, so the wrapper's status is the container's:
the one a restart policy, Kubernetes or a crash alert sees. A stub ``python``
first on PATH stands in for ``python manage.py start_temporal_worker``.
"""

import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
WRAPPER = BACKEND / "bin" / "temporal-worker"


def _stub_python(directory: Path, body: str) -> dict:
    stub = directory / "python"
    stub.write_text("#!/bin/bash\n" + body + "\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {**os.environ, "PATH": f"{directory}{os.pathsep}{os.environ['PATH']}"}


def _wrapper(env) -> subprocess.Popen:
    return subprocess.Popen(
        ["bash", str(WRAPPER), "--task-queue", "tasks_s"],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


@pytest.mark.parametrize(
    "child,expected",
    [
        ("exit 0", 0),
        ("exit 1", 1),
        ("exit 2", 2),
        ("kill -KILL $$", 128 + signal.SIGKILL),
        ("kill -SEGV $$", 128 + signal.SIGSEGV),
    ],
    ids=["clean", "fatal", "exit-2", "killed", "segfault"],
)
def test_wrapper_exits_with_the_worker_status(tmp_path, child, expected):
    proc = _wrapper(_stub_python(tmp_path, "sleep 0.2\n" + child))
    output, _ = proc.communicate(timeout=60)
    assert proc.returncode == expected, output


@pytest.mark.parametrize(
    "on_term,expected",
    [
        ("trap 'exit 0' TERM", 0),
        ("", 0),
        ("trap 'exit 3' TERM", 3),
    ],
    ids=["graceful", "stops-on-the-signal", "fails-while-stopping"],
)
def test_relayed_shutdown_reports_how_the_worker_stopped(tmp_path, on_term, expected):
    ready = tmp_path / "ready"
    env = _stub_python(
        tmp_path, f'{on_term}\ntouch "{ready}"\nwhile true; do sleep 0.1; done'
    )
    proc = _wrapper(env)
    try:
        deadline = time.monotonic() + 30
        while not ready.exists():
            assert proc.poll() is None, proc.stdout.read()
            assert time.monotonic() < deadline, "stub worker never started"
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        output, _ = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert "Signaling worker to initiate graceful shutdown" in output, output
    assert proc.returncode == expected, output


@pytest.mark.slow
@pytest.mark.parametrize("mode,expected", [("fatal", 1), ("clean", 0)])
def test_wrapper_reports_the_real_command_status(tmp_path, mode, expected):
    from tfc.tests.test_temporal_worker_startup_recovery import _ENTRY

    # exec keeps the wrapper's worker pid on the interpreter running the real
    # command, as with `python manage.py start_temporal_worker`.
    env = _stub_python(
        tmp_path,
        'if [ "$1" = manage.py ] && [ "$2" = start_temporal_worker ]; then\n'
        '  exec "$REAL_PYTHON" -c "$WORKER_ENTRY" "$WORKER_MODE"\n'
        "fi\n"
        'exec "$REAL_PYTHON" "$@"',
    )
    env.update(
        DJANGO_SETTINGS_MODULE="tfc.settings.test",
        OTEL_ENABLED="false",
        REAL_PYTHON=sys.executable,
        WORKER_ENTRY=_ENTRY,
        WORKER_MODE=mode,
    )
    proc = _wrapper(env)
    output, _ = proc.communicate(timeout=600)
    assert ("worker_error" in output) == (mode == "fatal"), output[-3000:]
    assert proc.returncode == expected, output[-3000:]
