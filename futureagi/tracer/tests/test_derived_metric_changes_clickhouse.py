"""Discovery/repair probes on an owned CH25 sidecar, plus fail-closed contracts.

Set FI_METRIC_CHANGES_TEST_NATIVE_PORT for live probes. Each test owns a fresh
database. Small fixtures prove semantics, NOT the T14/T17 production throughput
or 24-hour staleness gate. No test uses the app database or production clients.
"""

import json
import os
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from clickhouse_driver import Client

from tracer.selectors.eval_tasks import continuous_candidates as arrivals
from tracer.services.derived_metrics import changes
from tracer.services.derived_metrics.changes import (
    Dependency,
    MetricChangeReadError,
    MetricReadBudget,
    ReconciliationCheckpoint,
    SourceKey,
    UnitKey,
    discover_changes,
    ns,
    reconcile_page,
    require_initial_dependencies,
)
from tracer.services.derived_metrics.settle import UnitSnapshot, settle_due_page

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=30)
SCHEMA = Path(__file__).resolve().parents[1] / "services/clickhouse/v2/schema"


class Analytics:
    supports_per_query_read_settings = True

    def __init__(self, client):
        self.client, self.calls, self.query_ids, self.server_caps = client, [], [], []

    def execute_ch_query(
        self, query, params, *, timeout_ms, settings, server_execution_cap_ms=None
    ):
        query_id = "metric-b04-" + uuid4().hex
        self.calls.append((query, params, settings))
        self.query_ids.append(query_id)
        self.server_caps.append(server_execution_cap_ms)
        data, columns = self.client.execute(
            query,
            params,
            with_column_types=True,
            settings={
                **settings,
                "max_execution_time": max(
                    0.001, (server_execution_cap_ms or timeout_ms) / 1000
                ),
            },
            query_id=query_id,
        )
        return SimpleNamespace(
            data=[dict(zip((c[0] for c in columns), row, strict=True)) for row in data]
        )


@pytest.fixture
def store():
    port = os.environ.get("FI_METRIC_CHANGES_TEST_NATIVE_PORT")
    if not port:
        pytest.skip("owned ClickHouse change-discovery sidecar not requested")
    admin = Client("127.0.0.1", port=int(port))
    database = "test_metric_changes_" + uuid4().hex
    admin.execute(f"CREATE DATABASE {database}")
    client = Client(
        "127.0.0.1",
        port=int(port),
        database=database,
        settings={"max_threads": 2, "max_memory_usage": 268435456},
    )
    try:
        for name in (
            "019_id_remap.sql",
            "033_metric_unit_state.sql",
            "034_metric_source_dependencies.sql",
        ):
            text = "\n".join(
                line.split("--", 1)[0]
                for line in (SCHEMA / name).read_text().splitlines()
            )
            for statement in text.split(";"):
                if statement.strip():
                    client.execute(statement)
        # Production key layout (002_spans_v2.sql); a small granule makes the
        # per-page read bound observable at fixture scale.
        client.execute(
            """CREATE TABLE spans (
            project_id UUID, org_id Nullable(UUID),
            observation_type LowCardinality(String) DEFAULT 'SPAN',
            service_name LowCardinality(String) DEFAULT '', trace_id String, id String,
            start_time DateTime64(6, 'UTC'), end_time Nullable(DateTime64(6, 'UTC')),
            parent_span_id String DEFAULT '', trace_session_id Nullable(UUID),
            end_user_id Nullable(UUID), is_deleted UInt8 DEFAULT 0, _version UInt64,
            INDEX idx_id id TYPE bloom_filter(0.001) GRANULARITY 1,
            INDEX idx_trace_id trace_id TYPE bloom_filter(0.001) GRANULARITY 1,
            INDEX idx_trace_session_id trace_session_id TYPE bloom_filter(0.001) GRANULARITY 1,
            INDEX idx_end_user_id end_user_id TYPE bloom_filter(0.001) GRANULARITY 1,
            INDEX auto_minmax_index__version _version TYPE minmax GRANULARITY 1
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        PRIMARY KEY (project_id, observation_type, service_name, toStartOfHour(start_time))
        ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)
        SETTINGS index_granularity = 64"""
        )
        for table, version, tail in (
            (
                "tracer_eval_logger_v2",
                "_version",
                "_version UInt64, is_deleted UInt8 DEFAULT 0",
            ),
            (
                "tracer_eval_logger",
                "_peerdb_version",
                "_peerdb_version UInt64, _peerdb_synced_at DateTime64(6, 'UTC'), deleted UInt8 DEFAULT 0",
            ),
        ):
            client.execute(
                f"""CREATE TABLE {table} (
                id UUID, trace_id Nullable(UUID), observation_span_id Nullable(String),
                trace_session_id Nullable(UUID), custom_eval_config_id UUID,
                created_at DateTime64(3, 'UTC'), {tail}
            ) ENGINE = ReplacingMergeTree({version})
            ORDER BY (custom_eval_config_id, created_at, id)"""
            )
        value = SimpleNamespace(
            client=client,
            analytics=Analytics(client),
            org=str(uuid4()),
            project=str(uuid4()),
            attempt=str(uuid4()),
            other_attempt=str(uuid4()),
            a=str(uuid4()),
            b=str(uuid4()),
            user_a=str(uuid4()),
            user_b=str(uuid4()),
            trace=str(uuid4()),
            span="span-a",
            config=str(uuid4()),
        )
        yield value
    finally:
        client.disconnect()
        admin.execute(f"DROP DATABASE {database} SYNC")
        admin.disconnect()


def seed_span(
    store,
    *,
    span=None,
    trace=None,
    session=None,
    user=None,
    version=None,
    deleted=0,
    start=OLD,
    org=None,
):
    store.client.execute(
        "INSERT INTO spans (project_id, org_id, trace_id, id, start_time, end_time, trace_session_id, end_user_id, is_deleted, _version) VALUES",
        [
            (
                store.project,
                org,
                trace or store.trace,
                span or store.span,
                start,
                start + timedelta(seconds=1),
                session or store.a,
                user or store.user_a,
                deleted,
                version or ns(NOW - timedelta(seconds=1)),
            )
        ],
    )


def seed_dependency(
    store,
    *,
    span=None,
    trace=None,
    session=None,
    user=None,
    deleted=0,
    attempt=None,
    version=1,
):
    store.client.execute(
        """INSERT INTO metric_source_dependencies
        (org_id, project_id, trace_id, span_id, raw_session_id, canonical_session_id,
            raw_user_id, canonical_user_id, unit_start, is_deleted, generation, attempt_id, _version) VALUES""",
        [
            (
                store.org,
                store.project,
                trace or store.trace,
                span or store.span,
                session or store.a,
                session or store.a,
                user or store.user_a,
                user or store.user_a,
                OLD,
                deleted,
                version,
                attempt or store.attempt,
                version,
            )
        ],
    )


def discover(store, **kwargs):
    return discover_changes(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(store.attempt,),
        watermark_ns=ns(NOW - timedelta(minutes=1)),
        eval_watermark_ns=ns(NOW - timedelta(minutes=1)),
        remap_cursor_ns=ns(NOW - timedelta(minutes=1)),
        ceiling=kwargs.pop("ceiling", NOW),
        **kwargs,
    )


def repair(store, checkpoint=None, **kwargs):
    return reconcile_page(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(store.attempt,),
        now=kwargs.pop("now", NOW),
        checkpoint=checkpoint,
        **kwargs,
    )


def complete_repair(store, checkpoint=None, **kwargs):
    pages = []
    for _ in range(100):
        page = repair(store, checkpoint, **kwargs)
        pages.append(page)
        # Exercise the exact JSON roundtrip used by the PG cursor. Only the
        # writer may actually save it, after this page's dependencies publish.
        checkpoint = ReconciliationCheckpoint.from_dict(
            json.loads(json.dumps(page.checkpoint.to_dict()))
        )
        if checkpoint.phase == "complete":
            return pages
    pytest.fail("retained-history keyset did not finish")


@pytest.mark.integration
def test_arrival_update_and_overlap_tail_discovered_with_half_open_ceiling(store):
    seed_span(store, version=ns(NOW - timedelta(seconds=20)))
    first = discover(store)
    assert first.source_keys == (SourceKey(store.trace, store.span),)
    assert UnitKey("conversation", store.a) in first.units
    assert first.cursor_updates["watermark_ns"] == ns(NOW)
    # The insert becomes visible only AFTER first's ceiling was selected, with
    # an assigned version in its overlap tail. It must be read next pass.
    seed_span(store, span="overlap", version=ns(NOW - timedelta(minutes=2)))
    next_pass = discover_changes(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(store.attempt,),
        watermark_ns=ns(NOW),
        eval_watermark_ns=ns(NOW),
        remap_cursor_ns=ns(NOW),
        ceiling=NOW + timedelta(minutes=1),
    )
    assert SourceKey(store.trace, "overlap") in next_pass.source_keys
    seed_span(store, span="at-ceiling", version=ns(NOW))
    assert SourceKey(store.trace, "at-ceiling") not in discover(store).source_keys
    assert (
        SourceKey(store.trace, "at-ceiling")
        in discover(store, ceiling=NOW + timedelta(seconds=1)).source_keys
    )
    print("B04 arrival probe query", store.analytics.query_ids[-1])


@pytest.mark.integration
@pytest.mark.parametrize("deleted", [False, True])
def test_move_or_delete_after_source_merge_invalidates_old_and_new_membership(
    store, deleted
):
    seed_dependency(store)
    seed_span(store, version=ns(NOW - timedelta(days=1)))
    seed_span(store, session=store.b, user=store.user_b, deleted=int(deleted))
    store.client.execute("OPTIMIZE TABLE spans FINAL")
    assert store.client.execute("SELECT count() FROM spans")[0][0] == 1
    result = discover(store)
    assert set(result.units) == {
        UnitKey("conversation", store.a),
        UnitKey("conversation", store.b),
    }
    assert set(result.user_ids) == {store.user_a, store.user_b}
    assert result.dependencies[0].canonical_session_id == store.b
    assert result.dependencies[0].is_deleted == deleted
    assert result.prior_dependencies[0].canonical_session_id == store.a
    assert result.prior_dependencies[0].unit_start == OLD


@pytest.mark.integration
def test_conversation_soft_delete_includes_all_spans_and_latest_tombstones(store):
    for span in ("one", "two"):
        seed_dependency(store, span=span)
        seed_span(store, span=span, version=ns(NOW - timedelta(days=1)))
        seed_span(store, span=span, deleted=1)
    store.client.execute("OPTIMIZE TABLE spans FINAL")
    result = discover(store)
    assert len(result.dependencies) == 2 and all(
        d.is_deleted for d in result.dependencies
    )
    assert result.units == (UnitKey("conversation", store.a),)


@pytest.mark.integration
@pytest.mark.parametrize("table", ["tracer_eval_logger_v2", "tracer_eval_logger"])
def test_eval_rerun_uses_storage_arrival_column_and_only_used_configs(
    store, settings, table
):
    settings.CH25_EVAL_LOGGER_TABLE = table
    seed_span(store, version=ns(NOW - timedelta(days=30)))
    eval_id = str(uuid4())

    def insert(config, arrival, relation_id=eval_id):
        v2 = table.endswith("_v2")
        columns = "id, trace_id, observation_span_id, trace_session_id, custom_eval_config_id, created_at, "
        columns += "_version" if v2 else "_peerdb_version, _peerdb_synced_at"
        row = (relation_id, store.trace, store.span, store.a, config, OLD)
        row += (ns(arrival),) if v2 else (ns(arrival), arrival)
        store.client.execute(f"INSERT INTO {table} ({columns}) VALUES", [row])

    insert(store.config, NOW - timedelta(days=1))
    assert discover(store, eval_config_ids=(store.config,)).units == ()
    insert(store.config, NOW - timedelta(seconds=1))
    store.client.execute(f"OPTIMIZE TABLE {table} FINAL")
    result = discover(store, eval_config_ids=(store.config,))
    assert result.units == (UnitKey("conversation", store.a),)
    queries = [q for q, _, _ in store.analytics.calls if f"FROM {table}\n" in q]
    assert queries and all("custom_eval_config_id IN" in q for q in queries)
    assert (
        "_version >=" if table.endswith("_v2") else "_peerdb_synced_at >="
    ) in queries[-1]
    assert discover(store, eval_config_ids=(str(uuid4()),)).units == ()


@pytest.mark.integration
@pytest.mark.parametrize("table", ["trace_session_id_remap", "end_user_id_remap"])
def test_remap_only_change_expands_every_sibling_and_old_committed_owner(store, table):
    # Pick the smaller survivor-old deterministically, as the app does.
    old_a, old_b = sorted((str(uuid4()), str(uuid4())))
    new = str(uuid4())
    for index, old_id in enumerate((old_a, old_b)):
        kwargs = {"session": old_id} if table.startswith("trace") else {"user": old_id}
        seed_span(
            store,
            span=f"span-{index}",
            session=kwargs.get("session", (store.a, store.b)[index]),
            user=kwargs.get("user", (store.user_a, store.user_b)[index]),
            version=ns(OLD),
        )
        seed_dependency(
            store,
            span=f"span-{index}",
            session=kwargs.get("session", (store.a, store.b)[index]),
            user=kwargs.get("user", (store.user_a, store.user_b)[index]),
        )
    store.client.execute(
        f"INSERT INTO {table} (old_id, new_id, version) VALUES",
        [
            (old_a, new, NOW - timedelta(seconds=1)),
            (old_b, new, NOW - timedelta(days=1)),
        ],
    )
    store.client.execute(f"OPTIMIZE TABLE {table} FINAL")
    result = discover(store)
    assert len(result.source_keys) == 2
    if table.startswith("trace"):
        assert {u.unit_id for u in result.units} == {old_a, old_b}
        assert {d.canonical_session_id for d in result.dependencies} == {old_a}
    else:
        assert {u.unit_id for u in result.units} == {store.a, store.b}
        assert {d.canonical_user_id for d in result.dependencies} == {old_a}
        assert set(result.user_ids) == {old_a, old_b}
    assert not any(
        "_version" in q
        for q, _, _ in store.analytics.calls
        if f"FROM {table} FINAL\n" in q
    )


@pytest.mark.integration
@pytest.mark.parametrize("optimized", [False, True])
def test_old_span_and_eval_visibility_beyond_overlap_repaired_from_full_history(
    store, settings, optimized
):
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    seed_dependency(store)
    # An arrival was assigned ten minutes before the current cursor, but only
    # becomes visible now, on a 30-day-old conversation. Fast path misses it.
    assigned = NOW - timedelta(minutes=10)
    seed_span(store, version=ns(assigned), session=store.b)
    store.client.execute(
        """INSERT INTO tracer_eval_logger_v2
        (id, trace_id, observation_span_id, trace_session_id, custom_eval_config_id, created_at, _version) VALUES""",
        [
            (
                str(uuid4()),
                store.trace,
                store.span,
                store.b,
                store.config,
                OLD,
                ns(assigned),
            )
        ],
    )
    if optimized:
        store.client.execute("OPTIMIZE TABLE spans FINAL")
        store.client.execute("OPTIMIZE TABLE tracer_eval_logger_v2 FINAL")
    assert discover(store, eval_config_ids=(store.config,)).source_keys == ()
    pages = complete_repair(store, page_size=1)
    units = {u for page in pages for u in page.changes.units}
    assert units == {UnitKey("conversation", store.a), UnitKey("conversation", store.b)}
    assert pages[-1].checkpoint.completed_at == NOW
    assert not pages[-1].checkpoint.coverage_status(NOW)["stale"]
    require_initial_dependencies(pages[-1].checkpoint)
    # Membership is unchanged by an eval-only re-run. Reconciliation still
    # invalidates the unit; it must not compare only span _version/membership.
    again = complete_repair(store, pages[-1].checkpoint)
    assert UnitKey("conversation", store.b) in {
        u for p in again for u in p.changes.units
    }


@pytest.mark.integration
def test_repair_union_covers_absent_deleted_sources_and_orphan_lifecycle_units(store):
    seed_dependency(store, span="absent")
    seed_dependency(store, span="deleted", deleted=1)
    seed_span(store, span="new-unindexed", session=store.b, version=ns(OLD))
    store.client.execute(
        """INSERT INTO metric_unit_state
        (org_id, project_id, level, unit_id, first_seen, due_at, is_deleted, generation, attempt_id, _version) VALUES""",
        [
            (
                store.org,
                store.project,
                "conversation",
                "orphan",
                OLD,
                NOW,
                1,
                1,
                store.attempt,
                1,
            )
        ],
    )
    pages = complete_repair(store, page_size=1)
    keys = {k for p in pages for k in p.changes.source_keys}
    assert {k.span_id for k in keys} == {"absent", "deleted", "new-unindexed"}
    deps = {d.span_id: d for p in pages for d in p.changes.dependencies}
    assert deps["absent"].is_deleted and deps["deleted"].is_deleted
    assert not deps["new-unindexed"].is_deleted
    assert UnitKey("conversation", "orphan") in {
        u for p in pages for u in p.changes.units
    }
    assert pages[-1].checkpoint.scanned == 4
    # Retry of a page has the same keys and payloads. No cursor was saved by
    # discovery; checkpoint persistence is the manifest publisher's job.
    assert repair(store, page_size=1).changes == repair(store, page_size=1).changes


@pytest.mark.integration
def test_excluded_dependency_attempt_and_cross_tenant_rows_never_become_preimages(
    store,
):
    seed_dependency(store)
    seed_dependency(store, session=store.b, attempt=store.other_attempt, version=9)
    seed_span(store, session=store.a)
    result = discover(store)
    assert result.units == (UnitKey("conversation", store.a),)
    # Even after dependency/source merges, an excluded generation cannot hide
    # the old allowed row. A generation <= head predicate would be wrong here.
    store.client.execute("OPTIMIZE TABLE metric_source_dependencies FINAL")
    assert discover(store).units == result.units
    empty = discover_changes(
        store.analytics,
        org_id=store.org,
        project_id=str(uuid4()),
        committed_attempts=(store.attempt,),
        watermark_ns=ns(NOW - timedelta(minutes=1)),
        eval_watermark_ns=ns(NOW - timedelta(minutes=1)),
        remap_cursor_ns=ns(NOW - timedelta(minutes=1)),
        ceiling=NOW,
    )
    assert empty.units == () and empty.dependencies == ()


@pytest.mark.integration
def test_live_due_queue_filters_stale_and_excluded_attempts_after_source_recheck(store):
    rows = []
    for unit, version, deleted, due_at, attempt in (
        ("overdue", 1, 0, NOW - timedelta(days=3), store.attempt),
        ("settled", 1, 0, NOW - timedelta(days=3), store.attempt),
        ("settled", 2, 1, NOW - timedelta(days=3), store.attempt),
        ("renewed", 1, 0, NOW - timedelta(days=3), store.attempt),
        ("renewed", 2, 0, NOW + timedelta(minutes=30), store.attempt),
        ("uncommitted", 9, 0, NOW - timedelta(days=3), store.other_attempt),
    ):
        rows.append(
            (
                store.org,
                store.project,
                "conversation",
                unit,
                ns(OLD),
                OLD,
                due_at,
                deleted,
                version,
                attempt,
                version,
            )
        )
    store.client.execute(
        """INSERT INTO metric_unit_state
        (org_id, project_id, level, unit_id, last_arrival_ns, first_seen, due_at,
         is_deleted, generation, attempt_id, _version) VALUES""",
        rows,
    )
    store.client.execute("OPTIMIZE TABLE metric_unit_state FINAL")
    loaded = []

    def latest(keys, budget):
        loaded.extend(keys)
        return {k: UnitSnapshot(k, ns(NOW - timedelta(minutes=31)), OLD) for k in keys}

    result = settle_due_page(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(store.attempt,),
        now=NOW,
        settle_minutes=30,
        load_latest=latest,
    )
    assert result.nominated == 4 and result.verified == 1
    assert loaded == [UnitKey("conversation", "overdue")]
    assert result.updates[0].status == "settled"
    print("B04 due queue probe query", store.analytics.query_ids[-1])


class EmptyAnalytics:
    supports_per_query_read_settings = True

    def __init__(self, rows=(), fail_on=None):
        self.rows, self.calls, self.fail_on = list(rows), [], fail_on

    def execute_ch_query(
        self, query, params, *, server_execution_cap_ms=None, **kwargs
    ):
        self.calls.append(
            (
                query,
                params,
                {**kwargs, "server_execution_cap_ms": server_execution_cap_ms},
            )
        )
        if server_execution_cap_ms is None:
            raise AssertionError("metric reads must be server-capped")
        if len(self.calls) == self.fail_on:
            raise TimeoutError("unavailable fixture")
        return SimpleNamespace(data=self.rows)


@pytest.mark.unit
def test_metric_read_fails_closed_without_server_execution_cap_support():
    class UncappedAnalytics:
        def __init__(self):
            self.calls = 0

        def execute_ch_query(self, *args, **kwargs):
            self.calls += 1
            return SimpleNamespace(data=[])

    analytics = UncappedAnalytics()
    with pytest.raises(MetricChangeReadError, match="server-side deadline"):
        changes.read(analytics, "SELECT 1", {}, MetricReadBudget())
    assert analytics.calls == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    "reason", ["rows", "bytes", "queries", "deadline", "read_error"]
)
def test_failed_discovery_does_not_return_or_advance_any_watermark(reason):
    analytics = EmptyAnalytics(fail_on=1 if reason == "read_error" else None)
    options = {}
    if reason in {"rows", "bytes"}:
        analytics.rows = [{"trace_id": "t", "id": "s", "start_us": 0}] * 3
        options = {"max_rows": 1} if reason == "rows" else {"max_result_bytes": 1}
    if reason == "queries":
        options = {"max_queries": 1}
    budget = MetricReadBudget(**options)
    if reason == "deadline":
        budget.deadline = 0
    cursors = {
        "watermark_ns": ns(NOW),
        "eval_watermark_ns": ns(NOW),
        "remap_cursor_ns": ns(NOW),
    }
    original = dict(cursors)
    with pytest.raises(arrivals.ContinuousCandidateReadError):
        discover_changes(
            analytics,
            org_id=str(uuid4()),
            project_id=str(uuid4()),
            committed_attempts=(),
            **cursors,
            ceiling=NOW + timedelta(seconds=1),
            budget=budget,
        )
    assert cursors == original
    assert not any(
        "UPDATE" in query or "INSERT" in query for query, _, _ in analytics.calls
    )


@pytest.mark.unit
def test_server_cap_stops_slow_discovery_without_returning_cursors():
    class SlowAnalytics(EmptyAnalytics):
        def execute_ch_query(
            self,
            query,
            params,
            *,
            server_execution_cap_ms=None,
            **kwargs,
        ):
            self.calls.append(
                (
                    query,
                    params,
                    {**kwargs, "server_execution_cap_ms": server_execution_cap_ms},
                )
            )
            self.cap_ms = server_execution_cap_ms
            time.sleep(server_execution_cap_ms / 2000)
            raise TimeoutError("simulated ClickHouse execution-cap cancellation")

    analytics = SlowAnalytics()
    budget = MetricReadBudget(deadline_seconds=0.1)
    cursors = {
        "watermark_ns": ns(NOW),
        "eval_watermark_ns": ns(NOW),
        "remap_cursor_ns": ns(NOW),
    }
    original = dict(cursors)
    started = time.monotonic()
    with pytest.raises(arrivals.ContinuousCandidateReadError):
        discover_changes(
            analytics,
            org_id=str(uuid4()),
            project_id=str(uuid4()),
            committed_attempts=(),
            **cursors,
            ceiling=NOW + timedelta(seconds=1),
            budget=budget,
        )
    assert 0 < analytics.cap_ms < 100
    assert time.monotonic() - started < 0.1
    assert cursors == original


@pytest.mark.integration
def test_server_cancels_slow_discovery_before_it_returns_cursors(store):
    class SlowAnalytics:
        supports_per_query_read_settings = True

        def __init__(self, client):
            self.client = client
            self.cap_ms = None
            self.elapsed = None
            self.query_id = "metric-b04-timeout-" + uuid4().hex

        def execute_ch_query(
            self,
            query,
            params,
            *,
            timeout_ms,
            settings,
            server_execution_cap_ms=None,
        ):
            self.cap_ms = server_execution_cap_ms
            started = time.monotonic()
            try:
                return self.client.execute(
                    "SELECT sum(cityHash64(SHA256(toString(number)))) "
                    "FROM numbers_mt(100000000)",
                    query_id=self.query_id,
                    settings={
                        **settings,
                        "max_execution_time": server_execution_cap_ms / 1000,
                        "readonly": 2,
                        "timeout_overflow_mode": "throw",
                    },
                )
            finally:
                self.elapsed = time.monotonic() - started

    analytics = SlowAnalytics(store.client)
    # ClickHouse stops a capped scan cooperatively, a few ms past the cap
    # (25.3.14.14: 102-104 ms at a ~100 ms cap). The 0.4 s client bound below
    # leaves room for that polling and the round trip.
    budget = MetricReadBudget(deadline_seconds=0.1)
    cursors = {
        "watermark_ns": ns(NOW),
        "eval_watermark_ns": ns(NOW),
        "remap_cursor_ns": ns(NOW),
    }
    original = dict(cursors)
    with pytest.raises(arrivals.ContinuousCandidateReadError):
        discover_changes(
            analytics,
            org_id=store.org,
            project_id=store.project,
            committed_attempts=(),
            **cursors,
            ceiling=NOW + timedelta(seconds=1),
            budget=budget,
        )
    assert 0 < analytics.cap_ms <= 100
    assert analytics.elapsed < 0.4
    assert cursors == original
    # The server itself stopped the scan at the cap, not just the client.
    server_cap_overshoot_ms = 50
    store.client.execute("SYSTEM FLUSH LOGS")
    rows = store.client.execute(
        "SELECT exception_code, query_duration_ms FROM system.query_log "
        "WHERE query_id = %(query_id)s AND type = 'ExceptionWhileProcessing'",
        {"query_id": analytics.query_id},
    )
    assert len(rows) == 1
    assert rows[0][0] == 159
    assert rows[0][1] <= analytics.cap_ms + server_cap_overshoot_ms


@pytest.mark.unit
def test_application_read_policy_keeps_block_size_and_memory_but_drops_scan_caps():
    from tracer.services.clickhouse.application_read_policy import (
        application_read_settings,
    )

    budget = MetricReadBudget(max_memory_bytes=1234)
    analytics = EmptyAnalytics()
    changes.read(analytics, "SELECT 1", {}, budget, settings={"max_block_size": 50})
    sent = application_read_settings(analytics.calls[0][2]["settings"])
    # The per-page read bound depends on max_block_size reaching the server.
    assert sent["max_block_size"] == 50 and sent["max_memory_usage"] == 1234
    # By policy these never reach the server; the Python-side budget (and a
    # WallCappedAnalytics wrapper for time) is what bounds a proposal.
    assert sent["max_bytes_to_read"] == 0 and sent["max_result_rows"] == 0


@pytest.mark.unit
def test_read_budget_settings_reach_the_query_boundary_even_for_reused_readers():
    analytics = EmptyAnalytics()
    changes.read(
        analytics,
        "SELECT 1",
        {},
        MetricReadBudget(max_memory_bytes=1234, max_read_bytes=5678),
    )
    settings = analytics.calls[0][2]["settings"]
    assert (
        settings["max_memory_usage"] == 1234 and settings["max_bytes_to_read"] == 5678
    )
    assert settings["read_overflow_mode"] == settings["result_overflow_mode"] == "throw"
    # Statement-shaping settings cannot loosen the resource caps.
    changes.read(
        analytics,
        "SELECT 1",
        {},
        MetricReadBudget(max_memory_bytes=1234),
        settings={"max_memory_usage": 10**12, "max_block_size": 7},
    )
    shaped = analytics.calls[1][2]["settings"]
    assert shaped["max_memory_usage"] == 1234 and shaped["max_block_size"] == 7


@pytest.mark.unit
def test_candidate_cap_overflow_is_not_a_partial_success(monkeypatch):
    monkeypatch.setattr(changes, "MAX_KEYS", 1)
    with pytest.raises(arrivals.ContinuousCandidateOverflow):
        changes._limit((SourceKey("a", "s"), SourceKey("b", "s")))


@pytest.mark.unit
def test_repair_budget_failure_preserves_checkpoint_and_reports_stale_coverage():
    cp = ReconciliationCheckpoint(
        NOW - timedelta(days=2), phase="dependencies", after=("a", "b")
    )
    before = cp.to_dict()
    with pytest.raises(arrivals.ContinuousCandidateReadError):
        reconcile_page(
            EmptyAnalytics(fail_on=1),
            org_id=str(uuid4()),
            project_id=str(uuid4()),
            committed_attempts=(str(uuid4()),),
            now=NOW,
            checkpoint=cp,
        )
    assert cp.to_dict() == before
    assert cp.coverage_status(NOW)["stale"] and cp.coverage_status(NOW)["incomplete"]
    assert cp.coverage_status(NOW)["throughput_bound_proven"] is False
    assert cp.coverage_status(NOW)["moved_key_bound_passes"] == 2
    with pytest.raises(MetricChangeReadError, match="backfill is incomplete"):
        require_initial_dependencies(cp)
    done = replace(
        cp,
        phase="complete",
        after=None,
        completed_at=NOW,
        completed_passes=1,
        covered_through=NOW - timedelta(hours=3),
    )
    assert not done.coverage_status(NOW)["stale"]
    # Age counts from the start of the last complete pass, not its end.
    assert done.coverage_status(NOW + timedelta(hours=21, seconds=1))["stale"]
    require_initial_dependencies(done)
    assert ReconciliationCheckpoint.from_dict(done.to_dict()) == done


class SweepAnalytics(EmptyAnalytics):
    """Answers the day-range probe; every page statement returns no rows."""

    def __init__(self, first_day, last_day):
        super().__init__()
        self.days = (first_day, last_day)

    def execute_ch_query(self, query, params, **kwargs):
        self.calls.append((query, params, kwargs))
        if "min(start_time)" in query:
            first, last = self.days
            rows = [{"first_day": first, "last_day": last, "source_rows": 9}]
            return SimpleNamespace(data=rows)
        return SimpleNamespace(data=[])


@pytest.mark.unit
def test_repair_sweeps_every_retained_day_without_a_time_or_arrival_floor():
    from tracer.models.derived_metric import MetricProjectRefresh

    # Five years back: no retention or yesterday-only cutoff is applied.
    analytics = SweepAnalytics("2021-10-29", "2021-11-03")
    page = reconcile_page(
        analytics,
        org_id=str(uuid4()),
        project_id=str(uuid4()),
        committed_attempts=(str(uuid4()),),
        now=NOW,
    )
    probe, *days = (q for q, _, _ in analytics.calls)
    assert "start_time >=" not in probe and "_version" not in probe
    starts = [p["day_start"] for q, p, _ in analytics.calls[1:]]
    assert starts == [
        f"2021-{d} 00:00:00"
        for d in ("10-29", "10-30", "10-31", "11-01", "11-02", "11-03")
    ]
    assert all("_version" not in q and "argMax" not in q for q in days)
    assert all(
        "ORDER BY observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        in q
        for q in days
    )
    assert page.checkpoint.phase == "dependencies"
    assert page.changes.cursor_updates["pending_repair"] is True
    assert set(page.changes.cursor_updates) <= MetricProjectRefresh.PUBLISHABLE_FIELDS
    json.dumps(page.changes.cursor_updates["reconciliation_cursor"])


@pytest.mark.unit
def test_gap_days_are_skipped_in_bounded_probes_per_page():
    analytics = SweepAnalytics("2026-01-01", "2026-10-29")
    page = reconcile_page(
        analytics,
        org_id=str(uuid4()),
        project_id=str(uuid4()),
        committed_attempts=(),
        now=NOW,
    )
    assert len(analytics.calls) == 1 + changes.MAX_DAYS_PER_PAGE
    assert page.checkpoint.phase == "sources" and page.checkpoint.after is None
    assert page.checkpoint.day == date(2026, 2, 1)
    assert page.checkpoint.last_day == date(2026, 10, 29)
    resumed = reconcile_page(
        analytics,
        org_id=str(uuid4()),
        project_id=str(uuid4()),
        committed_attempts=(),
        now=NOW,
        checkpoint=ReconciliationCheckpoint.from_dict(page.checkpoint.to_dict()),
    )
    # The resumed page does not re-read the day range; it continues at Feb 1.
    assert (
        analytics.calls[len(analytics.calls) - changes.MAX_DAYS_PER_PAGE][1][
            "day_start"
        ]
        == "2026-02-01 00:00:00"
    )
    assert resumed.checkpoint.day == date(2026, 3, 4)


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"phase": "sources", "after": ("a", "b")},
        {"phase": "dependencies", "after": ("a",)},
        {"phase": "units", "day": date(2026, 1, 1), "last_day": date(2026, 1, 2)},
        {"phase": "sources", "day": date(2026, 1, 1)},
        {"phase": "nope"},
    ],
)
def test_invalid_checkpoint_shapes_are_rejected(kwargs):
    with pytest.raises(ValueError):
        ReconciliationCheckpoint(NOW, **kwargs)


@pytest.mark.unit
def test_keyset_is_an_expanded_prunable_chain():
    clause, params = changes.keyset_after(("a", "b", "c"), (1, 2, 3), "k")
    assert clause == (
        "(a > %(k_0)s OR (a = %(k_0)s AND (b > %(k_1)s OR (b = %(k_1)s AND c > %(k_2)s))))"
    )
    assert params == {"k_0": 1, "k_1": 2, "k_2": 3}
    with pytest.raises(ValueError):
        changes.keyset_after(("a",), (1, 2), "k")


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0, -1, 10001])
def test_invalid_repair_page_size_rejected(bad):
    with pytest.raises(ValueError):
        reconcile_page(
            EmptyAnalytics(),
            org_id=str(uuid4()),
            project_id=str(uuid4()),
            committed_attempts=(),
            now=NOW,
            page_size=bad,
        )


@pytest.mark.unit
def test_dependency_payload_keeps_complete_membership_but_no_publication_identity():
    value = Dependency("t", "s", "raw-session", "session", "raw-user", "user", OLD)
    assert value.key == SourceKey("t", "s")
    assert value.payload()["canonical_user_id"] == "user"
    assert not {"generation", "attempt_id", "_version"} & value.payload().keys()


@pytest.mark.unit
@pytest.mark.parametrize("reader", ["_remap_aliases", "_resolve_ids"])
def test_large_remap_windows_use_bounded_statement_inputs(reader):
    analytics = EmptyAnalytics()
    ids = tuple(str(uuid4()) for _ in range(2 * changes.KEY_BATCH + 1))
    result = getattr(changes, reader)(
        analytics, "end_user_id_remap", ids, MetricReadBudget()
    )
    if reader == "_remap_aliases":
        assert set(result) == set(ids)
    assert [len(p["resolve_ids"]) for _, p, _ in analytics.calls] == [
        changes.KEY_BATCH,
        changes.KEY_BATCH,
        1,
    ]


@pytest.mark.unit
def test_deadline_expiring_during_last_query_does_not_return_a_proposal():
    budget = MetricReadBudget()

    class ExpiredAnalytics(EmptyAnalytics):
        def execute_ch_query(self, query, params, **kwargs):
            budget.deadline = 0
            return super().execute_ch_query(query, params, **kwargs)

    with pytest.raises(MetricChangeReadError, match="deadline exceeded"):
        changes.read(ExpiredAnalytics(), "SELECT 1", {}, budget)


@pytest.mark.integration
def test_initial_backfill_with_empty_manifest_populates_source_dependencies(store):
    seed_span(store, version=ns(OLD))
    page = reconcile_page(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(),
        now=NOW,
    )
    assert len(page.changes.dependencies) == 1
    assert page.changes.prior_dependencies == ()
    assert page.changes.dependencies[0].canonical_session_id == store.a
    with pytest.raises(MetricChangeReadError, match="backfill is incomplete"):
        require_initial_dependencies(page.checkpoint)


@pytest.mark.integration
def test_persisted_repair_resumes_and_next_sweep_catches_insert_behind_keyset(store):
    seed_span(store, span="b", version=ns(OLD))
    seed_span(store, span="z", version=ns(OLD))
    first = repair(store, page_size=1)
    assert first.checkpoint.after == (
        "SPAN",
        "",
        OLD.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S"),
        store.trace,
        "b",
    )
    assert first.checkpoint.day == OLD.date() == first.checkpoint.last_day
    # Visibility changes after this prefix was scanned. Resume the saved cursor
    # and then start a new COMPLETE pass; no date floor may hide the new old key.
    seed_span(store, span="a", version=ns(NOW - timedelta(minutes=10)))
    saved = ReconciliationCheckpoint.from_dict(
        json.loads(json.dumps(first.checkpoint.to_dict()))
    )
    resumed = complete_repair(store, saved, page_size=1)
    assert SourceKey(store.trace, "z") in {
        k for p in resumed for k in p.changes.source_keys
    }
    assert SourceKey(store.trace, "a") not in {
        k for p in resumed for k in p.changes.source_keys
    }
    next_sweep = complete_repair(store, resumed[-1].checkpoint, page_size=1)
    assert SourceKey(store.trace, "a") in {
        k for p in next_sweep for k in p.changes.source_keys
    }
    assert next_sweep[-1].checkpoint.completed_passes == 2


@pytest.mark.integration
def test_settle_rechecks_live_source_and_reschedules_a_new_arrival(store):
    seed_span(store, version=ns(NOW - timedelta(seconds=1)))
    store.client.execute(
        """INSERT INTO metric_unit_state
        (org_id, project_id, level, unit_id, last_arrival_ns, first_seen,
         due_at, generation, attempt_id, _version) VALUES""",
        [
            (
                store.org,
                store.project,
                "conversation",
                store.a,
                ns(OLD),
                OLD,
                OLD + timedelta(minutes=30),
                1,
                store.attempt,
                1,
            )
        ],
    )
    budget = MetricReadBudget()

    def latest(keys, shared_budget):
        assert shared_budget is budget
        rows = changes.read(
            store.analytics,
            """SELECT max(_version) AS last_arrival_ns, min(start_time) AS first_seen
            FROM (
                SELECT project_id, trace_id, id, trace_session_id, start_time,
                    is_deleted, _version FROM spans
                WHERE project_id = toUUID(%(project)s)
                ORDER BY _version DESC LIMIT 1 BY project_id, trace_id, id
            ) WHERE is_deleted = 0 AND trace_session_id = toUUID(%(session)s)""",
            {"project": store.project, "session": store.a},
            shared_budget,
        )
        return {
            k: UnitSnapshot(k, int(rows[0]["last_arrival_ns"]), rows[0]["first_seen"])
            for k in keys
        }

    page = settle_due_page(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(store.attempt,),
        now=NOW,
        settle_minutes=30,
        load_latest=latest,
        budget=budget,
    )
    assert page.verified == 1 and page.updates[0].status == "open"
    assert page.updates[0].state.due_at == NOW + timedelta(minutes=30, seconds=-1)
    assert budget.attempts == 3


def query_reads(store, query_id):
    store.client.execute("SYSTEM FLUSH LOGS")
    rows = store.client.execute(
        "SELECT read_rows FROM system.query_log WHERE query_id = %(q)s AND type = 'QueryFinish'",
        {"q": query_id},
    )
    assert rows, query_id
    return rows[0][0]


@pytest.mark.integration
def test_end_user_merge_after_rows_exist_invalidates_every_affected_conversation(
    store,
):
    """P7 discovery side: X has two conversations, Y one; X and Y merge later."""
    user_x, user_y = sorted((str(uuid4()), str(uuid4())))
    conversations = {
        "x-1": (str(uuid4()), user_x),
        "x-2": (str(uuid4()), user_x),
        "y-1": (str(uuid4()), user_y),
    }
    for span, (session, user) in conversations.items():
        seed_span(store, span=span, session=session, user=user, version=ns(OLD))
        seed_dependency(store, span=span, session=session, user=user)
    unrelated = str(uuid4())
    seed_span(
        store, span="other", session=unrelated, user=str(uuid4()), version=ns(OLD)
    )
    seed_dependency(store, span="other", session=unrelated, user=str(uuid4()))
    merged = str(uuid4())
    store.client.execute(
        "INSERT INTO end_user_id_remap (old_id, new_id, version) VALUES",
        [
            (user_x, merged, NOW - timedelta(seconds=5)),
            (user_y, merged, NOW - timedelta(seconds=5)),
        ],
    )
    result = discover_changes(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(store.attempt,),
        watermark_ns=ns(NOW),
        eval_watermark_ns=ns(NOW),
        remap_cursor_ns=ns(NOW - timedelta(minutes=1)),
        ceiling=NOW,
    )
    assert {u.unit_id for u in result.units} == {s for s, _ in conversations.values()}
    assert set(result.user_ids) == {user_x, user_y}
    # The survivor-old id (the smaller old id) groups all three afterwards.
    assert {d.canonical_user_id for d in result.dependencies} == {user_x}
    assert {d.canonical_user_id for d in result.prior_dependencies} == {
        user_x,
        user_y,
    }
    assert SourceKey(store.trace, "other") not in result.source_keys


@pytest.mark.integration
def test_changed_span_with_superseded_raw_session_nominates_only_the_survivor(store):
    survivor, alias = sorted((str(uuid4()), str(uuid4())))
    group = str(uuid4())
    store.client.execute(
        "INSERT INTO trace_session_id_remap (old_id, new_id, version) VALUES",
        [(survivor, group, OLD), (alias, group, OLD)],
    )
    seed_span(store, session=alias, version=ns(NOW - timedelta(seconds=10)))
    result = discover(store)
    assert result.units == (UnitKey("conversation", survivor),)
    assert result.dependencies[0].raw_session_id == alias
    assert result.dependencies[0].canonical_session_id == survivor


@pytest.mark.integration
def test_foreign_org_rows_never_become_latest_membership(store):
    seed_span(store, org=str(uuid4()), version=ns(NOW - timedelta(seconds=10)))
    result = discover(store)
    assert result.source_keys == (SourceKey(store.trace, store.span),)
    # The physical row may still nominate a unit (a safe superset), but it can
    # never become a membership or preimage of this org's project.
    assert result.dependencies == () and result.prior_dependencies == ()
    assert result.user_ids == ()


@pytest.mark.integration
def test_settle_nomination_uses_both_minmax_indexes(store):
    rows = [
        (
            store.org,
            store.project,
            "conversation",
            f"u-{i}",
            OLD + timedelta(minutes=i),
            i % 2,
            store.attempt,
            1,
        )
        for i in range(500)
    ]
    store.client.execute(
        """INSERT INTO metric_unit_state
        (org_id, project_id, level, unit_id, due_at, is_deleted, attempt_id, _version) VALUES""",
        rows,
    )
    captured = []

    class Capture(Analytics):
        def execute_ch_query(
            self,
            query,
            params,
            *,
            timeout_ms,
            settings,
            server_execution_cap_ms=None,
        ):
            captured.append((query, params))
            return super().execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=settings,
                server_execution_cap_ms=server_execution_cap_ms,
            )

    settle_due_page(
        Capture(store.client),
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(store.attempt,),
        now=NOW,
        settle_minutes=30,
        load_latest=lambda keys, budget: {
            k: UnitSnapshot(k, ns(OLD), OLD) for k in keys
        },
        page_size=10,
    )
    nominate, params = captured[0]
    plan = "\n".join(
        row[0]
        for row in store.client.execute("EXPLAIN indexes = 1 " + nominate, params)
    )
    assert "ix_due" in plan and "ix_deleted" in plan, plan


@pytest.mark.integration
def test_outage_backlog_settles_once_and_published_tombstones_stay_settled(store):
    """P5 live: drain, publish as a new attempt, re-run under both attempts."""
    keys = [UnitKey("conversation", f"overdue-{i:02d}") for i in range(9)]
    renewed = UnitKey("conversation", "renewed")
    store.client.execute(
        """INSERT INTO metric_unit_state
        (org_id, project_id, level, unit_id, last_arrival_ns, first_seen, due_at,
         generation, attempt_id, _version) VALUES""",
        [
            (
                store.org,
                store.project,
                k.level,
                k.unit_id,
                ns(OLD),
                OLD,
                NOW - timedelta(days=4),
                1,
                store.attempt,
                1,
            )
            for k in [*keys, renewed]
        ],
    )
    fresh = ns(NOW - timedelta(minutes=2))

    def latest(ids, budget):
        return {
            k: UnitSnapshot(k, fresh if k == renewed else ns(OLD), OLD) for k in ids
        }

    budget, after, updates = MetricReadBudget(), None, []
    while True:
        page = settle_due_page(
            store.analytics,
            org_id=store.org,
            project_id=store.project,
            committed_attempts=(store.attempt,),
            now=NOW,
            settle_minutes=30,
            load_latest=latest,
            after=after,
            page_size=3,
            budget=budget,
        )
        updates.extend(page.updates)
        if page.exhausted:
            break
        after = page.after
    by_key = {u.state.key: u for u in updates}
    assert len(updates) == len(by_key) == 10
    assert {k for k, u in by_key.items() if u.status == "settled"} == set(keys)
    assert by_key[renewed].status == "open"
    assert by_key[renewed].state.due_at == NOW + timedelta(minutes=28)
    publish = str(uuid4())
    store.client.execute(
        """INSERT INTO metric_unit_state
        (org_id, project_id, level, unit_id, last_arrival_ns, first_seen, due_at,
         reopened, is_deleted, generation, attempt_id, _version) VALUES""",
        [
            (
                store.org,
                store.project,
                u.state.key.level,
                u.state.key.unit_id,
                u.state.last_arrival_ns,
                u.state.first_seen,
                u.state.due_at,
                int(u.state.reopened),
                int(u.state.is_deleted),
                2,
                publish,
                2,
            )
            for u in updates
        ],
    )

    def guarded(ids, budget, loaded):
        loaded.extend(ids)
        return latest(ids, budget)

    for tick in (NOW + timedelta(minutes=1), NOW + timedelta(days=3)):
        loaded = []
        again = settle_due_page(
            store.analytics,
            org_id=store.org,
            project_id=store.project,
            committed_attempts=(store.attempt, publish),
            now=tick,
            settle_minutes=30,
            load_latest=lambda ids, budget, seen=loaded: guarded(ids, budget, seen),
        )
        # Old generation-1 open rows are still nominated (DCM2 superset), but
        # the latest committed state is a tombstone: none is loaded or resettled.
        assert again.nominated >= len(keys)
        if tick < by_key[renewed].state.due_at:
            assert again.verified == 0 and loaded == [] and again.updates == ()
        else:
            assert loaded == [renewed]
            assert [u.status for u in again.updates] == ["settled"]


@pytest.mark.integration
def test_complete_sweep_covers_every_key_and_each_page_reads_about_one_page(store):
    total, page_size = 0, 50
    for day in range(3):
        start = OLD + timedelta(days=day)
        rows = []
        for i in range(700):
            rows.append(
                (
                    store.project,
                    None,
                    ("LLM", "SPAN", "TOOL")[i % 3],
                    f"trace-{day}-{i // 7:03d}",
                    f"span-{i:03d}",
                    start + timedelta(seconds=i * 7),
                    store.a,
                    store.user_a,
                    ns(OLD),
                )
            )
        store.client.execute(
            "INSERT INTO spans (project_id, org_id, observation_type, trace_id, id, start_time, trace_session_id, end_user_id, _version) VALUES",
            rows,
        )
        total += len(rows)
    # A second physical version of one span must not duplicate or stall it.
    seed_span(store, trace="trace-0-000", span="span-000", version=ns(NOW))
    store.client.execute("OPTIMIZE TABLE spans FINAL")
    store.analytics.query_ids.clear()
    store.analytics.calls.clear()
    pages = complete_repair(store, page_size=page_size)
    swept = {k for p in pages for k in p.changes.source_keys}
    assert len(swept) == total
    assert pages[-1].checkpoint.scanned >= total
    source_pages = [
        query_id
        for query_id, (query, _, _) in zip(
            store.analytics.query_ids, store.analytics.calls, strict=True
        )
        if "toStartOfHour(start_time) AS hour" in query
    ]
    reads = [query_reads(store, query_id) for query_id in source_pages]
    group = store.client.execute(
        """SELECT max(c) FROM (SELECT count() AS c FROM spans
        WHERE project_id = toUUID(%(p)s)
        GROUP BY toDate(start_time), observation_type, service_name,
            toStartOfHour(start_time))""",
        {"p": store.project},
    )[0][0]
    # Old design: every page re-read the whole project (probe: 133,696 rows
    # per page for a 133,333-row project). Here a page reads at most its own
    # rows plus the cursor's (type, service, hour) group, in whole granules:
    # trace_id/id are sorting-key columns but not primary-index columns.
    granule = 64
    assert max(reads) <= page_size + group + 2 * granule, (reads, group)
    assert 4 * max(reads) < total, (reads, total)
    print("B04 sweep reads", max(reads), sum(reads), "rows", total, "group", group)


@pytest.mark.integration
def test_dependency_phase_keeps_only_absent_deleted_or_moved_keys(store):
    seed_span(store, span="same", version=ns(OLD))
    seed_dependency(store, span="same")
    seed_span(store, span="moved", session=store.b, version=ns(OLD))
    seed_dependency(store, span="moved")
    seed_dependency(store, span="gone")
    seed_span(store, span="deleted-now", deleted=1, version=ns(OLD))
    seed_dependency(store, span="deleted-now")
    seed_span(store, span="deleted-before", deleted=1, version=ns(OLD))
    seed_dependency(store, span="deleted-before", deleted=1)
    cp = ReconciliationCheckpoint(NOW, phase="dependencies")
    page = repair(store, cp)
    # Identical live or identical deleted states were nominated by the source
    # phase (their physical rows exist); absent or diverging keys are added.
    assert {k.span_id for k in page.changes.source_keys} == {
        "moved",
        "gone",
        "deleted-now",
    }
    assert {d.span_id: d.is_deleted for d in page.changes.dependencies} == {
        "moved": False,
        "gone": True,
        "deleted-now": True,
    }
    assert page.checkpoint.phase == "units"
    assert set(page.changes.units) == {
        UnitKey("conversation", store.a),
        UnitKey("conversation", store.b),
    }
