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
