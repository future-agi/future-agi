"""``manage.py replace_unedited_demo_rows`` where an operator runs it.

The command rewrites seeded demo rows that nobody changed, so it runs where
the other operator commands run: in an operator job (SERVICE_TYPE=bootstrap
with STARTUP_DB_MUTATION_MODE=operator) and in the explicit local mode
(NO_STARTUP_DB_MUTATIONS=false outside a hosted deployment). An application
process refuses it. Its bare --dry-run prints the counts, locks no row and
writes nothing (test_replace_unedited_demo_rows.py checks that), so that one
form runs anywhere; with any other option it is refused in an application
process. The subprocess tests start a fresh process, so the mutation-free
startup guard in ``model_hub.apps`` sees the real argv; ``call_command`` in
the other tests never meets it.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import get_commands
from django.db import connection

from model_hub.apps import (
    OPERATOR_STARTUP_MUTATION_COMMANDS,
    explicit_management_mutation_authorized,
    guarded_management_command,
)

COMMAND = "replace_unedited_demo_rows"
BACKEND_ROOT = Path(__file__).resolve().parents[2]
GUARD_ENV = (
    "SERVICE_TYPE",
    "STARTUP_DB_MUTATION_MODE",
    "NO_STARTUP_DB_MUTATIONS",
    "ENV_TYPE",
    "CLOUD_DEPLOYMENT",
)
OPERATOR_JOB = {"SERVICE_TYPE": "bootstrap", "STARTUP_DB_MUTATION_MODE": "operator"}
LOCAL_MODE = {"NO_STARTUP_DB_MUTATIONS": "false"}
# No organization has this id, so a real run with it changes nothing.
NO_SUCH_ORGANIZATION = "4c1f5e2a-9b3d-4e8f-a6c0-d2b7e1f05a9c"
REFUSED = f"{COMMAND} is disabled during mutation-free startup"


def test_the_command_is_an_accounts_command_on_the_operator_list():
    # A typo on the list would admit nothing and refuse the real command.
    assert get_commands().get(COMMAND) == "accounts"
    assert COMMAND in OPERATOR_STARTUP_MUTATION_COMMANDS


@pytest.mark.parametrize(
    "prefix", [["manage.py"], ["python", "-m", "django"], ["django-admin"]]
)
def test_the_guard_admits_a_bare_dry_run_anywhere(prefix):
    assert guarded_management_command([*prefix, COMMAND, "--dry-run"]) is None


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--organization", NO_SUCH_ORGANIZATION],
        ["--dry-run", "--organization", NO_SUCH_ORGANIZATION],
        ["--organization", NO_SUCH_ORGANIZATION, "--dry-run"],
        ["--dry"],
    ],
    ids=[
        "bare",
        "one-organization",
        "dry-run-then-organization",
        "organization-then-dry-run",
        "abbreviated-dry-run",
    ],
)
def test_the_guard_holds_every_other_form(args):
    assert guarded_management_command(["manage.py", COMMAND, *args]) == COMMAND


@pytest.mark.parametrize(
    "env, authorized",
    [
        ({**OPERATOR_JOB, "ENV_TYPE": "production"}, True),
        ({**LOCAL_MODE, "ENV_TYPE": "local"}, True),
        ({**LOCAL_MODE, "ENV_TYPE": "production"}, False),
        ({"SERVICE_TYPE": "backend", "ENV_TYPE": "production"}, False),
        ({"SERVICE_TYPE": "backend", "STARTUP_DB_MUTATION_MODE": "operator"}, False),
        ({"ENV_TYPE": "local"}, False),
    ],
    ids=[
        "operator-job",
        "local-mode",
        "local-mode-on-a-hosted-install",
        "app-process",
        "operator-mode-in-an-app-process",
        "no-mode",
    ],
)
def test_the_command_runs_only_where_mutations_are_authorized(
    monkeypatch, env, authorized
):
    for name in GUARD_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for args in ([], ["--organization", NO_SUCH_ORGANIZATION]):
        argv = ["manage.py", COMMAND, *args]
        assert explicit_management_mutation_authorized(argv) is authorized


def _manage_py(*args, **env):
    """``python manage.py ...`` against this test database, as a new process."""
    environment = {k: v for k, v in os.environ.items() if k not in GUARD_ENV}
    environment.update(
        DJANGO_SETTINGS_MODULE="tfc.settings.test",
        PG_DB=connection.settings_dict["NAME"],
        NO_STARTUP_DB_MUTATIONS="true",
        FUTURE_AGI_LICENSE_URL="http://127.0.0.1:9",
    )
    environment.update(env)
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        start_new_session=True,
    )


def _assert_a_real_run_that_changed_nothing(result):
    """A real run's counts (the test settings may print a banner on stdout first)."""
    assert result.returncode == 0, result.stderr[-2000:]
    lines = result.stdout.splitlines()
    for line in (
        "organizations touched: 0",
        "rows replaced: 0",
        "organizations failed: 0",
    ):
        assert line in lines, result.stdout[-2000:]


@pytest.mark.django_db(transaction=True)
def test_an_app_process_refuses_the_command():
    refused = _manage_py(COMMAND, "--organization", NO_SUCH_ORGANIZATION)

    assert refused.returncode != 0
    assert REFUSED in refused.stderr


@pytest.mark.django_db(transaction=True)
def test_the_explicit_local_mode_runs_the_command():
    done = _manage_py(COMMAND, "--organization", NO_SUCH_ORGANIZATION, **LOCAL_MODE)

    _assert_a_real_run_that_changed_nothing(done)


@pytest.mark.django_db(transaction=True)
def test_an_operator_job_runs_the_command():
    done = _manage_py(COMMAND, "--organization", NO_SUCH_ORGANIZATION, **OPERATOR_JOB)

    _assert_a_real_run_that_changed_nothing(done)


# INSTALLATION.md shows the real run as this one-off operator process: the bare
# command with all three variables (test_replace_unedited_demo_rows_docs.py).
DOCUMENTED_OPERATOR_RUN = {**OPERATOR_JOB, "NO_STARTUP_DB_MUTATIONS": "false"}


@pytest.mark.django_db(transaction=True)
def test_the_documented_operator_run_runs_the_command():
    done = _manage_py(COMMAND, **DOCUMENTED_OPERATOR_RUN)

    _assert_a_real_run_that_changed_nothing(done)


@pytest.mark.django_db(transaction=True)
def test_a_bare_dry_run_runs_in_an_app_process():
    done = _manage_py(COMMAND, "--dry-run")

    assert done.returncode == 0, done.stderr[-2000:]
    lines = done.stdout.splitlines()
    assert any(line.startswith("rows that would be replaced: ") for line in lines)
    assert "organizations failed: 0" in lines


@pytest.mark.django_db(transaction=True)
def test_a_dry_run_with_another_option_is_refused_in_an_app_process():
    refused = _manage_py(COMMAND, "--dry-run", "--organization", NO_SUCH_ORGANIZATION)

    assert refused.returncode != 0
    assert REFUSED in refused.stderr
