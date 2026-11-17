"""Hosted operators can re-mirror traces and spans the analytics database missed.

The app mirrors each committed trace and span batch to ClickHouse; a batch
that still fails is logged with its row count and is not kept. The two
backfills copy those rows from Postgres again: ``ch25_backfill_traces`` for
traces and ``ch25_backfill`` for spans. ``ch25_validate`` then compares the
two databases, so an operator can check that the repair worked. Every hosted
Django process is mutation-free: ``ModelHubConfig.ready`` refuses any
management command that is not on ``OPERATOR_STARTUP_MUTATION_COMMANDS``, so
each of the three must be on that list, and only the one-shot bootstrap job
may run it.
"""

import os
import sys

import pytest
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError

from conftest import _ch_test_http_port, _open_ch_test_http_client
from model_hub.apps import (
    OPERATOR_STARTUP_MUTATION_COMMANDS,
    ModelHubConfig,
    explicit_management_mutation_authorized,
)

PROJECT = "00000000-0000-0000-0000-000000000001"
# One representative command line per form, as an operator types it.
OPERATOR_COMMAND_LINES = [
    ["ch25_backfill_traces"],
    ["ch25_backfill_traces", "--dry-run"],
    ["ch25_backfill_traces", "--project-id", PROJECT, "--since", "2026-11-12"],
    ["ch25_backfill"],
    ["ch25_backfill", "--status"],
    [
        "ch25_backfill",
        "--project-id",
        PROJECT,
        "--since",
        "2026-11-12T00:00:00",
        "--until",
        "2026-11-13T00:00:00",
    ],
    ["ch25_validate", "--counts"],
    ["ch25_validate", "--all", "--report", "/tmp/validation.json"],
]
COMMANDS = sorted({line[0] for line in OPERATOR_COMMAND_LINES})


def _hosted(monkeypatch, *, service_type, mode, no_mutations):
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    monkeypatch.setenv("SERVICE_TYPE", service_type)
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", mode)
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", no_mutations)


def _ready(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()


@pytest.mark.parametrize("command", COMMANDS)
def test_each_command_is_a_tracer_command_on_the_operator_list(command):
    # A typo on the list would allow nothing and refuse the real command.
    assert get_commands().get(command) == "tracer"
    assert command in OPERATOR_STARTUP_MUTATION_COMMANDS


@pytest.mark.parametrize("no_mutations", ["false", "true"])
@pytest.mark.parametrize("line", OPERATOR_COMMAND_LINES, ids=" ".join)
def test_hosted_bootstrap_job_may_run_the_command(monkeypatch, line, no_mutations):
    _hosted(
        monkeypatch,
        service_type="bootstrap",
        mode="operator",
        no_mutations=no_mutations,
    )
    argv = ["manage.py", *line]

    assert explicit_management_mutation_authorized(argv) is True
    _ready(monkeypatch, argv)


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize(
    "service_type,mode",
    [
        ("backend", "disabled"),
        ("worker", "disabled"),
        ("temporal-worker", "disabled"),
        ("temporal-schedules", "disabled"),
        # The bootstrap service without the operator mode is not the job.
        ("bootstrap", "disabled"),
    ],
)
def test_running_application_processes_still_refuse_the_command(
    monkeypatch, command, service_type, mode
):
    _hosted(monkeypatch, service_type=service_type, mode=mode, no_mutations="true")
    argv = ["manage.py", command]

    assert explicit_management_mutation_authorized(argv) is False
    with pytest.raises(RuntimeError, match=f"^{command} is disabled"):
        _ready(monkeypatch, argv)


def _analytics_state(client, database):
    """Every table's row count, the table set, and the pending mutations.

    count() without FINAL counts every inserted row, so an insert into a
    replacing table shows even when its rows would collapse on merge.
    """
    tables = client.query(
        "SELECT name, engine FROM system.tables WHERE database = {db:String}"
        " ORDER BY name",
        parameters={"db": database},
    ).result_rows
    counts = {}
    for name, engine in tables:
        if engine.endswith("MergeTree"):
            query = f"SELECT count() FROM `{database}`.`{name}`"
            counts[name] = client.query(query).result_rows[0][0]
    mutations = client.query(
        "SELECT count() FROM system.mutations WHERE database = {db:String}",
        parameters={"db": database},
    ).result_rows[0][0]
    return {
        "tables": [name for name, _ in tables],
        "counts": counts,
        "mutations": mutations,
    }


@pytest.mark.integration
@pytest.mark.django_db
def test_one_validate_run_writes_nothing_to_the_analytics_database(
    monkeypatch, tmp_path
):
    database = (
        os.environ.get("CH25_DATABASE") or os.environ.get("CH_DATABASE") or "test_tfc"
    )
    client = _open_ch_test_http_client(database=database)
    try:
        # The command reads its target from get_v2_config; point it at the
        # same test server and database this test observes.
        target = {
            "host": os.environ.get("CH25_HOST", "127.0.0.1"),
            "http_port": _ch_test_http_port().port,
            "user": os.environ.get("CH25_USER")
            or os.environ.get("CH_USERNAME")
            or "default",
            "password": os.environ.get("CH25_PASSWORD")
            or os.environ.get("CH_PASSWORD")
            or "",
            "database": database,
        }
        monkeypatch.setattr(
            "tracer.management.commands.ch25_validate.get_v2_config", lambda: target
        )
        before = _analytics_state(client, database)
        assert "backfill_checkpoints" in before["counts"]

        try:
            call_command(
                "ch25_validate", "--all", "--report", str(tmp_path / "report.json")
            )
        except CommandError as exc:
            # A parity gap is a valid answer; a crash is not.
            assert str(exc).startswith("validation FAILED"), str(exc)

        assert (tmp_path / "report.json").exists()
        assert _analytics_state(client, database) == before
    finally:
        client.close()
