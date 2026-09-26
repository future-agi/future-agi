"""Exact attribute-detail latest-state and result-shape regressions."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel

from tracer.models.project import Project
from tracer.serializers.dashboard import DashboardFilterValuesQuerySerializer
from tracer.serializers.span_attributes import SpanAttributeDetailResponseSerializer
from tracer.services.clickhouse.attribute_reads import AttributeQueryPage
from tracer.services.clickhouse.exact_attribute_detail import (
    EXACT_ATTRIBUTE_DETAIL_SQL,
    read_exact_attribute_detail,
)

PROJECT_ID = "c4de3065-12b5-488c-a814-aa1c8e3f856f"
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


class _Executor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, query, params, **kwargs):
        self.calls.append((query, params, kwargs))
        return AttributeQueryPage(list(self.rows), 1.0)


@pytest.fixture()
def isolated_exact_attribute_ch25():
    """A uniquely named, local-only CH25 database for real SQL execution.

    The test must never point at a shared or production host because it creates
    and drops its own ``spans`` table.  Keeping that table inside a disposable
    database also lets the production statement execute unchanged rather than
    relying on a textual table-name rewrite.
    """

    host = os.environ.get("CH25_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.skip("exact attribute SQL integration proof is local-only")

    try:
        from clickhouse_driver import Client

        admin = Client(
            host=host,
            port=int(os.environ.get("CH25_NATIVE_PORT", "9000")),
            user=os.environ.get("CH25_USER", "default"),
            password=os.environ.get("CH25_PASSWORD", ""),
            database="default",
            connect_timeout=2,
            send_receive_timeout=30,
        )
        admin.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse is unavailable: {exc!r}")

    database = f"_test_exact_attribute_detail_{uuid4().hex}"
    try:
        admin.execute(f"CREATE DATABASE {database}")
        client = Client(
            host=host,
            port=int(os.environ.get("CH25_NATIVE_PORT", "9000")),
            user=os.environ.get("CH25_USER", "default"),
            password=os.environ.get("CH25_PASSWORD", ""),
            database=database,
            connect_timeout=2,
            send_receive_timeout=30,
        )
        client.execute(
            """
            CREATE TABLE spans
            (
                project_id UUID,
                trace_id String,
                id String,
                start_time DateTime64(6, 'UTC'),
                attrs_string Map(String, String),
                attrs_number Map(String, Float64),
                attrs_bool Map(String, Bool),
                attributes_extra String,
                is_deleted UInt8,
                _version UInt64
            )
            ENGINE = MergeTree
            ORDER BY (project_id, trace_id, id, start_time, _version)
            """
        )
        yield client
    finally:
        # ``database`` is an unguessable, test-owned identifier; no shared
        # table or repository schema is ever touched by this cleanup.
        admin.execute(f"DROP DATABASE IF EXISTS {database}")


class _CH25ExactAttributeExecutor:
    """Adapt a real native client to the narrow attribute executor protocol."""

    def __init__(self, client):
        self.client = client
        self.queries = []

    def execute(self, query, params, **kwargs):
        self.queries.append(query)
        rows, columns = self.client.execute(
            query,
            params,
            with_column_types=True,
            settings=kwargs.get("settings"),
        )
        names = [name for name, _column_type in columns]
        return AttributeQueryPage(
            [dict(zip(names, row, strict=True)) for row in rows],
            0.0,
        )


def test_exact_sql_applies_mutable_predicates_only_after_latest_state_replay():
    candidate_source, latest_source = EXACT_ATTRIBUTE_DETAIL_SQL.split(
        "latest_spans AS", 1
    )
    latest_source, exploded_values = latest_source.split("exploded_values AS", 1)

    # Key predicates may prune the immutable identity seed.  They must not
    # filter the versions replayed by argMax, otherwise a later removal or
    # tombstone could resurrect an older value.
    assert "mapContains(candidate_source.attrs_string" in candidate_source
    assert "JSONHas(candidate_source.attributes_extra" in candidate_source
    assert "INNER JOIN candidate_identities AS candidate" in latest_source
    assert "argMax(" in latest_source
    assert "GROUP BY" in latest_source
    assert "is_deleted = 0" not in latest_source
    assert "attribute_source.attrs_string[%(attribute_key)s]" in latest_source
    assert "attribute_source.attrs_number[%(attribute_key)s]" in latest_source
    assert "attribute_source.attrs_bool[%(attribute_key)s]" in latest_source
    assert "tupleElement(latest_state, 1) = 0" in exploded_values
    assert "ARRAY JOIN arrayFilter(" in exploded_values
    assert "'__span__'" in exploded_values
    assert "sumIf(value_count, attribute_type = '__span__') OVER ()" in exploded_values
    assert "UNION ALL" not in exploded_values
    # ClickHouse expands CTEs at each consumer.  Keeping both expensive stages
    # single-consumer prevents one latest-state replay per typed storage family.
    assert EXACT_ATTRIBUTE_DETAIL_SQL.count("FROM latest_spans") == 1
    assert EXACT_ATTRIBUTE_DETAIL_SQL.count("FROM exploded_values") == 1
    assert EXACT_ATTRIBUTE_DETAIL_SQL.count("FROM grouped_values") == 1
    assert EXACT_ATTRIBUTE_DETAIL_SQL.index(
        "argMax("
    ) < EXACT_ATTRIBUTE_DETAIL_SQL.index("tupleElement(latest_state, 1) = 0")


@pytest.mark.integration
def test_exact_attribute_detail_executes_distinct_latest_state_contract_on_ch25(
    isolated_exact_attribute_ch25,
):
    """Execute the production statement against real CH25 latest-state data."""

    client = isolated_exact_attribute_ch25
    started_at = NOW - timedelta(hours=1)
    key = "mixed.storage"

    def row(
        trace_id,
        span_id,
        *,
        version,
        strings=None,
        numbers=None,
        booleans=None,
        is_deleted=0,
    ):
        return (
            PROJECT_ID,
            trace_id,
            span_id,
            started_at,
            strings or {},
            numbers or {},
            booleans or {},
            "{}",
            is_deleted,
            version,
        )

    client.execute(
        "INSERT INTO spans VALUES",
        [
            # One physical live span carries the same key in two typed maps.
            # It remains one overall span while both type summaries retain it.
            row(
                "trace-dual",
                "span-dual",
                version=1,
                strings={key: "dual"},
                numbers={key: 1.0},
            ),
            row(
                "trace-string",
                "span-string",
                version=1,
                strings={key: "string-only"},
            ),
            # A newer tombstone must remove the older key-bearing version.
            row(
                "trace-deleted",
                "span-deleted",
                version=1,
                strings={key: "tombstoned"},
            ),
            row(
                "trace-deleted",
                "span-deleted",
                version=2,
                is_deleted=1,
            ),
            # A newer live correction without the key must likewise remove the
            # older value from the exact current-state distribution.
            row(
                "trace-removed",
                "span-removed",
                version=1,
                numbers={key: 99.0},
            ),
            row("trace-removed", "span-removed", version=2),
        ],
    )

    executor = _CH25ExactAttributeExecutor(client)
    payload = read_exact_attribute_detail(
        project_id=PROJECT_ID,
        attribute_key=key,
        executor=executor,
        window_end=NOW,
    )

    assert executor.queries == [EXACT_ATTRIBUTE_DETAIL_SQL]
    assert payload["count"] == 2
    assert payload["types"] == [
        {"type": "string", "count": 2, "unique_values": 2},
        {"type": "number", "count": 1, "unique_values": 1},
    ]
    assert {
        (item["type"], item["value"], item["count"], item["percentage"])
        for item in payload["top_values"]
    } == {
        ("string", "dual", 1, 50.0),
        ("string", "string-only", 1, 50.0),
        ("number", 1, 1, 50.0),
    }
    assert all(
        item["value"] not in {"tombstoned", 99} for item in payload["top_values"]
    )


def test_exact_detail_parses_full_distribution_and_weighted_numeric_stats():
    executor = _Executor(
        [
            {
                "attribute_type": "number",
                "value_json": "10.0",
                "value_count": 3,
                "type_count": 5,
                "unique_values": 2,
                "numeric_min": 10.0,
                "numeric_max": 20.0,
                "numeric_avg": 14.0,
                "numeric_p50": 10.0,
                "numeric_p95": 20.0,
                "span_count": 5,
            },
            {
                "attribute_type": "number",
                "value_json": "20.0",
                "value_count": 2,
                "type_count": 5,
                "unique_values": 2,
                "numeric_min": 10.0,
                "numeric_max": 20.0,
                "numeric_avg": 14.0,
                "numeric_p50": 10.0,
                "numeric_p95": 20.0,
                "span_count": 5,
            },
        ]
    )

    payload = read_exact_attribute_detail(
        project_id=PROJECT_ID,
        attribute_key="latency.score",
        executor=executor,
        window_end=NOW,
    )

    assert payload["query_complete"] is True
    assert payload["query_status"] == "complete"
    assert payload["query_sampled"] is False
    assert payload["type"] == "number"
    assert payload["count"] == 5
    assert payload["unique_values"] == 2
    assert payload["top_values"] == [
        {"value": 10.0, "type": "number", "count": 3, "percentage": 60.0},
        {"value": 20.0, "type": "number", "count": 2, "percentage": 40.0},
    ]
    assert payload["types"] == [{"type": "number", "count": 5, "unique_values": 2}]
    assert payload["stats"] == {
        "min": 10.0,
        "max": 20.0,
        "avg": 14.0,
        "p50": 10.0,
        "p95": 20.0,
    }
    query, params, kwargs = executor.calls[0]
    assert query == EXACT_ATTRIBUTE_DETAIL_SQL
    assert params["attribute_key"] == "latency.score"
    assert "max_rows_to_read" not in kwargs["settings"]
    assert kwargs["settings"]["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
    assert kwargs["settings"]["max_memory_usage"] == 36 * 1024 * 1024 * 1024
    assert kwargs["timeout_ms"] == 30_000


def test_exact_detail_empty_result_is_complete_and_not_sampled():
    payload = read_exact_attribute_detail(
        project_id=PROJECT_ID,
        attribute_key="removed.by.latest.version",
        executor=_Executor([]),
        window_end=NOW,
    )

    assert payload["type"] is None
    assert payload["count"] == 0
    assert payload["unique_values"] == 0
    assert payload["top_values"] == []
    assert payload["query_complete"] is True
    assert payload["query_status"] == "complete"
    assert payload["query_sampled"] is False


def test_exact_detail_dominant_type_is_deterministic():
    shared = {
        "value_count": 2,
        "type_count": 2,
        "unique_values": 1,
        "numeric_min": None,
        "numeric_max": None,
        "numeric_avg": None,
        "numeric_p50": None,
        "numeric_p95": None,
        "span_count": 2,
    }
    payload = read_exact_attribute_detail(
        project_id=PROJECT_ID,
        attribute_key="mixed",
        executor=_Executor(
            [
                {**shared, "attribute_type": "boolean", "value_json": "true"},
                {**shared, "attribute_type": "string", "value_json": '"true"'},
            ]
        ),
        window_end=NOW,
    )

    assert payload["type"] == "string"
    assert payload["count"] == 2
    assert payload["unique_values"] == 2
    assert payload["types"] == [
        {"type": "string", "count": 2, "unique_values": 1},
        {"type": "boolean", "count": 2, "unique_values": 1},
    ]
    assert payload["top_values"][0]["value"] == "true"
    assert {row["type"] for row in payload["top_values"]} == {
        "string",
        "boolean",
    }


def test_exact_detail_total_and_percentages_use_distinct_physical_spans():
    """A dual-written live span is one span even when two type rows exist."""

    common = {
        "type_count": 2,
        "unique_values": 1,
        "numeric_min": None,
        "numeric_max": None,
        "numeric_avg": None,
        "numeric_p50": None,
        "numeric_p95": None,
        # Three distinct live spans exist overall. One appears in both typed
        # representations, so the per-type counts sum to four.
        "span_count": 3,
    }
    payload = read_exact_attribute_detail(
        project_id=PROJECT_ID,
        attribute_key="mixed.storage",
        executor=_Executor(
            [
                {
                    **common,
                    "attribute_type": "string",
                    "value_json": '"1"',
                    "value_count": 2,
                },
                {
                    **common,
                    "attribute_type": "number",
                    "value_json": "1.0",
                    "value_count": 2,
                },
            ]
        ),
        window_end=NOW,
    )

    assert payload["count"] == 3
    assert [summary["count"] for summary in payload["types"]] == [2, 2]
    assert all(
        row["percentage"] == pytest.approx(200 / 3) for row in payload["top_values"]
    )


def test_attribute_detail_exact_worker_namespace_dispatches(monkeypatch):
    from tracer.tasks import exact_aggregation

    identity = {
        "workspace_id": "workspace-a",
        "project_id": PROJECT_ID,
        "attribute_key": "final_status",
        "horizon_days": 365,
    }
    expected = {
        "key": "final_status",
        "type": "string",
        "count": 0,
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }
    captured = []

    def load(received_identity):
        captured.append(received_identity)
        return expected

    monkeypatch.setattr(exact_aggregation, "_attribute_detail_payload", load)

    assert (
        exact_aggregation._load_exact_payload("attribute-detail", identity) == expected
    )
    assert captured == [identity]


@pytest.mark.django_db
def test_attribute_detail_worker_uses_canonical_default_workspace_scope(
    monkeypatch, organization, workspace, user
):
    from tracer.tasks import exact_aggregation

    legacy_project = Project.no_workspace_objects.create(
        name="Legacy null attribute project",
        organization=organization,
        workspace=None,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )
    expected = {"query_complete": True, "query_status": "complete"}

    def read(**_kwargs):
        return expected

    monkeypatch.setattr(
        "tracer.services.clickhouse.exact_attribute_detail.read_exact_attribute_detail",
        read,
    )

    payload = exact_aggregation._attribute_detail_payload(
        {
            "organization_id": str(organization.id),
            "workspace_id": str(workspace.id),
            "project_id": str(legacy_project.id),
            "attribute_key": "final_status",
            "horizon_days": 365,
        }
    )

    assert payload is expected


@pytest.mark.django_db
def test_attribute_detail_worker_rejects_non_default_workspace_project(
    monkeypatch, organization, workspace, user
):
    from tracer.tasks import exact_aggregation

    other_workspace = Workspace.objects.create(
        name="Other attribute workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    other_project = Project.no_workspace_objects.create(
        name="Other workspace attribute project",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.exact_attribute_detail.read_exact_attribute_detail",
        lambda **_kwargs: pytest.fail("unauthorized project reached ClickHouse"),
    )

    with pytest.raises(ValueError, match="project scope is unavailable"):
        exact_aggregation._attribute_detail_payload(
            {
                "organization_id": str(organization.id),
                "workspace_id": str(workspace.id),
                "project_id": str(other_project.id),
                "attribute_key": "final_status",
                "horizon_days": 365,
            }
        )


def test_attribute_contracts_accept_all_types_and_pending_exact_response():
    for attribute_type in ("string", "number", "boolean", "array", "map", "json"):
        query = DashboardFilterValuesQuerySerializer(
            data={
                "metric_name": "metadata",
                "metric_type": "custom_attribute",
                "source": "traces",
                "page_size": 10,
                "attribute_type": attribute_type,
            }
        )
        assert query.is_valid(), query.errors
        assert query.validated_data["attribute_type"] == attribute_type

    pending = SpanAttributeDetailResponseSerializer(
        data={
            "key": "metadata",
            "type": None,
            "count": 0,
            "unique_values": 0,
            "top_values": [],
            "query_complete": False,
            "query_status": "pending",
            "query_sampled": False,
            "query_refreshing": True,
        }
    )
    assert pending.is_valid(), pending.errors
