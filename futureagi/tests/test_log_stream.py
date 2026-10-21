"""Where the application's console logs go (LOG_STREAM).

stdout by default: every long-running service keeps logging where Docker,
supervisord, Kubernetes and log shippers read it today. ``./bin/dev manage``
sends them to stderr, so a command whose output is meant for a file
(``sqlmigrate``, ``dumpdata``) writes only that output to stdout.

No Docker: bin/dev runs against a fake ``docker`` that records its arguments.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tfc.logging.config import get_logging_config

ROOT = Path(__file__).resolve().parents[2]


def console_stream(tmp_path: Path) -> str:
    return get_logging_config(str(tmp_path))["handlers"]["console"]["stream"]


def test_console_logs_go_to_stdout_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("LOG_STREAM", raising=False)
    assert console_stream(tmp_path) == "ext://sys.stdout"


@pytest.mark.parametrize("value", ["stderr", " STDERR "])
def test_log_stream_stderr_moves_console_logs_to_stderr(monkeypatch, tmp_path, value):
    monkeypatch.setenv("LOG_STREAM", value)
    assert console_stream(tmp_path) == "ext://sys.stderr"


@pytest.mark.parametrize("value", ["", "stdout", "file"])
def test_any_other_log_stream_keeps_stdout(monkeypatch, tmp_path, value):
    monkeypatch.setenv("LOG_STREAM", value)
    assert console_stream(tmp_path) == "ext://sys.stdout"


def test_dev_manage_sends_the_command_logs_to_stderr(tmp_path):
    record = tmp_path / "docker.jsonl"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['DEV_TEST_RECORD'], 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    docker.chmod(0o755)
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "bin" / "dev"),
            "--standalone",
            "manage",
            "sqlmigrate",
            "accounts",
            "0001",
        ],
        env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "DEV_TEST_RECORD": str(record),
            "COMPOSE_PROJECT_NAME": "devtest",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    exec_call = next(call for call in calls if "exec" in call)
    options = exec_call[exec_call.index("exec") + 1 : exec_call.index("app")]
    assert "LOG_STREAM=stderr" in options
    assert options[options.index("LOG_STREAM=stderr") - 1] == "-e"
    assert exec_call[exec_call.index("app") + 1 :] == [
        "python",
        "manage.py",
        "sqlmigrate",
        "accounts",
        "0001",
    ]
