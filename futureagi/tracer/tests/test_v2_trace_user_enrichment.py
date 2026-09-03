"""Dictionary-free, page-scoped V2 trace user enrichment contracts."""

from __future__ import annotations

import os
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from tracer.services.clickhouse.v2.query_builders.trace_list import (
    MAX_USER_PHYSICAL_IDENTITIES_PER_PAGE,
    TraceListQueryBuilderV2,
    UserEnrichmentLimitExceeded,
)
from tracer.services.clickhouse.v2.span_selectors import merge_content_rows
from tracer.views.trace import _collect_trace_enrichment_futures

PROJECT_A = "10000000-0000-0000-0000-000000000001"
PROJECT_B = "20000000-0000-0000-0000-000000000002"


@pytest.mark.unit
def test_user_enrichment_phase_one_bounds_remap_to_page_user_ids() -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_A,
        page_number=0,
        page_size=3,
    )

    query, params = builder.build_user_id_query(
        ["trace-a", "", "trace-a", "trace-b", "trace-c"]
    )

    assert params["user_trace_ids"] == ("trace-a", "trace-b", "trace-c")
    assert query.count("FROM spans AS sp") == 1
    assert query.count("end_user_id_remap") == 2
    assert "AS page_trace_user_rows" in query
    assert "page_end_user_group_ids AS" in query
    assert "WHERE new_id IN (SELECT new_id FROM page_end_user_group_ids)" in query
    assert "remap_lookup AS (" in query
    assert "ARRAY JOIN all_physical_end_user_ids AS any_id" in query
    assert "GROUP BY any_id" in query
    assert "LEFT ANY JOIN remap_lookup AS remap" in query
    assert (
        query.count("groupArray(tuple(project_id, trace_id, selected_end_user_id))")
        == 1
    )
    assert "arrayFirst(" not in query
    assert "OVER (PARTITION BY new_id)" not in query
    assert "end_users" not in query
    assert "end_users_dict" not in query
    assert "dictGet" not in query


@pytest.mark.unit
def test_user_enrichment_keeps_project_in_every_identity_join() -> None:
    builder = TraceListQueryBuilderV2(
        project_ids=[PROJECT_A, PROJECT_B],
        page_number=0,
        page_size=2,
    )

    identities = (
        (PROJECT_A, "shared-trace"),
        (PROJECT_B, "shared-trace"),
    )
    query, params = builder.build_user_id_query(
        ["shared-trace", "shared-trace"], trace_identities=identities
    )

    assert params["project_ids"] == (PROJECT_A, PROJECT_B)
    assert params["user_trace_identities"] == identities
    assert "(sp.project_id, sp.trace_id) IN %(user_trace_identities)s" in query
    assert "user_trace_ids" not in params


@pytest.mark.unit
def test_user_dimension_query_has_explicit_finite_composite_predicate() -> None:
    builder = TraceListQueryBuilderV2(project_ids=[PROJECT_A, PROJECT_B])

    query, params = builder.build_user_dimension_query(
        [
            {
                "project_id": PROJECT_A,
                "physical_end_user_ids": [
                    "00000000-0000-0000-0000-000000000010",
                    "00000000-0000-0000-0000-000000000020",
                ],
            },
            {
                "project_id": PROJECT_B,
                "physical_end_user_ids": ["00000000-0000-0000-0000-000000000010"],
            },
        ]
    )

    assert query.count("FROM end_users AS eu FINAL") == 1
    assert "(eu.project_id, eu.end_user_id)" in query
    assert "IN %(user_physical_identities)s" in query
    assert params["user_physical_identities"] == (
        (PROJECT_A, "00000000-0000-0000-0000-000000000010"),
        (PROJECT_A, "00000000-0000-0000-0000-000000000020"),
        (PROJECT_B, "00000000-0000-0000-0000-000000000010"),
    )


@pytest.mark.unit
def test_user_dimension_query_rejects_pathological_remap_fan_in() -> None:
    builder = TraceListQueryBuilderV2(project_id=PROJECT_A)
    physical_ids = [
        f"00000000-0000-0000-{value:04x}-000000000000"
        for value in range(MAX_USER_PHYSICAL_IDENTITIES_PER_PAGE + 1)
    ]

    with pytest.raises(UserEnrichmentLimitExceeded):
        builder.build_user_dimension_query(
            [{"project_id": PROJECT_A, "physical_end_user_ids": physical_ids}]
        )


@pytest.mark.unit
def test_two_phase_resolution_preserves_same_trace_text_per_project() -> None:
    user_a = "00000000-0000-0000-0000-000000000010"
    user_b = "00000000-0000-0000-0000-000000000020"

    class FakeAnalytics:
        def __init__(self) -> None:
            self.calls = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((query, params, timeout_ms, settings))
            if len(self.calls) == 1:
                return SimpleNamespace(
                    data=[
                        {
                            "project_id": PROJECT_A,
                            "trace_id": "shared",
                            "resolved_end_user_id": user_a,
                            "physical_end_user_ids": [user_a],
                        },
                        {
                            "project_id": PROJECT_B,
                            "trace_id": "shared",
                            "resolved_end_user_id": user_b,
                            "physical_end_user_ids": [user_b],
                        },
                    ]
                )
            return SimpleNamespace(
                data=[
                    {
                        "project_id": PROJECT_A,
                        "end_user_id": user_a,
                        "user_id": "tenant-a",
                        "version": datetime(2026, 8, 3, tzinfo=UTC),
                    },
                    {
                        "project_id": PROJECT_B,
                        "end_user_id": user_b,
                        "user_id": "tenant-b",
                        "version": datetime(2026, 8, 3, tzinfo=UTC),
                    },
                ]
            )

    analytics = FakeAnalytics()
    result = TraceListQueryBuilderV2(
        project_ids=[PROJECT_A, PROJECT_B], page_size=2
    ).resolve_user_ids_for_trace_identities(
        [(PROJECT_A, "shared"), (PROJECT_B, "shared")],
        analytics,
        settings={"max_threads": 1},
    )

    assert result.data == [
        {"project_id": PROJECT_A, "trace_id": "shared", "user_id": "tenant-a"},
        {"project_id": PROJECT_B, "trace_id": "shared", "user_id": "tenant-b"},
    ]
    assert result.query_count == 2
    assert analytics.calls[1][1]["user_physical_identities"] == (
        (PROJECT_A, user_a),
        (PROJECT_B, user_b),
    )


@pytest.mark.unit
def test_user_enrichment_empty_page_does_not_emit_a_query() -> None:
    builder = TraceListQueryBuilderV2(project_id=PROJECT_A)

    assert builder.build_user_id_query([]) == ("", {})
    assert builder.build_user_id_query(["", ""]) == ("", {})


@pytest.mark.unit
def test_user_enrichment_rejects_more_targets_than_the_page_contract() -> None:
    builder = TraceListQueryBuilderV2(project_id=PROJECT_A, page_size=2)

    with pytest.raises(ValueError, match="bounded page size"):
        builder.build_user_id_query(["trace-a", "trace-b", "trace-c"])


@pytest.mark.unit
def test_only_stalled_optional_user_future_degrades() -> None:
    required = Future()
    required.set_result(SimpleNamespace(data=[{"trace_id": "trace-a"}]))
    stalled_user = Future()

    results, degradation = _collect_trace_enrichment_futures(
        {required: "content", stalled_user: "users"}, timeout_seconds=0.001
    )

    assert results["content"].data == [{"trace_id": "trace-a"}]
    assert results["users"] is None
    assert degradation == ("TimeoutError", None)


@pytest.mark.unit
def test_stalled_required_future_is_never_masked_by_optional_user() -> None:
    required = Future()
    stalled_user = Future()

    with pytest.raises(TimeoutError):
        _collect_trace_enrichment_futures(
            {required: "content", stalled_user: "users"}, timeout_seconds=0.001
        )


@pytest.mark.unit
def test_optional_user_access_failure_degrades_but_programming_error_surfaces() -> None:
    class AccessDenied(Exception):
        code = 497

    denied_user = Future()
    denied_user.set_exception(AccessDenied("private server detail"))

    results, degradation = _collect_trace_enrichment_futures(
        {denied_user: "users"}, timeout_seconds=0.1
    )

    assert results == {"users": None}
    assert degradation == ("AccessDenied", 497)

    broken_user = Future()
    broken_user.set_exception(ValueError("compiler bug"))
    with pytest.raises(ValueError, match="compiler bug"):
        _collect_trace_enrichment_futures({broken_user: "users"}, timeout_seconds=0.1)


@pytest.mark.unit
def test_org_content_merge_keys_same_trace_by_project() -> None:
    rows = [
        {"project_id": PROJECT_A, "trace_id": "shared"},
        {"project_id": PROJECT_B, "trace_id": "shared"},
    ]
    content_rows = [
        {"project_id": PROJECT_A, "trace_id": "shared", "input": "tenant-a"},
        {"project_id": PROJECT_B, "trace_id": "shared", "input": "tenant-b"},
    ]

    merge_content_rows(
        rows,
        content_rows,
        id_key=("project_id", "trace_id"),
        keys=("input",),
    )

    assert [row["input"] for row in rows] == ["tenant-a", "tenant-b"]


@pytest.mark.unit
def test_content_query_preserves_project_in_public_identity() -> None:
    builder = TraceListQueryBuilderV2(project_ids=[PROJECT_A, PROJECT_B])

    query, _ = builder.build_content_query(["shared"])

    assert "toString(project_id) AS project_id" in query
    assert "LIMIT 1 BY project_id, trace_id" in query


def _local_ch25_client():
    """Return an explicitly local native client or skip the integration proof."""

    host = os.environ.get("CH25_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.skip("V2 trace-user integration proof is restricted to local ClickHouse")
    try:
        from clickhouse_driver import Client

        client = Client(
            host=host,
            port=int(os.environ.get("CH25_TCP_PORT", "19000")),
            user=os.environ.get("CH25_USER", "default"),
            password=os.environ.get("CH25_PASSWORD", ""),
            database="default",
            connect_timeout=2,
            send_receive_timeout=10,
        )
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse is unavailable for integration proof: {exc!r}")
    return client


@pytest.mark.integration
def test_user_enrichment_executes_with_latest_remap_tombstone_and_tenant_scope() -> (
    None
):
    admin = _local_ch25_client()
    database = f"test_trace_user_enrichment_{uuid4().hex}"
    try:
        admin.execute(f"CREATE DATABASE {database}")
    except Exception as exc:
        pytest.skip(
            f"local ClickHouse cannot create an isolated test database: {exc!r}"
        )

    try:
        from clickhouse_driver import Client

        client = Client(
            host=os.environ.get("CH25_HOST", "127.0.0.1"),
            port=int(os.environ.get("CH25_TCP_PORT", "19000")),
            user=os.environ.get("CH25_USER", "default"),
            password=os.environ.get("CH25_PASSWORD", ""),
            database=database,
            connect_timeout=2,
            send_receive_timeout=10,
        )
        client.execute(
            """
            CREATE TABLE spans
            (
                project_id UUID,
                trace_id String,
                id String,
                start_time DateTime64(6, 'UTC'),
                end_user_id Nullable(UUID),
                is_deleted UInt8,
                _version UInt64
            )
            ENGINE = ReplacingMergeTree(_version)
            ORDER BY (project_id, trace_id, id, start_time)
            """
        )
        client.execute(
            """
            CREATE TABLE end_users
            (
                project_id UUID,
                end_user_id UUID,
                user_id String,
                version DateTime64(6, 'UTC'),
                is_deleted UInt8
            )
            ENGINE = ReplacingMergeTree(version)
            ORDER BY (project_id, end_user_id)
            """
        )
        client.execute(
            """
            CREATE TABLE end_user_id_remap
            (
                old_id UUID,
                new_id UUID,
                version DateTime64(6, 'UTC')
            )
            ENGINE = ReplacingMergeTree(version)
            ORDER BY old_id
            """
        )

        project_a = UUID(PROJECT_A)
        project_b = UUID(PROJECT_B)
        survivor = UUID("00000000-0000-0000-0000-000000000010")
        live_sibling = UUID("00000000-0000-0000-0000-000000000020")
        remapped_new = UUID("00000000-0000-0000-0000-000000000099")
        chained_new = UUID("00000000-0000-0000-0000-0000000000aa")
        tenant_b_user = UUID("00000000-0000-0000-0000-0000000000b0")
        empty_user = UUID("00000000-0000-0000-0000-0000000000e0")
        now = datetime(2026, 8, 3, 12, tzinfo=UTC)

        client.execute(
            "INSERT INTO end_user_id_remap VALUES",
            [
                (survivor, remapped_new, now),
                (live_sibling, remapped_new, now + timedelta(microseconds=1)),
                # The shared new id is also an old id in another group.  The
                # any-id lookup must deterministically dedup this chain.
                (remapped_new, chained_new, now + timedelta(microseconds=2)),
            ],
        )
        client.execute(
            "INSERT INTO end_users VALUES",
            [
                (project_a, survivor, "stale-survivor", now, 0),
                (project_a, survivor, "stale-survivor", now + timedelta(seconds=1), 1),
                (project_a, live_sibling, "tenant-a-live", now, 0),
                (project_a, empty_user, "", now, 0),
                (project_b, tenant_b_user, "tenant-b-live", now, 0),
            ],
        )
        client.execute(
            "INSERT INTO spans VALUES",
            [
                # The newest physical span is tombstoned and must not win.
                (
                    project_a,
                    "shared-trace",
                    "deleted",
                    now + timedelta(seconds=3),
                    survivor,
                    0,
                    1,
                ),
                (
                    project_a,
                    "shared-trace",
                    "deleted",
                    now + timedelta(seconds=3),
                    survivor,
                    1,
                    2,
                ),
                (
                    project_a,
                    "shared-trace",
                    "live",
                    now + timedelta(seconds=2),
                    live_sibling,
                    0,
                    1,
                ),
                # A post-remap span carrying the new id resolves to the same label.
                (project_a, "new-id-trace", "live-new", now, remapped_new, 0, 1),
                (project_a, "nil-trace", "nil", now, None, 0, 1),
                (project_a, "empty-trace", "empty", now, empty_user, 0, 1),
                # Same trace text in another tenant must never cross the join.
                (project_b, "shared-trace", "tenant-b", now, tenant_b_user, 0, 1),
            ],
        )

        builder = TraceListQueryBuilderV2(
            project_ids=[PROJECT_A, PROJECT_B],
            page_number=0,
            page_size=6,
        )

        class LocalAnalytics:
            def __init__(self):
                self.calls = []

            def execute_ch_query(
                self, query, params, *, timeout_ms, settings
            ) -> SimpleNamespace:
                self.calls.append((query, params, timeout_ms, settings))
                rows, columns = client.execute(
                    query,
                    params,
                    settings=settings or {},
                    with_column_types=True,
                )
                names = [column[0] for column in columns]
                return SimpleNamespace(
                    data=[dict(zip(names, row, strict=True)) for row in rows]
                )

        identities = [
            (PROJECT_A, "shared-trace"),
            (PROJECT_A, "new-id-trace"),
            (PROJECT_A, "nil-trace"),
            (PROJECT_A, "empty-trace"),
            (PROJECT_A, "missing-trace"),
            (PROJECT_B, "shared-trace"),
        ]
        analytics = LocalAnalytics()
        resolution = builder.resolve_user_ids_for_trace_identities(
            identities,
            analytics,
            timeout_ms=5_000,
            settings={"max_threads": 1},
        )

        assert {
            (row["project_id"], row["trace_id"]): row["user_id"]
            for row in resolution.data
        } == {
            (PROJECT_A, "shared-trace"): "tenant-a-live",
            (PROJECT_A, "new-id-trace"): "tenant-a-live",
            (PROJECT_B, "shared-trace"): "tenant-b-live",
        }
        assert len(resolution.data) == 3
        assert resolution.query_count == 2
        assert len(analytics.calls) == 2

        phase_one_query, phase_one_params, *_ = analytics.calls[0]
        assert phase_one_query.count("FROM spans AS sp") == 1
        assert phase_one_query.count("FROM end_user_id_remap") == 2
        assert "FROM end_users" not in phase_one_query

        phase_two_query, phase_two_params, *_ = analytics.calls[1]
        assert phase_two_query.count("FROM end_users AS eu FINAL") == 1
        assert "(eu.project_id, eu.end_user_id)" in phase_two_query
        assert "IN %(user_physical_identities)s" in phase_two_query
    finally:
        # The target is a unique, locally-created database whose exact name is
        # known above; teardown cannot touch an existing workspace database.
        admin.execute(f"DROP DATABASE IF EXISTS {database} SYNC")
