"""Exercise the real management entrypoint, including AppConfig authorization."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from model_hub.apps import (
    STARTUP_SAFE_MANAGEMENT_COMMANDS,
    explicit_management_mutation_authorized,
)


@pytest.mark.parametrize(
    "service", ["backend", "worker", "temporal-worker", "bootstrap"]
)
@pytest.mark.parametrize("mode", ["disabled", "operator"])
def test_erase_requires_explicit_operator_pair(monkeypatch, service, mode):
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "true")
    monkeypatch.setenv("SERVICE_TYPE", service)
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", mode)
    assert explicit_management_mutation_authorized(
        ["manage.py", "erase_metric_data"]
    ) is (service == "bootstrap" and mode == "operator")
    assert "erase_metric_data" not in STARTUP_SAFE_MANAGEMENT_COMMANDS


@pytest.mark.django_db(transaction=True)
def test_real_erase_command_queues_durable_request(workspace):
    from django.db import connection

    from tracer.models.derived_metric import MetricErasureRequest

    backend = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PG_DB": connection.settings_dict["NAME"],
        "SERVICE_TYPE": "bootstrap",
        "STARTUP_DB_MUTATION_MODE": "operator",
        "NO_STARTUP_DB_MUTATIONS": "true",
    }
    result = subprocess.run(
        [
            sys.executable,
            str(backend / "manage.py"),
            "erase_metric_data",
            "--organization",
            str(workspace.organization_id),
            "--scope",
            "organization",
        ],
        cwd=backend,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert "Queued erasure" in result.stdout
    assert MetricErasureRequest.objects.filter(
        organization_key=workspace.organization_id, scope_kind="organization"
    ).exists()


def test_real_erase_command_rejects_ordinary_process():
    backend = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(backend / "manage.py"),
            "erase_metric_data",
            "--organization",
            str(uuid4()),
            "--scope",
            "organization",
        ],
        cwd=backend,
        env={
            **os.environ,
            "NO_STARTUP_DB_MUTATIONS": "true",
            "SERVICE_TYPE": "backend",
            "STARTUP_DB_MUTATION_MODE": "disabled",
        },
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode != 0
    assert "erase_metric_data is disabled during mutation-free startup" in result.stderr


@pytest.mark.parametrize("broken_sink", [False, True])
def test_erase_failure_keeps_subjects_out_of_logs_and_command_error(
    monkeypatch, caplog, capfd, broken_sink
):
    import logging

    from django.core.management.base import CommandError

    from tracer.management.commands import erase_metric_data as command

    private = "synthetic-private-subject"

    def reject(**kwargs):
        raise ValueError(private) from RuntimeError(private)

    def fail_handler(record):
        raise OSError(private)

    monkeypatch.setattr(command, "erase_metric_data", reject)
    if broken_sink:
        monkeypatch.setattr(logging.getLogger(command.__name__), "handle", fail_handler)
    with pytest.raises(CommandError, match="Erasure was not queued") as raised:
        command.Command().handle(
            subjects_file=None,
            organization=str(uuid4()),
            project=None,
            scope="organization",
        )
    assert private not in str(raised.value) and raised.value.__suppress_context__
    captured = capfd.readouterr()
    assert private not in caplog.text + captured.out + captured.err
    if broken_sink:
        assert "private_boundary_log_unavailable" in captured.err
    else:
        (record,) = [
            record for record in caplog.records if record.name == command.__name__
        ]
        assert record.msg["event"] == "metric_erasure_queue_failed"
        assert record.msg["error_type"] == "ValueError"
        # Inspect the raw record as an exporter would, not just the renderer.
        _, marker, tb = record.exc_info
        assert (
            tb is marker.__traceback__ is marker.__context__ is marker.__cause__ is None
        )
        assert private not in str(marker)
