"""Live ClickHouse proof for parameter-bound bounded-filter Map keys."""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from time import monotonic
from unittest import mock

import pytest
from clickhouse_driver import Client

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.attribute_reads import (
    _STRATIFIED_CANDIDATE_SQL,
    ATTRIBUTE_READ_CANDIDATE_LIMIT,
    AttributeKeyRow,
    AttributeQueryPage,
    AttributeReadSelector,
)
from tracer.services.clickhouse.bounded_graph_reads import GraphCandidateSample
from tracer.services.clickhouse.graph_dispatch import (
    _finite_annotation_rows,
    _finite_trace_span_candidates,
    _trace_system_metric_query,
)
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_span_filter_plans,
)
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
    rewrite_v1_sql_to_v2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

pytestmark = pytest.mark.integration

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_NATIVE_PORT = int(os.environ.get("CH25_NATIVE_PORT", "19000"))
CH_USER = os.environ.get("CH25_USER", "default")
CH_PASSWORD = os.environ.get("CH25_PASSWORD", "")


def _unix_microseconds(value: datetime) -> int:
    utc_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    delta = utc_value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


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
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"CH 25.3 not reachable on {CH_HOST}:{CH_NATIVE_PORT} ({exc!r})")
    return client


@pytest.fixture()
def attribute_table(ch_client):
    table = f"_test_bounded_filter_key_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            id String,
            _version UInt64,
            attrs_string Map(String, String)
        )
        ENGINE = MergeTree
        ORDER BY (id, _version)
        """
    )
    try:
        yield table
    finally:
        ch_client.execute(f"DROP TABLE {table}")


@pytest.fixture()
def bounded_span_table(ch_client):
    table = f"_test_bounded_span_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            id String,
            project_id UUID,
            trace_id String,
            parent_span_id Nullable(String),
            trace_name String,
            trace_session_id Nullable(String),
            project_version_id Nullable(UUID),
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
            _version UInt64,
            attrs_string Map(String, String),
            attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8),
            attributes_extra String
        )
        ENGINE = MergeTree
        ORDER BY (
            project_id,
            observation_type,
            service_name,
            toStartOfHour(start_time),
            trace_id,
            id
        )
        """
    )
    try:
        yield table
    finally:
        ch_client.execute(f"DROP TABLE {table}")


@pytest.fixture()
def bounded_score_table(ch_client):
    table = f"_test_bounded_score_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            id UUID,
            trace_id Nullable(UUID),
            observation_span_id String,
            label_id UUID,
            annotator_id Nullable(UUID),
            value String,
            deleted Bool,
            created_at DateTime64(6, 'UTC'),
            _peerdb_version UInt64
        )
        ENGINE = ReplacingMergeTree(_peerdb_version)
        ORDER BY (label_id, id)
        """
    )
    try:
        yield table
    finally:
        ch_client.execute(f"DROP TABLE {table}")


@pytest.fixture()
def production_score_table(ch_client):
    """Create an ephemeral Score table with the deployed tracer-project column.

    ``CDC_MODEL_HUB_SCORE`` intentionally stays schema-change-free for this
    release. Production already receives ``tracer_project_id`` through the
    Score CDC schema, so this test-only table models that deployed shape without
    changing or exercising runtime DDL.
    """

    from tracer.services.clickhouse.schema import CDC_MODEL_HUB_SCORE

    table = f"_test_canonical_score_{uuid.uuid4().hex[:8]}"
    ddl = CDC_MODEL_HUB_SCORE.replace(
        "CREATE TABLE IF NOT EXISTS model_hub_score",
        f"CREATE TABLE {table}",
        1,
    )
    ddl = re.sub(
        r"ENGINE = ReplicatedReplacingMergeTree\([^)]*\)",
        "ENGINE = ReplacingMergeTree(_peerdb_version)",
        ddl,
        count=1,
    )
    ddl = ddl.replace(
        "    project_id Nullable(UUID),\n",
        "    project_id Nullable(UUID),\n    tracer_project_id UUID,\n",
        1,
    )
    ch_client.execute(ddl)
    try:
        yield table
    finally:
        ch_client.execute(f"DROP TABLE {table}")


@pytest.fixture(params=("legacy", "v2"))
def eval_latest_table(ch_client, request):
    suffix = "_v2" if request.param == "v2" else ""
    table = f"_test_eval_latest_{uuid.uuid4().hex[:8]}{suffix}"
    state_columns = (
        "is_deleted UInt8, _version UInt64"
        if request.param == "v2"
        else (
            "deleted Nullable(UInt8), _peerdb_is_deleted UInt8, _peerdb_version Int64"
        )
    )
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            id UUID,
            trace_id Nullable(UUID),
            observation_span_id Nullable(String),
            custom_eval_config_id UUID,
            output_bool Nullable(UInt8),
            output_float Nullable(Float64),
            output_str Nullable(String),
            output_str_list String,
            error UInt8,
            created_at DateTime64(6, 'UTC'),
            {state_columns}
        )
        ENGINE = MergeTree
        ORDER BY (custom_eval_config_id, created_at, id)
        """
    )
    try:
        yield table, request.param
    finally:
        ch_client.execute(f"DROP TABLE {table}")


def _patched_eval_config_resolution(config_id):
    values = mock.MagicMock()
    values.__iter__ = lambda self: iter([config_id])
    values.first.return_value = None
    fake_qs = mock.MagicMock()
    fake_qs.exists.return_value = True
    fake_qs.filter.return_value = fake_qs
    fake_qs.values_list.return_value = values
    objects = mock.MagicMock()
    objects.filter.return_value = fake_qs
    template_mgr = mock.MagicMock()
    template_mgr.filter.return_value.values.return_value.first.return_value = None
    return (
        mock.patch(
            "tracer.models.custom_eval_config.CustomEvalConfig.objects", objects
        ),
        mock.patch(
            "model_hub.models.evals_metric.EvalTemplate.no_workspace_objects",
            template_mgr,
        ),
    )


def test_eval_filters_resolve_latest_value_error_and_tombstone_on_ch25(
    ch_client, bounded_span_table, eval_latest_table
) -> None:
    """Execute legacy and v2 predicates against conflicting physical versions."""
    from django.test import override_settings

    eval_table, table_kind = eval_latest_table
    project_id = "00000000-0000-4000-8000-000000000031"
    config_id = "00000000-0000-4000-8000-000000000032"
    created_at = datetime(2025, 1, 8, 10, 0, tzinfo=UTC)
    entities = {
        "control": "00000000-0000-4000-8000-000000000041",
        "changed": "00000000-0000-4000-8000-000000000042",
        "errored": "00000000-0000-4000-8000-000000000043",
        "cdc-tombstone": "00000000-0000-4000-8000-000000000044",
        "app-tombstone": "00000000-0000-4000-8000-000000000045",
    }
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, start_time, created_at, is_deleted, _version)
        VALUES
        """,
        [
            (span_id, project_id, trace_id, created_at, created_at, 0, 1)
            for span_id, trace_id in entities.items()
        ],
    )

    logical_ids = {name: str(uuid.uuid4()) for name in entities}
    physical_rows = [
        ("control", 1, 0.8, 0, 0, 0),
        ("changed", 1, 0.8, 0, 0, 0),
        ("changed", 2, 0.2, 0, 0, 0),
        ("errored", 1, 0.8, 0, 0, 0),
        ("errored", 2, 0.8, 1, 0, 0),
        ("cdc-tombstone", 1, 0.8, 0, 0, 0),
        ("cdc-tombstone", 2, 0.8, 0, 1, 0),
        ("app-tombstone", 1, 0.8, 0, 0, 0),
        ("app-tombstone", 2, 0.8, 0, 0, 1),
    ]
    common_rows = [
        (
            logical_ids[name],
            entities[name],
            name,
            config_id,
            None,
            score,
            None,
            "[]",
            error,
            created_at,
        )
        for name, _version, score, error, _cdc_deleted, _app_deleted in physical_rows
    ]
    if table_kind == "v2":
        rows = [
            (*common, int(cdc_deleted or app_deleted), version)
            for common, (
                _name,
                version,
                _score,
                _error,
                cdc_deleted,
                app_deleted,
            ) in zip(common_rows, physical_rows, strict=True)
        ]
        state_columns = "is_deleted, _version"
    else:
        rows = [
            (*common, app_deleted, cdc_deleted, version)
            for common, (
                _name,
                version,
                _score,
                _error,
                cdc_deleted,
                app_deleted,
            ) in zip(common_rows, physical_rows, strict=True)
        ]
        state_columns = "deleted, _peerdb_is_deleted, _peerdb_version"
    ch_client.execute(
        f"""
        INSERT INTO {eval_table}
            (id, trace_id, observation_span_id, custom_eval_config_id,
             output_bool, output_float, output_str, output_str_list, error,
             created_at, {state_columns})
        VALUES
        """,
        rows,
    )

    candidate_entities = tuple(
        (trace_id, span_id) for span_id, trace_id in entities.items()
    )
    builder_kwargs = {
        "table": bounded_span_table,
        "project_id": project_id,
        "query_mode": ClickHouseFilterBuilder.QUERY_MODE_SPAN,
        "score_date_scope": False,
        "candidate_ids_param": "candidate_span_ids",
        "candidate_entities_param": "candidate_span_entities",
    }
    eval_filter = [
        {
            "column_id": config_id,
            "filter_config": {
                "col_type": "EVAL_METRIC",
                "filter_type": "number",
                "filter_op": "equals",
                "filter_value": 80,
            },
        }
    ]
    has_eval_filter = [
        {
            "column_id": "has_eval",
            "filter_config": {
                "filter_type": "boolean",
                "filter_op": "equals",
                "filter_value": True,
            },
        }
    ]
    p1, p2 = _patched_eval_config_resolution(config_id)
    filter_builder_cls = ClickHouseFilterBuilder
    if table_kind == "v2":
        from tracer.services.clickhouse.eval_logger_table import eval_logger_source

        def test_eval_source(alias="", include_cdc_tombstone_guard=False):
            return eval_logger_source(
                alias,
                include_cdc_tombstone_guard,
                table=eval_table,
            )

        class EphemeralEvalFilterBuilderV2(ClickHouseFilterBuilderV2):
            _eval_logger_source = staticmethod(test_eval_source)

        filter_builder_cls = EphemeralEvalFilterBuilderV2

    # Production config remains restricted to canonical identifiers. This
    # ephemeral local-CH table is admitted only inside this test context.
    with (
        override_settings(CH25_EVAL_LOGGER_TABLE=eval_table),
        mock.patch(
            "tracer.services.clickhouse.eval_logger_table.SUPPORTED_EVAL_LOGGER_TABLES",
            frozenset({eval_table}),
        ),
        p1,
        p2,
    ):
        value_where, value_params = filter_builder_cls(**builder_kwargs).translate(
            eval_filter
        )
        has_eval_where, has_eval_params = filter_builder_cls(
            **builder_kwargs
        ).translate(has_eval_filter)

    def execute(where, params):
        return [
            row[0]
            for row in ch_client.execute(
                f"""
                SELECT id FROM {bounded_span_table}
                WHERE project_id = toUUID(%(project_id)s)
                  AND is_deleted = 0
                  AND {where}
                ORDER BY id
                """,
                {
                    **params,
                    "project_id": project_id,
                    "candidate_span_ids": tuple(entities),
                    "candidate_span_entities": candidate_entities,
                },
            )
        ]

    # Only the newest live, successful, still-matching value survives.
    assert execute(value_where, value_params) == ["control"]
    # has_eval intentionally includes a latest errored/nonmatching eval, but
    # never resurrects either kind of latest tombstone.
    assert execute(has_eval_where, has_eval_params) == [
        "changed",
        "control",
        "errored",
    ]


def test_special_attribute_key_and_literal_value_execute_in_seed_and_latest_queries(
    ch_client, attribute_table
) -> None:
    key = "café final status '50%_\\path"
    needle = "Café%_\\literal"
    plan = compile_span_filter_plans(
        [
            {
                "column_id": key,
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": needle,
                },
            }
        ]
    )[0]
    seed_predicate = rewrite_v1_sql_to_v2(plan.seed_predicate)
    aggregates = tuple(rewrite_v1_sql_to_v2(item) for item in plan.aggregates)
    predicate = rewrite_v1_sql_to_v2(plan.predicate)

    emitted_sql = " ".join((seed_predicate, *aggregates, predicate))
    assert key not in emitted_sql
    assert "%(latest_filter_key_0)s" in emitted_sql

    ch_client.execute(
        f"INSERT INTO {attribute_table} (id, _version, attrs_string) VALUES",
        [
            ("match", 1, {key: "stale"}),
            ("match", 2, {key: f"prefix {needle} suffix"}),
            ("miss", 1, {key: "different"}),
        ],
    )

    seed_rows = ch_client.execute(
        f"SELECT id FROM {attribute_table} WHERE {seed_predicate} ORDER BY id",
        plan.params,
    )
    latest_rows = ch_client.execute(
        f"""
        SELECT id
        FROM (
            SELECT id, {", ".join(aggregates)}
            FROM {attribute_table}
            GROUP BY id
        )
        WHERE {predicate}
        ORDER BY id
        """,
        plan.params,
    )

    assert seed_rows == [("match",)]
    assert latest_rows == [("match",)]


def test_streaming_attribute_candidate_datetime_param_executes_on_ch25(
    ch_client, bounded_span_table
) -> None:
    """CH25 accepts integer-microsecond DateTime64 segment bounds."""

    query = _STRATIFIED_CANDIDATE_SQL.format(candidate_predicate="1 = 1").replace(
        "FROM spans", f"FROM {bounded_span_table}"
    )
    segment_start = datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
    segment_end = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    rows = ch_client.execute(
        query,
        {
            "project_ids": ("00000000-0000-4000-8000-000000000001",),
            "segment_start_us": _unix_microseconds(segment_start),
            "segment_end_us": _unix_microseconds(segment_end),
            "candidate_limit": 25,
        },
    )

    assert rows == []


def test_cardinality_sampler_executes_candidate_and_latest_replay_on_ch25(
    ch_client, bounded_span_table
) -> None:
    """The real CH25 parser and engine accept the cardinality replay SQL."""

    project_id = "00000000-0000-4000-8000-000000000004"
    session_id = "00000000-0000-4000-8000-000000000104"
    window_end = datetime(2025, 1, 8, 10, 0, tzinfo=UTC)
    rows = [
        ("span-1", "trace-1", window_end - timedelta(minutes=3), 1),
        ("span-2", "trace-1", window_end - timedelta(minutes=2), 2),
        ("span-3", "trace-2", window_end - timedelta(minutes=1), 3),
    ]
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, trace_session_id, start_time, created_at,
             is_deleted, _version)
        VALUES
        """,
        [
            (
                span_id,
                project_id,
                trace_id,
                session_id,
                started_at,
                started_at,
                0,
                version,
            )
            for span_id, trace_id, started_at, version in rows
        ],
    )

    class LocalExecutor:
        def execute(self, query, params, *, timeout_ms, settings):
            rewritten = query.replace("FROM spans", f"FROM {bounded_span_table}")
            raw_rows, columns = ch_client.execute(
                rewritten,
                params,
                with_column_types=True,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 0.001),
                },
            )
            names = [column[0] for column in columns]
            return AttributeQueryPage(
                data=[dict(zip(names, row, strict=False)) for row in raw_rows],
                query_time_ms=0,
            )

    read = AttributeReadSelector(
        LocalExecutor(), now=window_end, wall_timeout_ms=5_000
    ).sample_cardinality([project_id], horizon_days=7)

    assert read.max_spans_per_trace == 2
    assert read.max_traces_per_session == 2
    assert read.metadata.query_complete is True
    assert read.metadata.query_count == 2


def test_graph_trace_span_replay_isolates_reused_ids_and_tenants_on_ch25(
    ch_client, bounded_span_table
) -> None:
    """Composite replay keeps same-id spans even when another tenant is newer."""

    project_id = "00000000-0000-4000-8000-000000000016"
    other_project_id = "00000000-0000-4000-8000-000000000017"
    window_start = datetime(2025, 1, 8, 9, 0, tzinfo=UTC)
    window_end = datetime(2025, 1, 8, 10, 0, tzinfo=UTC)
    trace_a = "trace-a"
    trace_b = "trace-b"
    started_a = (window_end - timedelta(minutes=2)).replace(microsecond=123_456)
    started_b = (window_end - timedelta(minutes=1)).replace(microsecond=654_321)

    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, start_time, created_at, latency_ms,
             total_tokens, prompt_tokens, completion_tokens, status,
             is_deleted, _version)
        VALUES
        """,
        [
            (
                "shared-span-id",
                project_id,
                trace_a,
                started_a,
                started_a,
                10,
                11,
                5,
                6,
                "OK",
                0,
                1,
            ),
            (
                "shared-span-id",
                project_id,
                trace_b,
                started_b,
                started_b,
                20,
                22,
                10,
                12,
                "OK",
                0,
                2,
            ),
            # A higher version in another project must not win either local
            # identity's latest-state reduction.
            (
                "shared-span-id",
                other_project_id,
                trace_a,
                started_a,
                started_a,
                999,
                999,
                999,
                999,
                "ERROR",
                1,
                999,
            ),
        ],
    )

    sample = GraphCandidateSample(
        rows=(
            {"trace_id": trace_a, "root_span_id": "root-a", "start_time": started_a},
            {"trace_id": trace_b, "root_span_id": "root-b", "start_time": started_b},
        ),
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        window_start=window_start,
        window_end=window_end,
        elapsed_ms=0,
        query_count=0,
        rows_returned=0,
        result_payload_bytes=0,
        total_rows_lower_bound=2,
    )

    class LocalAnalytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            rewritten = query.replace("FROM spans", f"FROM {bounded_span_table}")
            rows, columns = ch_client.execute(
                rewritten,
                params,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 5.0),
                },
                with_column_types=True,
            )
            names = [name for name, _type in columns]
            mapped = [dict(zip(names, row, strict=True)) for row in rows]
            return QueryResult(mapped, len(mapped), "clickhouse", 0.0, names)

    identities, truncated, _, _ = _finite_trace_span_candidates(
        analytics=LocalAnalytics(),
        sample=sample,
        project_id=project_id,
        started=monotonic(),
        timeout_cap_ms=5_000,
    )
    assert set(identities) == {
        (trace_a, "shared-span-id", _unix_microseconds(started_a)),
        (trace_b, "shared-span-id", _unix_microseconds(started_b)),
    }
    assert truncated is False

    query, params = _trace_system_metric_query(
        sample=sample,
        span_identities=identities,
        interval="hour",
        project_id=project_id,
    )
    rows = (
        LocalAnalytics()
        .execute_ch_query(
            query,
            params,
            timeout_ms=5_000,
            settings={"max_threads": 1},
        )
        .data
    )

    assert rows[0]["traffic_count"] == 2
    assert rows[0]["total_tokens"] == 33


def test_annotation_graph_does_not_read_legacy_score_table_on_ch25(
    ch_client,
    production_score_table,
    monkeypatch,
) -> None:
    """Direct-write graph decoration must not query the legacy CDC score table."""

    project_id = "00000000-0000-4000-8000-000000000021"
    trace_id = "00000000-0000-4000-8000-000000000022"
    label_id = "00000000-0000-4000-8000-000000000023"
    organization_id = "00000000-0000-4000-8000-000000000024"
    span_id = "canonical-span"
    window_start = datetime(2025, 1, 8, 9, 0, tzinfo=UTC)
    created_at = window_start + timedelta(minutes=1)
    window_end = window_start + timedelta(hours=1)
    ch_client.execute(
        f"""
        INSERT INTO {production_score_table}
            (id, source_type, trace_id, observation_span_id,
             tracer_project_id, label_id, value, organization_id,
             created_at, updated_at, _peerdb_synced_at,
             _peerdb_is_deleted, _peerdb_version)
        VALUES
        """,
        [
            (
                "00000000-0000-4000-8000-000000000025",
                "OBSERVATION_SPAN",
                trace_id,
                span_id,
                project_id,
                label_id,
                '{"rating":4.5}',
                organization_id,
                created_at,
                created_at,
                created_at,
                0,
                1,
            )
        ],
    )
    sample = GraphCandidateSample(
        rows=({"trace_id": trace_id, "id": span_id, "start_time": created_at},),
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        window_start=window_start,
        window_end=window_end,
        elapsed_ms=0,
        query_count=0,
        rows_returned=0,
        result_payload_bytes=0,
        total_rows_lower_bound=1,
    )

    score_read = {}

    class ProjectScoreSource:
        def annotation_rows_for_candidates(self, **kwargs):
            score_read.update(kwargs)
            return [{"created_at": created_at, "value": {"rating": 4.5}}]

    monkeypatch.setattr(
        "tracer.services.clickhouse.graph_dispatch.AnnotationLabelScoresProjectPG",
        ProjectScoreSource,
    )

    class NoLegacyScoreAnalytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            raise AssertionError(f"unexpected ClickHouse score read: {query}")

    rows, truncated, query_count, raw_count = _finite_annotation_rows(
        analytics=NoLegacyScoreAnalytics(),
        sample=sample,
        project_id=project_id,
        observe_type="span",
        trace_span_identities=(),
        label_id=label_id,
        started=monotonic(),
    )

    assert rows == [
        {
            "created_at": created_at,
            "value": {"rating": 4.5},
        }
    ]
    assert truncated is False
    assert query_count == 1
    assert raw_count == 1
    assert score_read["project_id"] == project_id
    assert score_read["span_entities"] == ((trace_id, span_id),)


def test_attribute_detail_executes_latest_state_and_tombstones_on_ch25(
    ch_client, bounded_span_table
) -> None:
    """The detail selector counts only the active newest row per span id."""

    project_id = "00000000-0000-4000-8000-000000000014"
    window_end = datetime(2025, 1, 8, 10, 0, tzinfo=UTC)
    key = "final_status"

    def row(
        span_id: str,
        version: int,
        value: str,
        *,
        trace_id: str | None = None,
        started_at: datetime | None = None,
        deleted: int = 0,
    ) -> tuple:
        physical_start = started_at or window_end - timedelta(minutes=10)
        return (
            span_id,
            project_id,
            trace_id if trace_id is not None else f"trace-{span_id}",
            physical_start,
            physical_start,
            deleted,
            version,
            {key: value},
            {},
            {},
            "{}",
        )

    # Non-zero microseconds prove the replay predicate preserves DateTime64(6)
    # precision through clickhouse-driver tuple parameters.
    shared_start = window_end - timedelta(minutes=20) + timedelta(microseconds=123456)
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, start_time, created_at, is_deleted,
             _version, attrs_string, attrs_number, attrs_bool, attributes_extra)
        VALUES
        """,
        [
            row("live", 1, "Rejected"),
            row("updated", 1, "stale-value"),
            row("updated", 2, "Rejected"),
            row("deleted", 1, "must-not-resurrect"),
            row("deleted", 2, "must-not-resurrect", deleted=1),
            row(
                "shared",
                1,
                "Rejected",
                trace_id="trace-shared-a",
                started_at=shared_start,
            ),
            row(
                "shared",
                1,
                "must-not-resurrect",
                trace_id="trace-shared-b",
                started_at=shared_start,
            ),
            row(
                "shared",
                2,
                "must-not-resurrect",
                trace_id="trace-shared-b",
                started_at=shared_start,
                deleted=1,
            ),
            row(
                "shared",
                1,
                "Rejected",
                trace_id="trace-shared-a",
                started_at=shared_start + timedelta(minutes=1),
            ),
        ],
    )

    class LocalExecutor:
        def execute(self, query, params, *, timeout_ms, settings):
            rewritten = query.replace("FROM spans", f"FROM {bounded_span_table}")
            raw_rows, columns = ch_client.execute(
                rewritten,
                params,
                with_column_types=True,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 0.001),
                },
            )
            names = [column[0] for column in columns]
            return AttributeQueryPage(
                data=[dict(zip(names, value, strict=False)) for value in raw_rows],
                query_time_ms=0,
            )

    read = AttributeReadSelector(
        LocalExecutor(),
        now=window_end,
        wall_timeout_ms=5_000,
        typed_only=True,
    ).read_detail([project_id], key, horizon_days=7)

    assert read.attribute_type == "string"
    assert [(item.value, item.count) for item in read.rows] == [("Rejected", 4)]
    assert read.metadata.query_complete is True
    # Typed values use a light version certificate between candidate discovery
    # and heavy value hydration, so stale/tombstoned candidates cannot force
    # unnecessary Map reads.
    assert read.metadata.query_count == 3


def test_exact_attribute_discovery_exclusion_pages_past_stale_sample_on_ch25(
    ch_client, bounded_span_table
) -> None:
    """A live rare key survives 513 stale identities in the same dense band."""

    project_id = "00000000-0000-4000-8000-000000000024"
    window_end = datetime(2025, 1, 8, 10, 0, tzinfo=UTC)
    key = "final_status"
    physical_rows = []
    for index in range(ATTRIBUTE_READ_CANDIDATE_LIMIT + 1):
        span_id = f"stale-{index:04d}"
        trace_id = f"trace-stale-{index:04d}"
        started_at = window_end - timedelta(seconds=index + 1)
        physical_rows.extend(
            [
                (
                    span_id,
                    project_id,
                    trace_id,
                    started_at,
                    started_at,
                    0,
                    1,
                    {key: "historical"},
                ),
                # Latest state clears the key. These 513 recent identities
                # must not prevent a targeted lookup from continuing into the
                # adjacent older horizon band.
                (
                    span_id,
                    project_id,
                    trace_id,
                    started_at,
                    started_at,
                    0,
                    2,
                    {},
                ),
            ]
        )
    # Keep the live row in the same hour but lexically after the storage-order
    # sample. The ordered fallback then reaches it only after its first 512
    # newest physical identities, exercising a real keyset continuation.
    live_started_at = window_end - timedelta(minutes=30)
    physical_rows.append(
        (
            "zz-rare-live",
            project_id,
            "zz-trace-rare-live",
            live_started_at,
            live_started_at,
            0,
            1,
            {key: "Rejected"},
        )
    )
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, start_time, created_at, is_deleted,
             _version, attrs_string)
        VALUES
        """,
        physical_rows,
    )

    class LocalExecutor:
        def execute(self, query, params, *, timeout_ms, settings):
            rewritten = query.replace("FROM spans", f"FROM {bounded_span_table}")
            raw_rows, columns = ch_client.execute(
                rewritten,
                params,
                with_column_types=True,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 0.001),
                },
            )
            names = [column[0] for column in columns]
            return AttributeQueryPage(
                data=[dict(zip(names, row, strict=False)) for row in raw_rows],
                query_time_ms=0,
            )

    # Empty-search browse deliberately remains one sampled candidate page.
    browse = AttributeReadSelector(
        LocalExecutor(),
        now=window_end,
        wall_timeout_ms=10_000,
        typed_only=True,
    ).discover_keys([project_id], horizon_days=7)
    assert browse.metadata.query_complete is False
    assert browse.metadata.query_error_code == "sample_limit"
    assert browse.metadata.query_count == 2

    exact = AttributeReadSelector(
        LocalExecutor(),
        now=window_end,
        wall_timeout_ms=10_000,
        typed_only=True,
    ).discover_keys([project_id], exact_key=key, horizon_days=7)
    assert exact.rows == (AttributeKeyRow(key, "string", 1),)
    assert exact.metadata.query_complete is True
    assert exact.metadata.query_count == 6


def test_json_array_picker_replays_tombstones_project_versions_and_older_key_ch25(
    ch_client, bounded_span_table
) -> None:
    """JSON overflow discovery stays finite and accepts only actionable arrays."""

    project_id = "00000000-0000-4000-8000-000000000025"
    old_project_version = "00000000-0000-4000-8000-000000000026"
    new_project_version = "00000000-0000-4000-8000-000000000027"
    window_end = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
    key = "json_choices"
    recent = window_end - timedelta(days=1)
    older = window_end - timedelta(days=8)
    rows = [
        # A recent raw match is cleared at latest state and must not hide the
        # live key in the adjacent older band.
        (
            "cleared",
            project_id,
            "trace-cleared",
            None,
            recent,
            recent,
            0,
            1,
            {},
            f'{{"{key}":["stale"]}}',
        ),
        (
            "cleared",
            project_id,
            "trace-cleared",
            None,
            recent,
            recent,
            0,
            2,
            {},
            "{}",
        ),
        # Tombstones are replayed rather than trusted from the candidate row.
        (
            "deleted",
            project_id,
            "trace-deleted",
            None,
            recent - timedelta(minutes=1),
            recent - timedelta(minutes=1),
            0,
            1,
            {},
            f'{{"{key}":["deleted"]}}',
        ),
        (
            "deleted",
            project_id,
            "trace-deleted",
            None,
            recent - timedelta(minutes=1),
            recent - timedelta(minutes=1),
            1,
            2,
            {},
            f'{{"{key}":["deleted"]}}',
        ),
        # A project-version change is payload state, not a new physical span.
        # The latest value must win without fragmenting identity replay.
        (
            "live-older",
            project_id,
            "trace-live-older",
            old_project_version,
            older,
            older,
            0,
            1,
            {},
            f'{{"{key}":["old"]}}',
        ),
        (
            "live-older",
            project_id,
            "trace-live-older",
            new_project_version,
            older,
            older,
            0,
            2,
            {},
            f'{{"{key}":["Rejected",true,18446744073709551615]}}',
        ),
        # Typed Maps remain discoverable when JSON array mode is enabled.
        (
            "typed-map",
            project_id,
            "trace-typed-map",
            None,
            older - timedelta(minutes=1),
            older - timedelta(minutes=1),
            0,
            1,
            {"final_status": "Rejected"},
            "{}",
        ),
        # Eval mapping may expose this path; a filter picker must not claim an
        # object has supported array-membership semantics.
        (
            "json-object",
            project_id,
            "trace-json-object",
            None,
            older - timedelta(minutes=2),
            older - timedelta(minutes=2),
            0,
            1,
            {},
            '{"json_object":{"nested":true}}',
        ),
    ]
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, project_version_id, start_time,
             created_at, is_deleted, _version, attrs_string, attributes_extra)
        VALUES
        """,
        rows,
    )

    class LocalExecutor:
        def execute(self, query, params, *, timeout_ms, settings):
            rewritten = query.replace("FROM spans", f"FROM {bounded_span_table}")
            raw_rows, columns = ch_client.execute(
                rewritten,
                params,
                with_column_types=True,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 0.001),
                },
            )
            names = [column[0] for column in columns]
            return AttributeQueryPage(
                data=[dict(zip(names, row, strict=False)) for row in raw_rows],
                query_time_ms=0,
            )

    def selector(mode):
        return AttributeReadSelector(
            LocalExecutor(),
            now=window_end,
            wall_timeout_ms=10_000,
            typed_only=True,
            json_attribute_mode=mode,
        )

    array_key = selector("arrays").discover_keys(
        [project_id],
        exact_key=key,
        horizon_days=14,  # gitleaks:allow
    )
    assert array_key.rows == (AttributeKeyRow(key, "array", 1),)
    assert array_key.metadata.query_complete is True

    values = selector("arrays").read_values([project_id], key, horizon_days=14)
    assert {
        (type(row.value).__name__, row.value, row.count) for row in values.rows
    } == {
        ("str", "Rejected", 1),
        ("bool", True, 1),
        ("int", 18446744073709551615, 1),
    }
    assert values.metadata.query_complete is True

    typed_key = selector("arrays").discover_keys(
        [project_id], exact_key="final_status", horizon_days=14
    )
    assert typed_key.rows == (AttributeKeyRow("final_status", "string", 1),)

    filtered_object = selector("arrays").discover_keys(
        [project_id], exact_key="json_object", horizon_days=14
    )
    assert filtered_object.rows == ()
    assert filtered_object.metadata.query_complete is True

    eval_object = selector("all").discover_keys(
        [project_id], exact_key="json_object", horizon_days=14
    )
    assert eval_object.rows == (AttributeKeyRow("json_object", "json", 1),)
    assert eval_object.metadata.query_complete is True


def test_bounded_reader_handles_duplicates_tombstone_and_latest_updates(
    ch_client, bounded_span_table
) -> None:
    project_id = "00000000-0000-4000-8000-000000000001"
    window_end = datetime(2025, 1, 1, 10, 0)
    window_start = window_end - timedelta(minutes=15)
    key = "final status"

    def row(
        identity: str,
        version: int,
        start_time: datetime,
        value: str,
        *,
        deleted: int = 0,
    ) -> tuple:
        return (
            identity,
            project_id,
            f"trace-{identity}",
            None,
            f"trace-{identity}",
            None,
            identity,
            "span",
            "OK",
            start_time,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            start_time,
            deleted,
            version,
            {key: value},
            {},
            {},
            "{}",
        )

    rows = [
        row("duplicate", version, window_end - timedelta(minutes=1), "Rejected")
        for version in range(1, 151)
    ]
    rows.extend(
        [
            row("stable", 1, window_end - timedelta(minutes=2), "Rejected"),
            row("moved", 1, window_end - timedelta(minutes=3), "Rejected"),
            row("moved", 2, window_end - timedelta(minutes=3), "Rejected"),
            row("tombstoned", 1, window_end - timedelta(minutes=4), "Rejected"),
            row(
                "tombstoned",
                2,
                window_end - timedelta(minutes=4),
                "Rejected",
                deleted=1,
            ),
            row(
                "changed", 1, window_end - timedelta(minutes=4, seconds=30), "Rejected"
            ),
            row(
                "changed",
                2,
                window_end - timedelta(minutes=4, seconds=30),
                "Approved",
            ),
        ]
    )
    columns = [
        "id",
        "project_id",
        "trace_id",
        "parent_span_id",
        "trace_name",
        "trace_session_id",
        "name",
        "observation_type",
        "status",
        "start_time",
        "end_time",
        "latency_ms",
        "cost",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "model",
        "provider",
        "end_user_id",
        "created_at",
        "is_deleted",
        "_version",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "attributes_extra",
    ]
    ch_client.execute(
        f"INSERT INTO {bounded_span_table} ({', '.join(columns)}) VALUES", rows
    )

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = bounded_span_table

    class LocalAnalytics:
        def execute_ch_query(
            self, query, params, *, timeout_ms, settings
        ) -> QueryResult:
            query_settings = {
                **settings,
                # This is a semantic integration test. Give a cold local CH
                # process room to compile the first query; production budgets
                # are asserted by the unit-level executor tests.
                "max_execution_time": max(timeout_ms / 1000, 5.0),
            }
            data, schema = ch_client.execute(
                query,
                params,
                settings=query_settings,
                with_column_types=True,
            )
            names = [name for name, _type in schema]
            mapped = [dict(zip(names, values, strict=True)) for values in data]
            return QueryResult(
                data=mapped,
                row_count=len(mapped),
                backend_used="clickhouse",
                query_time_ms=0.0,
            )

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        },
        {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
    ]
    page = read_bounded_filter_page(
        builder=LocalSpanBuilder(project_id=project_id, filters=filters),
        analytics=LocalAnalytics(),
        filters=filters,
        key_field="id",
        page_number=0,
        page_size=10,
        deadline_ms=10_000,
    )

    assert page.complete is True
    assert [item["id"] for item in page.rows] == ["duplicate", "stable", "moved"]
    # A selective typed-Map equality now uses the bounded raw-witness anchor
    # before exact latest-state classification.
    assert page.attempts[0].kind == "anchor"
    assert page.attempts[0].rows_returned == 5


def test_bounded_span_reader_isolates_reused_ids_by_trace_on_ch25(
    ch_client, bounded_span_table
) -> None:
    """A tombstone in one trace cannot hide the same OTel span ID in another."""

    project_id = "00000000-0000-4000-8000-000000000015"
    window_end = datetime(2025, 1, 1, 10, 0)
    window_start = window_end - timedelta(minutes=15)
    key = "final_status"

    def row(
        trace_id: str,
        minute: int,
        version: int,
        *,
        deleted: int = 0,
    ) -> tuple:
        started = window_end - timedelta(minutes=minute)
        return (
            "shared-span-id",
            project_id,
            trace_id,
            None,
            trace_id,
            None,
            trace_id,
            "span",
            "OK",
            started,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            started,
            deleted,
            version,
            {key: "Rejected"},
            {},
            {},
            "{}",
        )

    columns = [
        "id",
        "project_id",
        "trace_id",
        "parent_span_id",
        "trace_name",
        "trace_session_id",
        "name",
        "observation_type",
        "status",
        "start_time",
        "end_time",
        "latency_ms",
        "cost",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "model",
        "provider",
        "end_user_id",
        "created_at",
        "is_deleted",
        "_version",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "attributes_extra",
    ]
    ch_client.execute(
        f"INSERT INTO {bounded_span_table} ({', '.join(columns)}) VALUES",
        [
            row("trace-a", 1, 1),
            row("trace-b", 2, 1),
            row("trace-b", 2, 2, deleted=1),
            row("trace-c", 3, 1),
            row("trace-d", 4, 1),
        ],
    )

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = bounded_span_table

    class LocalAnalytics:
        def execute_ch_query(
            self, query, params, *, timeout_ms, settings
        ) -> QueryResult:
            data, schema = ch_client.execute(
                query,
                params,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 5.0),
                },
                with_column_types=True,
            )
            names = [name for name, _type in schema]
            mapped = [dict(zip(names, values, strict=True)) for values in data]
            return QueryResult(
                data=mapped,
                row_count=len(mapped),
                backend_used="clickhouse",
                query_time_ms=0.0,
            )

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        },
        {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
    ]
    page = read_bounded_filter_page(
        builder=LocalSpanBuilder(project_id=project_id, filters=filters),
        analytics=LocalAnalytics(),
        filters=filters,
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=10_000,
    )

    assert page.complete is True
    assert [(item["trace_id"], item["id"]) for item in page.rows] == [
        ("trace-a", "shared-span-id"),
        ("trace-c", "shared-span-id"),
    ]
    assert page.has_more is True


def test_eval_identity_only_span_and_trace_classifiers_execute_on_ch25(
    ch_client, bounded_span_table
) -> None:
    project_id = "00000000-0000-4000-8000-000000000003"
    start = datetime(2025, 1, 1, 9, 0)
    end = datetime(2025, 1, 1, 10, 0)
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, start_time, created_at,
             is_deleted, _version, attrs_string)
        VALUES
        """,
        [
            (
                "span-eval",
                project_id,
                "trace-eval",
                None,
                end - timedelta(minutes=1),
                end - timedelta(minutes=1),
                0,
                1,
                {"final_status": "Rejected"},
            )
        ],
    )
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
            },
        },
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
    ]

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = bounded_span_table

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = bounded_span_table

    common = {
        "project_id": project_id,
        "filters": filters,
        "bounded_internal_scan": True,
        "bounded_identity_only": True,
        "bounded_sampling_salt": "task-salt",
        "bounded_sampling_rate": 100.0,
    }
    span_sql, span_params = LocalSpanBuilder(**common).build_filter_match_query(
        ["span-eval"]
    )
    trace_sql, trace_params = LocalTraceBuilder(**common).build_filter_match_query(
        ["trace-eval"]
    )

    span_rows = ch_client.execute(
        span_sql,
        span_params,
        settings={"max_execution_time": 10, "max_threads": 1},
    )
    trace_rows = ch_client.execute(
        trace_sql,
        trace_params,
        settings={"max_execution_time": 10, "max_threads": 1},
    )

    # Span classifiers now preserve project_id before the public span id.
    assert str(span_rows[0][0]) == project_id
    assert span_rows[0][1] == "span-eval"
    assert trace_rows[0][0] == "trace-eval"


@pytest.mark.parametrize("row_type", ["spans", "traces"])
@pytest.mark.parametrize("limit", [5_100, 10_000])
def test_eval_candidate_reader_proves_large_prefix_on_ch25(
    ch_client,
    bounded_span_table,
    row_type: str,
    limit: int,
) -> None:
    """The real CH25 parser/engine proves both sides of the old 5.1k cliff."""

    project_id = "00000000-0000-4000-8000-000000000034"
    window_end = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    window_start = window_end - timedelta(hours=1)
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, start_time, created_at,
             is_deleted, _version, attrs_string)
        VALUES
        """,
        [
            (
                f"span-scale-{index:05d}",
                project_id,
                f"trace-scale-{index:05d}",
                None,
                window_end - timedelta(minutes=1, microseconds=index),
                window_end - timedelta(minutes=1, microseconds=index),
                0,
                1,
                {"final_status": "Rejected"},
            )
            for index in range(limit + 1)
        ],
    )

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        },
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
    ]

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = bounded_span_table

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = bounded_span_table

    class LocalAnalytics:
        def execute_ch_query(
            self, query, params, *, timeout_ms, settings
        ) -> QueryResult:
            data, schema = ch_client.execute(
                query,
                params,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 5.0),
                },
                with_column_types=True,
            )
            names = [name for name, _type in schema]
            mapped = [dict(zip(names, values, strict=True)) for values in data]
            return QueryResult(mapped, len(mapped), "clickhouse", 0.0)

    common = {
        "project_id": project_id,
        "filters": filters,
        "bounded_internal_scan": True,
        "bounded_identity_only": True,
        "bounded_sampling_salt": "task-salt",
        "bounded_sampling_rate": 100.0,
    }
    if row_type == "traces":
        builder = LocalTraceBuilder(**common, bounded_bulk_scan=True)
        key_field = "trace_id"
    else:
        builder = LocalSpanBuilder(**common)
        key_field = "id"

    page = read_bounded_filter_page(
        builder=builder,
        analytics=LocalAnalytics(),
        filters=filters,
        key_field=key_field,
        page_number=0,
        page_size=limit,
        deadline_ms=10_000,
        max_seed_attempts=128,
        max_candidates=512,
        max_query_count=128,
        classify_batch_size=200,
    )

    assert page.complete is True
    assert len(page.rows) == limit
    assert page.has_more is True
    # Candidate reads plus a few empty adjacent slices must remain below the
    # shared hard ceiling; the pre-fix 50-ID trace classifier required at
    # least 151 reads for 10k before it could issue its first query.
    assert page.query_count <= 128


def test_json_array_integer_membership_preserves_precision_on_ch25(
    ch_client, bounded_span_table
) -> None:
    project_id = "00000000-0000-4000-8000-000000000025"
    started_at = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    exact_integer = 9_007_199_254_740_993
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, start_time, created_at,
             is_deleted, _version, attributes_extra)
        VALUES
        """,
        [
            (
                "json-exact",
                project_id,
                "trace-json-exact",
                None,
                started_at,
                started_at,
                0,
                1,
                '{"ids":[9007199254740993,18446744073709551615,-9007199254740993]}',
            ),
            (
                "json-adjacent",
                project_id,
                "trace-json-adjacent",
                None,
                started_at,
                started_at,
                0,
                1,
                '{"ids":[9007199254740992]}',
            ),
            (
                "json-safe-int",
                project_id,
                "trace-json-safe-int",
                None,
                started_at,
                started_at,
                0,
                1,
                '{"ids":[1]}',
            ),
            (
                "json-safe-double",
                project_id,
                "trace-json-safe-double",
                None,
                started_at,
                started_at,
                0,
                1,
                '{"ids":[1.0]}',
            ),
        ],
    )

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    (started_at - timedelta(minutes=1)).isoformat(),
                    (started_at + timedelta(minutes=1)).isoformat(),
                ],
            },
        },
        {
            "column_id": "ids",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "array",
                "filter_op": "contains",
                "filter_value": [exact_integer],
            },
        },
    ]

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = bounded_span_table

    sql, params = LocalSpanBuilder(
        project_id=project_id, filters=filters
    ).build_filter_match_query(["json-exact", "json-adjacent"])
    rows = ch_client.execute(sql, params)

    assert [row[1] for row in rows] == ["json-exact"]

    safe_filters = [
        filters[0],
        {
            **filters[1],
            "filter_config": {
                **filters[1]["filter_config"],
                "filter_value": [1],
            },
        },
    ]
    safe_sql, safe_params = LocalSpanBuilder(
        project_id=project_id,
        filters=safe_filters,
    ).build_filter_match_query(
        ["json-safe-int", "json-safe-double", "json-exact", "json-adjacent"]
    )
    safe_rows = ch_client.execute(safe_sql, safe_params)

    assert {row[1] for row in safe_rows} == {"json-safe-int", "json-safe-double"}


def _json_map_filter(
    operation: str,
    value: dict[str, object] | None = None,
) -> dict:
    config: dict[str, object] = {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "map",
        "filter_op": operation,
    }
    if value is not None:
        config["filter_value"] = value
    return {"column_id": "context", "filter_config": config}


def test_json_map_semantics_execute_against_latest_state_on_ch25(
    ch_client, bounded_span_table
) -> None:
    project_id = "00000000-0000-4000-8000-000000000071"
    started_at = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    rows = [
        (
            "map-exact",
            project_id,
            "trace-exact",
            started_at,
            started_at,
            0,
            1,
            '{"context":{"tier":"vip","attempt":2,"accepted":true}}',
        ),
        (
            "map-superset",
            project_id,
            "trace-superset",
            started_at,
            started_at,
            0,
            1,
            '{"context":{"tier":"vip","attempt":2.0,"accepted":true,"region":"us"}}',
        ),
        (
            "map-mismatch",
            project_id,
            "trace-mismatch",
            started_at,
            started_at,
            0,
            1,
            '{"context":{"tier":"standard","attempt":2,"accepted":true}}',
        ),
        (
            "map-wrong-type",
            project_id,
            "trace-wrong-type",
            started_at,
            started_at,
            0,
            1,
            '{"context":["vip"]}',
        ),
        (
            "map-missing",
            project_id,
            "trace-missing",
            started_at,
            started_at,
            0,
            1,
            "{}",
        ),
        # Latest payload wins over the older matching object.
        (
            "map-updated",
            project_id,
            "trace-updated",
            started_at,
            started_at,
            0,
            1,
            '{"context":{"tier":"vip","attempt":2}}',
        ),
        (
            "map-updated",
            project_id,
            "trace-updated",
            started_at,
            started_at,
            0,
            2,
            '{"context":{"tier":"standard","attempt":2}}',
        ),
        # A latest tombstone must never satisfy any map operation.
        (
            "map-deleted",
            project_id,
            "trace-deleted",
            started_at,
            started_at,
            0,
            1,
            '{"context":{"tier":"vip","attempt":2}}',
        ),
        (
            "map-deleted",
            project_id,
            "trace-deleted",
            started_at,
            started_at,
            1,
            2,
            '{"context":{"tier":"vip","attempt":2}}',
        ),
    ]
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, start_time, created_at,
             is_deleted, _version, attributes_extra)
        VALUES
        """,
        rows,
    )
    time_filter = {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [
                (started_at - timedelta(minutes=1)).isoformat(),
                (started_at + timedelta(minutes=1)).isoformat(),
            ],
        },
    }
    candidates = [
        "map-exact",
        "map-superset",
        "map-mismatch",
        "map-wrong-type",
        "map-missing",
        "map-updated",
        "map-deleted",
    ]

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = bounded_span_table

    cases = [
        (
            "contains",
            {"tier": "vip", "attempt": 2},
            {"map-exact", "map-superset"},
        ),
        (
            "not_contains",
            {"tier": "vip", "attempt": 2},
            {"map-mismatch", "map-updated"},
        ),
        (
            "equals",
            {"tier": "vip", "attempt": 2, "accepted": True},
            {"map-exact"},
        ),
        (
            "not_equals",
            {"tier": "vip", "attempt": 2, "accepted": True},
            {"map-superset", "map-mismatch", "map-updated"},
        ),
        ("is_null", None, {"map-wrong-type", "map-missing"}),
        (
            "is_not_null",
            None,
            {"map-exact", "map-superset", "map-mismatch", "map-updated"},
        ),
    ]
    for operation, value, expected in cases:
        sql, params = LocalSpanBuilder(
            project_id=project_id,
            filters=[time_filter, _json_map_filter(operation, value)],
        ).build_filter_match_query(candidates)
        rows = ch_client.execute(sql, params)
        assert {row[1] for row in rows} == expected, operation


def test_json_map_composes_with_array_typed_maps_and_trace_any_span_on_ch25(
    ch_client, bounded_span_table
) -> None:
    project_id = "00000000-0000-4000-8000-000000000072"
    started_at = datetime(2025, 1, 1, 11, 0, tzinfo=UTC)
    root_id = "map-root"
    child_id = "map-child"
    trace_id = "trace-map-composed"
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, start_time, created_at,
             is_deleted, _version, attrs_string, attrs_number, attrs_bool,
             attributes_extra)
        VALUES
        """,
        [
            (
                root_id,
                project_id,
                trace_id,
                None,
                started_at,
                started_at,
                0,
                1,
                {},
                {},
                {},
                "{}",
            ),
            (
                child_id,
                project_id,
                trace_id,
                root_id,
                started_at + timedelta(microseconds=1),
                started_at + timedelta(microseconds=1),
                0,
                1,
                {"final_status": "Rejected"},
                {"score": 0.9},
                {"reviewed": 1},
                '{"context":{"tier":"vip","attempt":2},"tags":["priority","customer"]}',
            ),
        ],
    )
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    (started_at - timedelta(minutes=1)).isoformat(),
                    (started_at + timedelta(minutes=1)).isoformat(),
                ],
            },
        },
        _json_map_filter("contains", {"tier": "vip", "attempt": 2}),
        {
            "column_id": "tags",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "array",
                "filter_op": "contains",
                "filter_value": ["priority"],
            },
        },
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
        {
            "column_id": "score",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 0.8,
            },
        },
        {
            "column_id": "reviewed",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "boolean",
                "filter_op": "equals",
                "filter_value": True,
            },
        },
    ]

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = bounded_span_table

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = bounded_span_table

    span_sql, span_params = LocalSpanBuilder(
        project_id=project_id, filters=filters
    ).build_filter_match_query([root_id, child_id])
    span_rows = ch_client.execute(span_sql, span_params)
    assert {row[1] for row in span_rows} == {child_id}

    trace_sql, trace_params = LocalTraceBuilder(
        project_id=project_id, filters=filters
    ).build_filter_match_query([trace_id])
    trace_rows = ch_client.execute(trace_sql, trace_params)
    assert {row[1] for row in trace_rows} == {trace_id}


def test_trace_classifier_resolves_latest_update_and_tombstone_in_trace(
    ch_client, bounded_span_table
) -> None:
    """Mutable payload/deletion state is latest; span-to-trace identity is fixed."""

    project_id = "00000000-0000-4000-8000-000000000002"
    window_end = datetime(2025, 1, 1, 10, 0)
    window_start = window_end - timedelta(minutes=15)
    key = "final_status"

    def row(
        identity: str,
        version: int,
        trace_id: str,
        parent_span_id: str | None,
        value: str,
        *,
        minute: int,
        is_deleted: int = 0,
    ) -> tuple:
        start_time = window_end - timedelta(minutes=minute)
        return (
            identity,
            project_id,
            trace_id,
            parent_span_id,
            trace_id,
            None,
            identity,
            "span",
            "OK",
            start_time,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            start_time,
            is_deleted,
            version,
            {key: value},
            {},
            {},
            "{}",
        )

    rows = [
        row("root-a", 1, "trace-a", None, "Approved", minute=1),
        row("child-updated", 1, "trace-a", "root-a", "Rejected", minute=2),
        row(
            "child-updated",
            2,
            "trace-a",
            "root-a",
            "Approved",
            minute=2,
        ),
        row("child-deleted", 1, "trace-a", "root-a", "Rejected", minute=3),
        row(
            "child-deleted",
            2,
            "trace-a",
            "root-a",
            "Rejected",
            minute=3,
            is_deleted=1,
        ),
    ]
    columns = [
        "id",
        "project_id",
        "trace_id",
        "parent_span_id",
        "trace_name",
        "trace_session_id",
        "name",
        "observation_type",
        "status",
        "start_time",
        "end_time",
        "latency_ms",
        "cost",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "model",
        "provider",
        "end_user_id",
        "created_at",
        "is_deleted",
        "_version",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "attributes_extra",
    ]
    ch_client.execute(
        f"INSERT INTO {bounded_span_table} ({', '.join(columns)}) VALUES", rows
    )

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = bounded_span_table

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        },
        {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
    ]
    query, params = LocalTraceBuilder(
        project_id=project_id,
        filters=filters,
    ).build_filter_match_query(["trace-a"])
    result = ch_client.execute(query, params)

    assert result == []


def test_trace_classifier_keeps_reused_span_ids_isolated_by_trace_identity(
    ch_client, bounded_span_table
) -> None:
    """Span IDs are reduced within their deployed trace-scoped identity."""

    project_id = "00000000-0000-4000-8000-000000000009"
    window_end = datetime(2025, 1, 1, 10, 0)
    window_start = window_end - timedelta(minutes=15)

    def row(
        identity: str,
        trace_id: str,
        parent_span_id: str | None,
        minute: int,
        attrs: dict[str, str],
        *,
        version: int,
    ) -> tuple:
        started = window_end - timedelta(minutes=minute)
        return (
            identity,
            project_id,
            trace_id,
            parent_span_id,
            trace_id,
            identity,
            "span",
            "OK",
            started,
            started,
            0,
            version,
            attrs,
        )

    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, trace_name, name,
             observation_type, status, start_time, created_at, is_deleted,
             _version, attrs_string)
        VALUES
        """,
        [
            row("root-a", "trace-a", None, 1, {}, version=1),
            row("root-b", "trace-b", None, 2, {}, version=1),
            row(
                "shared-child-id",
                "trace-a",
                "root-a",
                3,
                {"final_status": "Rejected"},
                version=1,
            ),
            row(
                "shared-child-id",
                "trace-b",
                "root-b",
                4,
                {"final_status": "Rejected"},
                version=2,
            ),
        ],
    )

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        },
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
    ]

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = bounded_span_table

    query, params = LocalTraceBuilder(
        project_id=project_id,
        filters=filters,
    ).build_filter_match_query(["trace-a", "trace-b"])
    result = ch_client.execute(query, params)

    assert {row[1] for row in result} == {"trace-a", "trace-b"}


def test_trace_pages_keep_older_live_root_when_newer_raw_root_is_tombstoned(
    ch_client, bounded_span_table
) -> None:
    """The raw seed ID is only an order bound; latest state chooses a live root."""

    project_id = "00000000-0000-4000-8000-000000000008"
    window_end = datetime(2025, 1, 1, 10, 0)
    window_start = window_end - timedelta(minutes=15)
    key = "final_status"

    def root(
        identity: str,
        trace_id: str,
        minute: int,
        *,
        version: int = 1,
        is_deleted: int = 0,
    ) -> tuple:
        started = window_end - timedelta(minutes=minute)
        return (
            identity,
            project_id,
            trace_id,
            None,
            trace_id,
            identity,
            "span",
            "OK",
            started,
            started,
            is_deleted,
            version,
            {key: "Rejected"},
        )

    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, trace_name, name,
             observation_type, status, start_time, created_at, is_deleted,
             _version, attrs_string)
        VALUES
        """,
        [
            # trace-multi's newest raw root is later tombstoned.  Root B remains
            # live and is the canonical row for the trace.
            root("root-b-live", "trace-multi", 4),
            root("root-a-tombstoned", "trace-multi", 1),
            root(
                "root-a-tombstoned",
                "trace-multi",
                1,
                version=2,
                is_deleted=1,
            ),
            # Its canonical key is newer than root B, despite appearing after
            # trace-multi's false-positive raw seed key.
            root("root-c-live", "trace-control", 2),
        ],
    )

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        },
        {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
        {
            "column_id": "trace_name",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "contains",
                "filter_value": "trace",
            },
        },
    ]

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = bounded_span_table

    class LocalAnalytics:
        def execute_ch_query(
            self, query, params, *, timeout_ms, settings
        ) -> QueryResult:
            data, schema = ch_client.execute(
                query,
                params,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 5.0),
                },
                with_column_types=True,
            )
            names = [name for name, _type in schema]
            mapped = [dict(zip(names, values, strict=True)) for values in data]
            return QueryResult(mapped, len(mapped), "clickhouse", 0.0)

    builder = LocalTraceBuilder(project_id=project_id, filters=filters)
    seed_sql, seed_params = builder.build_filter_ordered_seed_page(
        slice_start=window_end - timedelta(minutes=5),
        slice_end=window_end,
        limit=50,
    )
    seed_data, seed_schema = ch_client.execute(
        seed_sql, seed_params, with_column_types=True
    )
    seed_names = [name for name, _type in seed_schema]
    seed_rows = [dict(zip(seed_names, row, strict=True)) for row in seed_data]
    multi_seed = next(row for row in seed_rows if row["trace_id"] == "trace-multi")
    assert multi_seed["root_span_id"] == "root-a-tombstoned"

    first = read_bounded_filter_page(
        builder=builder,
        analytics=LocalAnalytics(),
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=1,
        deadline_ms=10_000,
    )
    second = read_bounded_filter_page(
        builder=builder,
        analytics=LocalAnalytics(),
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=1,
        deadline_ms=10_000,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=first.rows[-1]["trace_id"],
    )

    assert first.complete is True and second.complete is True
    assert [(row["trace_id"], row["root_span_id"]) for row in first.rows] == [
        ("trace-control", "root-c-live")
    ]
    assert [(row["trace_id"], row["root_span_id"]) for row in second.rows] == [
        ("trace-multi", "root-b-live")
    ]
    assert first.has_more is True
    assert second.has_more is False


def test_trace_root_seed_and_any_span_match_execute_on_different_spans(
    ch_client, bounded_span_table
) -> None:
    project_id = "00000000-0000-4000-8000-000000000006"
    other_project_id = "00000000-0000-4000-8000-000000000007"
    window_end = datetime(2025, 1, 1, 10, 0)
    window_start = window_end - timedelta(minutes=15)

    def row(
        identity: str,
        trace_id: str,
        parent_span_id: str | None,
        minute: int,
        attrs: dict[str, str],
        *,
        tenant: str = project_id,
    ) -> tuple:
        started = window_end - timedelta(minutes=minute)
        return (
            identity,
            tenant,
            trace_id,
            parent_span_id,
            started,
            started,
            0,
            1,
            attrs,
        )

    rows = [
        row("root-both", "trace-both", None, 10, {}),
        row(
            "child-status",
            "trace-both",
            "root-both",
            9,
            {"customer.final_status": "Rejected"},
        ),
        row(
            "child-country",
            "trace-both",
            "root-both",
            8,
            {"customer.country": "ES"},
        ),
        row("root-partial", "trace-partial", None, 7, {}),
        row(
            "child-partial",
            "trace-partial",
            "root-partial",
            6,
            {"customer.final_status": "Rejected"},
        ),
        row("root-other", "trace-other", None, 5, {}, tenant=other_project_id),
        row(
            "child-other-status",
            "trace-other",
            "root-other",
            4,
            {"customer.final_status": "Rejected"},
            tenant=other_project_id,
        ),
        row(
            "child-other-country",
            "trace-other",
            "root-other",
            3,
            {"customer.country": "ES"},
            tenant=other_project_id,
        ),
    ]
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, start_time, created_at,
             is_deleted, _version, attrs_string)
        VALUES
        """,
        rows,
    )

    def attribute_filter(key: str, value: str) -> dict:
        return {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": value,
            },
        }

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        },
        attribute_filter("customer.final_status", "Rejected"),
        attribute_filter("customer.country", "ES"),
    ]

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = bounded_span_table

    class LocalAnalytics:
        def execute_ch_query(
            self, query, params, *, timeout_ms, settings
        ) -> QueryResult:
            data, schema = ch_client.execute(
                query,
                params,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 5.0),
                },
                with_column_types=True,
            )
            names = [name for name, _type in schema]
            mapped = [dict(zip(names, values, strict=True)) for values in data]
            return QueryResult(mapped, len(mapped), "clickhouse", 0.0)

    builder = LocalTraceBuilder(project_id=project_id, filters=filters)
    page = read_bounded_filter_page(
        builder=builder,
        analytics=LocalAnalytics(),
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=10,
        deadline_ms=10_000,
    )

    assert page.complete is True
    assert [item["trace_id"] for item in page.rows] == ["trace-both"]
    assert all(str(item["project_id"]) == project_id for item in page.rows)
    assert any(attempt.kind == "anchor" for attempt in page.attempts)
    assert any(attempt.kind == "classify" for attempt in page.attempts)


def test_trace_any_span_classifier_matches_children_outside_root_window(
    ch_client, bounded_span_table
) -> None:
    """The date binds the root while any current child can satisfy attributes."""

    project_id = "00000000-0000-4000-8000-000000000008"
    window_start = datetime(2025, 1, 1, 9, 45)
    window_end = datetime(2025, 1, 1, 10, 0)

    def span(
        identity: str,
        trace_id: str,
        parent_span_id: str | None,
        started: datetime,
        attrs: dict[str, str],
        *,
        version: int = 1,
    ) -> tuple:
        return (
            identity,
            project_id,
            trace_id,
            parent_span_id,
            started,
            started,
            0,
            version,
            attrs,
        )

    rows = [
        # The raw anchor sees this old in-window Rejected version, but latest
        # state for the same immutable identity no longer matches.
        span(
            "root-stale",
            "trace-stale",
            None,
            window_end - timedelta(minutes=10),
            {},
        ),
        span(
            "child-stale",
            "trace-stale",
            "root-stale",
            window_end - timedelta(minutes=9),
            {"customer.final_status": "Rejected"},
        ),
        span(
            "child-stale",
            "trace-stale",
            "root-stale",
            window_end - timedelta(minutes=9),
            {"customer.final_status": "Accepted"},
            version=2,
        ),
        span(
            "child-country-stale",
            "trace-stale",
            "root-stale",
            window_end - timedelta(minutes=8),
            {"customer.country": "ES"},
        ),
        # This trace has no matching child inside the request window.  The
        # temporal anchor therefore returns no candidate for it; only ordered
        # canonical-root acquisition plus global child replay can retain it.
        span(
            "root-outside-only",
            "trace-outside-only",
            None,
            window_end - timedelta(minutes=7),
            {},
        ),
        span(
            "child-outside-only",
            "trace-outside-only",
            "root-outside-only",
            window_end + timedelta(days=3),
            {
                "customer.final_status": "Rejected",
                "customer.country": "ES",
            },
        ),
        # A child outside the root window remains part of this trace. It must
        # satisfy any-span membership even when written more than one day later.
        span(
            "child-two-days-late",
            "trace-stale",
            "root-stale",
            window_end + timedelta(days=2),
            {"customer.final_status": "Rejected"},
        ),
        # A current child before the root window is also a valid witness.
        span(
            "child-before-start",
            "trace-stale",
            "root-stale",
            window_start - timedelta(microseconds=1),
            {"customer.final_status": "Rejected"},
        ),
        # A normal in-window trace can satisfy independent leaves on different
        # children.
        span(
            "root-live",
            "trace-live",
            None,
            window_end - timedelta(minutes=6),
            {},
        ),
        span(
            "child-live-status",
            "trace-live",
            "root-live",
            window_end - timedelta(minutes=5),
            {"customer.final_status": "Rejected"},
        ),
        span(
            "child-live-country",
            "trace-live",
            "root-live",
            window_end - timedelta(minutes=4),
            {"customer.country": "ES"},
        ),
        # The lower bound is inclusive for any-span membership.
        span(
            "root-boundary",
            "trace-boundary",
            None,
            window_start,
            {},
        ),
        span(
            "child-boundary-status",
            "trace-boundary",
            "root-boundary",
            window_start,
            {"customer.final_status": "Rejected"},
        ),
        span(
            "child-boundary-country",
            "trace-boundary",
            "root-boundary",
            window_start + timedelta(microseconds=1),
            {"customer.country": "ES"},
        ),
    ]
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, start_time, created_at,
             is_deleted, _version, attrs_string)
        VALUES
        """,
        rows,
    )

    def attribute_filter(key: str, value: str) -> dict:
        return {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": value,
            },
        }

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        },
        attribute_filter("customer.final_status", "Rejected"),
        attribute_filter("customer.country", "ES"),
    ]

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = bounded_span_table

    class LocalAnalytics:
        def execute_ch_query(
            self, query, params, *, timeout_ms, settings
        ) -> QueryResult:
            data, schema = ch_client.execute(
                query,
                params,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 5.0),
                },
                with_column_types=True,
            )
            names = [name for name, _type in schema]
            mapped = [dict(zip(names, values, strict=True)) for values in data]
            return QueryResult(mapped, len(mapped), "clickhouse", 0.0)

    builder = LocalTraceBuilder(project_id=project_id, filters=filters)
    page = read_bounded_filter_page(
        builder=builder,
        analytics=LocalAnalytics(),
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=10,
        deadline_ms=10_000,
    )

    assert page.complete is True
    assert [item["trace_id"] for item in page.rows] == [
        "trace-live",
        "trace-outside-only",
        "trace-stale",
        "trace-boundary",
    ]
    assert any(attempt.kind == "anchor" for attempt in page.attempts)
    assert any(attempt.kind == "classify" for attempt in page.attempts)


def test_direct_write_map_types_execute_and_overflow_json_degrades_explicitly(
    ch_client, bounded_span_table
) -> None:
    project_id = "00000000-0000-4000-8000-000000000003"
    window_end = datetime(2025, 1, 1, 10, 0)
    window_start = window_end - timedelta(minutes=15)
    start_time = window_end - timedelta(minutes=1)
    columns = [
        "id",
        "project_id",
        "trace_id",
        "parent_span_id",
        "trace_name",
        "trace_session_id",
        "name",
        "observation_type",
        "status",
        "start_time",
        "end_time",
        "latency_ms",
        "cost",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "model",
        "provider",
        "end_user_id",
        "created_at",
        "is_deleted",
        "_version",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "attributes_extra",
    ]
    ch_client.execute(
        f"INSERT INTO {bounded_span_table} ({', '.join(columns)}) VALUES",
        [
            (
                "typed",
                project_id,
                "trace-typed",
                None,
                "trace-typed",
                None,
                "typed",
                "span",
                "OK",
                start_time,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                start_time,
                0,
                1,
                {"string_key": "Alpha"},
                {"number_key": 2.5},
                {"bool_key": 1},
                '{"overflow_payload":{"nested":[1,2]}}',
            )
        ],
    )

    def time_filter() -> dict:
        return {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        }

    def attribute_filter(key: str, filter_type: str, value: object) -> dict:
        return {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": filter_type,
                "filter_op": "equals",
                "filter_value": value,
            },
        }

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = bounded_span_table

    typed_filters = [
        attribute_filter("string_key", "text", "Alpha"),
        attribute_filter("number_key", "number", 2.5),
        attribute_filter("bool_key", "boolean", True),
    ]
    for item in typed_filters:
        builder = LocalSpanBuilder(project_id=project_id, filters=[time_filter(), item])
        query, params = builder.build_filter_match_query(["typed"])
        row = ch_client.execute(query, params)[0]
        assert str(row[0]) == project_id
        assert row[1] == "typed"

    combined_builder = LocalSpanBuilder(
        project_id=project_id,
        filters=[time_filter(), *typed_filters],
    )
    combined_query, combined_params = combined_builder.build_filter_match_query(
        ["typed"]
    )
    combined_row = ch_client.execute(combined_query, combined_params)[0]
    assert str(combined_row[0]) == project_id
    assert combined_row[1] == "typed"

    json_filter = attribute_filter("overflow_payload", "json", {"nested": [1, 2]})
    for filters in (
        [time_filter(), json_filter],
        [time_filter(), typed_filters[0], json_filter],
    ):
        builder = LocalSpanBuilder(project_id=project_id, filters=filters)
        assert builder.supports_bounded_filter_scan() is False
        assert (
            builder.bounded_filter_degraded_error_code() == "unsupported_filter_shape"
        )


def test_candidate_scoped_annotation_residual_executes_on_ch25(
    ch_client, bounded_span_table, production_score_table
) -> None:
    project_id = "00000000-0000-4000-8000-000000000005"
    label_id = "00000000-0000-4000-8000-000000000105"
    trace_id = "00000000-0000-4000-8000-000000000205"
    started_at = datetime(2025, 1, 1, 10, 0)
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, start_time, created_at,
             is_deleted, _version)
        VALUES
        """,
        [
            (
                "annotated-span",
                project_id,
                trace_id,
                None,
                started_at,
                started_at,
                0,
                1,
            )
        ],
    )
    ch_client.execute(
        f"""
        INSERT INTO {production_score_table}
            (id, source_type, trace_id, observation_span_id,
             tracer_project_id, label_id, annotator_id, value,
             organization_id, deleted, created_at, updated_at,
             _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
        VALUES
        """,
        [
            (
                "00000000-0000-4000-8000-000000000305",
                "OBSERVATION_SPAN",
                trace_id,
                "annotated-span",
                project_id,
                label_id,
                None,
                '{"text":"approved"}',
                "00000000-0000-4000-8000-000000000405",
                False,
                started_at,
                started_at,
                started_at,
                0,
                1,
            )
        ],
    )

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    (started_at - timedelta(minutes=1)).isoformat(),
                    (started_at + timedelta(minutes=1)).isoformat(),
                ],
            },
        },
        {
            "column_id": label_id,
            "filter_config": {
                "col_type": "ANNOTATION",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "approved",
            },
        },
    ]

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = bounded_span_table

    query, params = LocalSpanBuilder(
        project_id=project_id,
        filters=filters,
    ).build_filter_match_query_from_seed_rows(
        [
            {
                "project_id": project_id,
                "trace_id": trace_id,
                "id": "annotated-span",
                "start_time": started_at,
            }
        ]
    )
    query = query.replace("model_hub_score", production_score_table).replace(
        "FROM spans ", f"FROM {bounded_span_table} "
    )

    rows = ch_client.execute(query, params)

    assert str(rows[0][0]) == project_id
    assert rows[0][1] == "annotated-span"
    assert params["candidate_span_ids"] == ("annotated-span",)
    assert params["candidate_span_entities"] == ((trace_id, "annotated-span"),)


def test_span_annotation_filter_rejects_same_id_score_from_other_project_ch25(
    ch_client, bounded_span_table, production_score_table
) -> None:
    """A foreign tenant's Score cannot match a project-local span identity."""

    project_a = "00000000-0000-4000-8000-000000000515"
    project_b = "00000000-0000-4000-8000-000000000516"
    trace_id = "00000000-0000-4000-8000-000000000517"
    label_id = "00000000-0000-4000-8000-000000000518"
    organization_id = "00000000-0000-4000-8000-000000000519"
    shared_span_id = "tenant-local-span"
    started_at = datetime(2025, 1, 1, 10, 30, tzinfo=UTC)
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, start_time, created_at,
             is_deleted, _version)
        VALUES
        """,
        [
            (
                shared_span_id,
                project_id,
                trace_id,
                None,
                started_at,
                started_at,
                0,
                1,
            )
            for project_id in (project_a, project_b)
        ],
    )
    ch_client.execute(
        f"""
        INSERT INTO {production_score_table}
            (id, source_type, trace_id, observation_span_id,
             tracer_project_id, label_id, value, organization_id,
             created_at, updated_at, _peerdb_synced_at,
             _peerdb_is_deleted, _peerdb_version)
        VALUES
        """,
        [
            (
                "00000000-0000-4000-8000-000000000520",
                "OBSERVATION_SPAN",
                trace_id,
                shared_span_id,
                project_b,
                label_id,
                '{"text":"approved"}',
                organization_id,
                started_at,
                started_at,
                started_at,
                0,
                1,
            )
        ],
    )
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    (started_at - timedelta(minutes=1)).isoformat(),
                    (started_at + timedelta(minutes=1)).isoformat(),
                ],
            },
        },
        {
            "column_id": label_id,
            "filter_config": {
                "col_type": "ANNOTATION",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "approved",
            },
        },
    ]

    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = bounded_span_table

    query, params = LocalSpanBuilder(
        project_id=project_a,
        filters=filters,
    ).build_filter_match_query_from_seed_rows(
        [
            {
                "project_id": project_a,
                "trace_id": trace_id,
                "id": shared_span_id,
                "start_time": started_at,
            }
        ]
    )
    query = query.replace("model_hub_score", production_score_table).replace(
        "FROM spans ", f"FROM {bounded_span_table} "
    )

    assert ch_client.execute(query, params) == []
    assert params["project_id"] == project_a
    assert "s.tracer_project_id = toUUID(%(project_id)s)" in query


def test_org_trace_candidate_identity_tuple_executes_on_ch25(
    ch_client, bounded_span_table
) -> None:
    """String-bound candidate tuples must compare safely with UUID projects."""

    project_a = "00000000-0000-4000-8000-000000000405"
    project_b = "00000000-0000-4000-8000-000000000406"
    trace_id = "shared-trace"
    started_at = datetime(2025, 1, 1, 11, 0, tzinfo=UTC)
    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, start_time, created_at,
             is_deleted, _version, attrs_string)
        VALUES
        """,
        [
            (
                root_id,
                project_id,
                trace_id,
                None,
                started_at,
                started_at,
                0,
                1,
                {"final_status": "Rejected"},
            )
            for project_id, root_id in (
                (project_a, "org-root-a"),
                (project_b, "org-root-b"),
            )
        ],
    )
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    (started_at - timedelta(minutes=1)).isoformat(),
                    (started_at + timedelta(minutes=1)).isoformat(),
                ],
            },
        },
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
    ]

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = bounded_span_table

    query, params = LocalTraceBuilder(
        project_ids=[project_a, project_b],
        filters=filters,
        bounded_identity_only=True,
    ).build_filter_match_query_from_seed_rows(
        [
            {
                "project_id": project_id,
                "trace_id": trace_id,
                "root_span_id": root_id,
                "start_time": started_at,
            }
            for project_id, root_id in (
                (project_a, "org-root-a"),
                (project_b, "org-root-b"),
            )
        ]
    )

    rows = ch_client.execute(query, params)

    assert {(str(row[0]), row[1]) for row in rows} == {
        (project_a, trace_id),
        (project_b, trace_id),
    }


def test_trace_candidate_typed_map_prefilter_executes_exact_latest_state_on_ch25(
    ch_client, bounded_span_table
) -> None:
    """The finite witness prefilter is tenant scoped and latest-state exact."""

    project_id = "00000000-0000-4000-8000-000000000601"
    other_project_id = "00000000-0000-4000-8000-000000000602"
    window_start = datetime(2025, 1, 2, 10, 0, tzinfo=UTC)
    window_end = window_start + timedelta(hours=1)

    def span_version(
        trace_id: str,
        span_id: str,
        start_time: datetime,
        version: int,
        *,
        value: str | None,
        deleted: int = 0,
        tenant: str = project_id,
    ) -> tuple[object, ...]:
        return (
            span_id,
            tenant,
            trace_id,
            None,
            start_time,
            start_time,
            deleted,
            version,
            {"final_status": value} if value is not None else {},
        )

    ch_client.execute(
        f"""
        INSERT INTO {bounded_span_table}
            (id, project_id, trace_id, parent_span_id, start_time, created_at,
             is_deleted, _version, attrs_string)
        VALUES
        """,
        [
            # A historical matching value is not sufficient once the latest
            # version of the same physical span clears the key.
            span_version(
                "trace-cleared",
                "span-cleared",
                window_start + timedelta(minutes=10),
                1,
                value="Rejected",
            ),
            span_version(
                "trace-cleared",
                "span-cleared",
                window_start + timedelta(minutes=10),
                2,
                value=None,
            ),
            # A latest tombstone removes an otherwise matching span.
            span_version(
                "trace-tombstoned",
                "span-tombstoned",
                window_start + timedelta(minutes=20),
                1,
                value="Rejected",
            ),
            span_version(
                "trace-tombstoned",
                "span-tombstoned",
                window_start + timedelta(minutes=20),
                2,
                value="Rejected",
                deleted=1,
            ),
            # A corrected latest value is included.
            span_version(
                "trace-live",
                "span-live",
                window_start + timedelta(minutes=30),
                1,
                value="Pending",
            ),
            span_version(
                "trace-live",
                "span-live",
                window_start + timedelta(minutes=30),
                2,
                value="Rejected",
            ),
            # The lower bound is inclusive.
            span_version(
                "trace-at-start",
                "span-at-start",
                window_start,
                1,
                value="Rejected",
            ),
            # Candidate witnesses are intentionally global: the authoritative
            # classifier, not this necessary-leaf superset, enforces the root
            # request window.
            span_version(
                "trace-at-end",
                "span-at-end",
                window_end,
                1,
                value="Rejected",
            ),
            # A matching row under another tenant cannot leak through even
            # when its trace id was supplied as a candidate.
            span_version(
                "trace-foreign",
                "span-foreign",
                window_start + timedelta(minutes=40),
                1,
                value="Rejected",
                tenant=other_project_id,
            ),
        ],
    )

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        },
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
    ]

    class LocalTraceBuilder(TraceListQueryBuilderV2):
        TABLE = bounded_span_table

    candidate_rows = [
        {"trace_id": trace_id}
        for trace_id in (
            "trace-cleared",
            "trace-tombstoned",
            "trace-live",
            "trace-at-start",
            "trace-at-end",
            "trace-foreign",
        )
    ]
    query, params = LocalTraceBuilder(
        project_id=project_id,
        filters=filters,
    ).build_filter_candidate_witness_probe(candidate_rows)

    # These are v2-only names. Their presence proves the inherited v1 builder
    # crossed the V2RewriteMixin boundary before the statement was executed.
    assert "attrs_string" in query
    assert "_version" in query
    assert "span_attr_str" not in query
    assert "_peerdb_version" not in query

    rows = ch_client.execute(query, params)

    assert {row[0] for row in rows} == {
        "trace-live",
        "trace-at-start",
        "trace-at-end",
    }
