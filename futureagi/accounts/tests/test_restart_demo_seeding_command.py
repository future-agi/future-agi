"""Operators start the demo seeds that stopped after a lasting failure again.

A hosted deployment refuses ``manage.py shell`` (model_hub/apps.py), so
``manage.py restart_demo_seeding`` is how an operator reaches
``accounts.demo_seeding.restart_demo_seeding`` there. The subprocess test runs
the command as an operator does, so the start-up guard sees the real argv.
"""

import os
import subprocess
import sys
import uuid
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import (
    CommandError,
    call_command,
    get_commands,
    load_command_class,
)
from django.db import connection
from django.utils import timezone
from structlog.testing import capture_logs

from accounts.models import Organization, User
from accounts.models.demo_seed import DemoSeedRequest
from model_hub.apps import OPERATOR_STARTUP_MUTATION_COMMANDS

COMMAND = "restart_demo_seeding"
BACKEND_ROOT = Path(__file__).resolve().parents[2]
GUARD_ENV = (
    "SERVICE_TYPE",
    "STARTUP_DB_MUTATION_MODE",
    "NO_STARTUP_DB_MUTATIONS",
    "ENV_TYPE",
    "CLOUD_DEPLOYMENT",
)
OPERATOR_JOB = {"SERVICE_TYPE": "bootstrap", "STARTUP_DB_MUTATION_MODE": "operator"}
MALFORMED = "not-an-organization-id"
REFUSAL = "The --organization value is not an organization id (a UUID)."
RESTARTED_NONE = "Restarted 0 stopped demo seed requests."
RESTARTED_ONE = "Restarted 1 stopped demo seed request."
RESTARTED_TWO = "Restarted 2 stopped demo seed requests."
DRY_RUN_ONE = "Dry run: 1 stopped demo seed request would restart; nothing changed."
DRY_RUN_TWO = "Dry run: 2 stopped demo seed requests would restart; nothing changed."


def _request(name, *, traces, datasets, stopped):
    organization = Organization.objects.create(name=name)
    user = User.objects.create_user(
        email=f"{name}-{uuid.uuid4().hex[:8]}@example.com",
        password="SafePassword123!",
        name=name,
        organization=organization,
    )
    now = timezone.now()
    return DemoSeedRequest.objects.create(
        user=user,
        organization=organization,
        traces_seeded=traces,
        datasets_seeded=datasets,
        completed_at=now - timedelta(hours=1) if stopped else None,
        last_attempt_at=now - timedelta(hours=2),
    )


def _state(request):
    request.refresh_from_db()
    return (
        request.traces_seeded,
        request.datasets_seeded,
        request.completed_at,
        request.last_attempt_at,
    )


def _states(seeds):
    return {name: _state(request) for name, request in seeds.items()}


@pytest.fixture
def seeds(db):
    """Two stopped requests, one finished and one never stopped."""
    return {
        "stopped": _request("stopped", traces=True, datasets=False, stopped=True),
        "stopped_early": _request(
            "stopped-early", traces=False, datasets=False, stopped=True
        ),
        "finished": _request("finished", traces=True, datasets=True, stopped=True),
        "pending": _request("pending", traces=False, datasets=False, stopped=False),
    }


def _run(*args):
    out = StringIO()
    call_command(COMMAND, *args, stdout=out)
    return out.getvalue().splitlines()


def test_the_command_is_an_accounts_command_on_the_operator_list():
    # A typo on the list would admit nothing and refuse the real command.
    assert get_commands().get(COMMAND) == "accounts"
    assert COMMAND in OPERATOR_STARTUP_MUTATION_COMMANDS


def test_help_says_what_the_command_does():
    command = load_command_class("accounts", COMMAND)
    text = " ".join(command.create_parser("manage.py", COMMAND).format_help().split())

    assert "stopped after a lasting failure" in text
    assert "--organization" in text
    assert "--dry-run" in text


def test_restart_clears_every_stop_and_leaves_other_requests_alone(seeds):
    finished = _state(seeds["finished"])
    pending = _state(seeds["pending"])

    assert _run() == [RESTARTED_TWO]

    assert _state(seeds["stopped"]) == (True, False, None, None)
    assert _state(seeds["stopped_early"]) == (False, False, None, None)
    assert _state(seeds["finished"]) == finished
    assert _state(seeds["pending"]) == pending
    # Running it again finds nothing left to restart.
    assert _run() == [RESTARTED_NONE]


def test_organization_restarts_only_that_organization(seeds):
    other = _state(seeds["stopped_early"])

    lines = _run("--organization", str(seeds["stopped"].organization_id))

    assert lines == [RESTARTED_ONE]
    assert _state(seeds["stopped"]) == (True, False, None, None)
    assert _state(seeds["stopped_early"]) == other


def test_unknown_organization_restarts_nothing_and_succeeds(seeds):
    before = _states(seeds)

    lines = _run("--organization", str(uuid.uuid4()))

    assert lines == [RESTARTED_NONE]
    assert _states(seeds) == before


@pytest.mark.parametrize(
    "organization, line",
    [(None, DRY_RUN_TWO), ("stopped", DRY_RUN_ONE)],
    ids=["every-organization", "one-organization"],
)
def test_dry_run_prints_the_count_and_changes_nothing(seeds, organization, line):
    before = _states(seeds)
    args = ["--dry-run"]
    if organization:
        args += ["--organization", str(seeds[organization].organization_id)]

    assert _run(*args) == [line]

    assert _states(seeds) == before


@pytest.mark.parametrize(
    "value",
    [MALFORMED, "", "1234", "0b7c1a5e-7d0a-4f4e-9d3c-2f1a8b6e4c11-0", "../org"],
)
def test_malformed_organization_is_refused_with_exit_code_2(seeds, value):
    before = _states(seeds)

    with pytest.raises(CommandError) as refused:
        call_command(COMMAND, "--organization", value, stdout=StringIO())

    assert refused.value.returncode == 2
    assert str(refused.value) == REFUSAL
    assert _states(seeds) == before


def test_output_and_logs_carry_no_organization_data(seeds):
    named = str(seeds["stopped"].organization_id)
    # Every id and address of the four requests except the one passed.
    others = [
        str(value)
        for request in seeds.values()
        for value in (request.organization_id, request.user_id, request.user.email)
    ]
    others.remove(named)

    with capture_logs() as logs:
        lines = _run("--organization", named) + _run()

    assert lines == [RESTARTED_ONE, RESTARTED_ONE]
    restarted = [entry for entry in logs if entry["event"] == "demo_seed_restarted"]
    assert [entry["organization_id"] for entry in restarted] == [named, None]
    assert [entry["count"] for entry in restarted] == [1, 1]
    for text in [*lines, *map(repr, logs)]:
        for value in others:
            assert value not in text


def _manage_py(*args, **env):
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


@pytest.mark.django_db(transaction=True)
def test_the_command_runs_as_an_operator_job_and_nowhere_else():
    stopped = _request("operator-run", traces=True, datasets=False, stopped=True)
    before = _state(stopped)
    organization_id = str(stopped.organization_id)

    refused = _manage_py(COMMAND, "--organization", organization_id)
    assert refused.returncode != 0
    assert f"{COMMAND} is disabled during mutation-free startup" in refused.stderr
    assert _state(stopped) == before

    malformed = _manage_py(COMMAND, "--organization", MALFORMED, **OPERATOR_JOB)
    assert malformed.returncode == 2, malformed.stderr[-2000:]
    assert REFUSAL in malformed.stderr
    assert _state(stopped) == before

    dry_run = _manage_py(COMMAND, "--dry-run", **OPERATOR_JOB)
    assert dry_run.returncode == 0, dry_run.stderr[-2000:]
    # The test settings may print a banner on stdout first.
    assert DRY_RUN_ONE in dry_run.stdout.splitlines()
    assert _state(stopped) == before

    restarted = _manage_py(COMMAND, "--organization", organization_id, **OPERATOR_JOB)
    assert restarted.returncode == 0, restarted.stderr[-2000:]
    assert RESTARTED_ONE in restarted.stdout.splitlines()
    assert _state(stopped) == (True, False, None, None)
