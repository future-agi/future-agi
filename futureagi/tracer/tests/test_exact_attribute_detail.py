"""Exact attribute-detail latest-state and result-shape regressions."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel

from tracer.models.project import Project
from tracer.serializers.dashboard import DashboardFilterValuesQuerySerializer
from tracer.serializers.span_attributes import SpanAttributeDetailResponseSerializer
from tracer.services.clickhouse import exact_attribute_detail as detail_module
from tracer.services.clickhouse.attribute_reads import (
    AttributeQueryPage,
    V2AttributeQueryExecutor,
)
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.exact_attribute_detail import (
    EXACT_ATTRIBUTE_DETAIL_READ_SETTINGS,
    EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_READ_SETTINGS,
    EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL,
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


DEV_SQL_SHA256 = "e1f450175717df31002cb4bce78751013adab5cb1128c008e44fe1879580f88c"
SEMI_JOIN_HEAD_SHA256 = (
    "b354681626059931cd61fa5b6f3681f9e2a27f0ce35ef8fe05712ed702ea5634"
)
_MIB = 1024 * 1024
_SETTINGS_LANE = SimpleNamespace(
    server_enforced_readonly=False,
    allow_query_settings_with_server_readonly=False,
)


class _FlaggedExecutor(_Executor):
    """A double whose client identifies the settings lane, so level 1 runs."""

    def __init__(self, rows, client=_SETTINGS_LANE):
        super().__init__(rows)
        self.client = client


def _read(executor, key="latency.score"):
    return read_exact_attribute_detail(
        project_id=PROJECT_ID, attribute_key=key, executor=executor, window_end=NOW
    )


@pytest.fixture()
def isolated_exact_attribute_ch25():
    """A uniquely named, local-only CH25 database for real SQL execution.

    The test must never point at a shared or production host because it creates
    and drops its own ``spans`` table.  Keeping that table inside a disposable
    database also lets the production statement execute unchanged rather than
    relying on a textual table-name rewrite.
    """

    from conftest import _open_ch_test_native_client

    if os.environ.get("CH25_HOST", "127.0.0.1") not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        pytest.fail("exact attribute SQL integration requires isolated loopback CH25")
    if os.environ.get("CI") and not os.environ.get("FI_CH_TEST_SIDECAR_NATIVE_PORT"):
        pytest.fail("backend CI must name its verified test sidecar")

    def open_required(**kwargs):
        try:
            return _open_ch_test_native_client(send_receive_timeout=30, **kwargs)
        except pytest.skip.Exception:
            pytest.fail(
                "required isolated CH25 prerequisite unavailable", pytrace=False
            )

    database = f"_test_exact_attribute_detail_{uuid4().hex}"
    admin = open_required(owned_database=database)
    client = None
    try:
        admin.execute(f"CREATE DATABASE {database}")
        client = open_required(database=database)
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
        try:
            if client is not None:
                client.disconnect()
            admin.execute(f"DROP DATABASE IF EXISTS {database} SYNC")
        finally:
            admin.disconnect()


class _CH25ExactAttributeExecutor:
    """Adapt a real native client to the narrow attribute executor protocol.

    ``client`` carries only the lane flags, so the reader sees the settings
    lane and runs the configured level; the raw driver is ``driver``.
    """

    def __init__(self, driver):
        self.driver = driver
        self.client = SimpleNamespace(
            server_enforced_readonly=False,
            allow_query_settings_with_server_readonly=False,
        )
        self.queries = []

    def execute(self, query, params, **kwargs):
        self.queries.append(query)
        rows, columns = self.driver.execute(
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
@pytest.mark.parametrize("level", [0, 1])
def test_exact_attribute_detail_executes_distinct_latest_state_contract_on_ch25(
    isolated_exact_attribute_ch25, settings, level
):
    """Execute the production statement against real CH25 latest-state data."""

    settings.EXACT_ATTRIBUTE_DETAIL_SQL_LEVEL = level
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

    expected_sql = {
        0: EXACT_ATTRIBUTE_DETAIL_SQL,
        1: EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL,
    }
    assert executor.queries == [expected_sql[level]]
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


_EDGE_KEY = "edge.key"
_BUILT_LEVELS = (0, 1)
# Payload fields that describe the request rather than the data.
_REQUEST_FIELDS = frozenset(
    {
        "key",
        "query_complete",
        "query_status",
        "query_sampled",
        "query_window_start",
        "query_window_end",
        "query_count",
        "query_elapsed_ms",
    }
)


def _edge_row(trace_id, span_id, started_at, version, *, strings=None, numbers=None):
    return (
        PROJECT_ID,
        trace_id,
        span_id,
        started_at,
        strings or {},
        numbers or {},
        {},
        "{}",
        0,
        version,
    )


def _one_string(value, count=1, total=1):
    return {
        "type": "string",
        "types": [{"type": "string", "count": total, "unique_values": 1}],
        "count": total,
        "unique_values": 1,
        "top_values": [
            {
                "value": value,
                "type": "string",
                "count": count,
                "percentage": count * 100.0 / total,
            }
        ],
    }


def _data_fields(payload):
    return {k: v for k, v in payload.items() if k not in _REQUEST_FIELDS}


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["tie", "moved", "corrected", "absent"])
def test_levels_agree_on_latest_state_edge_cases_on_ch25(
    isolated_exact_attribute_ch25, settings, scenario
):
    """Every built level returns the hand-computed latest state on real CH25.

    Each scenario has its own oracle; agreement between levels alone would
    not catch two levels returning the same wrong answer.
    """

    client = isolated_exact_attribute_ch25
    started_at = NOW - timedelta(hours=1)
    if scenario == "tie":
        # One identity, one _version, two contents, in two separate parts.
        for value in ("left", "right"):
            client.execute(
                "INSERT INTO spans VALUES",
                [
                    _edge_row(
                        "trace-tie",
                        "span-tie",
                        started_at,
                        1,
                        strings={_EDGE_KEY: value},
                    )
                ],
            )
        allowed = [_one_string("left"), _one_string("right")]
    elif scenario == "moved":
        client.execute(
            "INSERT INTO spans VALUES",
            [
                _edge_row(
                    "trace-moved",
                    "span-moved",
                    started_at,
                    1,
                    strings={_EDGE_KEY: "old"},
                ),
                _edge_row(
                    "trace-moved", "span-moved", started_at, 2, numbers={_EDGE_KEY: 5.0}
                ),
            ],
        )
        stats = {"min": 5.0, "max": 5.0, "avg": 5.0, "p50": 5.0, "p95": 5.0}
        allowed = [
            {
                "type": "number",
                "types": [{"type": "number", "count": 1, "unique_values": 1}],
                "count": 1,
                "unique_values": 1,
                "top_values": [
                    {"value": 5, "type": "number", "count": 1, "percentage": 100.0}
                ],
                **stats,
                "stats": stats,
            }
        ]
    elif scenario == "corrected":
        # A corrected start_time is a second physical identity; both are live.
        client.execute(
            "INSERT INTO spans VALUES",
            [
                _edge_row(
                    "trace-fixed",
                    "span-fixed",
                    started_at,
                    1,
                    strings={_EDGE_KEY: "first"},
                ),
                _edge_row(
                    "trace-fixed",
                    "span-fixed",
                    started_at + timedelta(seconds=1),
                    2,
                    strings={_EDGE_KEY: "second"},
                ),
            ],
        )
        allowed = [
            {
                "type": "string",
                "types": [{"type": "string", "count": 2, "unique_values": 2}],
                "count": 2,
                "unique_values": 2,
                "top_values": [
                    {
                        "value": "first",
                        "type": "string",
                        "count": 1,
                        "percentage": 50.0,
                    },
                    {
                        "value": "second",
                        "type": "string",
                        "count": 1,
                        "percentage": 50.0,
                    },
                ],
            }
        ]
    else:
        client.execute(
            "INSERT INTO spans VALUES",
            [
                _edge_row(
                    "trace-other", "span-other", started_at, 1, strings={"other": "x"}
                )
            ],
        )
        allowed = [{"type": None, "count": 0, "unique_values": 0, "top_values": []}]

    payloads = {}
    for level in _BUILT_LEVELS:
        settings.EXACT_ATTRIBUTE_DETAIL_SQL_LEVEL = level
        executor = _CH25ExactAttributeExecutor(client)
        payload = read_exact_attribute_detail(
            project_id=PROJECT_ID,
            attribute_key=_EDGE_KEY,
            executor=executor,
            window_end=NOW,
        )
        assert len(executor.queries) == 1
        assert _data_fields(payload) in allowed, (scenario, level, payload)
        payload.pop("query_elapsed_ms")
        payloads[level] = payload
    if scenario != "tie":
        # A tie may legally resolve to either version at each level.
        assert payloads[0] == payloads[1]


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


def test_level0_sql_settings_and_deadline_are_dev_byte_for_byte(settings):
    settings.EXACT_ATTRIBUTE_DETAIL_SQL_LEVEL = 0
    settings.EXACT_ATTRIBUTE_DETAIL_RESHAPED_TIMEOUT_MS = 120_000
    executor = _FlaggedExecutor([])
    _read(executor)
    query, _params, kwargs = executor.calls[0]
    assert len(EXACT_ATTRIBUTE_DETAIL_SQL.encode()) == 8_822
    assert hashlib.sha256(EXACT_ATTRIBUTE_DETAIL_SQL.encode()).hexdigest() == (
        DEV_SQL_SHA256
    )
    assert query is EXACT_ATTRIBUTE_DETAIL_SQL
    assert kwargs["settings"] == {
        "max_threads": 1,
        "max_block_size": 512,
        "preferred_block_size_bytes": 4 * _MIB,
        "preferred_max_column_in_block_size_bytes": 4 * _MIB,
        "optimize_aggregation_in_order": 1,
        "max_bytes_before_external_group_by": 32 * _MIB,
        "max_bytes_before_external_sort": 32 * _MIB,
        "optimize_use_projections": 0,
        "allow_experimental_projection_optimization": 0,
        "max_bytes_to_read": 36 * 1024 * _MIB,
        "max_memory_usage": 36 * 1024 * _MIB,
        "read_overflow_mode": "throw",
        "max_result_rows": 1_001,
        "max_result_bytes": 64 * _MIB,
        "result_overflow_mode": "throw",
        "timeout_overflow_mode": "throw",
    }
    assert kwargs["timeout_ms"] == 30_000


def _dev_slice(start, end):
    sql = EXACT_ATTRIBUTE_DETAIL_SQL
    at = sql.index(start)
    return sql[at : sql.index(end, at)]


_DEV_KEY_PREDICATE = _dev_slice(
    "        (\n            indexHint(has(mapKeys(candidate_source.attrs_string)",
    "    GROUP BY project_id, trace_id, id, start_time\n",
)
_DEV_ARGMAX_TUPLE = _dev_slice(
    "            tuple(\n                attribute_source.is_deleted,",
    "            attribute_source._version\n        ) AS latest_state",
)


def test_semi_join_sql_reuses_dev_text_verbatim():
    head = detail_module._SEMI_JOIN_LATEST_SPANS_SQL
    tail_at = EXACT_ATTRIBUTE_DETAIL_SQL.index("exploded_values AS")
    assert EXACT_ATTRIBUTE_DETAIL_SQL.count("exploded_values AS") == 1
    assert EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL == (
        head + EXACT_ATTRIBUTE_DETAIL_SQL[tail_at:]
    )
    assert head.count(_DEV_KEY_PREDICATE) == 1
    assert head.index(_DEV_KEY_PREDICATE) > head.index(") IN\n")
    assert head.count(_DEV_ARGMAX_TUPLE) == 1
    assert hashlib.sha256(head.encode()).hexdigest() == SEMI_JOIN_HEAD_SHA256


def test_semi_join_replay_filters_only_immutable_identity():
    head = detail_module._SEMI_JOIN_LATEST_SPANS_SQL
    sql = EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL
    replay = head[head.index("FROM spans AS attribute_source") : head.index(") IN\n")]
    for banned in ("mapContains", "JSONHas", "has(mapKeys", "is_deleted"):
        assert banned not in replay
    for banned in ("INNER JOIN", "UNION", "DISTINCT"):
        assert banned not in sql
    assert "project_id" not in head[head.rindex("GROUP BY") :]
    for consumer in (
        "FROM latest_spans",
        "FROM exploded_values",
        "FROM grouped_values",
    ):
        assert sql.count(consumer) == 1
    assert sql.index("argMax(") < sql.index("tupleElement(latest_state, 1) = 0")


@pytest.mark.parametrize(
    "sql", [EXACT_ATTRIBUTE_DETAIL_SQL, EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL]
)
def test_no_bare_percent_in_any_statement(sql):
    assert "%" not in re.sub(r"%\(\w+\)s", "", sql)


def test_semi_join_settings_pin_limits():
    level1 = EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_READ_SETTINGS
    assert level1["optimize_aggregation_in_order"] == 0
    assert level1["set_overflow_mode"] == "throw"
    assert level1["max_rows_in_set"] == 0
    assert level1["max_bytes_in_set"] == 512 * _MIB
    assert level1["use_index_for_in_with_subqueries_max_values"] == 100_000
    assert {k: v for k, v in level1.items() if k.startswith("group_by_two_level")} == {
        "group_by_two_level_threshold_bytes": 128 * _MIB,
        "group_by_two_level_threshold": 32_768,
    }
    for mode in ("read_overflow_mode", "result_overflow_mode", "timeout_overflow_mode"):
        assert level1[mode] == "throw"
    assert level1["max_threads"] == 1
    assert "max_rows_to_read" not in level1
    changed = {
        k for k in level1 if level1[k] != EXACT_ATTRIBUTE_DETAIL_READ_SETTINGS.get(k)
    }
    assert changed == {
        "optimize_aggregation_in_order",
        "use_index_for_in_with_subqueries_max_values",
        "set_overflow_mode",
        "max_rows_in_set",
        "max_bytes_in_set",
        "group_by_two_level_threshold_bytes",
        "group_by_two_level_threshold",
    }


_NUMERIC_ROWS = [
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
]


# Routing only: the double returns the same rows at every level, so the
# payloads are equal by construction. It pins which statement and settings
# each level sends; the CH25 tests above execute the statements.
def test_level_selects_statement_and_payload_is_identical(settings):
    payloads = {}
    for level, sql, level_settings in (
        (0, EXACT_ATTRIBUTE_DETAIL_SQL, EXACT_ATTRIBUTE_DETAIL_READ_SETTINGS),
        (
            1,
            EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL,
            EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_READ_SETTINGS,
        ),
    ):
        settings.EXACT_ATTRIBUTE_DETAIL_SQL_LEVEL = level
        executor = _FlaggedExecutor(_NUMERIC_ROWS)
        payload = _read(executor)
        query, _params, kwargs = executor.calls[0]
        assert query is sql
        assert kwargs["settings"] is level_settings
        payload.pop("query_elapsed_ms")
        payloads[level] = payload
    assert payloads[0] == payloads[1]


@pytest.mark.parametrize(
    ("level", "reshaped", "wall", "expected"),
    [
        (0, 120_000, 180_000, 30_000),
        (1, 30_000, 180_000, 30_000),
        (1, 120_000, 180_000, 120_000),
        (1, 120_000, 60_000, 60_000),
        (1, 30_000, 10_000, 30_000),
        (1, 10_000, 180_000, 10_000),
    ],
)
def test_deadline_follows_effective_level(settings, level, reshaped, wall, expected):
    settings.EXACT_ATTRIBUTE_DETAIL_SQL_LEVEL = level
    settings.EXACT_ATTRIBUTE_DETAIL_RESHAPED_TIMEOUT_MS = reshaped
    settings.GRAPH_BACKGROUND_WALL_MS = wall
    executor = _FlaggedExecutor([])
    _read(executor)
    assert executor.calls[0][2]["timeout_ms"] == expected


@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("level", [0, 1, 2])
def test_lane_rule_on_the_production_call_shape(settings, monkeypatch, level, allowed):
    settings.EXACT_ATTRIBUTE_DETAIL_SQL_LEVEL = level
    settings.EXACT_ATTRIBUTE_DETAIL_RESHAPED_TIMEOUT_MS = 120_000
    settings.GRAPH_BACKGROUND_WALL_MS = 180_000
    # The pool is built lazily, so constructing the client opens no connection.
    client = ClickHouseClient(
        host="127.0.0.1",
        server_enforced_readonly=True,
        allow_query_settings_with_server_readonly=allowed,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.get_v2_query_client",
        lambda: client,
    )
    calls = []

    def record(self, query, params, **kwargs):
        calls.append((query, kwargs["settings"], kwargs["timeout_ms"]))
        return AttributeQueryPage([], 1.0)

    monkeypatch.setattr(V2AttributeQueryExecutor, "execute", record)
    read_exact_attribute_detail(
        project_id=PROJECT_ID, attribute_key="latency.score", window_end=NOW
    )
    if level == 0 or not allowed:
        expected = (
            EXACT_ATTRIBUTE_DETAIL_SQL,
            EXACT_ATTRIBUTE_DETAIL_READ_SETTINGS,
            30_000,
        )
    else:
        # Level 2 on a build whose highest level is 1 runs level 1.
        expected = (
            EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL,
            EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_READ_SETTINGS,
            120_000,
        )
    assert calls == [expected]


@pytest.mark.parametrize(
    "client",
    [
        "no-client",
        SimpleNamespace(),
        SimpleNamespace(
            server_enforced_readonly=0, allow_query_settings_with_server_readonly=False
        ),
    ],
)
def test_executor_without_lane_flags_runs_level0(settings, client):
    settings.EXACT_ATTRIBUTE_DETAIL_SQL_LEVEL = 1
    executor = _Executor([]) if client == "no-client" else _FlaggedExecutor([], client)
    _read(executor)
    query, _params, kwargs = executor.calls[0]
    assert query is EXACT_ATTRIBUTE_DETAIL_SQL
    assert kwargs["settings"] is EXACT_ATTRIBUTE_DETAIL_READ_SETTINGS
    assert kwargs["timeout_ms"] == 30_000


@pytest.mark.parametrize(
    ("highest", "configured", "expected_sql"),
    [
        (1, 2, EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL),
        (1, 1, EXACT_ATTRIBUTE_DETAIL_SEMI_JOIN_SQL),
        (0, 1, EXACT_ATTRIBUTE_DETAIL_SQL),
    ],
)
def test_level_is_clamped_to_the_build(
    settings, monkeypatch, highest, configured, expected_sql
):
    monkeypatch.setattr(detail_module, "_HIGHEST_BUILT_LEVEL", highest)
    settings.EXACT_ATTRIBUTE_DETAIL_SQL_LEVEL = configured
    executor = _FlaggedExecutor([])
    _read(executor)
    assert executor.calls[0][0] is expected_sql


def test_production_executor_is_not_an_application_read(monkeypatch):
    seen = []

    class _Recording:
        def __init__(self, *args, **kwargs):
            seen.append((args, kwargs))
            self.client = None

        def execute(self, query, params, **kwargs):
            return AttributeQueryPage([], 1.0)

    monkeypatch.setattr(detail_module, "V2AttributeQueryExecutor", _Recording)
    read_exact_attribute_detail(
        project_id=PROJECT_ID, attribute_key="latency.score", window_end=NOW
    )
    assert len(seen) == 1
    args, kwargs = seen[0]
    assert args == ()
    assert kwargs.get("application_read", False) is False
