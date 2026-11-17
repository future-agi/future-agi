"""hosted_gateway_migrate's PostgreSQL session limits per phase.

An applied phase on a quiesced system (--apply --quiesced) has nothing live to
wait behind, so a long migration is not cancelled part-way: no statement limit,
and a 60-second lock wait so a forgotten live writer surfaces as an error, not an
endless wait. A plan (no --apply) and the check phase keep 2 s / 30 s; the key
phases keep their own lock wait (--key-lock-timeout-ms). The schema phase's
wall-clock deadline stays the outer bound (test_hosted_schema_deadline.py). Each
migration's started line names the limits PostgreSQL reports for the session as it
starts, so a rehearsal log shows the statement limit in force while 0031 runs.
"""

import io
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.db import connections
from django.test import override_settings

from agentcc.management.commands.hosted_gateway_migrate import Command
from agentcc.tests.test_gateway_key_operator import operator_environment
from agentcc.tests.test_hosted_startup import hosted_settings

MODULE = "agentcc.management.commands.hosted_gateway_migrate"
QUIESCED = "-c lock_timeout=60000 -c statement_timeout=0"
LIVE = "-c lock_timeout=2000 -c statement_timeout=30000"


def _options_for(*args):
    """The session options hosted_gateway_migrate connects with for these arguments."""
    env = operator_environment()
    connection = MagicMock(vendor="postgresql")
    connection.settings_dict = {"OPTIONS": {"original": "kept"}}
    fake = MagicMock()
    fake.databases = {"default_direct": {}}
    fake.__getitem__.return_value = connection
    seen = []

    def run_phase(self, conn, phase, key_op, options):
        seen.append(conn.settings_dict["OPTIONS"]["options"])

    with (
        patch.dict(os.environ, env, clear=True),
        override_settings(**hosted_settings(env)),
        patch(MODULE + ".connections", fake),
        patch(MODULE + ".schema_phase_deadline"),
        patch.object(Command, "run_phase", run_phase),
    ):
        call_command("hosted_gateway_migrate", *args)
    assert connection.settings_dict["OPTIONS"] == {"original": "kept"}, (
        "the prior options come back"
    )
    assert len(seen) == 1
    return seen[0]


@pytest.mark.parametrize("phase", ["schema", "backfill"])
def test_an_applied_phase_on_a_quiesced_system_has_no_statement_limit_and_a_60_second_lock_wait(
    phase,
):
    extra = ["--compatible-runtime-revision", "a" * 40] if phase == "backfill" else []
    assert _options_for(phase, "--apply", "--quiesced", *extra) == QUIESCED


@pytest.mark.parametrize(
    "args",
    [
        ["schema"],
        ["schema", "--quiesced"],
        ["backfill"],
        ["check"],
        ["check", "--quiesced"],
    ],
    ids=" ".join,
)
def test_a_plan_and_the_check_phase_keep_2_and_30_seconds(args):
    assert _options_for(*args) == LIVE


def test_the_key_phases_keep_their_own_lock_wait():
    # key-verify takes the same path; its operator environment needs the target key as
    # the runtime key, which test_gateway_key_operator.py covers.
    common = [
        "key-transition",
        "--quiesced",
        "--compatible-runtime-revision",
        "a" * 40,
        "--key-lock-timeout-ms",
        "1500",
    ]
    assert _options_for(*common) == (
        "-c lock_timeout=1500 -c statement_timeout=30000 "
        "-c default_transaction_read_only=on"
    )
    assert _options_for(*common, "--apply") == (
        "-c lock_timeout=1500 -c statement_timeout=30000"
    )


def test_the_operator_texts_state_the_limits_and_each_phases_outer_bound():
    import argparse

    from agentcc import hosted_migration_progress

    parser = argparse.ArgumentParser()
    hosted_migration_progress.add_schema_deadline_argument(parser)
    (deadline,) = [
        action
        for action in parser._actions
        if "--schema-deadline-seconds" in action.option_strings
    ]
    assert deadline.help.endswith(
        "With --apply --quiesced there is no statement limit and a 60 s lock "
        "wait; otherwise 2/30 s."
    )
    assert "stay 2/30" not in deadline.help
    note = " ".join(hosted_migration_progress.__doc__.split())
    assert "this deadline is then the schema phase's outer bound" in note
    assert (
        "an applied backfill phase is bounded only by the hosted Job's own deadline "
        "(activeDeadlineSeconds)" in note
    )


def test_each_migration_started_line_names_the_limits_in_force():
    from agentcc.hosted_migration_progress import MigrationProgress

    output = MagicMock()
    progress = MigrationProgress(
        output, limits=lambda: "statement_timeout=0 lock_timeout=1min"
    )
    migration = SimpleNamespace(app_label="prism", name="0031_request_log_v2_indexes")
    progress("apply_start", migration, False)
    progress("apply_success", migration, False)
    started, applied = [call.args[0] for call in output.write.call_args_list]
    assert started.startswith(
        "migration=prism.0031_request_log_v2_indexes status=started "
    )
    assert started.endswith(" statement_timeout=0 lock_timeout=1min")
    assert "statement_timeout" not in applied


def test_the_phase_prints_the_server_limits_as_each_migration_starts():
    """The schema phase asks the session itself (SHOW) when a migration starts."""
    env = operator_environment()
    connection = MagicMock(vendor="postgresql")
    connection.settings_dict = {"OPTIONS": {}}
    cursor = connection.cursor.return_value.__enter__.return_value
    # The phase lock, then SHOW statement_timeout and SHOW lock_timeout.
    cursor.fetchone.side_effect = [(True,), ("0",), ("1min",)]
    fake = MagicMock()
    fake.databases = {"default_direct": {}}
    fake.__getitem__.return_value = connection
    executor = MagicMock()
    executor.migration_plan.return_value = []
    migration = SimpleNamespace(app_label="prism", name="0031_request_log_v2_indexes")
    output = io.StringIO()
    with (
        patch.dict(os.environ, env, clear=True),
        override_settings(**hosted_settings(env)),
        patch(MODULE + ".connections", fake),
        patch(MODULE + ".schema_phase_deadline"),
        patch(MODULE + ".MigrationExecutor", return_value=executor) as factory,
        patch(MODULE + ".phase_targets", return_value=[]),
        patch(MODULE + ".validate_plan"),
    ):
        executor.migrate.side_effect = lambda *a, **k: factory.call_args_list[0].kwargs[
            "progress_callback"
        ]("apply_start", migration, False)
        call_command(
            "hosted_gateway_migrate", "schema", "--apply", "--quiesced", stdout=output
        )
    started = next(
        line
        for line in output.getvalue().splitlines()
        if line.startswith("migration=prism.0031_request_log_v2_indexes status=started")
    )
    assert started.endswith(" statement_timeout=0 lock_timeout=1min")
    executed = [call.args[0] for call in cursor.execute.call_args_list]
    assert "SHOW statement_timeout" in executed and "SHOW lock_timeout" in executed


@pytest.mark.django_db
def test_a_statement_slower_than_30_seconds_completes_under_the_quiesced_options():
    """The real session: PostgreSQL connected with the options the command builds."""
    options = _options_for("schema", "--apply", "--quiesced")
    session = connections["default"].copy()
    session.settings_dict["OPTIONS"] = {
        **session.settings_dict.get("OPTIONS", {}),
        "options": options,
    }
    try:
        from agentcc.management.commands.hosted_gateway_migrate import (
            session_limits_text,
        )

        assert session_limits_text(session) == "statement_timeout=0 lock_timeout=1min"
        with session.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            assert cursor.fetchone()[0] == "0"
            cursor.execute("SHOW lock_timeout")
            assert cursor.fetchone()[0] == "1min"
            cursor.execute("SELECT pg_sleep(30.5)")
            assert cursor.fetchone() is not None
    finally:
        session.close()
