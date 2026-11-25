# ruff: noqa: F811 -- shared isolated ClickHouse and PG fixtures
"""Real bucket checkpoint/delta equivalence and end-to-end pinned HTTP readers."""

import gc
import json
import sys
import threading
from contextlib import ExitStack, contextmanager, suppress
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from inspect import signature
from pathlib import Path
from time import perf_counter, perf_counter_ns, process_time_ns, thread_time_ns
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import connection
from django.db.models import QuerySet

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricVersion,
    MetricGenerationPin,
)
from tracer.serializers.derived_metric import (
    MetricDriversSerializer,
    MetricKpiSerializer,
    MetricTrendSerializer,
)
from tracer.services.derived_metrics import reads
from tracer.services.derived_metrics.buckets import live_parts_select
from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.parts import (
    SCALAR_PART_COLUMNS,
    stratum_parts_select,
)
from tracer.services.derived_metrics.queries import Scope, aggregate_select
from tracer.services.derived_metrics.storage import BUCKET_TOTALS, FIELD_VALUES
from tracer.tests.test_derived_metric_parts import assert_same, rows_as_dicts
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    field_query,
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    definition_for,
    seed_span_revision,
)
from tracer.tests.test_derived_metric_uncertainty import insert_units, unit

pytestmark = pytest.mark.usefixtures("enabled_metrics")
AT = datetime(2026, 1, 10, 12, tzinfo=UTC)
WINDOW = {
    "window_start": AT,
    "window_end": AT + timedelta(hours=1),
    "rounding": "Rounded to 15 minutes",
}


def case(text="mean(y)", kind="custom"):
    scope = Scope(uuid4(), uuid4(), uuid4())
    metric = SimpleNamespace(
        id=uuid4(),
        organization_id=scope.organization,
        workspace_id=scope.workspace,
        project_id=scope.project,
    )
    definition = SimpleNamespace(
        id=uuid4(),
        metric_id=metric.id,
        version=1,
        kind=kind,
        level="conversation",
        formula=text,
        inputs=[
            {
                "alias": a,
                "source": "span",
                "field": "attribute_number",
                "attribute_key": a,
                "reducer": "latest",
            }
            for a in ("y", "x")
        ],
        field_spec={},
        weighting="inverse_probability",
        cohort_key="",
        group_dims=[],
        unit="",
        value_format="number",
        selection_unit="session",
    )
    attempt = uuid4()
    manifest = SimpleNamespace(
        project_id=scope.project,
        checkpoint_attempt_id=attempt,
        allowed_attempts=lambda: [(1, attempt)],
    )
    return scope, metric, definition, attempt, manifest


def stage_parts(
    store,
    scope,
    metric,
    definition,
    attempt,
    units,
    *,
    generation=1,
    table="metric_bucket_totals_checkpoint",
    start=AT,
    scope_key=None,
    rollup="overall",
):
    snapshot = uuid4()
    insert_units(store.client, scope, snapshot, units)
    formula = compile_formula(definition.formula, ["y", "x"])
    source = "SELECT *, toDateTime(%(bucket)s, 'UTC') AS bucket_start FROM derived_metric_inputs WHERE snapshot_id = %(snapshot)s"
    if scope_key is not None:
        source = f"SELECT * REPLACE(%(scope_key)s AS sampling_scope) FROM ({source})"
    params = {
        "snapshot": str(snapshot),
        "bucket": start,
        **formula.parameters,
        "scope_key": scope_key,
    }
    parts = stratum_parts_select(
        source, formula, bucket_column="bucket_start", rollup=rollup
    )
    columns = (
        "org_id",
        "workspace_id",
        "project_id",
        "metric_id",
        "metric_version",
        "generation",
        "attempt_id",
        "version",
        "bucket_start",
        "group_key",
        "scope_key",
        "stratum_key",
        *SCALAR_PART_COLUMNS,
    )
    params.update(
        org=scope.organization,
        workspace=scope.workspace,
        project=scope.project,
        metric=metric.id,
        attempt=attempt,
        generation=generation,
    )
    store.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) SELECT %(org)s, %(workspace)s, %(project)s, %(metric)s, 1, %(generation)s, %(attempt)s, %(generation)s, bucket_start, group_key, scope_key, stratum_key, {', '.join(SCALAR_PART_COLUMNS)} FROM ({parts})",
        params,
    )
    return rows_as_dicts(
        store.client, aggregate_select(source, formula, rollup="overall"), params
    )[0]


@pytest.mark.parametrize("text", ["sum(y)", "ratio(y, x)", "mean(y > 0)"])
@pytest.mark.parametrize("design", ["closed", "live", "unweighted"])
def test_checkpoint_kpi_equals_direct_aggregate_with_all_uncertainty(
    publication_store, text, design
):
    scope, metric, definition, attempt, manifest = case(text)
    units = [
        unit(
            y,
            1 if design == "unweighted" else 0.5,
            design=design,
            population=24 if design == "closed" else None,
            x=i + 1,
        )
        for i, y in enumerate(
            [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0, 3.0, 4.0, 0.0, 1.0]
        )
    ]
    direct = stage_parts(publication_store, scope, metric, definition, attempt, units)
    actual = reads._aggregate(publication_store, metric, definition, manifest, WINDOW)
    assert_same(actual, direct)
    rendered = reads.point(actual, definition)
    assert rendered["value_format"] == "number"
    assert rendered["eligible_count"] == 12
    assert rendered["rate_eligible"] is (text == "mean(y > 0)")
    if text == "mean(y > 0)":
        assert actual["ci_status"] == "ok" and actual["ci_interval"] == "wilson"
        assert actual["ci_high"] - actual["value"] != pytest.approx(
            actual["value"] - actual["ci_low"]
        )


def test_heterogeneous_weights_and_missing_values_keep_real_counts(publication_store):
    scope, metric, definition, attempt, manifest = case("ratio(y, x)")
    units = [
        unit(1.0, 0.25, design="live", x=7),
        unit(3.0, 0.5, design="live", x=2),
        unit(None, 0.5, design="live", x=9),
    ]
    direct = stage_parts(publication_store, scope, metric, definition, attempt, units)
    actual = reads._aggregate(publication_store, metric, definition, manifest, WINDOW)
    assert_same(actual, direct)
    assert actual["value"] == pytest.approx(10 / 32)
    assert actual["missing_count"] == 1
    assert actual["ci_status"] == "not_estimable"
    assert actual["ci_reason"]


def test_pending_delta_is_invisible_then_replaces_not_adds_and_tombstone_wins(
    publication_store,
):
    scope, metric, definition, base, manifest = case("sum(y)")
    stage_parts(
        publication_store, scope, metric, definition, base, [unit(4, 1, design="live")]
    )
    pending = uuid4()
    stage_parts(
        publication_store,
        scope,
        metric,
        definition,
        pending,
        [unit(90, 1, design="live")],
        generation=2,
        table="metric_bucket_totals",
    )
    assert (
        reads._aggregate(publication_store, metric, definition, manifest, WINDOW)[
            "value"
        ]
        == 4
    )
    manifest.allowed_attempts = lambda: [(1, base), (2, pending)]
    assert (
        reads._aggregate(publication_store, metric, definition, manifest, WINDOW)[
            "value"
        ]
        == 90
    )
    publication_store.execute(
        "INSERT INTO metric_bucket_totals SELECT * REPLACE(toUInt8(1) AS is_deleted, toUInt64(3) AS version) FROM metric_bucket_totals"
    )
    empty = reads._aggregate(publication_store, metric, definition, manifest, WINDOW)
    assert empty["status"] == "no_data" and empty["value"] is None


def test_mixed_scopes_across_buckets_are_rejected_not_averaged(publication_store):
    scope, metric, definition, base, manifest = case("mean(y)")
    stage_parts(
        publication_store,
        scope,
        metric,
        definition,
        base,
        [unit(4, 0.5, design="live")],
        scope_key="A",
    )
    stage_parts(
        publication_store,
        scope,
        metric,
        definition,
        base,
        [unit(7, 0.5, design="live")],
        start=AT + timedelta(minutes=15),
        scope_key="B",
    )
    row = reads._aggregate(publication_store, metric, definition, manifest, WINDOW)
    assert row["value"] is None and row["status"] == "invalid_probability"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("level", ["conversation", "span"])
def test_real_refresh_pin_kpi_previous_trend_drivers_and_unit_values(
    live_writer, workspace, observe_project, level, auth_client, monkeypatch
):
    from tracer.services.derived_metrics import source_retention
    from tracer.services.derived_metrics.retention import retention_cutoff

    writer = live_writer
    # Match the fixture writer's policy at the authoritative reader boundary;
    # the fixed January window is retained here, not exempted from retention.
    monkeypatch.setattr(
        source_retention,
        "current_cutoff",
        lambda organization_id, now=None: retention_cutoff(
            writer.retention_days(), now=now or writer.lease.clock()
        ),
    )
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    initial = definition_for(writer, workspace, observe_project)
    definition = initial
    if level == "span":
        definition = DerivedMetricVersion.objects.create(
            metric=initial.metric,
            version=2,
            **{**initial.semantics(), "level": "span", "selection_unit": "span"},
        )
    sessions = [uuid4() for _ in range(5)]
    traces = [uuid4() for _ in range(5)]
    for index, value in enumerate([2.0, 4.0, 12.0, 99.0, 10.0]):
        at = AT + timedelta(minutes=5) if index != 4 else AT - timedelta(minutes=55)
        # Keep the deliberately open unit in a separate quarter. Its entire
        # bucket is nonfinal; the first bucket's independent truth is (2+4+12)/3.
        if index == 3:
            at = AT + timedelta(minutes=20)
        seed_span_revision(
            writer,
            scope,
            traces[index],
            f"s{index}",
            sessions[index],
            at,
            value,
            revision=1,
            ended=index != 3,
            arrival=at if index != 3 else datetime.now(UTC),
        )
    from tracer.services.derived_metrics.changes import ChangeBatch, SourceKey, UnitKey

    change = ChangeBatch(
        tuple(SourceKey(str(trace), f"s{i}") for i, trace in enumerate(traces)),
        tuple(UnitKey("conversation", str(s)) for s in sessions),
        (),
        (),
        (),
        {"watermark_ns": int((AT + timedelta(hours=2)).timestamp()) * 10**9},
    )
    head = writer.refresh(
        change,
        [definition],
        scope,
        start=AT - timedelta(hours=1),
        end=AT + timedelta(hours=1),
        as_of=datetime.now(UTC),
    )
    DerivedMetricVersion.objects.filter(pk=definition.pk).update(
        backfill_status="complete"
    )
    DerivedMetric.activate_version(
        definition.metric_id, definition.id, expected_active_id=None
    )
    metric = DerivedMetric.objects.select_related("project", "active_version").get(
        pk=definition.metric_id
    )
    kpi = reads.read_kpi(metric, WINDOW, store=writer.store)
    assert kpi["current"]["value"] == pytest.approx(6.0)
    assert kpi["current"]["eligible_count"] == 3
    assert kpi["partial"] is True
    assert kpi["current"]["unit_noun"] == (
        "spans" if level == "span" else "conversations"
    )
    assert kpi["previous"]["point_status"] == "ok"
    assert kpi["previous"]["value"] == 10.0 and kpi["delta"] == -4.0
    assert kpi["delta_uncertainty"]["reason"] == "paired_covariance_unavailable"
    assert kpi["provenance"]["manifest_id"] == head.id
    assert kpi["provenance"]["updated_at"] == head.published_by.committed_at
    contract = MetricKpiSerializer(data=kpi)
    assert contract.is_valid(), contract.errors
    trend = reads.read_trend(metric, {**WINDOW, "points": 4}, store=writer.store)
    assert len(trend["results"]) == 4
    assert [r["value"] for r in trend["results"]] == [6.0, None, None, None]
    contract = MetricTrendSerializer(data=trend)
    assert contract.is_valid(), contract.errors
    grouped = reads.read_trend(
        metric, {**WINDOW, "points": 4, "group_by": "region"}, store=writer.store
    )
    assert len(grouped["results"]) == 1
    assert grouped["results"][0]["group_key"] == '["value","west"]'
    assert grouped["results"][0]["value"] == 6.0
    if level == "span":
        # Span drivers and unit values now read the writer's committed span
        # evidence (see test_derived_metric_span_evidence_clickhouse.py for the
        # value-level coverage); here they must be readable, not unavailable.
        drivers = reads.read_units(
            metric, WINDOW, drivers=True, store=writer.store, retention_days=lambda: 0
        )
        assert isinstance(drivers["results"], list)
        values = reads.read_units(
            metric,
            {**WINDOW, "context": "spans", "unit_ids": ["absent"]},
            store=writer.store,
            retention_days=lambda: 0,
        )
        # No committed row is not a computed unit with missing inputs. Span and
        # conversation evidence share the existing not_calculated contract.
        assert [(r["unit_id"], r["status"]) for r in values["results"]] == [
            ("absent", "not_calculated")
        ]
        absent = values["results"][0]
        assert absent["no_value_reason"] == "not_calculated"
        assert absent["value_number"] is None
        assert absent["eligible_count"] == absent["evaluated_count"] == 0
        return
    first = reads.read_units(
        metric,
        {**WINDOW, "limit": 1},
        drivers=True,
        store=writer.store,
        retention_days=lambda: 0,
    )
    assert len(first["results"]) == 1
    assert first["results"][0]["value_number"] == 2.0
    assert first["results"][0]["influence"] == -2.0
    second = reads.read_units(
        metric,
        {**WINDOW, "limit": 2, "after": first["next_cursor"]},
        drivers=True,
        store=writer.store,
        retention_days=lambda: 0,
    )
    assert [r["value_number"] for r in second["results"]] == [4.0]
    assert second["results"][0]["influence"] == -1.0
    assert second["next_cursor"] is None
    contract = MetricDriversSerializer(data=first)
    assert contract.is_valid(), contract.errors
    ids = [r["unit_id"] for r in first["results"] + second["results"]] + [
        str(sessions[2]),
        "absent",
    ]
    values = reads.read_units(
        metric,
        {
            **WINDOW,
            "context": "spans" if level == "span" else "sessions",
            "unit_ids": ids,
        },
        store=writer.store,
        retention_days=lambda: 0,
    )
    assert [r["value_number"] for r in values["results"]] == [2.0, 4.0, 12.0, None]
    assert values["results"][-1]["status"] == "not_calculated"
    assert not MetricGenerationPin.objects.filter(
        project_id=observe_project.id
    ).exists()
    with pytest.raises(Exception, match="Cursor changed"):
        reads.read_units(
            metric,
            {**WINDOW, "limit": 1, "after": first["next_cursor"] + "bad"},
            drivers=True,
            store=writer.store,
            retention_days=lambda: 0,
        )
    history = reads.read_history(metric, {"limit": 30})
    assert any(row["kind"] == "recalculation" for row in history["results"])
    expired = reads.read_units(
        metric,
        {
            **WINDOW,
            "context": "spans" if level == "span" else "sessions",
            "unit_ids": ids[:1],
        },
        store=writer.store,
        retention_days=lambda: 1,
    )
    assert expired["results"][0]["no_value_reason"] == "retention_expired"
    assert expired["results"][0]["value_number"] is None
    assert expired["results"][0]["inputs"] == {}

    @contextmanager
    def connection():
        yield writer.store

    query = {k: v.isoformat() for k, v in WINDOW.items() if k.startswith("window_")}
    with patch.object(reads, "read_store", connection):
        response = auth_client.get(f"/tracer/derived-metrics/{metric.id}/kpi/", query)
    assert response.status_code == 200, response.data
    assert response.data["current"]["value"] == 6.0
    # A pending physical field and bucket write cannot change either evidence or
    # the KPI. The stored head is the publication boundary, not max(generation).
    if level == "conversation":
        pending = writer.begin(versions=[{"id": definition.id, "metric_id": metric.id}])
        field, params = field_query(
            unit_id=ids[0], metric=metric.id, n=999.0, start=AT + timedelta(minutes=5)
        )
        for payload in writer.freeze(pending, FIELD_VALUES, field, params):
            writer.store.insert(FIELD_VALUES, payload)
        from tracer.services.derived_metrics.publication import committed_rows_select

        bucket, params = committed_rows_select(
            BUCKET_TOTALS,
            head,
            org_id=metric.organization_id,
            project_id=metric.project_id,
        )
        bucket = f"SELECT * REPLACE(toFloat64(9000) AS weighted_n) FROM ({bucket})"
        for payload in writer.freeze(pending, BUCKET_TOTALS, bucket, params):
            writer.store.insert(BUCKET_TOTALS, payload)
        after_pending = reads.read_kpi(metric, WINDOW, store=writer.store)
        assert after_pending["provenance"]["manifest_id"] == head.id
        assert after_pending["current"]["value"] == 6.0
        still_old = reads.read_units(
            metric,
            {**WINDOW, "context": "sessions", "unit_ids": ids[:1]},
            store=writer.store,
            retention_days=lambda: 0,
        )
        assert still_old["results"][0]["value_number"] == 2.0


@pytest.mark.parametrize("kind", ["unique_count", "percentile"])
def test_sketch_buckets_merge_real_states_and_empty_is_not_zero(
    publication_store, kind
):
    scope, metric, definition, attempt, manifest = case("mean(y)", kind=kind)
    definition.field_spec = {"value_alias": "y", "percentile": 0.5}
    field, params = field_query(metric=metric.id, start=AT, n=4)
    parts = live_parts_select(definition, field)
    columns = [
        "org_id",
        "workspace_id",
        "project_id",
        "metric_id",
        "metric_version",
        "generation",
        "attempt_id",
        "version",
    ]
    from tracer.services.derived_metrics.buckets import KEYS, STATS

    columns += list(KEYS + STATS)
    params.update(
        org=scope.organization,
        workspace=scope.workspace,
        project=scope.project,
        metric=metric.id,
        attempt=attempt,
    )
    publication_store.execute(
        f"INSERT INTO metric_bucket_totals_checkpoint ({', '.join(columns)}) SELECT %(org)s, %(workspace)s, %(project)s, %(metric)s, 1, 1, %(attempt)s, 1, {', '.join(KEYS + STATS)} FROM ({parts})",
        params,
    )
    row = reads._aggregate(publication_store, metric, definition, manifest, WINDOW)
    assert row["value"] == (1.0 if kind == "unique_count" else 4.0)
    assert row["ci_status"] == "not_estimable"
    row = reads._aggregate(
        publication_store,
        metric,
        definition,
        manifest,
        {**WINDOW, "window_start": AT - timedelta(hours=1), "window_end": AT},
    )
    assert row["value"] is None and row["status"] == "no_data"


class _M10Probes:
    """Side observations only: never use a probe reading in the gate statistic."""

    def __init__(self, phase):
        self.phase = phase
        self.thread = threading.get_ident()
        self.schedstat = (
            Path(f"/proc/self/task/{threading.get_native_id()}/schedstat")
            if sys.platform == "linux"
            else None
        )
        self.measuring = False
        self.gc_start = None
        self.data = {
            "boundaries": [],
            "pg_statements": [],
            "gc_intervals": [],
            "omitted_boundaries": 0,
            "omitted_pg_statements": 0,
            "omitted_gc_intervals": 0,
            "unpaired_gc_events": 0,
            "probe_errors": 0,
        }

    def observe(self, fn, *args):
        # Only diagnostic callbacks go here, never the service/DB invocation.
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001 - best effort; don't log private exception text
            self.data["probe_errors"] += 1
            return None

    def run_delay(self):
        if self.schedstat is not None:
            try:
                with self.schedstat.open("rb") as stat:
                    return int(stat.read(128).split()[1])
            except (OSError, IndexError, ValueError):
                pass
        return None  # Absent/unreadable is not a measured zero.

    def calibrate(self):
        readers = (thread_time_ns, process_time_ns, self.run_delay)
        costs = [0, 0, 0]
        counts = [0, 0, 0]
        start = perf_counter_ns()
        for _ in range(20):
            for i, read in enumerate(readers):
                before = perf_counter_ns()
                value = read()
                duration = perf_counter_ns() - before
                if value is not None:
                    counts[i] += 1
                    costs[i] += duration
        self.data["pre_timer_calibration"] = {
            "sets": 20,
            "ns_per_set_including_loop": (perf_counter_ns() - start) / 20,
            "ns_per_successful_call": {
                name: costs[i] / counts[i] if counts[i] else None
                for i, name in enumerate(("thread_cpu", "process_cpu", "run_delay"))
            },
            "run_delay_reads_available": counts[2],
        }

    def clocks(self):
        return {
            "monotonic_s": perf_counter(),
            "thread_cpu_ns": thread_time_ns(),
            "process_cpu_ns": process_time_ns(),
            "run_delay_ns": self.run_delay(),
        }

    def endpoint(self, name):
        # Keep enumeration outside both the gate and the endpoint clock delta.
        stamp = self.data[name] = {} if name == "sample_start" else self.clocks()
        threads = threading.enumerate()
        stamp["thread_names"] = [thread.name[:48] for thread in threads[:16]]
        stamp["omitted_threads"] = max(0, len(threads) - 16)
        if name == "sample_start":
            stamp.update(self.clocks())

    def mark(self, name):
        # Coarse boundaries, never per row, point, PG statement or phase switch.
        if len(self.data["boundaries"]) < 24:
            self.data["boundaries"].append({"at": name, **self.clocks()})
        else:
            self.data["omitted_boundaries"] += 1

    @staticmethod
    def fingerprint(sql):
        # Never stringify driver objects, interpolate bindings, or emit even
        # the leading bytes of SQL: comments and inline literals can be private.
        if not isinstance(sql, (str, bytes)):
            return "SQL:opaque"
        raw = sql.encode() if isinstance(sql, str) else sql
        words = raw[:80].lstrip().split(None, 1)
        verb = words[0].upper() if words else b""
        if verb in {
            b"SELECT",
            b"DELETE",
            b"INSERT",
            b"UPDATE",
            b"SAVEPOINT",
            b"RELEASE",
            b"WITH",
        }:
            op = verb.decode("ascii")
        else:
            op = "SQL"
        return f"{op}:{sha256(raw).hexdigest()[:24]}"

    def statement(self, started, phase, sql, ok):
        ended = perf_counter()
        if len(self.data["pg_statements"]) < 64:
            self.data["pg_statements"].append(
                {
                    "phase": phase,
                    "start_monotonic_s": started,
                    "s": ended - started,
                    "fingerprint": self.fingerprint(sql),
                    "ok": ok,
                }
            )
        else:
            self.data["omitted_pg_statements"] += 1

    def execute(self, execute, sql, params, many, context):
        if not self.measuring:
            return execute(sql, params, many, context)
        phase = self.phase()
        started = self.observe(perf_counter)
        ok = False
        try:
            result = execute(sql, params, many, context)
            ok = True
            return result
        finally:
            if started is not None:
                self.observe(self.statement, started, phase, sql, ok)

    def gc_event(self, event, info):
        if not self.measuring:
            return
        now = perf_counter()
        generation = info["generation"]
        if event == "start":
            if self.gc_start is not None:
                self.data["unpaired_gc_events"] += 1
            self.gc_start = (
                generation,
                now,
                self.phase(),
                threading.get_ident() == self.thread,
            )
        elif event == "stop":
            start, self.gc_start = self.gc_start, None
            if start is None or start[0] != generation:
                self.data["unpaired_gc_events"] += 1
            elif len(self.data["gc_intervals"]) < 16:
                self.data["gc_intervals"].append(
                    {
                        "generation": generation,
                        "start_monotonic_s": start[1],
                        "s": now - start[1],
                        "start_phase": start[2],
                        "end_phase": self.phase(),
                        "on_sample_thread": start[3],
                    }
                )
            else:
                self.data["omitted_gc_intervals"] += 1

    @contextmanager
    def installed(self):
        def callback(event, info):
            self.observe(self.gc_event, event, info)

        stack = ExitStack()
        try:
            self.observe(
                lambda: stack.enter_context(connection.execute_wrapper(self.execute))
            )
            self.observe(gc.callbacks.append, callback)
            yield
        finally:
            self.observe(gc.callbacks.remove, callback)  # Remove only our own callback.
            self.observe(stack.close)
            if self.gc_start is not None:
                self.data["unpaired_gc_events"] += 1


@contextmanager
def _m10_failure_report(days, deltas):
    samples = []  # Three modes x twelve service calls; no fixture or cache-drop SQL.
    try:
        yield samples
    except Exception:
        # A broken output stream/encoder must not replace the actual failure.
        with suppress(Exception):
            print(
                "M10_SERVICE_DIAGNOSTICS "
                + json.dumps(
                    {
                        "days": days,
                        "deltas": deltas,
                        "phase_basis": (
                            "Exclusive client wall seconds partition elapsed_s. After "
                            "aggregate _rows returns, inline assembly/status/sort share "
                            "one interval up to require_window; neither individual inline "
                            "operations nor DB lock/server time are measured. Pin "
                            "revalidation includes serialized enter/body/exit; owned "
                            "delete is the QuerySet.delete call, not the entire exit."
                        ),
                        "probe_basis": (
                            "Subtract adjacent cumulative clock readings for intervals. "
                            "Endpoints flank the wall timer. Null run_delay_ns means "
                            "unavailable. Process CPU includes other threads. PG, CH "
                            "and GC timings overlap phases: do not add. Calibration "
                            "is outside the timer; in-call observers are not free and "
                            "no overhead is subtracted."
                        ),
                        "query_ids": (
                            "Test UUIDs passed to native execute attempts; null if the "
                            "adapter lacks query_id or an existing ID was left untouched."
                        ),
                        "samples": samples,
                    },
                    separators=(",", ":"),
                )
            )
        raise


@contextmanager
def _m10_sample(store, mode, index, diagnostics):
    from tracer.services.derived_metrics import publication, source_retention

    sample = {
        "mode": mode,
        "index_0based": index,
        "cache": "cold" if index < 2 else "warm",
    }
    phases, queries = {}, []
    active = "other"
    probes = _M10Probes(lambda: active)
    sample["probes"] = probes.data

    def mark(name):
        probes.observe(probes.mark, name)

    # These calls are synchronous on the fixture's own client. Attribute each
    # interval to exactly one phase, including on exceptions and nested calls.
    def switch(label):
        nonlocal active, since
        now = perf_counter()
        phases[active] = phases.get(active, 0.0) + (now - since)
        previous, active, since = active, label, now
        return previous

    def on_sample_thread():
        return probes.measuring and threading.get_ident() == probes.thread

    def timed(label, fn):
        def call(*args, **kwargs):
            if not on_sample_thread():
                return fn(*args, **kwargs)
            previous = switch(label)
            try:
                return fn(*args, **kwargs)
            finally:
                switch(previous)

        return call

    pinned_read = reads.pinned_read

    @contextmanager
    def pin(*args, **kwargs):
        if not on_sample_thread():
            with pinned_read(*args, **kwargs) as pinned:
                yield pinned
            return
        previous = switch("pg_pin_entry")
        try:
            with pinned_read(*args, **kwargs) as pinned:
                switch(previous)  # Never charge the yielded service body to the pin.
                try:
                    yield pinned
                finally:
                    switch("pg_pin_exit")
                    mark("pin_body_exit")
        finally:
            switch(previous)

    real_serialized = publication.serialized_project

    @contextmanager
    def revalidate(*args, **kwargs):
        previous = switch("pg_pin_revalidation")
        mark("revalidation_enter")
        try:
            # Include lock acquisition, is_usable and transaction exit. Never
            # open a transaction or revalidate a pin on the observer's behalf.
            with real_serialized(*args, **kwargs) as held:
                yield held
        finally:
            switch(previous)
            mark("revalidation_exit")

    def serialized(*args, **kwargs):
        if active == "pg_pin_exit" and on_sample_thread():
            return revalidate(*args, **kwargs)
        return real_serialized(*args, **kwargs)

    real_delete = QuerySet.delete

    def delete(qs, *args, **kwargs):
        if (
            qs.model is not MetricGenerationPin
            or active not in {"pg_pin_entry", "pg_pin_exit"}
            or not on_sample_thread()
        ):
            return real_delete(qs, *args, **kwargs)
        previous = switch("pg_owned_pin_delete")
        mark("owned_delete_enter")
        try:
            return real_delete(qs, *args, **kwargs)
        finally:
            switch(previous)
            mark("owned_delete_exit")

    real_rows = reads._rows
    aggregate_rows = timed("aggregate_rows_python", real_rows)

    def rows(*args, **kwargs):
        # Both the finality probe and the aggregate construct MetricReadStore
        # around the same fixture client. Keep finality's own label in _rows.
        if active == "finality" or not on_sample_thread():
            return real_rows(*args, **kwargs)
        result = aggregate_rows(*args, **kwargs)
        switch("post_aggregate_to_require_window")
        mark("aggregate_rows_return")
        return result

    check_window = timed("require_window", source_retention.require_window)

    def require_window(*args, **kwargs):
        after_aggregate = active in {
            "post_aggregate_to_require_window",
            "post_window_to_pin_exit",
        }
        if not after_aggregate or not on_sample_thread():
            return check_window(*args, **kwargs)
        mark("post_aggregate_require_window_enter")
        try:
            return check_window(*args, **kwargs)
        finally:
            switch("post_window_to_pin_exit")
            mark("post_aggregate_require_window_exit")

    execute = store.client.execute
    accepts_id = "query_id" in signature(execute).parameters
    ch_phases = {
        "visibility": "ch_visibility",
        "finality": "ch_finality",
        "aggregate_rows_python": "ch_aggregate",
    }
    omitted = 0

    def query(*args, **kwargs):
        nonlocal omitted
        query_id = None
        if accepts_id and len(args) <= 2 and "query_id" not in kwargs:
            query_id = kwargs["query_id"] = str(uuid4())
        label = ch_phases.get(active, "ch_other")
        previous = switch(label)
        start = since
        try:
            return execute(*args, **kwargs)
        finally:
            switch(previous)
            if len(queries) < 8:
                queries.append(
                    {
                        "phase": label,
                        "start_monotonic_s": start,
                        "s": since - start,
                        "id": query_id,
                    }
                )
            else:
                omitted += 1
            if label == "ch_aggregate":
                mark("ch_aggregate_exit")

    probes.observe(probes.calibrate)
    with (
        probes.installed(),
        patch.object(reads, "pinned_read", pin),
        patch.object(publication, "serialized_project", serialized),
        patch.object(QuerySet, "delete", delete),
        patch.object(source_retention, "require_window", require_window),
        patch.object(
            source_retention,
            "retained_start",
            timed("retained_start", source_retention.retained_start),
        ),
        patch.object(
            source_retention,
            "current_cutoff",
            timed("retention_policy_cutoff", source_retention.current_cutoff),
        ),
        patch.object(
            reads, "provenance", timed("response_provenance", reads.provenance)
        ),
        patch.object(
            reads, "_disclosure", timed("response_disclosure", reads._disclosure)
        ),
        patch.object(
            store, "assert_visible", timed("visibility", store.assert_visible)
        ),
        patch.object(reads, "read_finality", timed("finality", reads.read_finality)),
        patch.object(reads, "_rows", rows),
        patch.object(reads, "point", timed("python_point_shaping", reads.point)),
        patch.object(store.client, "execute", query),
    ):
        probes.observe(probes.endpoint, "sample_start")
        utc_start = datetime.now(UTC)
        start = since = perf_counter()
        try:
            probes.measuring = True
            yield sample
        finally:
            switch("finished")
            probes.measuring = False
            utc_end = datetime.now(UTC)
            probes.observe(probes.endpoint, "sample_end")
            sample.update(
                start_utc=utc_start.isoformat(),
                end_utc=utc_end.isoformat(),
                start_monotonic_s=start,
                end_monotonic_s=since,
                elapsed_s=since - start,
                exclusive_phase_s=phases,
                ch_queries=queries,
                omitted_ch_queries=omitted,
            )
            diagnostics.append(sample)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("days", "deltas"), [(1, 4), (7, 4), (30, 4), (90, 4), (90, 19)]
)
def test_m10_complete_service_budget_with_source_retained_parts(
    live_writer, workspace, observe_project, days, deltas, monkeypatch
):
    """Real PG pins, current PLUS previous, and genuinely dimension-grouped trend.

    This is a compacted read-path gate, not the separate 10M source/refresh gate.
    The repeated fixture buckets contain retained-only sufficient statistics.
    The expired seed is physically removed before constructing the same read grid.
    19 deltas is the most a head carries before its next pass compacts it
    (METRICS_MAX_DELTAS_BEFORE_COMPACTION's default), under the same budget.
    """
    from ee.usage.models.usage import PlanEntitlement
    from tfc.capabilities import retention as policy_adapter
    from tfc.licensing.types import DeploymentFlavor
    from tracer.services.derived_metrics.publication import committed_rows_select
    from tracer.services.derived_metrics.retention import expire_project
    from tracer.services.derived_metrics.storage import RETIRED_PARTS
    from tracer.tests.test_derived_metric_retention_clickhouse import (
        numeric_definition,
        seed_fields_and_buckets,
    )

    monkeypatch.setattr(
        policy_adapter, "get_deployment_flavor", lambda: DeploymentFlavor.CLOUD
    )
    policy, _ = PlanEntitlement.objects.get_or_create(
        organization=workspace.organization,
        plan="free",
        feature="retention_traces_days",
        defaults={"value_int": 900},
    )
    policy.value_int = 900
    policy.save(update_fields=["value_int"])
    writer, size = live_writer, days * 96
    watermark = int((AT + timedelta(days=days + 1)).timestamp()) * 10**9
    template = numeric_definition(uuid4())
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="M10 complete reads",
    )
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="custom",
        level="conversation",
        formula="sum(y)",
        inputs=template.inputs,
        selection_unit="session",
        weighting="none",
        cohort_key="region",
        group_dims=["region"],
        backfill_status="complete",
    )
    old_id, live_id, retained_id = str(uuid4()), str(uuid4()), str(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [
            (old_id, AT - timedelta(days=181), 4, ""),
            (live_id, AT + timedelta(minutes=10), 6, ""),
            (retained_id, AT + timedelta(minutes=11), 4, ""),
        ],
    )
    expired = expire_project(
        writer,
        [definition],
        retention_days=lambda: 180,
        now=AT + timedelta(days=1, minutes=7),
    )
    assert expired["purged"]
    # A changed retained survivor rebuilds from the two live units only.
    head = seed_fields_and_buckets(
        writer,
        definition,
        [
            (live_id, AT + timedelta(minutes=10), 8, ""),
        ],
    )
    retired, params = committed_rows_select(
        RETIRED_PARTS,
        head,
        org_id=metric.organization_id,
        project_id=metric.project_id,
    )
    assert (
        writer.store.execute(
            f"SELECT unit_count, weighted_n FROM ({retired}) WHERE is_deleted = 0",
            params,
        )
        == []
    )
    head = writer.compact()
    source, params = committed_rows_select(
        BUCKET_TOTALS,
        head,
        org_id=metric.organization_id,
        project_id=metric.project_id,
    )
    assert writer.store.execute(
        f"SELECT unit_count, weighted_n FROM ({source}) WHERE is_deleted = 0",
        params,
    ) == [(2, 12.0)]
    # Publish a full previous/current fixture through the writer before timing.
    # Two actual dimension values per bucket, not time-only pseudo-grouping.
    projection = ", ".join(
        "toDateTime(%(fixture_start)s, 'UTC') + toIntervalSecond(intDiv(n.number, 2) * 900) AS bucket_start"
        if name == "bucket_start"
        else "toJSONString([toJSONString(tuple('value', if(modulo(n.number, 2) = 0, 'east', 'west')))]) AS group_key"
        if name == "group_key"
        else f"b.{name}"
        for name in BUCKET_TOTALS.columns
    )
    attempt = writer.begin(versions=[{"id": definition.id, "metric_id": metric.id}])
    writer.freeze(
        attempt,
        BUCKET_TOTALS,
        "SELECT "
        + projection
        + f" FROM ({source}) AS b CROSS JOIN numbers(%(fixture_size)s) AS n",
        {
            **params,
            "fixture_start": AT - timedelta(days=days),
            "fixture_size": size * 4,
        },
        collect_payloads=False,
    )
    writer.seal(attempt, cursors={"watermark_ns": watermark})
    writer.stage_and_publish(attempt)
    head = writer.compact()
    source, params = committed_rows_select(
        BUCKET_TOTALS,
        head,
        org_id=metric.organization_id,
        project_id=metric.project_id,
    )
    source = f"SELECT * FROM ({source}) WHERE group_key = 'region-a'"
    # The original, unencoded fixture group is outside this dimension grid.
    # Tombstone it in the first bounded delta (after collapse it must not count).
    for index in range(deltas):
        attempt = writer.begin(versions=[{"id": definition.id, "metric_id": metric.id}])
        replacement = (
            f"SELECT * REPLACE(toUInt8(1) AS is_deleted) FROM ({source})"
            if index == 0
            else (
                "SELECT * FROM metric_bucket_totals_checkpoint WHERE "
                "attempt_id = toUUID(%(fixture_checkpoint)s) AND bucket_start = %(fixture_bucket)s "
                "AND group_key != 'region-a'"
            )
        )
        bindings = (
            params
            if index == 0
            else {
                "fixture_checkpoint": str(head.checkpoint_attempt_id),
                "fixture_bucket": AT + timedelta(minutes=15 * index),
            }
        )
        writer.freeze(attempt, BUCKET_TOTALS, replacement, bindings)
        writer.seal(attempt, cursors={"watermark_ns": watermark})
        current_head = writer.stage_and_publish(attempt)
    DerivedMetric.activate_version(metric.id, definition.id, expected_active_id=None)
    metric = DerivedMetric.objects.select_related("project", "active_version").get(
        pk=metric.id
    )
    with _m10_failure_report(days, deltas) as diagnostics:
        _assert_m10_service_budget(
            writer, metric, current_head, days, deltas, size, diagnostics
        )


def _assert_m10_service_budget(
    writer, metric, current_head, days, deltas, size, diagnostics
):
    window = {**WINDOW, "window_end": AT + timedelta(days=days)}
    measurements = {}
    for mode in ["kpi", "trend", "dimension_trend"]:
        samples = []
        for index in range(12):
            if index < 2:
                writer.store.execute("SYSTEM DROP MARK CACHE")
                writer.store.execute("SYSTEM DROP UNCOMPRESSED CACHE")
            with _m10_sample(writer.store, mode, index, diagnostics) as sample:
                if mode == "kpi":
                    result = reads.read_kpi(metric, window, store=writer.store)
                else:
                    result = reads.read_trend(
                        metric,
                        {
                            **window,
                            "points": min(days, 90),
                            "group_by": "region" if mode == "dimension_trend" else "",
                        },
                        store=writer.store,
                    )
            samples.append(sample["elapsed_s"])
            assert result["provenance"]["manifest_id"] == current_head.id
            if mode == "kpi":
                for period in ("current", "previous"):
                    assert result[period]["eligible_count"] == size * 4
                    assert result[period]["value"] == size * 24.0
                assert result["delta"] == 0.0
            else:
                assert sum(r["eligible_count"] for r in result["results"]) == size * 4
                assert sum(r["value"] for r in result["results"]) == size * 24.0
                if mode == "dimension_trend":
                    assert {r["group_key"] for r in result["results"]} == {
                        '["value","east"]',
                        '["value","west"]',
                    }
                    assert len(result["results"]) == days * 2
        measurements[mode] = {
            "cold_max_s": max(samples[:2]),
            "warm_p95_s": sorted(samples[2:])[9],
            "samples_s": samples,
            "budget_s": (
                0.8 if mode == "dimension_trend" else 0.5 if days == 90 else 0.3
            ),
        }
    # Print ALL measurements even when a strict gate fails.
    print(
        "M10_SERVICE "
        + json.dumps(
            {
                "days": days,
                "buckets": size,
                "dimension_values": 2,
                "deltas": deltas,
                "expired_parts_absent": True,
                **measurements,
            }
        )
    )
    for measurement in measurements.values():
        assert measurement["cold_max_s"] <= measurement["budget_s"], measurements
        assert measurement["warm_p95_s"] <= measurement["budget_s"], measurements
