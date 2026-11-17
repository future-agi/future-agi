"""hosted_gateway_migrate verify-indexes: the update's stop check as a phase.

The hosted Job runs only hosted_gateway_migrate <phase>, so the request-log index
check (agentcc_check_request_log_indexes) is a phase too. It prints the command's
lines and stops the same way, through default_direct in a read-only session under
the operator lock, and writes nothing. It refuses --apply. It is not part of the
check phase, which refuses a pending plan and runs after backfill.
"""

import io
import os
import re
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import override_settings

from agentcc.tests.test_gateway_key_operator import operator_environment
from agentcc.tests.test_hosted_startup import hosted_settings
from agentcc.tests.test_request_log_index_check import NAMES, _execute, _restore

pytestmark = pytest.mark.django_db(transaction=True)

MODULE = "agentcc.management.commands.hosted_gateway_migrate"
PHASE = "verify-indexes"
WRITES = re.compile(
    r"^\s*(CREATE|DROP|ALTER|UPDATE|INSERT|DELETE|TRUNCATE|COPY|GRANT|REVOKE)\b", re.I
)
LOCK = "hashtext('futureagi-hosted-phases')"


class _Direct:
    """connections as hosted_gateway_migrate sees them: default_direct is a
    separate session on this test's database."""

    def __init__(self):
        self.session = connections["default"].copy()
        self.databases = {"default_direct": self.session.settings_dict}
        self.asked = []

    def __getitem__(self, alias):
        self.asked.append(alias)
        return self.session


@contextmanager
def _operator(direct):
    env = operator_environment()
    with (
        patch.dict(os.environ, env, clear=True),
        override_settings(**hosted_settings(env)),
        patch(MODULE + ".connections", direct),
    ):
        yield


def _phase(direct):
    """Run the phase; return (stdout lines, the statements it sent that write,
    the session options it connected with)."""
    out = io.StringIO()
    writes, options = [], []

    def record(execute, sql, params, many, context):
        options.append(direct.session.settings_dict["OPTIONS"].get("options", ""))
        if WRITES.match(str(sql)):
            writes.append(str(sql))
        return execute(sql, params, many, context)

    try:
        with (
            _operator(direct),
            direct.session.execute_wrapper(record),
        ):
            try:
                call_command("hosted_gateway_migrate", PHASE, stdout=out)
            finally:
                _phase.lines = out.getvalue().splitlines()
    finally:
        direct.session.close()
    return _phase.lines, writes, options


def _command():
    out = io.StringIO()
    try:
        call_command("agentcc_check_request_log_indexes", stdout=out)
    finally:
        _command.lines = out.getvalue().splitlines()
    return _command.lines


def test_verify_indexes_prints_the_checks_lines_in_a_read_only_session():
    direct = _Direct()
    lines, writes, options = _phase(direct)
    assert lines == _command()
    assert lines[-1] == "request-log indexes: 5 of 5 valid"
    assert direct.asked == ["default_direct"]
    assert writes == []
    assert options and all(
        "-c default_transaction_read_only=on" in seen for seen in options
    )


def test_verify_indexes_stops_like_the_command_on_an_invalid_index():
    try:
        _execute(
            "UPDATE pg_index SET indisvalid = false WHERE indexrelid = %s::regclass",
            [NAMES[1]],
        )
        with pytest.raises(CommandError) as command:
            _command()
        with pytest.raises(CommandError) as phase:
            _phase(_Direct())
        assert str(phase.value) == str(command.value)
        assert str(phase.value).startswith(
            f"stop: request-log indexes not valid: {NAMES[1]} invalid."
        )
        assert phase.value.returncode == command.value.returncode == 1
        assert _phase.lines == _command.lines
    finally:
        _restore()


def test_verify_indexes_waits_for_no_other_operator():
    # A schema phase holding the operator lock may still be building an index.
    with connections["default"].cursor() as cursor:
        cursor.execute(f"SELECT pg_advisory_lock({LOCK})")
    try:
        with pytest.raises(
            CommandError, match="^Another hosted migration operator holds the lock$"
        ):
            _phase(_Direct())
        assert _phase.lines == []
    finally:
        with connections["default"].cursor() as cursor:
            cursor.execute(f"SELECT pg_advisory_unlock({LOCK})")


@pytest.mark.parametrize("args", [["--apply"], ["--apply", "--quiesced"]])
def test_verify_indexes_refuses_apply_before_any_connection(args):
    direct = MagicMock()
    with (
        _operator(direct),
        pytest.raises(CommandError, match="^The verify-indexes phase is read-only$"),
    ):
        call_command("hosted_gateway_migrate", PHASE, *args)
    direct.__getitem__.assert_not_called()
