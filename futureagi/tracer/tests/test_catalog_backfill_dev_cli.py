from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tracer.services.clickhouse.v2 import catalog_backfill_dev_cli as cli

PROJECT_ID = "11111111-1111-4111-8111-111111111111"


def _argv(*extra: str, write: bool = False) -> list[str]:
    return [
        "--environment",
        "development",
        "--cloud-deployment",
        "DEV",
        "--dev-identity",
        "dev:unit-test",
        "--ack",
        cli.CATALOG_BACKFILL_ACK,
        "--project-id",
        PROJECT_ID,
        "--since",
        "2026-08-01T00:00:00Z",
        "--until",
        "2026-08-01T01:00:00Z",
        "--epoch",
        "103",
        "--source-url",
        "http://source-dev:8123",
        "--source-database",
        "futureagi",
        "--source-username",
        "catalog_source_reader",
        "--catalog-url",
        "https://catalog-dev:8443",
        "--catalog-database",
        "property_catalog_dev_unit",
        "--catalog-username",
        "catalog_writer",
        "--execute-writes" if write else "--dry-run",
        *extra,
    ]


def _env() -> dict[str, str]:
    return {
        cli.RUNTIME_ENVIRONMENT_ENV: "development",
        cli.RUNTIME_CLOUD_DEPLOYMENT_ENV: "DEV",
        cli.DEV_ENDPOINT_IDENTITY_ENV: "dev:unit-test",
        cli.SOURCE_PASSWORD_ENV: "source-secret",
        cli.CATALOG_PASSWORD_ENV: "catalog-secret",
    }


def test_parse_config_requires_explicit_dev_scope_and_distinct_endpoints() -> None:
    parsed = cli.parse_config(_argv(), environ=_env())
    assert parsed.backfill.project_id == PROJECT_ID
    assert parsed.backfill.catalog_epoch == 103
    assert parsed.backfill.dry_run
    assert parsed.source.host == "source-dev"
    assert parsed.source.port == 8123
    assert not parsed.source.secure
    assert parsed.source.database == "futureagi"
    assert parsed.catalog.host == "catalog-dev"
    assert parsed.catalog.port == 8443
    assert parsed.catalog.secure
    assert parsed.catalog.database == "property_catalog_dev_unit"


def test_execute_writes_mode_is_explicit_and_never_inferred() -> None:
    parsed = cli.parse_config(_argv(write=True), environ=_env())
    assert not parsed.backfill.dry_run
    with pytest.raises(SystemExit):
        cli.parse_config(_argv()[:-1], environ=_env())
    with pytest.raises(SystemExit):
        cli.parse_config(_argv() + ["--execute-writes"], environ=_env())


@pytest.mark.parametrize(
    ("argv", "environment", "message"),
    [
        (
            _argv(),
            {
                cli.RUNTIME_ENVIRONMENT_ENV: "development",
                cli.RUNTIME_CLOUD_DEPLOYMENT_ENV: "DEV",
                cli.DEV_ENDPOINT_IDENTITY_ENV: "dev:unit-test",
                cli.CATALOG_PASSWORD_ENV: "x",
            },
            "SOURCE_PASSWORD",
        ),
        (
            _argv(),
            {
                cli.RUNTIME_ENVIRONMENT_ENV: "development",
                cli.RUNTIME_CLOUD_DEPLOYMENT_ENV: "DEV",
                cli.DEV_ENDPOINT_IDENTITY_ENV: "dev:unit-test",
                cli.SOURCE_PASSWORD_ENV: "x",
            },
            "CATALOG_PASSWORD",
        ),
        (
            _argv("--source-url", "http://user:pw@source-dev:8123"),
            _env(),
            "without credentials",
        ),
        (
            _argv("--catalog-url", "http://catalog-dev:8123/path"),
            _env(),
            "without credentials",
        ),
        (
            _argv("--catalog-database", "futureagi"),
            _env(),
            "must be distinct",
        ),
        (
            _argv("--catalog-database", "production_catalog"),
            _env(),
            "must start",
        ),
    ],
)
def test_parse_config_fails_closed_on_credentials_url_and_database_aliasing(
    argv: list[str], environment: dict[str, str], message: str
) -> None:
    with pytest.raises(cli.CatalogBackfillError, match=message):
        cli.parse_config(argv, environ=environment)


def test_parse_config_refuses_non_dev_or_bad_sentinel_before_connections() -> None:
    environment = _env()
    environment[cli.RUNTIME_ENVIRONMENT_ENV] = "production"
    with pytest.raises(cli.CatalogBackfillError, match="explicitly equal development"):
        cli.parse_config(_argv(), environ=environment)
    argv = _argv()
    argv[argv.index("--environment") + 1] = "production"
    with pytest.raises(cli.CatalogBackfillError, match="development-only"):
        cli.parse_config(argv, environ=_env())
    argv = _argv()
    argv[argv.index("--ack") + 1] = "yes"
    with pytest.raises(cli.CatalogBackfillError, match="acknowledgement"):
        cli.parse_config(argv, environ=_env())

    environment = _env()
    environment[cli.RUNTIME_CLOUD_DEPLOYMENT_ENV] = "US"
    with pytest.raises(cli.CatalogBackfillError, match="explicitly equal DEV"):
        cli.parse_config(_argv(), environ=environment)

    environment = _env()
    environment[cli.DEV_ENDPOINT_IDENTITY_ENV] = "dev:different"
    with pytest.raises(cli.CatalogBackfillError, match="exactly match"):
        cli.parse_config(_argv(), environ=environment)


class FakeClient:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs
        self.closed = False

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class FakeSummary:
    stopped: bool
    dry_run: bool
    project_id: str = PROJECT_ID


def test_run_builds_four_bounded_clients_closes_all_and_renders_json() -> None:
    clients: list[FakeClient] = []
    runner_inputs: list[tuple[Any, Any, Any]] = []
    output: list[str] = []
    errors: list[str] = []

    def client_factory(**kwargs: Any) -> FakeClient:
        client = FakeClient(kwargs)
        clients.append(client)
        return client

    class Runner:
        def __init__(self, io: Any, config: Any, *, stop_requested: Any) -> None:
            runner_inputs.append((io, config, stop_requested))

        def run(self) -> FakeSummary:
            return FakeSummary(stopped=False, dry_run=True)

    exit_code = cli.run(
        _argv(),
        environ=_env(),
        client_factory=client_factory,
        runner_factory=Runner,
        stdout=output.append,
        stderr=errors.append,
    )
    assert exit_code == cli.EXIT_OK
    assert len(clients) == 4
    assert all(client.closed for client in clients)
    assert clients[0].kwargs == {
        "host": "source-dev",
        "port": 8123,
        "secure": False,
        "username": "catalog_source_reader",
        "password": "source-secret",
        "database": "futureagi",
        "connect_timeout": 5,
        "send_receive_timeout": 10.0,
        "query_retries": 0,
        "autogenerate_query_id": False,
    }
    assert clients[1].kwargs["host"] == "catalog-dev"
    assert clients[1].kwargs["secure"] is True
    assert clients[1].kwargs["password"] == "catalog-secret"
    assert clients[1].kwargs["database"] == "property_catalog_dev_unit"
    assert clients[2].kwargs == clients[0].kwargs
    assert clients[3].kwargs == clients[1].kwargs
    assert len(runner_inputs) == 1
    assert '"project_id": "11111111-1111-4111-8111-111111111111"' in output[0]
    assert errors == []


def test_run_returns_resumable_exit_code_when_runner_stops_at_boundary() -> None:
    errors: list[str] = []

    def client_factory(**kwargs: Any) -> FakeClient:
        return FakeClient(kwargs)

    class Runner:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def run(self) -> FakeSummary:
            return FakeSummary(stopped=True, dry_run=False)

    assert (
        cli.run(
            _argv(write=True),
            environ=_env(),
            client_factory=client_factory,
            runner_factory=Runner,
            stderr=errors.append,
        )
        == cli.EXIT_INCOMPLETE
    )
    assert any("rerun to resume" in message for message in errors)


def test_run_sanitizes_connection_and_driver_failures() -> None:
    messages: list[str] = []

    def connection_failure(**_kwargs: Any) -> Any:
        raise RuntimeError("catalog-secret must never be printed")

    assert (
        cli.run(
            _argv(),
            environ=_env(),
            client_factory=connection_failure,
            stderr=messages.append,
        )
        == cli.EXIT_RUNTIME_ERROR
    )
    assert messages == ["connection failed: RuntimeError"]


def test_direct_script_help_runs_without_django_or_clickhouse_connect() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "services/clickhouse/v2/catalog_backfill_dev_cli.py"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert "Django-free" in result.stdout
    assert "--execute-writes" in result.stdout
    assert "Traceback" not in result.stderr


def test_flat_five_file_bundle_runs_without_repository_package_init(tmp_path) -> None:
    source_dir = Path(__file__).resolve().parents[1] / "services/clickhouse/v2"
    sources = {
        name: source_dir / name
        for name in (
            "catalog_backfill_dev_cli.py",
            "attribute_catalog_backfill.py",
            "attribute_catalog_builder.py",
            "attribute_catalog_codec.py",
        )
    }
    sources["attribute_suggestion_contract.py"] = (
        Path(__file__).resolve().parents[1] / "utils/attribute_suggestion_contract.py"
    )
    for name, source in sources.items():
        shutil.copy2(source, tmp_path / name)
    result = subprocess.run(
        [sys.executable, str(tmp_path / "catalog_backfill_dev_cli.py"), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert "Django-free" in result.stdout
    assert result.stderr == ""
