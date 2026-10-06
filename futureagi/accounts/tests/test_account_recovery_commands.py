"""The account commands self-hosted installs tell an operator to run.

"Forgot password" without email, INSTALLATION.md and the configuration
reference (deploy/env-reference.toml, "Email")
name ``manage.py reset_password``. Each command runs here as the operator runs
it, in a fresh process, so the mutation-free startup guard in
``model_hub.apps`` sees the real argv.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.db import connection

from accounts.models import User
from model_hub.apps import guarded_management_command

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "command", ["reset_password", "changepassword", "createsuperuser"]
)
@pytest.mark.parametrize(
    "prefix", [["manage.py"], ["python", "-m", "django"], ["django-admin"]]
)
def test_startup_guard_admits_the_account_recovery_commands(prefix, command):
    assert guarded_management_command([*prefix, command, "--email", "a@b.c"]) is None


@pytest.mark.parametrize(
    "command", ["shell", "dbshell", "flush", "loaddata", "migrate"]
)
def test_startup_guard_still_blocks_other_commands(command):
    assert guarded_management_command(["manage.py", command]) == command


def _manage_py(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    """``python manage.py ...`` against this test database, as a new process
    with no terminal (getpass then reads stdin)."""
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "tfc.settings.test",
        "PG_DB": connection.settings_dict["NAME"],
        "NO_STARTUP_DB_MUTATIONS": "true",
    }
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=BACKEND_ROOT,
        env=environment,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=300,
        start_new_session=True,
    )


@pytest.fixture
def locked_out_user(transactional_db):
    return User.objects.create_user(
        email="owner@example.com", password="forgotten-Passw0rd!", name="Owner"
    )


def test_reset_password_as_documented_sets_the_new_password(locked_out_user):
    # INSTALLATION.md pipes the password in, off the command line.
    result = _manage_py(
        "reset_password",
        "--email",
        "owner@example.com",
        stdin="Recovered-Passw0rd!\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Password updated for 'owner@example.com'." in result.stdout
    locked_out_user.refresh_from_db()
    assert locked_out_user.check_password("Recovered-Passw0rd!")
    assert not locked_out_user.check_password("forgotten-Passw0rd!")


def test_changepassword_sets_the_new_password(locked_out_user):
    result = _manage_py(
        "changepassword",
        "owner@example.com",
        stdin="Recovered-Passw0rd!\nRecovered-Passw0rd!\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    locked_out_user.refresh_from_db()
    assert locked_out_user.check_password("Recovered-Passw0rd!")


def test_a_blocked_command_still_fails_before_it_runs(transactional_db):
    result = _manage_py("flush", "--noinput")

    assert result.returncode != 0
    assert "flush is disabled during mutation-free startup" in result.stderr


@pytest.mark.parametrize("line_end", ["\n", "\r\n"], ids=["bash", "powershell"])
def test_create_user_reads_the_password_the_installers_pipe_in(
    transactional_db, line_end
):
    """bin/install and bin/install.ps1 pipe the first account's password in,
    so it never shows on a command line: without a terminal, getpass reads
    it from stdin. Windows PowerShell ends the line with \\r\\n, which the
    signup serializer trims."""
    result = _manage_py(
        "create_user",
        "--email",
        "first-owner@futureagi.com",
        "--name",
        "First Owner",
        stdin=f"Piped-Passw0rd!{line_end}",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    user = User.objects.get(email="first-owner@futureagi.com")
    assert user.check_password("Piped-Passw0rd!")
