"""Hosted operators can run the gateway's repair and report commands.

Every hosted Django process is mutation-free: ``ModelHubConfig.ready`` refuses
any management command that is not on ``OPERATOR_STARTUP_MUTATION_COMMANDS``,
even in the one-shot bootstrap job. The deploy runs ``agentcc_check_key_org``
(the key-org report, and ``--fix`` to repair), the managed-credit
operations run the ledger, upstream-key and export commands, and the
request-log credential scrub (a report, and ``--apply`` to mask); each must be
on that list, and only the bootstrap job may run them.
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
# One representative command line per command, as an operator types it.
OPERATOR_COMMAND_LINES = [
    ["agentcc_check_key_org"],
    ["agentcc_check_key_org", "--fix"],
    ["agentcc_managed_credits", ORG, "--show"],
    ["agentcc_managed_credits", ORG, "--grant", "25", "--note", "pilot"],
    ["agentcc_managed_upstream_keys", ORG, "--show"],
    ["agentcc_export_ledger", ORG, "--format", "csv", "--out", "/tmp/ledger"],
    ["agentcc_scrub_request_log_credentials"],
    ["agentcc_scrub_request_log_credentials", "--apply"],
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
    # A typo on the list would allow nothing and refuse the real command.
    assert get_commands().get(command) == "agentcc"
    assert command in OPERATOR_STARTUP_MUTATION_COMMANDS


@pytest.mark.parametrize("no_mutations", ["false", "true"])
@pytest.mark.parametrize("line", OPERATOR_COMMAND_LINES, ids=" ".join)
def test_hosted_bootstrap_job_may_run_the_command(monkeypatch, line, no_mutations):
    # The operator bootstrap job form. entrypoint.sh exports
    # NO_STARTUP_DB_MUTATIONS=false for the operator bootstrap, but its
    # bootstrap branch runs migrate and exits, so the operator runs these as
    # the container command (python manage.py ...), bypassing it; the pod spec
    # then usually carries NO_STARTUP_DB_MUTATIONS=true.
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
def test_hosted_application_processes_refuse_the_command(
    monkeypatch, command, service_type, mode
):
    _hosted(monkeypatch, service_type=service_type, mode=mode, no_mutations="true")
    argv = ["manage.py", command]

    assert explicit_management_mutation_authorized(argv) is False
    with pytest.raises(RuntimeError, match=f"^{command} is disabled"):
        _ready(monkeypatch, argv)
