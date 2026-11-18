import io
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call

import pytest
from django.core.management import call_command

from tfc.management.commands import register_temporal_schedules as command_module

# The exit hook prints, so it only arrives if stdout is flushed after the hooks.
_ENTRY = """
import atexit, os, sys, threading
from unittest.mock import AsyncMock, patch

import django

django.setup()

from django.core.management import execute_from_command_line
from tfc.management.commands import register_temporal_schedules as command


class _Teardown:
    def __del__(self, write=os.write):
        write(1, b"interpreter shutdown ran\\n")


def _finish_once_main_is_leaving():
    threading.main_thread().join()
    os.write(1, b"worker finished\\n")


async def _list(client):
    threading.Thread(target=_finish_once_main_is_leaving).start()
    return ["one", "two"]


sys.teardown_marker = _Teardown()
atexit.register(print, "exit hooks ran")
with (
    patch.object(command, "get_client", AsyncMock(return_value=object())),
    patch.object(command, "a_list_schedules", _list),
):
    execute_from_command_line(["manage.py", "register_temporal_schedules", "--list"])
"""


@pytest.fixture
def exit_steps(monkeypatch):
    steps = Mock()
    monkeypatch.setattr(command_module.threading, "_shutdown", steps.threads)
    monkeypatch.setattr(command_module.atexit, "_run_exitfuncs", steps.hooks)
    monkeypatch.setattr(command_module.os, "_exit", steps.exit)
    return steps


@pytest.mark.slow
def test_success_runs_the_exit_steps_and_leaves_without_interpreter_shutdown():
    # Inherited unbuffered output would hide a missing flush.
    env = {
        **{
            name: value
            for name, value in os.environ.items()
            if name != "PYTHONUNBUFFERED"
        },
        "DJANGO_SETTINGS_MODULE": "tfc.settings.test",
        "OTEL_ENABLED": "false",
    }
    result = subprocess.run(
        [sys.executable, "-c", _ENTRY],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-4000:]
    lines = result.stdout.splitlines()
    assert "  - two" in lines
    assert "worker finished" in lines
    assert "exit hooks ran" in lines
    assert "interpreter shutdown ran" not in lines


def test_success_takes_the_exit_steps_in_the_interpreter_order(monkeypatch, exit_steps):
    monkeypatch.setattr(command_module, "get_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(command_module, "a_list_schedules", AsyncMock(return_value=[]))

    command_module.Command().run_from_argv(
        ["manage.py", "register_temporal_schedules", "--skip-checks", "--list"]
    )

    assert exit_steps.mock_calls == [call.threads(), call.hooks(), call.exit(0)]


def test_command_error_keeps_its_status_and_normal_shutdown(monkeypatch, exit_steps):
    get_client = AsyncMock()
    monkeypatch.setattr(command_module, "get_client", get_client)

    with pytest.raises(SystemExit) as exited:
        command_module.Command().run_from_argv(
            ["manage.py", "register_temporal_schedules", "--skip-checks", "--pause", ""]
        )

    assert exited.value.code == 1
    get_client.assert_not_awaited()
    assert exit_steps.mock_calls == []


def test_unexpected_error_is_not_turned_into_success(monkeypatch, exit_steps):
    monkeypatch.setattr(
        command_module, "get_client", AsyncMock(side_effect=RuntimeError("refused"))
    )

    with pytest.raises(RuntimeError, match="refused"):
        command_module.Command().run_from_argv(
            ["manage.py", "register_temporal_schedules", "--skip-checks"]
        )

    assert exit_steps.mock_calls == []


def test_call_command_returns_to_its_caller(monkeypatch, exit_steps):
    monkeypatch.setattr(command_module, "get_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        command_module, "a_list_schedules", AsyncMock(return_value=["one"])
    )
    output = io.StringIO()

    call_command("register_temporal_schedules", "--list", stdout=output)

    assert "  - one" in output.getvalue()
    assert exit_steps.mock_calls == []
