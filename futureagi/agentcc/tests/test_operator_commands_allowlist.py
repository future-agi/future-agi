"""Operators can run the gateway's report and repair commands.

A mutation-free Django process (``ModelHubConfig.ready``) refuses any
management command that is not on ``OPERATOR_STARTUP_MUTATION_COMMANDS``,
even in the one-shot bootstrap job. These agentcc operator commands are on
that list: ``agentcc_check_key_org`` (a report, and ``--fix``),
``agentcc_check_log_org`` (a read-only report) and
``agentcc_scrub_request_log_credentials`` (a report, and ``--apply``). Only
the bootstrap job may run them.
"""

import sys

import pytest
from django.core.management import get_commands

from model_hub.apps import (
    OPERATOR_STARTUP_MUTATION_COMMANDS,
    ModelHubConfig,
    explicit_management_mutation_authorized,
)

ORG = "00000000-0000-0000-0000-000000000001"
# One representative command line per form, as an operator types it.
OPERATOR_COMMAND_LINES = [
    ["agentcc_check_key_org"],
    ["agentcc_check_key_org", "--org", ORG],
    ["agentcc_check_key_org", "--fix"],
    ["agentcc_check_log_org", "--since", "2026-09-01"],
    ["agentcc_check_log_org", "--org", ORG],
    ["agentcc_scrub_request_log_credentials"],
    ["agentcc_scrub_request_log_credentials", "--apply"],
    ["agentcc_scrub_request_log_credentials", "--org", ORG],
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
def test_each_command_is_an_agentcc_command_on_the_operator_list(command):
    # Each listed name is a real agentcc command.
    assert get_commands().get(command) == "agentcc"
    assert command in OPERATOR_STARTUP_MUTATION_COMMANDS


@pytest.mark.parametrize("no_mutations", ["false", "true"])
@pytest.mark.parametrize("line", OPERATOR_COMMAND_LINES, ids=" ".join)
def test_the_bootstrap_job_may_run_the_command(monkeypatch, line, no_mutations):
    # The operator runs it as the bootstrap job's container command (python
    # manage.py ...); the pod spec may carry NO_STARTUP_DB_MUTATIONS either way.
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
def test_application_processes_still_refuse_the_command(
    monkeypatch, command, service_type, mode
):
    _hosted(monkeypatch, service_type=service_type, mode=mode, no_mutations="true")
    argv = ["manage.py", command]

    assert explicit_management_mutation_authorized(argv) is False
    with pytest.raises(RuntimeError, match=f"^{command} is disabled"):
        _ready(monkeypatch, argv)
