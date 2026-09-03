"""Score-filter live-state regression proof on the CH25 spans path."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from clickhouse_driver import Client

from tracer.services.clickhouse.v2.query_builders.filters import rewrite_v1_sql_to_v2
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_NATIVE_PORT = int(os.environ.get("CH25_NATIVE_PORT", "19000"))
CH_USER = os.environ.get("CH25_USER", "default")
CH_PASSWORD = os.environ.get("CH25_PASSWORD", "")

PROJECT_ID = "00000000-0000-4000-8000-000000000601"
TRACE_ID = "00000000-0000-4000-8000-000000000602"
SESSION_ID = "00000000-0000-4000-8000-000000000603"
LABEL_ID = "00000000-0000-4000-8000-000000000604"
SCORE_ID = "00000000-0000-4000-8000-000000000605"
SPAN_ID = "score-filter-root"
STARTED_AT = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _filters() -> list[dict]:
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    (STARTED_AT - timedelta(minutes=1)).isoformat(),
                    (STARTED_AT + timedelta(minutes=1)).isoformat(),
                ],
            },
        },
        {
            "column_id": LABEL_ID,
            "filter_config": {
                "col_type": "ANNOTATION",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "approved",
            },
        },
    ]


def _assert_every_score_read_has_both_tombstones(sql: str) -> None:
    score_reads = sql.count("FROM model_hub_score AS s FINAL")
    assert score_reads > 0
    assert sql.count("s.deleted = false") == score_reads
    assert sql.count("s._peerdb_is_deleted = 0") == score_reads
    assert "s.is_deleted = 0" not in sql


@pytest.mark.unit
@pytest.mark.parametrize("surface", ["trace", "span", "session"])
def test_score_filter_sql_keeps_cdc_tombstone_on_every_surface(surface: str) -> None:
    if surface == "trace":
        builder = TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=_filters(),
        )
        sql, _ = builder.build_filter_match_query_from_seed_rows(
            [
                {
                    "project_id": PROJECT_ID,
                    "trace_id": TRACE_ID,
                    "root_span_id": SPAN_ID,
                    "start_time": STARTED_AT,
                }
            ]
        )
    elif surface == "span":
        builder = SpanListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=_filters(),
        )
        sql, _ = builder.build_filter_match_query_from_seed_rows(
            [
                {
                    "project_id": PROJECT_ID,
                    "trace_id": TRACE_ID,
                    "id": SPAN_ID,
                    "start_time": STARTED_AT,
                }
            ]
        )
    else:
        builder = SessionListQueryBuilderV2(
            project_id=PROJECT_ID,
            filters=_filters(),
            bounded_internal_scan=True,
        )
        sql, _ = builder.build_filter_match_query([SESSION_ID])

    _assert_every_score_read_has_both_tombstones(sql)


@pytest.mark.unit
def test_v2_rewrite_preserves_only_proven_score_alias_cdc_columns() -> None:
    sql = rewrite_v1_sql_to_v2(
        "SELECT s._peerdb_is_deleted, sp._peerdb_is_deleted "
        "FROM model_hub_score AS s FINAL "
        "LEFT JOIN tracer_observation_span AS sp ON sp.id = s.observation_span_id"
    )

    assert "s._peerdb_is_deleted" in sql
    assert "sp.is_deleted" in sql
    assert "sp._peerdb_is_deleted" not in sql


@pytest.fixture(scope="module")
def ch_client():
    client = Client(
        host=CH_HOST,
        port=CH_NATIVE_PORT,
        user=CH_USER,
        password=CH_PASSWORD,
        connect_timeout=3,
    )
    try:
        client.execute("SELECT version()")
    except Exception as exc:
        pytest.skip(f"CH25 not reachable on {CH_HOST}:{CH_NATIVE_PORT} ({exc!r})")
    return client


@pytest.fixture()
def score_filter_tables(ch_client):
    suffix = uuid.uuid4().hex[:8]
    spans_table = f"_test_score_filter_spans_{suffix}"
    scores_table = f"_test_score_filter_scores_{suffix}"
    ch_client.execute(
        f"""
        CREATE TABLE {spans_table} (
            id String,
            project_id UUID,
            trace_id String,
            parent_span_id Nullable(String),
            trace_name String,
            trace_session_id Nullable(UUID),
            name String,
            service_name String DEFAULT '',
            observation_type String,
            status Nullable(String),
            start_time DateTime64(6, 'UTC'),
            end_time Nullable(DateTime64(6, 'UTC')),
            latency_ms Nullable(Float64),
            cost Nullable(Float64),
            total_tokens Nullable(Int64),
            prompt_tokens Nullable(Int64),
            completion_tokens Nullable(Int64),
            model Nullable(String),
            provider Nullable(String),
            end_user_id Nullable(UUID),
            created_at DateTime64(6, 'UTC'),
            is_deleted UInt8,
            _version UInt64
        )
        ENGINE = ReplacingMergeTree(_version)
        ORDER BY (project_id, trace_id, id, start_time)
        """
    )
    ch_client.execute(
        f"""
        CREATE TABLE {scores_table} (
            id UUID,
            trace_id Nullable(UUID),
            observation_span_id Nullable(String),
            tracer_project_id UUID,
            label_id UUID,
            annotator_id Nullable(UUID),
            value String,
            deleted Bool,
            created_at DateTime64(6, 'UTC'),
            _peerdb_is_deleted UInt8,
            _peerdb_version UInt64
        )
        ENGINE = ReplacingMergeTree(_peerdb_version)
        ORDER BY (label_id, created_at, id)
        """
    )
    ch_client.execute(
        f"""
        INSERT INTO {spans_table}
            (id, project_id, trace_id, parent_span_id, trace_name,
             trace_session_id, name, observation_type, status, start_time,
             end_time, latency_ms, cost, total_tokens, prompt_tokens,
             completion_tokens, model, provider, end_user_id, created_at,
             is_deleted, _version)
        VALUES
        """,
        [
            (
                SPAN_ID,
                PROJECT_ID,
                TRACE_ID,
                None,
                "score-filter-trace",
                SESSION_ID,
                "score-filter-root",
                "agent",
                "OK",
                STARTED_AT,
                STARTED_AT + timedelta(seconds=1),
                1000.0,
                0.01,
                10,
                6,
                4,
                "test-model",
                "test-provider",
                None,
                STARTED_AT,
                0,
                1,
            )
        ],
    )
    try:
        yield spans_table, scores_table
    finally:
        for table in (scores_table, spans_table):
            ch_client.execute(f"DROP TABLE IF EXISTS {table}")


def _insert_score_version(ch_client, scores_table: str, *, hard_deleted: int) -> None:
    ch_client.execute(
        f"""
        INSERT INTO {scores_table}
            (id, trace_id, observation_span_id, tracer_project_id, label_id,
             annotator_id, value, deleted, created_at, _peerdb_is_deleted,
             _peerdb_version)
        VALUES
        """,
        [
            (
                SCORE_ID,
                TRACE_ID,
                SPAN_ID,
                PROJECT_ID,
                LABEL_ID,
                None,
                '{"text":"approved"}',
                False,
                STARTED_AT,
                hard_deleted,
                hard_deleted + 1,
            )
        ],
    )


def _surface_query(
    surface: str,
    *,
    spans_table: str,
    scores_table: str,
) -> tuple[str, dict]:
    if surface == "trace":

        class LocalBuilder(TraceListQueryBuilderV2):
            TABLE = spans_table

        builder = LocalBuilder(project_id=PROJECT_ID, filters=_filters())
        sql, params = builder.build_filter_match_query_from_seed_rows(
            [
                {
                    "project_id": PROJECT_ID,
                    "trace_id": TRACE_ID,
                    "root_span_id": SPAN_ID,
                    "start_time": STARTED_AT,
                }
            ]
        )
    elif surface == "span":

        class LocalBuilder(SpanListQueryBuilderV2):
            TABLE = spans_table

        builder = LocalBuilder(project_id=PROJECT_ID, filters=_filters())
        sql, params = builder.build_filter_match_query_from_seed_rows(
            [
                {
                    "project_id": PROJECT_ID,
                    "trace_id": TRACE_ID,
                    "id": SPAN_ID,
                    "start_time": STARTED_AT,
                }
            ]
        )
    else:

        class LocalBuilder(SessionListQueryBuilderV2):
            TABLE = spans_table

        builder = LocalBuilder(
            project_id=PROJECT_ID,
            filters=_filters(),
            bounded_internal_scan=True,
        )
        params = {
            "project_id": PROJECT_ID,
            "start_date": STARTED_AT - timedelta(minutes=1),
            "end_date": STARTED_AT + timedelta(minutes=1),
        }
        relational_ctes, predicates, relational_params = (
            builder._bounded_relational_membership_plan(
                [_filters()[1]],
                scope_to_request_window=True,
                available_params=params,
            )
        )
        params.update(relational_params)
        assert len(predicates) == 1
        sql = f"""
        WITH resolved_root_sessions AS (
            SELECT trace_id, trace_session_id AS session_id
            FROM {spans_table}
            WHERE project_id = %(project_id)s
              AND trace_session_id = toUUID('{SESSION_ID}')
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND is_deleted = 0
        ){relational_ctes}
        SELECT DISTINCT session_id
        FROM resolved_root_sessions
        WHERE {predicates[0]}
        """

    sql = sql.replace("model_hub_score", scores_table).replace(
        "FROM spans ", f"FROM {spans_table} "
    )
    return sql, params


@pytest.mark.integration
@pytest.mark.parametrize("surface", ["trace", "span", "session"])
def test_higher_score_cdc_tombstone_is_excluded_on_ch25(
    ch_client,
    score_filter_tables,
    surface: str,
) -> None:
    spans_table, scores_table = score_filter_tables
    _insert_score_version(ch_client, scores_table, hard_deleted=0)
    sql, params = _surface_query(
        surface,
        spans_table=spans_table,
        scores_table=scores_table,
    )

    assert ch_client.execute(sql, params), "live Score version must match"

    # The higher CDC version deliberately retains deleted=false. FINAL selects
    # it, and only the hard-tombstone predicate can prevent resurrection.
    _insert_score_version(ch_client, scores_table, hard_deleted=1)

    assert ch_client.execute(sql, params) == []
