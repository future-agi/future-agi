"""Real-ClickHouse proof that Session and Users EVAL graphs compile.

Both graphs select their candidate traces from the configured eval logger
under the ``candidate_eval`` alias, inside SQL that the v2 builder rewrites
as a whole. On the legacy PeerDB table (``CH25_EVAL_LOGGER_TABLE`` default)
the rewrite once renamed ``candidate_eval._peerdb_is_deleted`` to
``candidate_eval.is_deleted``, a column that table does not have, and every
Session/Users EVAL graph failed with ClickHouse code 47 (HTTP 500), while
Trace/Span graphs of the same metric worked.

The statement is rendered by ``read_exact_eval_graph`` and executed through
``AnalyticsQueryService`` exactly as a request does. The legacy table is
created from the repo's own ``CDC_EVAL_LOGGER`` DDL under a unique name, in
the configured test database next to the v2 ``spans``/``end_users``/remap
tables the membership SQL joins. The defect is raised while ClickHouse
analyses the statement, so the statement completing is the assertion.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import override_settings

from conftest import _ch_test_native_client, _ch_test_native_port
from tracer.services.clickhouse import exact_graph_reads
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.schema import CDC_EVAL_LOGGER, _to_single_node_engine

pytestmark = pytest.mark.integration

PROJECT_ID = "22222222-2222-4222-8222-222222222222"
EVAL_CONFIG_ID = "33333333-3333-4333-8333-333333333333"


def _test_database() -> str:
    database = os.getenv("CH25_DATABASE") or os.getenv("CH_DATABASE") or ""
    if not database:
        pytest.skip("no test ClickHouse configured")
    if not database.lower().lstrip("_").startswith("test_"):
        pytest.skip("test ClickHouse database is not a test_* database")
    return database


@pytest.fixture(scope="module")
def ch_database():
    return _test_database()


@pytest.fixture(scope="module")
def ch_client(ch_database):
    with _ch_test_native_client(database=ch_database) as client:
        yield client


@pytest.fixture()
def legacy_eval_table(ch_client):
    table = f"_test_entity_eval_logger_{uuid.uuid4().hex[:10]}"
    ddl = _to_single_node_engine(CDC_EVAL_LOGGER).replace(
        "CREATE TABLE IF NOT EXISTS tracer_eval_logger (",
        f"CREATE TABLE {table} (",
    )
    assert table in ddl
    ch_client.execute(ddl)
    columns = {
        row[0]
        for row in ch_client.execute(
            "SELECT name FROM system.columns"
            " WHERE database = currentDatabase() AND table = %(table)s",
            {"table": table},
        )
    }
    # The shape the dev/prod PeerDB mirror has: CDC tombstone + app soft
    # delete, and no v2 ``is_deleted`` column.
    assert {"_peerdb_is_deleted", "deleted"} <= columns
    assert "is_deleted" not in columns
    try:
        with (
            override_settings(CH25_EVAL_LOGGER_TABLE=table),
            mock.patch(
                "tracer.services.clickhouse.eval_logger_table."
                "SUPPORTED_EVAL_LOGGER_TABLES",
                frozenset({table}),
            ),
        ):
            yield table
    finally:
        ch_client.execute(f"DROP TABLE IF EXISTS {table}")


def _analytics(database: str) -> AnalyticsQueryService:
    service = AnalyticsQueryService()
    service._ch_client = ClickHouseClient(
        host=os.environ.get("CH25_HOST", "127.0.0.1"),
        port=_ch_test_native_port().port,
        database=database,
    )
    return service


@pytest.mark.parametrize("aggregation_context", ["session", "user"])
def test_entity_eval_graph_runs_on_legacy_eval_logger(
    monkeypatch,
    ch_database,
    legacy_eval_table,
    aggregation_context,
):
    config = SimpleNamespace(
        name="quality",
        eval_template=SimpleNamespace(config={"output": "SCORE"}, choices=[]),
    )
    monkeypatch.setattr(
        exact_graph_reads.CustomEvalConfig.objects,
        "select_related",
        lambda *_args: SimpleNamespace(get=lambda **_kwargs: config),
    )
    analytics = _analytics(ch_database)
    statements = []
    execute = analytics.execute_ch_query

    def recording_execute(query, *args, **kwargs):
        statements.append(query)
        return execute(query, *args, **kwargs)

    monkeypatch.setattr(analytics, "execute_ch_query", recording_execute)
    end = datetime(2026, 9, 25, 12)
    start = end - timedelta(days=183)

    result = exact_graph_reads.read_exact_eval_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[
            {
                "column_id": "start_time",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start, end],
                },
            }
        ],
        interval="week",
        req_data_config={"id": EVAL_CONFIG_ID, "output_type": "SCORE"},
        observe_type="trace",
        aggregation_context=aggregation_context,
    )

    assert result["query_status"] == "complete"
    assert len(statements) == 1
    assert f"FROM {legacy_eval_table} AS candidate_eval FINAL" in statements[0]
    assert "candidate_eval._peerdb_is_deleted = 0" in statements[0]
