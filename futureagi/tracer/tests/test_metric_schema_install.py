# ruff: noqa: F811 -- shared pytest fixtures imported from the admission suite
"""The Metrics ClickHouse schema step and its readiness probe.

The file list and the probe's markers are checked against the packaged SQL;
the CLI, the API gate and the refresh gate run against fakes, never a server.
"""

import os
import re
from pathlib import Path

import pytest
import structlog.testing

from tracer.services.clickhouse import v2 as clickhouse_v2
from tracer.services.clickhouse.v2 import apply_schema
from tracer.services.derived_metrics import admission, schema_install
from tracer.tests.test_derived_metric_admission import (  # noqa: F401
    enabled,
    state_factory,
)

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "services/clickhouse/v2/schema"
CONFIG = {
    "host": "ch.internal",
    "http_port": 8124,
    "tcp_port": 9001,
    "user": "metrics_admin",
    "password": "not-a-real-password",
    "database": "fi",
}


@pytest.fixture
def probe(monkeypatch):
    """The real cache, starting empty; the probe's answer is set per test."""
    monkeypatch.setattr(schema_install, "_installed", False)
    monkeypatch.setattr(schema_install, "_recheck_at", 0.0)
    monkeypatch.setattr(schema_install, "_absent", ())
    answer = {"rows": [], "calls": 0}

    def fake_probe():
        answer["calls"] += 1
        if isinstance(answer["rows"], BaseException):
            raise answer["rows"]
        return schema_install.missing(lambda sql: answer["rows"])

    monkeypatch.setattr(schema_install, "_probe", fake_probe)
    return answer


def _sql(filename):
    return (SCHEMA_DIR / filename).read_text()


def _created_tables(filename):
    return re.findall(
        r"^CREATE TABLE IF NOT EXISTS (\w+)", _sql(filename), flags=re.MULTILINE
    )


# ---------------------------------------------------------------------------
# The file list and the markers match the packaged SQL
# ---------------------------------------------------------------------------


def test_files_are_009_011_then_every_packaged_metrics_file_in_order():
    packaged = sorted(p.name for p in SCHEMA_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    metrics = [name for name in packaged if 28 <= int(name[:3]) <= 38]
    assert metrics[-1] == "038_metric_snapshot_sketches.sql"
    assert schema_install.FILES == (
        "009_per_eval_rollup.sql",
        "011_eval_logger_v2.sql",
        *metrics,
    )
    for filename in schema_install.FILES:
        assert (SCHEMA_DIR / filename).is_file(), filename


def test_markers_cover_every_metrics_table_and_the_later_columns():
    created = [
        table
        for filename in schema_install.FILES[2:]
        for table in _created_tables(filename)
    ]
    assert sorted(created) == sorted(schema_install.TABLES)
    for filename in schema_install.FILES[2:]:
        for statement in re.split(r";\s*\n", _sql(filename)):
            match = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", statement)
            if match:
                assert re.search(r"^\s*project_id\s", statement, re.M), match[1]
    assert re.search(r"ADD COLUMN IF NOT EXISTS ci_interval", _sql(SQL_029))
    assert "derived_metrics AS" in _sql("030_derived_metric_view_refresh.sql")
    assert re.search(r"ADD COLUMN IF NOT EXISTS value_text", _sql(SQL_038))
    assert re.search(
        r"tracer_eval_logger_v2 ADD COLUMN IF NOT EXISTS config_hash", _sql(SQL_028)
    )
    assert "tracer_eval_logger_v2" in _created_tables("011_eval_logger_v2.sql")


SQL_028 = "028_derived_metrics.sql"
SQL_029 = "029_derived_metric_uncertainty.sql"
SQL_038 = "038_metric_snapshot_sketches.sql"


def test_probe_is_one_parameter_free_read_and_names_what_is_missing():
    seen = []

    def execute(sql):
        seen.append(sql)
        return [(t, c) for t, c in schema_install.MARKERS if t != "metric_unit_state"]

    assert schema_install.missing(execute) == ["metric_unit_state.project_id"]
    (sql,) = seen
    assert sql.startswith("SELECT table, name FROM system.columns ")
    assert "currentDatabase()" in sql and "%(" not in sql
    assert schema_install.missing(lambda _: list(schema_install.MARKERS)) == []
    assert len(schema_install.missing(lambda _: [])) == len(schema_install.MARKERS)


# ---------------------------------------------------------------------------
# The readiness cache
# ---------------------------------------------------------------------------


def test_installed_is_remembered_and_missing_is_rechecked_after_a_while(probe):
    now = [100.0]
    clock = lambda: now[0]  # noqa: E731
    with pytest.raises(schema_install.MetricSchemaNotInstalled) as missing:
        schema_install.ensure_installed(clock=clock)
    assert missing.value.code == "metric_schema_not_installed"
    assert str(missing.value).startswith("Metrics schema not installed. Run `")
    # Within the recheck interval the answer is reused, not probed again.
    with pytest.raises(schema_install.MetricSchemaNotInstalled):
        schema_install.ensure_installed(clock=clock)
    assert probe["calls"] == 1
    probe["rows"] = list(schema_install.MARKERS)
    now[0] += schema_install.RECHECK_SECONDS
    schema_install.ensure_installed(clock=clock)
    probe["rows"] = []
    schema_install.ensure_installed(clock=clock)
    assert probe["calls"] == 2


def test_a_probe_that_cannot_connect_caches_nothing(probe):
    probe["rows"] = ConnectionError("refused")
    with pytest.raises(ConnectionError):
        schema_install.ensure_installed()
    probe["rows"] = list(schema_install.MARKERS)
    schema_install.ensure_installed()
    assert probe["calls"] == 2


# ---------------------------------------------------------------------------
# The CLI: python -m tracer.services.derived_metrics.schema_install
# ---------------------------------------------------------------------------


@pytest.fixture
def runner(monkeypatch, settings, probe):
    settings.METRICS_ENABLED = 1
    monkeypatch.delenv("ENV_TYPE", raising=False)
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    monkeypatch.setenv("CH_PASSWORD", "the-callers-own")
    monkeypatch.setattr(clickhouse_v2, "get_v2_config", lambda: dict(CONFIG))
    calls = {"argv": [], "password": [], "exit": 0}

    def fake_main(argv):
        calls["argv"].append(list(argv))
        calls["password"].append(os.environ.get("CH_PASSWORD"))
        if calls["exit"] == 0:
            probe["rows"] = list(schema_install.MARKERS)
        return calls["exit"]

    monkeypatch.setattr(apply_schema, "main", fake_main)
    return calls


def test_disabled_metrics_change_nothing(runner, settings, probe):
    settings.METRICS_ENABLED = 0
    assert schema_install.main(["--apply"]) == 0
    assert schema_install.main(["--check"]) == 0
    assert runner["argv"] == [] and probe["calls"] == 0


def test_apply_runs_the_packaged_runner_on_the_v2_target_then_verifies(runner, probe):
    assert schema_install.main(["--apply"]) == 0
    (argv,) = runner["argv"]
    assert argv == [
        "--ch-host",
        "ch.internal",
        "--ch-http-port",
        "8124",
        "--ch-user",
        "metrics_admin",
        "--ch-database",
        "fi",
        "--files",
        *schema_install.FILES,
    ]
    # Never --force, never a password in argv; CH_PASSWORD is restored after.
    assert "--force" not in argv and CONFIG["password"] not in argv
    assert runner["password"] == [CONFIG["password"]]
    assert os.environ["CH_PASSWORD"] == "the-callers-own"
    assert probe["calls"] == 1


@pytest.mark.parametrize("code", [1, 2, 3])
def test_a_runner_failure_is_the_exit_code_and_says_what_to_do(
    runner, probe, capsys, code
):
    runner["exit"] = code
    assert schema_install.main(["--apply"]) == code
    out = capsys.readouterr().out
    if code == 2:
        assert "METRICS_ENABLED=0" in out
    assert probe["calls"] == 0


def test_check_only_reads_and_fails_while_missing(runner, probe, capsys):
    assert schema_install.main(["--check"]) == 1
    assert "Metrics schema not installed: missing " in capsys.readouterr().out
    probe["rows"] = list(schema_install.MARKERS)
    assert schema_install.main(["--check"]) == 0
    assert runner["argv"] == []


@pytest.mark.parametrize("code", [1, 2, 3])
def test_advisory_reports_a_failure_but_lets_startup_continue(
    runner, probe, capsys, code
):
    runner["exit"] = code
    with structlog.testing.capture_logs() as logs:
        assert schema_install.main(["--apply", "--advisory"]) == 0
    assert "continuing without Metrics" in capsys.readouterr().out
    (record,) = [r for r in logs if r["event"] == "metrics_schema_step_failed"]
    assert record["log_level"] == "error" and record["step"] == "apply"
    assert record["cause"] == schema_install.CAUSES[code]
    runner["exit"] = 0
    probe["rows"] = []
    assert schema_install.main(["--check", "--advisory"]) == 0
    assert "Metrics schema not installed: missing " in capsys.readouterr().out


def test_advisory_turns_an_unexpected_error_into_a_log_line(
    runner, monkeypatch, capsys
):
    def broken():
        raise RuntimeError("ch.internal:8124 secret detail")

    monkeypatch.setattr(clickhouse_v2, "get_v2_config", broken)
    assert schema_install.main(["--apply", "--advisory"]) == 0
    out = capsys.readouterr().out
    assert "(RuntimeError)" in out and "secret detail" not in out
    with pytest.raises(RuntimeError):
        schema_install.main(["--apply"])


def test_hosted_production_never_applies(runner, monkeypatch, probe):
    monkeypatch.setenv("ENV_TYPE", "prod")
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    assert schema_install.main(["--apply"]) == 0
    assert runner["argv"] == [] and probe["calls"] == 0


# ---------------------------------------------------------------------------
# The API and the refresh worker refuse with one stable code
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_api_refuses_metrics_without_their_schema(auth_client, settings, probe):
    from tracer.tests.test_derived_metric_api import ROOT

    settings.METRICS_ENABLED = 1
    response = auth_client.get(ROOT)
    assert response.status_code == 503, response.data
    assert response.data["code"] == "metric_schema_not_installed"
    assert response.data["retryable"] is True
    assert response.data["message"].startswith("Metrics schema not installed.")

    probe["rows"] = ConnectionError("refused")
    assert auth_client.get(ROOT).status_code == 503  # still within the recheck
    schema_install._recheck_at = 0.0
    # A probe that cannot connect is not a refusal: the request goes on.
    assert auth_client.get(ROOT).status_code == 200

    probe["rows"] = list(schema_install.MARKERS)
    assert auth_client.get(ROOT).status_code == 200


@pytest.mark.django_db(transaction=True)
def test_a_refresh_without_the_schema_fails_before_preparing_an_attempt(
    enabled, state_factory, monkeypatch, probe
):
    from datetime import UTC, datetime

    import clickhouse_driver

    from tracer.models.derived_metric import MetricRefreshAttempt

    now = datetime(2026, 11, 5, 12, tzinfo=UTC)
    state = state_factory(pending_backfill=True)

    class Client:
        def __init__(self, **kwargs):
            pass

        def execute(self, sql, *args, **kwargs):
            assert sql == schema_install.probe_query(), "the probe runs first"
            return []

        def disconnect(self):
            pass

    monkeypatch.setattr(clickhouse_driver, "Client", Client)
    (reservation,) = admission.reserve_projects(clock=lambda: now)
    with pytest.raises(schema_install.MetricSchemaNotInstalled):
        admission.execute_reservation(reservation.payload(), clock=lambda: now)
    state.refresh_from_db()
    assert state.last_error_code == "metric_schema_not_installed"
    assert state.consecutive_failures == 1
    assert state.pending_backfill and state.watermark_ns == 0
    assert state.next_due_at is None and state.lease_token is None
    assert not MetricRefreshAttempt.objects.filter(project_id=state.project_id)
