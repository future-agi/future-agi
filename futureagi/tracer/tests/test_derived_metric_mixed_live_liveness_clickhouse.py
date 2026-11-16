# ruff: noqa: F811 -- shared isolated ClickHouse / PostgreSQL fixtures
"""The live mixed-grain workload must publish history, not just checkpoints.

Runs against a disposable native ClickHouse with its analyzer enabled, once on
25.3.14.14 (the live review stack) and once on 25.8.33.6. The cloud runner
provides the tiered storage policy used by the real spans DDL. Unlike the
conversation-only liveness fixture, this creates the actual two review recipes
through save_definition, including the 5,600-row span metric whose initial
discovery exceeds the scheduled writer's 1,000-item admission cap.

No clocks, deadlines, page sizes, SQL results, or retirement planners are faked.
The real admission retains its 540-second cooperative deadline, native 60-second
receive timeout, and per-query remaining-budget reduction. The 45-second product
budget is asserted AFTER a reservation unwinds, so it cannot itself manufacture
the transport TimeoutError this regression is intended to catch. Every native
statement's generated SQL is measured and bounded independently of the
proposal's size; the activated KPIs must equal an oracle computed from the seed.

Where the sweep's page boundaries fall among the seeded quarters depends on the
wall clock, so a page can emit whole-quarter spans of a conversation just past
its boundary (seed_end is printed). The deterministic regression for that case
is test_finite_mixed_refresh_computes_an_unowned_quarter_mate in the retention
suite.
"""

import json
from datetime import UTC, datetime, timedelta
from time import monotonic, time_ns
from uuid import NAMESPACE_DNS, NAMESPACE_URL, uuid5

import pytest

from tracer.models.derived_metric import (
    MetricProjectRefresh,
    MetricReadManifest,
    MetricRefreshAttempt,
)
from tracer.services.clickhouse import v2
from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements
from tracer.services.derived_metrics import admission, reads
from tracer.services.derived_metrics.publication import (
    MAX_BATCH_ROWS,
    MAX_COMPACTION_BYTES,
    WRITE_SETTINGS,
    ClickHousePublicationStore,
    committed_rows_select,
)
from tracer.services.derived_metrics.service import save_definition
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests.test_derived_metric_clickhouse import SCHEMA
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    metric_ch,
    publication_store,
)

RESERVATION_BUDGET_SECONDS = 45
MAX_RESERVATIONS = 12
RETENTION_DAYS = 90
# A statement that carries one frozen batch inline (an INSERT of a frozen page,
# or a capture of one) is bounded by the hex of the largest page cap. Every
# other statement names its relations: it must stay small whatever the
# proposal's size. The live failure generated 16-25 MB statements.
INLINE_BATCH_SQL_BYTES = 2 * MAX_COMPACTION_BYTES + 1024 * 1024
OTHER_SQL_BYTES = 1024 * 1024
INLINE_MARKERS = ("format(RowBinary", "format(JSONEachRow")


def live_current_retention_policy(organization_id):
    """The same authoritative fixture policy for the native writer and readers."""
    return RETENTION_DAYS


def _definitions(project, workspace, user):
    specs = [
        (
            "Resolution share",
            "share",
            "conversation",
            {
                "alias": "resolved",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "value_type": "number",
                "reducer": "latest",
                "ordering": "sentence",
            },
            [{"alias": "resolved", "operator": "eq", "value": 1}],
        ),
        (
            "Average response latency",
            "average",
            "span",
            {
                "alias": "latency",
                "source": "span",
                "field": "latency_ms",
                "value_type": "number",
                "reducer": "mean",
                "ordering": "sentence",
            },
            [],
        ),
    ]
    definitions = []
    for name, kind, level, source, conditions in specs:
        metric = save_definition(
            {
                "name": name,
                "description": "Mixed-grain live review regression fixture",
                "kind": kind,
                "level": level,
                "selection_unit": "span" if level == "span" else "session",
                "weighting": "none",
                "unit": "ms" if level == "span" else "",
                "value_format": "duration" if level == "span" else "percent",
                "inputs": [source],
                "field_spec": {
                    "inputs": [source],
                    "value_alias": source["alias"],
                    "conditions": conditions,
                },
                "filters": [],
            },
            project,
            workspace,
            user,
        )
        definitions.append(metric.versions.get(version=metric.current_version))
    return definitions


def _seed_live_shape(store, project, organization, end):
    # Production source schema, not the simplified conversation-only fixture.
    # TTL removal mirrors additive schema 020; current policy is enforced by the
    # retirement planner. No retention transition occurs during this test.
    store.execute("DROP TABLE spans")
    for filename in ("002_spans_v2.sql", "013_attributes_extra_as_string.sql"):
        for sql in split_statements((SCHEMA / filename).read_text()):
            store.execute(sql)
    store.execute("ALTER TABLE spans REMOVE TTL")
    start = end - timedelta(days=7)
    rows, latencies, resolution = [], [], {}
    version = time_ns()
    for trace_number in range(2800):
        conversation, turn = divmod(trace_number, 8)
        day, day_offset = divmod(conversation, 50)
        trace = uuid5(NAMESPACE_URL, f"review-1110-{trace_number}").hex
        root = uuid5(NAMESPACE_DNS, f"root-review-{trace_number}").hex[:16]
        child = uuid5(NAMESPACE_DNS, f"child-review-{trace_number}").hex[:16]
        session = uuid5(NAMESPACE_URL, f"mixed-live-conversation-{conversation:03d}")
        user = uuid5(NAMESPACE_URL, f"mixed-live-contact-{conversation // 2:03d}")
        at = start + timedelta(
            days=day, hours=1, milliseconds=day_offset * 1_000_000, minutes=turn
        )
        duration = 400 + (trace_number * 137) % 4200
        resolved = int(conversation % 5 != 0)
        resolution[str(session)] = resolved
        for span, parent, name, observation_type, latency in (
            (
                root,
                "",
                ("support.answer", "billing.resolve", "order.lookup", "account.assist")[
                    conversation % 4
                ],
                "chain",
                duration,
            ),
            (child, root, "llm.response", "llm", max(100, duration - 160)),
        ):
            latencies.append(latency)
            # A span arrives as it ends; the newest is ~9 hours before "end",
            # so every conversation is settled when the KPI is read.
            arrived = at + timedelta(milliseconds=latency, minutes=1)
            rows.append(
                (
                    str(project.id),
                    str(organization),
                    span,
                    parent,
                    trace,
                    str(session),
                    str(user),
                    at,
                    at + timedelta(milliseconds=latency),
                    arrived,
                    arrived,
                    version + len(rows),
                    name,
                    observation_type,
                    "Support quality review",
                    latency,
                    "gpt-4o" if conversation % 2 == 0 else "gpt-4o-mini",
                    "openai",
                    "OK",
                    {
                        "resolved": float(resolved),
                        "escalated": float(not resolved),
                        "csat": float(3 + conversation % 3),
                    },
                    {
                        "region": ("North America", "Europe", "Asia Pacific")[
                            conversation % 3
                        ],
                        "plan": ("Enterprise", "Growth", "Starter")[conversation % 3],
                        "intent": ("billing", "delivery", "account", "product")[
                            conversation % 4
                        ],
                    },
                )
            )
    store.client.execute(
        "INSERT INTO spans (project_id, org_id, id, parent_span_id, trace_id, "
        "trace_session_id, end_user_id, start_time, end_time, created_at, updated_at, "
        "_version, name, observation_type, service_name, latency_ms, model, provider, "
        "status, attrs_number, attrs_string) VALUES",
        rows,
    )
    assert store.execute(
        "SELECT count(), uniqExact(trace_session_id), uniqExact(trace_id), "
        "uniqExact(end_user_id), countIf(attrs_number['resolved'] = 1) FROM spans"
    ) == [(5600, 350, 2800, 175, 4480)]
    oracle = {
        # Conversation share: latest resolved per conversation (constant within
        # one), over conversations; span average: mean latency over all spans.
        "conversation": (
            len(resolution),
            sum(resolution.values()) / len(resolution),
        ),
        "span": (len(latencies), sum(latencies) / len(latencies)),
    }
    # A second oracle straight from raw telemetry, never the metric pipeline.
    raw = store.execute(
        "SELECT (SELECT count() FROM (SELECT DISTINCT trace_session_id FROM spans)), "
        "(SELECT avg(r) FROM (SELECT argMax(attrs_number['resolved'], start_time) AS r "
        "FROM spans GROUP BY trace_session_id)), "
        "(SELECT count() FROM spans), (SELECT avg(latency_ms) FROM spans)"
    )[0]
    assert raw[0] == oracle["conversation"][0] and raw[2] == oracle["span"][0]
    assert raw[1] == pytest.approx(oracle["conversation"][1])
    assert raw[3] == pytest.approx(oracle["span"][1])
    return start, oracle


def _snapshot(project, definitions):
    state = MetricProjectRefresh.for_project(project.id)
    return {
        "head": str(state.head_manifest_id),
        "backfill_last_progress_at": state.backfill_last_progress_at,
        "pending_backfill": state.pending_backfill,
        "generation": state.generation_high_water,
        "fencing_epoch": state.fencing_epoch,
        "consecutive_failures": state.consecutive_failures,
        "last_error_code": state.last_error_code,
        "lease_token": str(state.lease_token),
        "definitions": [
            {
                "name": definition.metric.name,
                "level": definition.level,
                "backfill_status": definition.backfill_status,
                "days_done": definition.backfill_days_done,
                "days_total": definition.backfill_days_total,
                "activated_at": definition.activated_at,
            }
            for definition in definitions
        ],
        "attempts": list(
            MetricRefreshAttempt.objects.filter(project=project)
            .order_by("generation")
            .values("generation", "purpose", "state", "abort_reason", "source_ceilings")
        ),
    }


@pytest.mark.django_db(transaction=True)
def test_live_mixed_span_and_conversation_backfill_publishes_within_budget(
    publication_store, workspace, observe_project, user, settings, monkeypatch
):
    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    # Native writer and reader both resolve the real importable fixture policy.
    settings.METRICS_RETENTION_DAYS_PROVIDER = (
        f"{__name__}.live_current_retention_policy"
    )
    store = publication_store
    native = store.client.connection
    monkeypatch.setattr(
        v2,
        "get_v2_config",
        lambda: {
            "host": native.host,
            "tcp_port": native.port,
            "user": native.user,
            "password": native.password,
            "database": native.database,
        },
    )
    end = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    start, oracle = _seed_live_shape(
        store, observe_project, workspace.organization_id, end
    )
    definitions = _definitions(observe_project, workspace, user)
    assert {d.level for d in definitions} == {"span", "conversation"}
    assert MAX_BATCH_ROWS == 500
    assert WRITE_SETTINGS["max_threads"] == 2
    assert WRITE_SETTINGS["max_execution_time"] == 60
    assert WRITE_SETTINGS["max_memory_usage"] == 512 * 1024 * 1024
    version = store.execute("SELECT version(), getSetting('enable_analyzer')")[0]
    assert version[1] == 1
    print(
        "MIXED_LIVE_CONFIG "
        + json.dumps(
            {
                "clickhouse": version,
                "data": {
                    "conversations": 350,
                    "traces": 2800,
                    "spans": 5600,
                    "days": 7,
                    "seed_end": end,
                },
                "retention_days": RETENTION_DAYS,
                "write_settings": WRITE_SETTINGS,
                "native_receive_timeout_seconds": 60,
                "cooperative_deadline_seconds": 540,
                "reservation_liveness_seconds": RESERVATION_BUDGET_SECONDS,
                "max_reservations": MAX_RESERVATIONS,
            },
            default=str,
        ),
        flush=True,
    )
    execute = ClickHousePublicationStore.execute
    query = admission.NativeMetricAnalytics.execute_ch_query
    observations, sizes = [], {"inline": [0, ""], "other": [0, ""]}

    def measure(sql, params):
        # Bound SQL length: the text plus every substituted parameter value.
        size = len(sql) + sum(
            sql.count(f"%({key})s") * len(str(value))
            for key, value in (params or {}).items()
        )
        kind = "inline" if any(m in sql for m in INLINE_MARKERS) else "other"
        if size > sizes[kind][0]:
            sizes[kind] = [size, sql[:160]]
        return size

    def observe(run, sql, params):
        bound_size = measure(sql, params)
        began, error = monotonic(), None
        try:
            return run()
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            elapsed = monotonic() - began
            if elapsed > 0.5 or bound_size > 1_000_000 or error:
                observation = {
                    "seconds": round(elapsed, 3),
                    "estimated_bound_sql_bytes": bound_size,
                    "error": error,
                    "query_prefix": sql[:160],
                }
                observations.append(observation)
                print("MIXED_LIVE_QUERY " + json.dumps(observation), flush=True)

    def observed_execute(self, sql, params=None, **kwargs):
        return observe(lambda: execute(self, sql, params, **kwargs), sql, params)

    def observed_query(self, sql, params, **kwargs):
        return observe(lambda: query(self, sql, params, **kwargs), sql, params)

    monkeypatch.setattr(ClickHousePublicationStore, "execute", observed_execute)
    monkeypatch.setattr(
        admission.NativeMetricAnalytics, "execute_ch_query", observed_query
    )
    for reservation_number in range(1, MAX_RESERVATIONS + 1):
        (reservation,) = admission.reserve_projects()
        began, error = monotonic(), None
        try:
            admission.execute_reservation(reservation.payload())
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:500]}"
        elapsed = monotonic() - began
        for definition in definitions:
            definition.refresh_from_db()
        snapshot = _snapshot(observe_project, definitions)
        snapshot.update(reservation=reservation_number, seconds=elapsed, error=error)
        print("MIXED_LIVE_RESERVATION " + json.dumps(snapshot, default=str), flush=True)
        assert error is None and elapsed <= RESERVATION_BUDGET_SECONDS, (
            "Mixed span/conversation initial backfill violated the reservation "
            f"liveness contract: {json.dumps(snapshot, default=str)}; "
            f"slow_queries={json.dumps(observations[-5:])}"
        )
        assert MetricProjectRefresh.for_project(observe_project.id).lease_token is None
        if all(d.backfill_status == "complete" for d in definitions):
            break
    else:
        pytest.fail(
            "Mixed span/conversation backfill never activated both definitions "
            f"within {MAX_RESERVATIONS} reservations: "
            + json.dumps(_snapshot(observe_project, definitions), default=str)
        )

    attempts = list(
        MetricRefreshAttempt.objects.filter(project=observe_project).values_list(
            "purpose", "state", "abort_reason", "source_ceilings"
        )
    )
    # The live symptoms: initial history diverted into repair pages, each draft
    # replaced (aborted as retention_replan) by a full privacy checkpoint even
    # though no logical expiry occurred. History is backfill; with nothing
    # expiring, the integrated retention plan publishes the draft itself.
    assert not [a for a in attempts if a[0] == "repair"], attempts
    assert not [a for a in attempts if a[2] == "retention_replan"], attempts
    assert not [a for a in attempts if a[3].get("logical_expiry")], attempts
    print("MIXED_LIVE_SQL_BOUNDS " + json.dumps(sizes), flush=True)
    assert sizes["inline"][0] <= INLINE_BATCH_SQL_BYTES, sizes
    assert sizes["other"][0] <= OTHER_SQL_BYTES, sizes
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.backfill_last_progress_at and not state.pending_backfill
    # Arrival cursors were consumed (only) by the completing publication.
    assert state.watermark_ns and state.eval_watermark_ns and state.remap_cursor_ns
    head = MetricReadManifest.objects.get(id=state.head_manifest_id)
    fields, params = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=workspace.organization_id,
        project_id=observe_project.id,
    )
    manifest_fields = dict(
        store.execute(
            f"SELECT toString(field_id), count() FROM ({fields}) "
            "WHERE is_deleted = 0 GROUP BY field_id",
            params,
        )
    )
    for definition in definitions:
        expected_count, expected_value = oracle[definition.level]
        definition.metric.refresh_from_db()
        assert definition.activated_at and definition.backfill_status == "complete"
        assert definition.backfill_days_done == definition.backfill_days_total
        assert definition.metric.active_version_id == definition.id
        assert manifest_fields[str(definition.metric_id)] == expected_count
        kpi = reads.read_kpi(
            definition.metric,
            {"window_start": start, "window_end": end},
            store=store,
        )
        print(
            "MIXED_LIVE_KPI "
            + json.dumps(
                {
                    "name": definition.metric.name,
                    "level": definition.level,
                    "eligible": kpi["current"]["eligible_count"],
                    "value": kpi["current"]["value"],
                    "oracle": [expected_count, expected_value],
                },
                default=str,
            ),
            flush=True,
        )
        assert kpi["current"]["eligible_count"] == expected_count
        assert kpi["current"]["value"] == pytest.approx(expected_value)
