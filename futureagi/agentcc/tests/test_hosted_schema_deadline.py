"""No database or hosted resources: CLI, budget, progress and child wiring."""

import argparse
import inspect
import io
import os
import signal
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from agentcc.hosted_migration_progress import (
    DEFAULT_SCHEMA_DEADLINE_SECONDS,
    MigrationProgress,
    schema_deadline_seconds,
    schema_phase_deadline,
)
from agentcc.management.commands.hosted_gateway_migrate import Command
from agentcc.tests import hosted_rehearsal_cases
from agentcc.tests.hosted_rehearsal_cases import argument_parser
from agentcc.tests.hosted_rehearsal_process import run_operator
from agentcc.tests.hosted_rehearsal_support import Rehearsal
from agentcc.tests.test_gateway_key_operator import operator_environment
from agentcc.tests.test_hosted_startup import hosted_settings


@pytest.mark.parametrize("seconds", [None, "17", "900", "86400"])
def test_command_and_rehearsal_schema_budget(seconds):
    flags = [] if seconds is None else ["--schema-deadline-seconds", seconds]
    actual = (
        Command()
        .create_parser("manage.py", "hosted_gateway_migrate")
        .parse_args(["schema", *flags])
    )
    wrapper = argument_parser().parse_args(["empty", *flags])
    assert (
        actual.schema_deadline_seconds
        == wrapper.schema_deadline_seconds
        == (900 if seconds is None else int(seconds))
    )


@pytest.mark.parametrize(
    "value", ["0", "-1", "86401", "1.5", "nan", "inf", "no", "", "１２", "9" * 5000]
)
def test_nonsense_deadlines_refused(value):
    with pytest.raises(argparse.ArgumentTypeError):
        schema_deadline_seconds(value)
    with pytest.raises(CommandError):
        Command().create_parser("manage.py", "hosted_gateway_migrate").parse_args(
            ["schema", "--schema-deadline-seconds", value]
        )
    with pytest.raises(SystemExit):
        argument_parser().parse_args(["empty", "--schema-deadline-seconds", value])


@pytest.mark.parametrize("value", [0, -1, True, 1.5, float("inf"), None])
def test_programmatic_nonsense_refused_before_connection(value):
    env = operator_environment()
    with (
        patch.dict(os.environ, env, clear=True),
        override_settings(**hosted_settings(env)),
    ):
        with patch(
            "agentcc.management.commands.hosted_gateway_migrate.connections"
        ) as connections:
            with pytest.raises(CommandError, match="schema deadline"):
                call_command(
                    "hosted_gateway_migrate", "schema", schema_deadline_seconds=value
                )
            connections.__getitem__.assert_not_called()


def test_progress_is_flushed_and_reports_each_migration_elapsed():
    output = MagicMock()
    with patch(
        "agentcc.hosted_migration_progress.time.monotonic", side_effect=[10, 11, 14.5]
    ):
        progress = MigrationProgress(output)
        migration = SimpleNamespace(
            app_label="prism", name="0046_org_config_db_defaults"
        )
        progress("render_start")
        progress("apply_start", migration, False)
        progress("apply_success", migration, False)
    lines = [call.args[0] for call in output.write.call_args_list]
    assert lines == [
        "migration=prism.0046_org_config_db_defaults status=started elapsed_seconds=0.000 phase_elapsed_seconds=1.000",
        "migration=prism.0046_org_config_db_defaults status=applied elapsed_seconds=3.500 phase_elapsed_seconds=4.500",
    ]
    assert output.flush.call_count == 2


def test_real_alarm_expires_and_restores_handler():
    prior = signal.getsignal(signal.SIGALRM)
    with pytest.raises(CommandError, match="exceeded 1s"):
        with schema_phase_deadline(1):
            try:
                signal.pause()
            except Exception:
                pytest.fail(
                    "historical catch-all must not swallow deadline cancellation"
                )
    assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)
    assert signal.getsignal(signal.SIGALRM) == prior


def test_deadline_cleanup_on_error_and_existing_timer_refusal():
    with pytest.raises(RuntimeError):
        with schema_phase_deadline(900):
            raise RuntimeError("synthetic")
    assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)
    with (
        patch(
            "agentcc.hosted_migration_progress.signal.getitimer", return_value=(60, 0)
        ),
        patch("agentcc.hosted_migration_progress.signal.setitimer") as arm,
    ):
        with pytest.raises(CommandError, match="existing alarm"):
            with schema_phase_deadline(900):
                pytest.fail("must not replace outer watchdog")
        arm.assert_not_called()


@pytest.mark.parametrize("seconds", [900, 17])
def test_wrapper_passes_budget_and_keeps_process_grace(seconds):
    rehearsal = object.__new__(Rehearsal)
    rehearsal.schema_deadline_seconds = seconds
    rehearsal.operator_timeout_seconds = 640
    rehearsal.environment = MagicMock(return_value={})
    rehearsal.source, rehearsal.target = "synthetic-source", "synthetic-target"
    rehearsal.timings = []
    result = subprocess.CompletedProcess(
        [], 0, "schema-phase elapsed_seconds=204.560\n", ""
    )
    with patch(
        "agentcc.tests.hosted_rehearsal_support.run_operator", return_value=result
    ) as run:
        rehearsal.run("schema", "--apply")
    assert run.call_args.args[0][-2:] == ["--schema-deadline-seconds", str(seconds)]
    assert run.call_args.kwargs["timeout"] == seconds + 30
    assert rehearsal.timings[-1]["schema_phase_seconds"] == 204.56
    assert rehearsal.timings[-1]["apply"] is True
    with patch(
        "agentcc.tests.hosted_rehearsal_support.run_operator", return_value=result
    ) as run:
        rehearsal.run("backfill")
    assert run.call_args.kwargs["timeout"] == 640
    assert "--schema-deadline-seconds" not in run.call_args.args[0]


# A measured empty-install operator phase took 236 s against the old fixed
# 180 s wrapper, so the wrapper killed a healthy install. Non-schema phases now
# get a configurable wrapper timeout that defaults to the schema budget.
MEASURED_EMPTY_INSTALL_SECONDS = 236
SYNTHETIC_REHEARSAL_ENV = {
    "G18_SYNTHETIC_REHEARSAL": "1",
    "OMEGA_EXEC_SOURCE_HEAD": "synthetic-revision",
    "PG_HOST": "localhost",
    "PG_PORT": "5432",
    "PG_USER": "synthetic",
    "PG_PASSWORD": "synthetic-password",
}


def synthetic_rehearsal(**kwargs):
    # Construction reads the environment only; no database is opened here.
    with patch.dict(os.environ, SYNTHETIC_REHEARSAL_ENV):
        return Rehearsal(**kwargs)


def wrapper_timeout(rehearsal, phase, *args):
    rehearsal.environment = MagicMock(return_value={})
    result = subprocess.CompletedProcess([], 0, "", "")
    with patch(
        "agentcc.tests.hosted_rehearsal_support.run_operator", return_value=result
    ) as run:
        rehearsal.run(phase, *args)
    return run.call_args.kwargs["timeout"]


@pytest.mark.parametrize("phase", ["backfill", "check", "key-transition"])
def test_default_operator_wrapper_covers_the_measured_install(phase):
    rehearsal = synthetic_rehearsal()
    timeout = wrapper_timeout(rehearsal, phase)
    assert timeout >= 600
    assert timeout > MEASURED_EMPTY_INSTALL_SECONDS
    assert (
        timeout >= rehearsal.schema_deadline_seconds == DEFAULT_SCHEMA_DEADLINE_SECONDS
    )


@pytest.mark.parametrize("seconds", [17, 640, 86400])
def test_operator_wrapper_timeout_is_configurable(seconds):
    assert "operator_timeout_seconds" in inspect.signature(Rehearsal).parameters
    rehearsal = synthetic_rehearsal(operator_timeout_seconds=seconds)
    assert wrapper_timeout(rehearsal, "backfill") == seconds
    assert wrapper_timeout(rehearsal, "check") == seconds
    # The schema phase keeps its own deadline plus process grace.
    assert wrapper_timeout(rehearsal, "schema", "--apply") == 930
    parsed, unknown = argument_parser().parse_known_args(
        ["empty", "--operator-timeout-seconds", str(seconds)]
    )
    assert unknown == []
    assert parsed.operator_timeout_seconds == seconds


def test_operator_wrapper_cli_default_and_refusals():
    parsed, unknown = argument_parser().parse_known_args(["empty"])
    assert unknown == []
    default = getattr(parsed, "operator_timeout_seconds", None)
    assert default is not None and default >= 600
    assert default >= parsed.schema_deadline_seconds
    for value in ["0", "-1", "86401", "1.5", "nan", "", "１２", "9" * 5000]:
        with pytest.raises(SystemExit):
            argument_parser().parse_args(["empty", "--operator-timeout-seconds", value])
        with pytest.raises(argparse.ArgumentTypeError):
            synthetic_rehearsal(operator_timeout_seconds=value)


@pytest.mark.parametrize(
    ("case", "volumes"), [("empty", 1), ("migrations", 3), ("keys", 1), ("backend", 1)]
)
def test_case_watchdog_never_preempts_a_valid_phase_budget(case, volumes):
    watchdog = getattr(hosted_rehearsal_cases, "case_watchdog_seconds", None)
    assert callable(watchdog)
    for schema, operator in [(900, 900), (900, 3600), (17, 640)]:
        seconds = watchdog(
            case, schema_deadline_seconds=schema, operator_timeout_seconds=operator
        )
        assert seconds >= volumes * (schema + 30 + operator)


def test_child_progress_relay_and_capture(capsys):
    result = run_operator(
        [
            sys.executable,
            "-c",
            "import sys; print('schema-phase deadline_seconds=900', flush=True); print('migration=prism.synthetic status=applied elapsed_seconds=0.001', flush=True); print('private diagnostic', file=sys.stderr)",
        ],
        env={},
        timeout=10,
    )
    assert result.returncode == 0
    assert "migration=prism.synthetic" in capsys.readouterr().out
    assert "deadline_seconds=900" in result.stdout
    assert "private diagnostic" in result.stderr


def test_child_watchdog_kills_and_reaps():
    with pytest.raises(subprocess.TimeoutExpired):
        run_operator(
            [sys.executable, "-c", "import time; time.sleep(60)"], env={}, timeout=0.1
        )


def test_command_wires_budget_progress_and_preserves_sql_timeouts():
    env = operator_environment()
    output = io.StringIO()
    connection = MagicMock(vendor="postgresql")
    connection.settings_dict = {"OPTIONS": {"original": "kept"}}
    connections = MagicMock()
    connections.databases = {"default_direct": {}}
    connections.__getitem__.return_value = connection
    executor = MagicMock()
    executor.migration_plan.return_value = []
    migration = SimpleNamespace(app_label="prism", name="synthetic")
    module = "agentcc.management.commands.hosted_gateway_migrate"
    with (
        patch.dict(os.environ, env, clear=True),
        override_settings(**hosted_settings(env)),
    ):
        with (
            patch(module + ".connections", connections),
            patch(module + ".schema_phase_deadline") as budget,
            patch(module + ".MigrationExecutor", return_value=executor) as factory,
            patch(module + ".phase_targets", return_value=[]),
            patch(module + ".validate_plan"),
        ):

            def migrate(*args, **kwargs):
                assert (
                    connection.settings_dict["OPTIONS"]["options"]
                    == "-c lock_timeout=2000 -c statement_timeout=30000"
                )
                callback = factory.call_args.kwargs["progress_callback"]
                callback("apply_start", migration, False)
                callback("apply_success", migration, False)

            executor.migrate.side_effect = migrate
            call_command(
                "hosted_gateway_migrate",
                "schema",
                "--apply",
                "--quiesced",
                "--schema-deadline-seconds",
                "123",
                stdout=output,
            )
    budget.assert_called_once_with(123)
    assert connection.settings_dict["OPTIONS"] == {"original": "kept"}
    assert connection.close.call_count == 2
    assert "migration=prism.synthetic status=applied" in output.getvalue()
    assert "schema-phase elapsed_seconds=" in output.getvalue()
