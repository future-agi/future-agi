"""Regression checks for advisory estimate metadata and connection isolation."""

from unittest.mock import patch

import pytest

from tracer.sql_workbench import runner, service

FILTERS = "{'analytics.spans': 'project_id = toUUID(\\'1\\')'}"


@pytest.mark.parametrize("primary_key", ["org_id", "", "tuple()"])
def test_estimate_withholds_a_shorter_or_empty_primary_key(primary_key):
    class MetadataClient:
        def execute(self, query, *args, **kwargs):
            if query.startswith("EXPLAIN"):
                return [("analytics", "spans", 10, 10000, 100)]
            assert query.startswith("SELECT name,")
            # ORDER BY includes the project, but the sparse primary index does not.
            key = primary_key if "primary_key" in query else "org_id, project_id, id"
            return [("spans", key, "MergeTree ORDER BY (org_id, project_id, id)")]

        def disconnect(self):
            pass

    with (
        patch("clickhouse_driver.Client", return_value=MetadataClient()),
        patch.object(runner, "connection_config", return_value={}),
        patch.object(runner, "verify_profile"),
        patch.dict(
            "os.environ",
            {
                "SQL_WORKBENCH_ESTIMATE_CH_USER": "estimator",
                "SQL_WORKBENCH_ESTIMATE_CH_PASSWORD": "test-only",
            },
        ),
    ):
        raw = runner.estimate(
            "SELECT count() FROM scoped", table_filters=FILTERS, database="analytics"
        )
    entry = service.summarize_estimate(raw, "analytics")["sources"][0]
    assert entry["estimated"] is False
    assert entry["parts"] is entry["marks"] is entry["max_rows"] is None


@pytest.mark.parametrize(
    "database,table,primary_key",
    [
        ("analytics", "spans", "org_id"),
        ("analytics", "spans", ""),
        ("analytics", "spans", "tuple()"),
        ("analytics", "spans", None),
        ("analytics", "model_hub_score", "label_id, created_at, id"),
        ("analytics", "unknown", "project_id"),
        ("other", "spans", "project_id"),
    ],
)
@pytest.mark.parametrize("local_first", [False, True])
def test_withheld_sources_cannot_disclose_a_scan_threshold(
    database, table, primary_key, local_first
):
    bound = runner.QUERY_SETTINGS["max_rows_to_read"]
    safe_table = "spans" if database == "other" else "traces"
    safe = {
        "database": "analytics",
        "table": safe_table,
        "parts": 1,
        "rows": 2,
        "marks": 1,
    }
    withheld = {
        "database": database,
        "table": table,
        "parts": 1,
        "marks": 1,
    }
    # Even a second physical read of the same dataset can have unsafe metadata.
    # Test both orders so a safe read cannot restore the aggregate flag.
    layout = {safe_table: ("project_id", "MergeTree")}
    if primary_key is None:
        layout.pop(table, None)
    else:
        layout[table] = (primary_key, "MergeTree")
    summaries = []
    for rows in (bound - 3, bound - 1):
        tables = [safe, {**withheld, "rows": rows}]
        if not local_first:
            tables.reverse()
        summaries.append(
            service.summarize_estimate(
                {"tables": tables, "layout": layout}, "analytics"
            )
        )
    # The raw aggregate crosses the real configured bound; no response field may
    # turn this withheld volume into even a single threshold bit.
    assert summaries[0] == summaries[1]
    assert summaries[0]["exceeds_scan_bound"] is None
    assert any(not source["estimated"] for source in summaries[0]["sources"])


@pytest.mark.parametrize("offset,exceeds", [(-1, False), (0, False), (1, True)])
def test_scan_threshold_is_preserved_when_every_source_is_estimated(offset, exceeds):
    bound = runner.QUERY_SETTINGS["max_rows_to_read"]
    raw = {
        "tables": [
            {
                "database": "analytics",
                "table": table,
                "parts": 1,
                "rows": rows,
                "marks": 1,
            }
            for table, rows in [("spans", 2), ("traces", bound - 2 + offset)]
        ],
        "layout": dict.fromkeys(("spans", "traces"), ("project_id, id", "MergeTree")),
    }
    summary = service.summarize_estimate(raw, "analytics")
    assert summary["exceeds_scan_bound"] is exceeds
    assert all(source["estimated"] for source in summary["sources"])


@pytest.mark.parametrize(
    "username,password",
    [
        ("", ""),
        ("executor", "test-only"),
        ("default", "test-only"),
        ("admin", "test-only"),
        ("estimator", ""),
    ],
)
def test_estimate_never_falls_back_to_execution_or_admin(
    username, password, monkeypatch, settings
):
    settings.CLICKHOUSE_V2 = {
        "CH25_USER": "admin",
        "CH25_HOST": "localhost",
        "CH25_DATABASE": "analytics",
    }
    monkeypatch.setenv("SQL_WORKBENCH_CH_USER", "executor")
    monkeypatch.setenv("SQL_WORKBENCH_CH_PASSWORD", "test-only")
    monkeypatch.setenv("SQL_WORKBENCH_ESTIMATE_CH_USER", username)
    monkeypatch.setenv("SQL_WORKBENCH_ESTIMATE_CH_PASSWORD", password)
    with patch("clickhouse_driver.Client") as client:
        with pytest.raises(runner.QueryUnavailable):
            runner.estimate(
                "SELECT count() FROM scoped",
                table_filters=FILTERS,
                database="analytics",
            )
    client.assert_not_called()


def test_estimate_profile_is_stricter_without_changing_execution_limits():
    from tracer.sql_workbench.provision import service_access_ddl

    kwargs = {
        "database": "analytics",
        "principal": "estimator",
        "password": "test-only" * 4,
        "cidr": "10.3.0.0/24",
    }
    estimate = service_access_ddl(**kwargs, estimate=True)
    execution = service_access_ddl(**kwargs)
    assert estimate[1:] == execution[1:]
    assert "max_concurrent_queries_for_user = 1 READONLY" in estimate[0]
    assert "max_execution_time = 5 READONLY" in estimate[0]
    assert "max_threads = 1 READONLY" in estimate[0]
    assert "max_concurrent_queries_for_user = 2 READONLY" in execution[0]
    assert "max_execution_time = 30 READONLY" in execution[0]
    assert estimate[0].count("CHANGEABLE_IN_READONLY") == 1


@pytest.mark.parametrize(
    "loose_setting",
    [
        "max_concurrent_queries_for_user",
        "max_execution_time",
        "max_execution_time_leaf",
        "max_threads",
    ],
)
def test_estimate_rejects_a_looser_profile(loose_setting):
    values = {**runner.ESTIMATE_SETTINGS, "max_concurrent_queries_for_user": 1}
    values[loose_setting] *= 2

    class ProfileClient:
        def execute(self, query):
            return [list(values.values())]

    with pytest.raises(runner.QueryUnavailable):
        runner.verify_profile(ProfileClient(), estimate=True)


def test_estimate_provision_command_requires_a_distinct_principal(
    monkeypatch, settings
):
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import CommandError

    settings.CLICKHOUSE_V2 = {"CH25_DATABASE": "analytics"}
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    monkeypatch.setenv("SQL_WORKBENCH_CH_USER", "executor")
    monkeypatch.setenv("SQL_WORKBENCH_ESTIMATE_CH_PASSWORD", "test-only" * 4)
    for principal in ("executor", "futureagi_sql_service"):
        with pytest.raises(CommandError, match="distinct"):
            call_command(
                "provision_sql_service",
                estimate=True,
                principal=principal,
                client_cidr="10.3.0.0/24",
                stdout=StringIO(),
            )
    output = StringIO()
    call_command(
        "provision_sql_service",
        estimate=True,
        principal="estimator",
        client_cidr="10.3.0.0/24",
        stdout=output,
    )
    assert "max_concurrent_queries_for_user = 1 READONLY" in output.getvalue()
