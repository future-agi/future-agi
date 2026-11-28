"""TH-10545 acceptance: trace-dashboard eval scores use the latest recorded
result per trace and are plotted at the trace's date.

Owner rules these tests encode (TH-10545, final):

* A trace's score for an eval template is the LATEST RECORDED result for that
  trace and template, plotted at the TRACE's date (its start), never at the
  evaluation's execution date. A trace re-evaluated weeks later stays on its
  own day, so every range that covers that day shows the same value.
* When that latest recorded result is a failure (``status='error'``) or has no
  numeric value, the trace shows no number (a dash, excluded from the
  average), not an older successful value.
* Winner: a trace-level result (``target_type='trace'``) is preferred; without
  one, the latest span-level result by recorded time (``created_at``, then the
  higher usage ``id``) wins. Span-level results feed trace dashboards. Ties
  are deterministic.
* Results recorded before project stamping (no ``project_id`` in the usage
  config) are attributed through their trace, as today. A stamped result whose
  stamped project differs from the trace's project is excluded.
* An eval output type that is empty, ``"reason"`` or missing never fails the
  whole widget.

Every test seeds the real CH25 test database: ``traces`` and ``spans`` through
the shared seed helpers and eval results in a test-owned copy of
``usage_apicalllog`` (same DDL, double-encoded ``config``). Queries go through
``DashboardWidgetViewSet._execute_ch_query_config``, the entry both widget
endpoints (``execute_query`` / ``preview_query``) and the exact-aggregation
worker use, and assert exact per-day values.

Each trace's ``traces.created_at`` equals its root span's start, and no eval
row is dated before its trace, so the tests hold whether a fix dates a trace by
its root span or by its ``traces`` row.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.core.cache import cache

from model_hub.models.ai_model import AIModel
from tracer.models.project import Project
from tracer.services import exact_aggregation_cache
from tracer.services.exact_aggregation_cache import (
    normalized_snapshot_identity,
    publish_exact_snapshot,
    read_or_schedule_exact_snapshot,
    snapshot_cache_key,
)
from tracer.tests._ch_seed import delete_ch_spans, seed_ch_spans, seed_ch_traces
from tracer.views.dashboard import (
    DashboardExactReadError,
    DashboardWidgetViewSet,
    _dashboard_snapshot_is_renderable,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

_MISSING = object()
_DASHBOARD_NAMESPACE = "dashboard-query"
# What dev c735cc1f (before the TH-10545 fix) keys dashboard snapshots with:
# ``_CACHE_VERSION`` in exact_aggregation_cache.py and the trace semantics
# marker the view adds to every trace-metric identity.
_PRE_FIX_CACHE_VERSION = 5
_PRE_FIX_TRACE_SEMANTICS = "physical-latest-complete-series-v2"
_STALE_SENTINEL = 0.123456


# ---------------------------------------------------------------------------
# Fixtures and seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def eval_usage():
    """Real CH25 executor over a test-owned ``usage_apicalllog`` copy.

    Same pattern as ``isolated_eval_usage_analytics`` in test_dashboard.py,
    except that statements without the usage table (system metrics, span
    reads) pass through unchanged instead of failing an assertion.
    """

    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.schema import (
        CDC_USAGE_APICALLLOG,
        _to_single_node_engine,
    )
    from tracer.services.clickhouse.v2 import get_v2_config

    config = get_v2_config()
    client = ClickHouseClient(
        host=config["host"],
        port=config["tcp_port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
    )
    table = f"_test_eval_trace_date_usage_{uuid4().hex[:12]}"
    try:
        client.execute(
            _to_single_node_engine(CDC_USAGE_APICALLLOG).replace(
                "CREATE TABLE IF NOT EXISTS usage_apicalllog",
                f"CREATE TABLE {table}",
            )
        )
    except Exception:
        client.execute(f"DROP TABLE IF EXISTS {table}")
        client.close()
        raise

    delegate = AnalyticsQueryService()
    delegate._ch_client = client

    class _IsolatedUsageAnalytics:
        def __init__(self):
            self.ch_client = client
            self.usage_table = table
            self.queries = []

        def execute_ch_query(self, query, params=None, timeout_ms=10000, settings=None):
            self.queries.append(query)
            return delegate.execute_ch_query(
                query.replace("usage_apicalllog", table),
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )

    try:
        yield _IsolatedUsageAnalytics()
    finally:
        client.execute(f"DROP TABLE IF EXISTS {table}")
        client.close()


def _sql_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _ch_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _day(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class _Seed:
    """Seed traces, spans and eval usage rows for one test."""

    def __init__(self, usage, workspace):
        self.usage = usage
        self.workspace = workspace
        self.span_ids: list[str] = []
        self._next_usage_id = 1000

    def trace(self, project, start, *, children=(), root_span=True, trace_id=None):
        """One trace: a root span at ``start`` plus optional child spans.

        ``children`` items are ``(offset, model, total_tokens)``; a child may
        start outside the window its root started in. ``trace_id`` reuses a
        UUID another project already holds.
        """

        trace_id = trace_id or str(uuid4())
        root_id = uuid4().hex[:16]
        rows = (
            [self._span(project, trace_id, root_id, None, start, None, 0)]
            if root_span
            else []
        )
        child_ids = []
        for offset, model, tokens in children:
            child_id = uuid4().hex[:16]
            child_ids.append(child_id)
            rows.append(
                self._span(
                    project, trace_id, child_id, root_id, start + offset, model, tokens
                )
            )
        if rows:
            seed_ch_spans(rows)
        self.span_ids.extend(row["id"] for row in rows)
        seed_ch_traces(
            [
                {
                    "id": trace_id,
                    "project_id": str(project.id),
                    "project_version_id": None,
                    "name": "th10545-trace",
                    "session_id": None,
                    "external_id": None,
                    "tags": "[]",
                    "metadata": "{}",
                    "input": "",
                    "output": "",
                    "error": "",
                    "error_analysis_status": "PENDING",
                    "created_at": start,
                    "updated_at": start,
                    "is_deleted": 0,
                }
            ]
        )
        return SimpleNamespace(
            id=trace_id, root=root_id, children=child_ids, project=project
        )

    @staticmethod
    def _span(project, trace_id, span_id, parent_id, start, model, tokens):
        return {
            "id": span_id,
            "trace_id": trace_id,
            "project_id": str(project.id),
            "org_id": str(project.organization_id),
            "parent_span_id": parent_id,
            "name": "child" if parent_id else "root",
            "observation_type": "llm" if parent_id else "chain",
            "status": "OK",
            "start_time": start,
            "end_time": start + timedelta(seconds=1),
            "latency_ms": 1000,
            "model": model,
            "total_tokens": tokens,
            "prompt_tokens": tokens,
            "completion_tokens": 0,
            "created_at": start,
            "updated_at": start,
        }

    def result(
        self,
        trace,
        template,
        at,
        *,
        value=None,
        status="success",
        level="span",
        span_id=None,
        stamp=_MISSING,
        usage_id=None,
        version=1,
        tombstone=False,
        organization_id=None,
        workspace_id=None,
    ):
        """One recorded eval result (one ``usage_apicalllog`` row).

        ``level='span'`` mirrors the span writer (span_id, no target_type);
        ``level='trace'`` mirrors the trace writer (``target_type='trace'``,
        anchored on the root span). ``stamp`` defaults to the trace's project,
        as current writers stamp it; pass ``None`` for a pre-stamping row.
        """

        if usage_id is None:
            usage_id = self._next_usage_id
            self._next_usage_id += 1
        config = {
            "reference_id": trace.id if level == "trace" else (span_id or trace.root),
            "trace_id": trace.id,
            "span_id": span_id or trace.root,
            "custom_eval_config_id": str(uuid4()),
            "source": "tracer",
        }
        if level == "trace":
            config["target_type"] = "trace"
        if stamp is _MISSING:
            stamp = trace.project.id
        if stamp is not None:
            config["project_id"] = str(stamp)
        if status == "success":
            config["output"] = {"output": value, "reason": "seeded"}
        else:
            config["error"] = "evaluation failed"
        organization_id = organization_id or self.workspace.organization_id
        workspace_id = workspace_id or self.workspace.id
        recorded = _ch_time(at)
        self.usage.ch_client.execute(
            f"INSERT INTO {self.usage.usage_table} "
            "(id, log_id, organization_id, workspace_id, status, config, source, "
            "source_id, deleted, created_at, updated_at, _peerdb_synced_at, "
            "_peerdb_is_deleted, _peerdb_version) "
            f"SELECT {int(usage_id)}, generateUUIDv4(), "
            f"toUUID('{organization_id}'), toUUID('{workspace_id}'), "
            f"'{_sql_literal(status)}', "
            f"toJSONString('{_sql_literal(json.dumps(config))}'), 'tracer', "
            f"'{_sql_literal(template)}', 0, "
            f"toDateTime64('{recorded}', 6, 'UTC'), "
            f"toDateTime64('{recorded}', 6, 'UTC'), "
            f"toDateTime64('{recorded}', 6, 'UTC'), {int(tombstone)}, {int(version)}"
        )
        return usage_id


@pytest.fixture
def seed(eval_usage, observe_project):
    seeder = _Seed(eval_usage, observe_project.workspace)
    cache.clear()
    try:
        yield seeder
    finally:
        cache.clear()
        delete_ch_spans(seeder.span_ids)


def _custom(start: datetime, end: datetime) -> dict:
    return {
        "custom_start": start.isoformat().replace("+00:00", "Z"),
        "custom_end": end.isoformat().replace("+00:00", "Z"),
    }


def _eval_metric(template, *, aggregation="avg", output_type="SCORE", filters=()):
    metric = {
        "id": template,
        "name": "trace_eval_score",
        "type": "eval_metric",
        "source": "traces",
        "config_id": template,
        "aggregation": aggregation,
        "filters": list(filters),
    }
    if output_type is not _MISSING:
        metric["output_type"] = output_type
    return metric


def _system_metric(name, aggregation, *, filters=()):
    return {
        "id": name,
        "name": name,
        "type": "system_metric",
        "source": "traces",
        "aggregation": aggregation,
        "filters": list(filters),
    }


def _config(projects, time_range, metrics, *, breakdowns=()):
    return {
        "project_ids": [str(project.id) for project in projects],
        "granularity": "day",
        "time_range": time_range,
        "metrics": metrics,
        "filters": [],
        "breakdowns": list(breakdowns),
    }


def _run_worker(seed, query_config):
    """Run the widget exactly as the exact-aggregation worker does."""

    workspace = seed.workspace
    with patch(
        "tracer.views.dashboard.V2AnalyticsQueryService", return_value=seed.usage
    ):
        try:
            response = DashboardWidgetViewSet()._execute_ch_query_config(
                deepcopy(query_config),
                workspace,
                refresh=True,
                _exact_worker=True,
                cache_identity_override={
                    "workspace_id": str(workspace.id),
                    "query_config": deepcopy(query_config),
                },
            )
        except DashboardExactReadError as exc:
            pytest.fail(
                f"the whole widget failed: {exc!r} (cause: {exc.__cause__!r}); "
                "both widget endpoints map this to HTTP 400"
            )
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["granularity"] == "day", "precondition: daily buckets"
    return result


def _run_public(seed, query_config, *, snapshot_probe=None):
    """Run the widget through the interactive (endpoint) lane.

    A cold cache falls through to the synchronous exact read. The read-cost
    router is pinned to "fits" so the request never hands off to the worker.
    """

    workspace = seed.workspace
    probe = snapshot_probe or MagicMock(side_effect=read_or_schedule_exact_snapshot)
    with (
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService", return_value=seed.usage
        ),
        patch("tracer.views.dashboard.exceeds_remaining_deadline", return_value=False),
        patch("tracer.views.dashboard.read_or_schedule_exact_snapshot", probe),
        patch(
            "tracer.views.dashboard._read_public_dashboard_query",
            side_effect=AssertionError("interactive read was handed to the worker"),
        ),
    ):
        try:
            response = DashboardWidgetViewSet()._execute_ch_query_config(
                deepcopy(query_config), workspace
            )
        except DashboardExactReadError as exc:
            pytest.fail(
                f"the whole widget failed: {exc!r} (cause: {exc.__cause__!r}); "
                "both widget endpoints map this to HTTP 400"
            )
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["granularity"] == "day", "precondition: daily buckets"
    return result


def _metric(result, name):
    [metric] = [item for item in result["metrics"] if item["name"] == name]
    assert not metric.get("error"), metric.get("error")
    return metric


def _points(result, name="trace_eval_score", *, series=None):
    """``{bucket start (UTC): value}`` for every bucket that has a number."""

    points = {}
    for line in _metric(result, name)["series"]:
        if series is not None and line["name"] != series:
            continue
        for point in line["data"]:
            if point["value"] is None:
                continue
            stamp = point["timestamp"]
            if isinstance(stamp, str):
                stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            assert stamp not in points, f"duplicate bucket {stamp} in {name}"
            points[stamp] = point["value"]
    return points


# ---------------------------------------------------------------------------
# 1. Range invariance
# ---------------------------------------------------------------------------


def test_same_trace_day_has_the_same_score_for_custom_30d_and_3m_ranges(
    observe_project, seed
):
    """Dev failure: dev keeps only usage rows executed inside the chosen range
    and elects the latest success per trace inside it, bucketed by execution
    time. The custom range (which ends before the re-evaluation) shows the
    first result, 0.2, on the trace day; 30D and 3M elect the re-evaluation,
    0.8, and plot it on the re-evaluation day, so the trace day is empty.
    Fixed: every range shows 0.8 on the trace day and nothing elsewhere.
    """

    template = str(uuid4())
    now = datetime.now(UTC)
    trace_day = _day(now - timedelta(days=20))
    trace = seed.trace(observe_project, trace_day + timedelta(hours=10))
    seed.result(trace, template, trace_day + timedelta(hours=11), value=0.2)
    seed.result(trace, template, now - timedelta(days=2), value=0.8)

    ranges = {
        "custom": _custom(trace_day - timedelta(days=2), trace_day + timedelta(days=3)),
        "30D": {"preset": "30D"},
        "3M": {"preset": "3M"},
    }
    observed = {
        name: _points(
            _run_worker(
                seed, _config([observe_project], time_range, [_eval_metric(template)])
            )
        )
        for name, time_range in ranges.items()
    }
    for name, points in observed.items():
        assert points == pytest.approx({trace_day: 0.8}), (name, observed)


# ---------------------------------------------------------------------------
# 2. Trace date, never execution date
# ---------------------------------------------------------------------------


def test_score_is_bucketed_on_the_trace_day_not_the_evaluation_day(
    observe_project, seed
):
    """Dev failure: dev buckets by ``usage.created_at`` (execution time) and
    bounds usage rows by the window. A trace from 45 days ago evaluated 3 days
    ago appears on the evaluation day under 3M and 30D, and not at all in a
    custom range around the trace day.
    Fixed: 3M and the custom range show 0.6 on the trace day; 30D, which covers
    the evaluation day but not the trace, shows nothing.
    """

    template = str(uuid4())
    now = datetime.now(UTC)
    trace_day = _day(now - timedelta(days=45))
    trace = seed.trace(observe_project, trace_day + timedelta(hours=10))
    seed.result(trace, template, now - timedelta(days=3), value=0.6)

    def points(time_range):
        return _points(
            _run_worker(
                seed, _config([observe_project], time_range, [_eval_metric(template)])
            )
        )

    assert points({"preset": "3M"}) == pytest.approx({trace_day: 0.6})
    assert points(
        _custom(trace_day - timedelta(days=1), trace_day + timedelta(days=2))
    ) == pytest.approx({trace_day: 0.6})
    assert points({"preset": "30D"}) == {}


# ---------------------------------------------------------------------------
# 3. Latest recorded result wins, including failures
# ---------------------------------------------------------------------------


def test_latest_recorded_failure_hides_older_success_and_newer_success_replaces(
    observe_project, seed
):
    """Dev failure: dev drops ``status='error'`` rows before electing a winner,
    so a trace whose newest recorded result failed still shows its older
    success (0.9 on day 0, count 1).
    Fixed:
    * day 0: the only trace's latest recorded result is an error, so there is
      no number at all (avg and count are both empty);
    * day 1: 0.3 and the newer of 0.1/0.7, so avg 0.5 and count 2 (each trace
      once). A newer result for the same trace and template in another
      organization/workspace must not leak in;
    * day 2: the latest recorded success has no numeric value, so no number.
    """

    template = str(uuid4())
    now = datetime.now(UTC)
    day0 = _day(now - timedelta(days=6))
    day1 = day0 + timedelta(days=1)

    failed_last = seed.trace(observe_project, day0 + timedelta(hours=10))
    seed.result(failed_last, template, day0 + timedelta(hours=11), value=0.9)
    seed.result(failed_last, template, day0 + timedelta(hours=12), status="error")

    single = seed.trace(observe_project, day1 + timedelta(hours=10))
    seed.result(single, template, day1 + timedelta(hours=11), value=0.3)
    replaced = seed.trace(observe_project, day1 + timedelta(hours=11))
    seed.result(replaced, template, day1 + timedelta(hours=12), value=0.1)
    seed.result(replaced, template, day1 + timedelta(hours=14), value=0.7)
    # Tenancy decoy: newest row for the same trace and template, other tenant.
    seed.result(
        replaced,
        template,
        day1 + timedelta(hours=16),
        value=0.05,
        organization_id=uuid4(),
        workspace_id=uuid4(),
    )

    non_numeric_last = seed.trace(observe_project, day0 + timedelta(days=2, hours=10))
    seed.result(
        non_numeric_last, template, day0 + timedelta(days=2, hours=11), value=0.4
    )
    seed.result(
        non_numeric_last,
        template,
        day0 + timedelta(days=2, hours=12),
        value="not applicable",
    )

    window = _custom(day0 - timedelta(days=1), day0 + timedelta(days=4))
    average = _points(
        _run_worker(seed, _config([observe_project], window, [_eval_metric(template)]))
    )
    assert average == pytest.approx({day1: 0.5})

    counts = _points(
        _run_worker(
            seed,
            _config(
                [observe_project], window, [_eval_metric(template, aggregation="count")]
            ),
        )
    )
    assert day0 not in counts, counts
    assert counts[day1] == 2, counts


# ---------------------------------------------------------------------------
# 4. Span-level results, trace-level preference, deterministic ties
# ---------------------------------------------------------------------------


def test_span_results_count_and_trace_result_beats_later_span_result(
    observe_project, seed
):
    """Dev failure: dev has no trace-over-span preference; it elects the newest
    success whatever its grain, so day 1 shows the later span result 0.1
    instead of the trace-level 0.9.
    Fixed (and guarded, since dev already does this):
    * day 0: only span-level results, on two spans; the latest recorded one
      (0.6) wins even though the older one has the higher usage id, so the
      election orders by recorded time before id. Span-level results must
      feed trace dashboards.
    * day 1: a trace-level result (0.9) beats a later span-level one (0.1).
    * day 2: two span results recorded at the same instant; the higher usage
      id (recorded later) wins (0.5), on every run.
    """

    template = str(uuid4())
    now = datetime.now(UTC)
    day0 = _day(now - timedelta(days=6))

    spans_only = seed.trace(
        observe_project,
        day0 + timedelta(hours=10),
        children=[(timedelta(minutes=1), None, 0), (timedelta(minutes=2), None, 0)],
    )
    older_id, newer_id = 910002, 910001
    assert older_id > newer_id, "precondition: id order opposes recorded order"
    seed.result(
        spans_only,
        template,
        day0 + timedelta(hours=11),
        value=0.2,
        span_id=spans_only.children[0],
        usage_id=older_id,
    )
    seed.result(
        spans_only,
        template,
        day0 + timedelta(hours=12),
        value=0.6,
        span_id=spans_only.children[1],
        usage_id=newer_id,
    )

    with_trace_result = seed.trace(
        observe_project,
        day0 + timedelta(days=1, hours=10),
        children=[(timedelta(minutes=1), None, 0)],
    )
    seed.result(
        with_trace_result,
        template,
        day0 + timedelta(days=1, hours=11),
        value=0.9,
        level="trace",
    )
    seed.result(
        with_trace_result,
        template,
        day0 + timedelta(days=1, hours=13),
        value=0.1,
        span_id=with_trace_result.children[0],
    )

    tied = seed.trace(
        observe_project,
        day0 + timedelta(days=2, hours=10),
        children=[(timedelta(minutes=1), None, 0), (timedelta(minutes=2), None, 0)],
    )
    same_instant = day0 + timedelta(days=2, hours=11)
    # Insert the expected winner first so insertion order cannot decide it.
    seed.result(
        tied,
        template,
        same_instant,
        value=0.5,
        span_id=tied.children[1],
        usage_id=900002,
    )
    seed.result(
        tied,
        template,
        same_instant,
        value=0.3,
        span_id=tied.children[0],
        usage_id=900001,
    )

    query_config = _config(
        [observe_project],
        _custom(day0 - timedelta(days=1), day0 + timedelta(days=4)),
        [_eval_metric(template)],
    )
    expected = {
        day0: 0.6,
        day0 + timedelta(days=1): 0.9,
        day0 + timedelta(days=2): 0.5,
    }
    for _ in range(3):
        assert _points(_run_worker(seed, query_config)) == pytest.approx(expected)


def test_latest_trace_level_failure_beats_later_span_success(observe_project, seed):
    """Rests on interpretation 1 in map.md section 5 (not yet confirmed by the
    owner): rule 3 prefers a trace-level result, and rule 2 says a failed
    winner shows no number, so a trace-level error outranks a later span
    success.
    Dev failure: dev drops the error before the election and shows the span
    result 0.4.
    Fixed: no number for the trace.
    """

    template = str(uuid4())
    now = datetime.now(UTC)
    day0 = _day(now - timedelta(days=6))
    trace = seed.trace(
        observe_project,
        day0 + timedelta(hours=10),
        children=[(timedelta(minutes=1), None, 0)],
    )
    seed.result(
        trace, template, day0 + timedelta(hours=11), status="error", level="trace"
    )
    seed.result(
        trace,
        template,
        day0 + timedelta(hours=12),
        value=0.4,
        span_id=trace.children[0],
    )

    result = _run_worker(
        seed,
        _config(
            [observe_project],
            _custom(day0 - timedelta(days=1), day0 + timedelta(days=2)),
            [_eval_metric(template)],
        ),
    )
    assert _points(result) == {}


# ---------------------------------------------------------------------------
# 5. Project stamps
# ---------------------------------------------------------------------------


def test_unstamped_history_counts_and_foreign_stamped_result_is_excluded(
    observe_project, organization, workspace, seed
):
    """Dev failure: dev never reads the stamped ``project_id``; it attributes
    every row through its trace and elects the newest, so day 1 shows the
    result stamped with another project (0.1).
    Fixed:
    * day 0: an unstamped (pre-v1.59) result is attributed through its trace
      and shown (0.4);
    * day 1: the newer result stamped with a different project is excluded,
      so the older unstamped result (0.7) is the trace's score;
    * day 2: a result stamped with the trace's own project counts (0.25).
    """

    other_project = Project.objects.create(
        name="TH-10545 other project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    template = str(uuid4())
    now = datetime.now(UTC)
    day0 = _day(now - timedelta(days=6))

    legacy = seed.trace(observe_project, day0 + timedelta(hours=10))
    seed.result(legacy, template, day0 + timedelta(hours=11), value=0.4, stamp=None)

    foreign = seed.trace(observe_project, day0 + timedelta(days=1, hours=10))
    seed.result(
        foreign, template, day0 + timedelta(days=1, hours=11), value=0.7, stamp=None
    )
    seed.result(
        foreign,
        template,
        day0 + timedelta(days=1, hours=12),
        value=0.1,
        stamp=other_project.id,
    )

    own = seed.trace(observe_project, day0 + timedelta(days=2, hours=10))
    seed.result(
        own,
        template,
        day0 + timedelta(days=2, hours=11),
        value=0.25,
        stamp=observe_project.id,
    )

    result = _run_worker(
        seed,
        _config(
            [observe_project],
            _custom(day0 - timedelta(days=1), day0 + timedelta(days=4)),
            [_eval_metric(template)],
        ),
    )
    assert _points(result) == pytest.approx(
        {
            day0: 0.4,
            day0 + timedelta(days=1): 0.7,
            day0 + timedelta(days=2): 0.25,
        }
    )


def test_stamped_result_survives_a_trace_uuid_shared_by_two_selected_projects(
    observe_project, organization, workspace, seed
):
    """Dev and the previous PR head fail: a trace UUID held by two selected
    projects was treated as ambiguous and dropped, so project A's stamped
    result vanished from the A+B dashboard (``{}``).
    Fixed (gilfoyle's item 1 acceptance):
    * A+B selected: the A-stamped result counts, is attributed to A and is
      dated by A's spans (day 0), not B's (day 1);
    * the unstamped result on the shared UUID stays ambiguous and is dropped;
    * B alone: the A-stamped result does not appear. B is then the only
      holder, so the unstamped result is B's, on B's day.
    """

    other_project = Project.objects.create(
        name="TH-10545 shared-uuid project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    template = str(uuid4())
    day0 = _day(datetime.now(UTC) - timedelta(days=6))
    in_a = seed.trace(observe_project, day0 + timedelta(hours=10))
    in_b = seed.trace(
        other_project, day0 + timedelta(days=1, hours=10), trace_id=in_a.id
    )
    seed.result(in_a, template, day0 + timedelta(days=2, hours=9), value=0.8)
    seed.result(
        in_b, template, day0 + timedelta(days=2, hours=10), value=0.3, stamp=None
    )
    window = _custom(day0 - timedelta(days=1), day0 + timedelta(days=4))

    both = _run_worker(
        seed,
        _config([observe_project, other_project], window, [_eval_metric(template)]),
    )
    assert _points(both) == pytest.approx({day0: 0.8})

    by_project = _run_worker(
        seed,
        _config(
            [observe_project, other_project],
            window,
            [_eval_metric(template)],
            breakdowns=[{"name": "project", "type": "system_metric"}],
        ),
    )
    series = {
        line["name"]
        for line in _metric(by_project, "trace_eval_score")["series"]
        if any(point["value"] is not None for point in line["data"])
    }
    # One series, project A's (the formatter shows its name, or the UUID when
    # no name map is passed).
    assert series in ({observe_project.name}, {str(observe_project.id)})

    only_b = _run_worker(
        seed, _config([other_project], window, [_eval_metric(template)])
    )
    assert _points(only_b) == pytest.approx({day0 + timedelta(days=1): 0.3})


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        pytest.param("is_set", None, "a", id="is_set"),
        pytest.param("is_not_set", None, "b", id="is_not_set"),
        pytest.param("equal_to", 0.8, "a", id="equals"),
    ],
)
def test_eval_filters_match_each_project_of_a_shared_trace_uuid_on_its_own_result(
    observe_project, organization, workspace, seed, operator, value, expected
):
    """Previous PR head fails: filter membership compared the trace UUID alone,
    so with A and B both holding UUID T and only A having a result, A's result
    also admitted B's trace (is_set and equals counted 2) and excluded it from
    is_not_set (counted 0). Fixed: membership is per (project, trace) pair, so
    each project's trace is judged on its own elected result.
    """

    other_project = Project.objects.create(
        name="TH-10545 shared-uuid filter project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    template = str(uuid4())
    day0 = _day(datetime.now(UTC) - timedelta(days=6))
    in_a = seed.trace(observe_project, day0 + timedelta(hours=10))
    seed.trace(other_project, day0 + timedelta(days=1, hours=10), trace_id=in_a.id)
    seed.result(in_a, template, day0 + timedelta(hours=11), value=0.8)
    leaf = {
        "metric_type": "eval_metric",
        "metric_name": template,
        "operator": operator,
        "output_type": "SCORE",
    }
    if value is not None:
        leaf["value"] = value
    result = _run_worker(
        seed,
        _config(
            [observe_project, other_project],
            _custom(day0 - timedelta(days=1), day0 + timedelta(days=3)),
            [_system_metric("trace_count", "count", filters=[leaf])],
        ),
    )
    a_day, b_day = day0, day0 + timedelta(days=1)
    assert _points(result, "trace_count") == {(a_day if expected == "a" else b_day): 1}


# ---------------------------------------------------------------------------
# 6. Empty / "reason" / missing output types never fail the widget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lane", ["worker", "endpoint"])
@pytest.mark.parametrize(
    "output_type",
    [
        pytest.param("reason", id="reason"),
        pytest.param("", id="empty"),
        pytest.param(_MISSING, id="missing"),
    ],
)
def test_unusual_eval_output_type_does_not_fail_the_widget(
    observe_project, seed, output_type, lane
):
    """Dev failure (``reason`` only): the eval filter compiler raises
    ``InvalidMetricCombinationError("Unsupported eval filter output type:
    REASON")`` (dashboard.py ``_build_subquery_filters``) while the metrics
    are prepared. The view wraps it in ``DashboardExactReadError`` and both
    widget endpoints answer HTTP 400 for the whole widget, so even the eval
    score series is lost.
    The ``empty`` and ``missing`` cases pass on dev; they guard the fallback
    to the template's type.
    Fixed: the widget answers; the eval score series is avg(0.9, 0.1) = 0.5,
    and the trace count filtered on "eval > 0.5" (the template type, SCORE,
    applied to the unusual payload type) is 1.
    """

    template = str(uuid4())
    now = datetime.now(UTC)
    day0 = _day(now - timedelta(days=3))
    high = seed.trace(observe_project, day0 + timedelta(hours=10))
    seed.result(high, template, day0 + timedelta(hours=11), value=0.9)
    low = seed.trace(observe_project, day0 + timedelta(hours=11))
    seed.result(low, template, day0 + timedelta(hours=12), value=0.1)

    eval_filter = {
        "column_id": template,
        "source": "traces",
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": 0.5,
        },
    }
    if output_type is not _MISSING:
        eval_filter["output_type"] = output_type
    query_config = _config(
        [observe_project],
        _custom(day0 - timedelta(days=1), day0 + timedelta(days=2)),
        [
            _eval_metric(template, output_type=output_type),
            _system_metric("trace_count", "count", filters=[eval_filter]),
        ],
    )

    run = _run_worker if lane == "worker" else _run_public
    result = run(seed, query_config)
    assert _points(result) == pytest.approx({day0: 0.5})
    assert _points(result, "trace_count") == {day0: 1}


# ---------------------------------------------------------------------------
# 6b. Eval filters and eval breakdowns on system metrics use the same rule
# ---------------------------------------------------------------------------


def _reevaluated_and_failed_last(seed, project, template):
    """Two traces 20 days ago, each re-evaluated after its first result.

    * ``reevaluated`` (day X): 0.2 recorded on its own day, re-evaluated to
      0.8 two days ago. Its latest recorded result is 0.8.
    * ``failed_last`` (day X + 1): 0.9 recorded on its own day, then a failed
      re-run an hour later. Its latest recorded result is a failure.
    """

    now = datetime.now(UTC)
    day_x = _day(now - timedelta(days=20))
    reevaluated = seed.trace(project, day_x + timedelta(hours=10))
    seed.result(reevaluated, template, day_x + timedelta(hours=11), value=0.2)
    seed.result(reevaluated, template, now - timedelta(days=2), value=0.8)
    failed_last = seed.trace(project, day_x + timedelta(days=1, hours=10))
    seed.result(failed_last, template, day_x + timedelta(days=1, hours=11), value=0.9)
    seed.result(
        failed_last,
        template,
        day_x + timedelta(days=1, hours=12),
        status="error",
    )
    ranges = {
        "custom": _custom(day_x - timedelta(days=2), day_x + timedelta(days=3)),
        "30D": {"preset": "30D"},
        "3M": {"preset": "3M"},
    }
    return day_x, ranges


def test_trace_count_eval_filter_uses_latest_recorded_result_in_every_range(
    observe_project, seed
):
    """Dev failure: dev's eval filter matches any success executed inside the
    chosen range. The custom range (ending before the re-evaluation) sees
    only 0.2 for day X, so it counts nothing there, but counts day X + 1
    through the 0.9 that a failed re-run replaced; 30D and 3M count both.
    Fixed: the filter follows each trace's latest recorded result, exactly
    like the eval-score widget: every range counts day X once (0.8 > 0.5) and
    never day X + 1 (latest result failed).
    """

    template = str(uuid4())
    day_x, ranges = _reevaluated_and_failed_last(seed, observe_project, template)
    eval_filter = {
        "column_id": template,
        "source": "traces",
        "output_type": "SCORE",
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": 0.5,
        },
    }
    observed = {
        name: _points(
            _run_worker(
                seed,
                _config(
                    [observe_project],
                    time_range,
                    [_system_metric("trace_count", "count", filters=[eval_filter])],
                ),
            ),
            "trace_count",
        )
        for name, time_range in ranges.items()
    }
    for name, points in observed.items():
        assert points == {day_x: 1}, (name, observed)


def test_trace_count_eval_breakdown_uses_latest_recorded_result_in_every_range(
    observe_project, seed
):
    """Dev failure: dev LEFT JOINs every successful usage row of the template
    to each span, so the re-evaluated trace counts in both the "20" and the
    "80" series, and the trace whose latest run failed is grouped under its
    older "90".
    Fixed: one elected row per trace. Day X is "80" only; day X + 1 is
    "(not set)" because that trace's latest recorded result failed. The same
    in every range.
    """

    template = str(uuid4())
    day_x, ranges = _reevaluated_and_failed_last(seed, observe_project, template)
    breakdown = {
        "name": template,
        "type": "eval_metric",
        "config_id": template,
        "output_type": "SCORE",
    }
    for name, time_range in ranges.items():
        result = _run_worker(
            seed,
            _config(
                [observe_project],
                time_range,
                [_system_metric("trace_count", "count")],
                breakdowns=[breakdown],
            ),
        )
        series = {
            line["name"]: _points(result, "trace_count", series=line["name"])
            for line in _metric(result, "trace_count")["series"]
        }
        populated = {key: value for key, value in series.items() if value}
        assert populated == {
            "80": {day_x: 1},
            "(not set)": {day_x + timedelta(days=1): 1},
        }, (name, series)


def test_eval_breakdown_label_is_the_same_for_every_metric_it_breaks_down(
    observe_project, seed
):
    """Previous PR head fails: a choice eval used as a breakdown with no
    output-type hint took the template's type only under a system metric
    ("red"). Under an eval metric of the same template it fell back to the
    score formatter, and under another template's eval metric too, so the same
    trace was grouped as "(not set)" or "0" there.
    Fixed: every breakdown path falls back to the template's configured type.
    """

    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate

    choice = EvalTemplate(
        name=str(uuid4()),
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Choice"},
    )
    EvalTemplate.no_workspace_objects.bulk_create([choice])
    choice_id = str(choice.id)
    score_id = str(uuid4())
    day0 = _day(datetime.now(UTC) - timedelta(days=5))
    trace = seed.trace(observe_project, day0 + timedelta(hours=10))
    seed.result(trace, choice_id, day0 + timedelta(hours=11), value="red")
    seed.result(trace, score_id, day0 + timedelta(hours=11), value=0.5)
    breakdown = {"name": choice_id, "type": "eval_metric", "config_id": choice_id}
    window = _custom(day0 - timedelta(days=1), day0 + timedelta(days=2))

    for name, metric in {
        "system metric": _system_metric("trace_count", "count"),
        "same template": _eval_metric(
            choice_id, aggregation="count", output_type="CHOICE"
        ),
        "other template": _eval_metric(score_id, aggregation="count"),
    }.items():
        result = _run_worker(
            seed,
            _config([observe_project], window, [metric], breakdowns=[breakdown]),
        )
        labels = {
            line["name"]
            for line in _metric(result, metric["name"])["series"]
            if any(point["value"] for point in line["data"])
        }
        assert labels == {"red"}, (name, labels)


def test_a_missing_score_is_not_a_zero_in_labels_or_filters(observe_project, seed):
    """Previous PR head fails: ``eval_score`` is stored as 0 when a result has
    no number, so a scoreless winner was labelled "0" (system-metric and
    cross-template breakdowns) and matched a score = 0 filter, while a real 0
    was "(not set)" under its own eval metric.
    Fixed: a real 0 is "0" and matches = 0; a scoreless result is
    "(not set)" and matches no score comparison.
    """

    template = str(uuid4())
    day0 = _day(datetime.now(UTC) - timedelta(days=5))
    zero = seed.trace(observe_project, day0 + timedelta(hours=10))
    seed.result(zero, template, day0 + timedelta(hours=11), value=0)
    scoreless = seed.trace(observe_project, day0 + timedelta(hours=12))
    seed.result(scoreless, template, day0 + timedelta(hours=13), value=None)
    window = _custom(day0 - timedelta(days=1), day0 + timedelta(days=2))
    breakdown = {
        "name": template,
        "type": "eval_metric",
        "config_id": template,
        "output_type": "SCORE",
    }

    for metric in (
        _system_metric("trace_count", "count"),
        _eval_metric(template, aggregation="count"),
    ):
        result = _run_worker(
            seed,
            _config([observe_project], window, [metric], breakdowns=[breakdown]),
        )
        series = {
            line["name"]: _points(result, metric["name"], series=line["name"])
            for line in _metric(result, metric["name"])["series"]
        }
        populated = {name: points for name, points in series.items() if points}
        assert populated == {"0": {day0: 1}, "(not set)": {day0: 1}}, metric["name"]

    equals_zero = {
        "column_id": template,
        "source": "traces",
        "output_type": "SCORE",
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "number",
            "filter_op": "equals",
            "filter_value": 0,
        },
    }
    filtered = _run_worker(
        seed,
        _config(
            [observe_project],
            window,
            [_system_metric("trace_count", "count", filters=[equals_zero])],
        ),
    )
    assert _points(filtered, "trace_count") == {day0: 1}


def test_a_sub_second_custom_window_keeps_its_fraction_for_every_metric(
    observe_project, seed
):
    """Previous PR head fails: clickhouse-driver renders a ``datetime`` bound
    without microseconds, so the custom window [12:00:00.25, 12:00:00.75)
    became [12:00:00, 12:00:00), an empty window, and a trace starting at
    12:00:00.5 showed neither its eval score nor its trace count.
    Fixed: a custom window's bounds keep their fraction in every metric of the
    widget, and a trace just outside the fraction stays out.
    """

    template = str(uuid4())
    day0 = _day(datetime.now(UTC) - timedelta(days=5))
    noon = day0 + timedelta(hours=12)
    inside = seed.trace(observe_project, noon + timedelta(milliseconds=500))
    seed.result(inside, template, noon + timedelta(hours=1), value=0.6)
    outside = seed.trace(observe_project, noon + timedelta(milliseconds=100))
    seed.result(outside, template, noon + timedelta(hours=1), value=0.2)
    window = _custom(
        noon + timedelta(milliseconds=250), noon + timedelta(milliseconds=750)
    )
    result = _run_worker(
        seed,
        _config(
            [observe_project],
            window,
            [_eval_metric(template), _system_metric("trace_count", "count")],
        ),
    )
    assert _points(result) == pytest.approx({day0: 0.6})
    assert _points(result, "trace_count") == {day0: 1}


def test_single_project_joins_are_unchanged_by_project_matching(observe_project, seed):
    """Regression guard; passes at the parent commit too. Item 1 made the
    cross-eval, span-root and system-breakdown joins match on the project as
    well as the trace. With one selected project and no shared UUIDs they
    must keep every row they had:
    * cross-eval: an eval metric broken down by another template;
    * span root: an eval metric broken down by a span column (status);
    * system breakdown: a trace count broken down by an eval template.
    """

    scored, other = str(uuid4()), str(uuid4())
    day0 = _day(datetime.now(UTC) - timedelta(days=5))
    traces = [
        seed.trace(observe_project, day0 + timedelta(hours=10 + hour))
        for hour in range(2)
    ]
    for trace, value in zip(traces, (0.4, 0.6), strict=True):
        seed.result(trace, scored, day0 + timedelta(hours=13), value=value)
        seed.result(trace, other, day0 + timedelta(hours=13), value=0.5)
    window = _custom(day0 - timedelta(days=1), day0 + timedelta(days=2))
    by_other = {
        "name": other,
        "type": "eval_metric",
        "config_id": other,
        "output_type": "SCORE",
    }

    cross = _run_worker(
        seed,
        _config(
            [observe_project], window, [_eval_metric(scored)], breakdowns=[by_other]
        ),
    )
    assert _points(cross, series="50") == pytest.approx({day0: 0.5})

    span_root = _run_worker(
        seed,
        _config(
            [observe_project],
            window,
            [_eval_metric(scored)],
            breakdowns=[{"name": "status", "type": "system_metric"}],
        ),
    )
    assert _points(span_root, series="OK") == pytest.approx({day0: 0.5})

    system = _run_worker(
        seed,
        _config(
            [observe_project],
            window,
            [_system_metric("trace_count", "count")],
            breakdowns=[by_other],
        ),
    )
    assert _points(system, "trace_count", series="50") == {day0: 2}


@pytest.mark.parametrize(
    "time_range",
    [
        pytest.param({"preset": "7D"}, id="preset"),
        pytest.param(
            {
                "custom_start": "2026-11-01T00:00:00Z",
                "custom_end": "2026-11-08T00:00:00Z",
            },
            id="whole-second-custom",
        ),
    ],
)
def test_presets_and_whole_second_windows_keep_their_window_sql(time_range):
    """Item 6 must not touch these windows: no exact-microsecond parameter is
    added and every window predicate keeps the plain ``%(start_date)s`` /
    ``%(end_date)s`` tokens. (The VM run also compares the SQL byte for byte
    with the parent commit.)
    """

    from tracer.services.clickhouse.query_builders.dashboard import (
        DashboardQueryBuilder,
    )

    template = str(uuid4())
    config = {
        "project_ids": [str(uuid4())],
        "organization_id": str(uuid4()),
        "workspace_id": str(uuid4()),
        "granularity": "day",
        "time_range": time_range,
        "metrics": [
            _eval_metric(template),
            _system_metric("trace_count", "count"),
        ],
        "filters": [],
        "breakdowns": [],
    }
    builder = DashboardQueryBuilder(config)
    for metric in config["metrics"]:
        sql, params = builder.build_metric_query(metric)
        assert "start_date_exact_us" not in params
        assert "end_date_exact_us" not in params
        assert "fromUnixTimestamp64Micro" not in sql
        assert "%(start_date)s" in sql and "%(end_date)s" in sql


def test_an_empty_choice_is_not_set_in_every_breakdown_path(observe_project, seed):
    """Previous PR head fails: the system-metric eval breakdown labelled an
    empty choice result "" while both eval-metric breakdowns labelled it
    "(not set)". Fixed: "(not set)" in all three paths.
    """

    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate

    choice = EvalTemplate(
        name=str(uuid4()),
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Choice"},
    )
    EvalTemplate.no_workspace_objects.bulk_create([choice])
    choice_id, score_id = str(choice.id), str(uuid4())
    day0 = _day(datetime.now(UTC) - timedelta(days=5))
    trace = seed.trace(observe_project, day0 + timedelta(hours=10))
    seed.result(trace, choice_id, day0 + timedelta(hours=11), value="")
    seed.result(trace, score_id, day0 + timedelta(hours=11), value=0.5)
    breakdown = {"name": choice_id, "type": "eval_metric", "config_id": choice_id}
    window = _custom(day0 - timedelta(days=1), day0 + timedelta(days=2))
    for name, metric in {
        "system metric": _system_metric("trace_count", "count"),
        "same template": _eval_metric(
            choice_id, aggregation="count", output_type="CHOICE"
        ),
        "other template": _eval_metric(score_id, aggregation="count"),
    }.items():
        result = _run_worker(
            seed,
            _config([observe_project], window, [metric], breakdowns=[breakdown]),
        )
        labels = {
            line["name"]
            for line in _metric(result, metric["name"])["series"]
            if any(point["value"] for point in line["data"])
        }
        assert labels == {"(not set)"}, (name, labels)


def test_an_absent_pass_fail_verdict_is_not_set_in_every_breakdown_path(
    observe_project, seed
):
    """Previous PR head fails: with item 3's template-type fallback a pass/fail
    breakdown labelled a result with no verdict "Failed" (its score is stored
    as 0). Fixed: an absent verdict is "(not set)" in all three paths, and a
    recorded "Passed" stays "Passed".
    """

    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate

    verdict = EvalTemplate(
        name=str(uuid4()),
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
    )
    EvalTemplate.no_workspace_objects.bulk_create([verdict])
    verdict_id, score_id = str(verdict.id), str(uuid4())
    day0 = _day(datetime.now(UTC) - timedelta(days=5))
    absent = seed.trace(observe_project, day0 + timedelta(hours=10))
    seed.result(absent, verdict_id, day0 + timedelta(hours=11), value=None)
    seed.result(absent, score_id, day0 + timedelta(hours=11), value=0.5)
    passed = seed.trace(observe_project, day0 + timedelta(hours=12))
    seed.result(passed, verdict_id, day0 + timedelta(hours=13), value="Passed")
    seed.result(passed, score_id, day0 + timedelta(hours=13), value=0.5)
    breakdown = {"name": verdict_id, "type": "eval_metric", "config_id": verdict_id}
    window = _custom(day0 - timedelta(days=1), day0 + timedelta(days=2))
    for name, metric in {
        "system metric": _system_metric("trace_count", "count"),
        "same template": _eval_metric(
            verdict_id, aggregation="count", output_type="PASS_FAIL"
        ),
        "other template": _eval_metric(score_id, aggregation="count"),
    }.items():
        result = _run_worker(
            seed,
            _config([observe_project], window, [metric], breakdowns=[breakdown]),
        )
        labels = {
            line["name"]
            for line in _metric(result, metric["name"])["series"]
            if any(point["value"] for point in line["data"])
        }
        assert labels == {"(not set)", "Passed"}, (name, labels)


# ---------------------------------------------------------------------------
# 7. Regression guards for the held candidate's findings (pass on dev)
# ---------------------------------------------------------------------------


def test_trace_count_filters_and_breakdowns_see_every_span_not_only_the_root(
    observe_project, seed
):
    """Regression guard; passes on dev. The held candidate patch evaluated
    trace_count filters and breakdowns on the root span only.

    Rule encoded (dev behaviour, unchanged by TH-10545): a trace counts in a
    day when ANY of its spans that started that day matches the filter or
    breakdown value. Here the model is set on child spans only; one trace's
    root started before the window. Eval-score changes must not reach
    system metrics, so both traces count: 2 with the filter, 2 in the "gpt-x"
    breakdown series.
    """

    now = datetime.now(UTC)
    day0 = _day(now - timedelta(days=5))
    seed.trace(
        observe_project,
        day0 + timedelta(hours=10),
        children=[(timedelta(minutes=1), "gpt-x", 100)],
    )
    seed.trace(
        observe_project,
        day0 - timedelta(hours=2),
        children=[(timedelta(hours=3), "gpt-x", 50)],
    )
    window = _custom(day0, day0 + timedelta(days=2))
    # The canonical shape the widget editor sends for a span-model filter.
    model_filter = {
        "column_id": "model",
        "property_id": "system_attribute:traces:model",
        "source": "traces",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "gpt-x",
            "col_type": "SYSTEM_METRIC",
        },
    }

    filtered = _run_worker(
        seed,
        _config(
            [observe_project],
            window,
            [_system_metric("trace_count", "count", filters=[model_filter])],
        ),
    )
    assert _points(filtered, "trace_count") == {day0: 2}

    broken_down = _run_worker(
        seed,
        _config(
            [observe_project],
            window,
            [_system_metric("trace_count", "count")],
            breakdowns=[{"name": "model", "type": "system_metric"}],
        ),
    )
    assert _points(broken_down, "trace_count", series="gpt-x") == {day0: 2}


def test_span_metric_counts_spans_whose_trace_root_is_outside_the_window(
    observe_project, seed
):
    """Regression guard; passes on dev. The held candidate patch required the
    trace root to be inside the chart window for span metrics.

    Rule encoded (dev behaviour, unchanged by TH-10545): span metrics read the
    spans that started inside the window, wherever their root started. The
    trace-date rule applies to eval scores only. Tokens: 100 (root in window)
    + 50 (root 2 hours before the window) = 150.
    """

    now = datetime.now(UTC)
    day0 = _day(now - timedelta(days=5))
    seed.trace(
        observe_project,
        day0 + timedelta(hours=10),
        children=[(timedelta(minutes=1), "gpt-x", 100)],
    )
    seed.trace(
        observe_project,
        day0 - timedelta(hours=2),
        children=[(timedelta(hours=3), "gpt-x", 50)],
    )

    result = _run_worker(
        seed,
        _config(
            [observe_project],
            _custom(day0, day0 + timedelta(days=2)),
            [_system_metric("tokens", "sum")],
        ),
    )
    assert _points(result, "tokens") == {day0: 150}


# ---------------------------------------------------------------------------
# 8. Preset snapshot cache
# ---------------------------------------------------------------------------


def _capture_identity(seed, query_config):
    """Cache identity the endpoint lane uses for ``query_config`` (cold run)."""

    probe = MagicMock(side_effect=read_or_schedule_exact_snapshot)
    result = _run_public(seed, query_config, snapshot_probe=probe)
    [call] = [
        item for item in probe.call_args_list if item.args[0] == _DASHBOARD_NAMESPACE
    ]
    return deepcopy(call.args[1]), result


def _worker_payload(seed, identity):
    """The exact-aggregation worker's payload for ``identity`` (not published)."""

    from tracer.tasks.exact_aggregation import _dashboard_payload

    with patch(
        "tracer.views.dashboard.V2AnalyticsQueryService", return_value=seed.usage
    ):
        return deepcopy(_dashboard_payload(deepcopy(identity)))


def _worker_publish(seed, identity):
    """Recompute and publish exactly as the exact-aggregation worker does."""

    payload = _worker_payload(seed, identity)
    publish_exact_snapshot(_DASHBOARD_NAMESPACE, identity, payload)
    return payload


def _pre_fix_snapshot_key(identity):
    """The key dev c735cc1f's view and worker use for this widget."""

    pre_fix_identity = {
        "workspace_id": identity["workspace_id"],
        "query_config": identity["query_config"],
        "trace_snapshot_semantics": _PRE_FIX_TRACE_SEMANTICS,
    }
    canonical = json.dumps(
        normalized_snapshot_identity(pre_fix_identity),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    key = (
        f"exact-aggregation:v{_PRE_FIX_CACHE_VERSION}:{_DASHBOARD_NAMESPACE}:"
        f"{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    )
    if (
        exact_aggregation_cache._CACHE_VERSION == _PRE_FIX_CACHE_VERSION
        and identity == pre_fix_identity
    ):
        # On dev this reconstruction must be the live key, so the test fails
        # because the old snapshot is served, not because the key is wrong.
        assert key == snapshot_cache_key(_DASHBOARD_NAMESPACE, identity)
    return key


def test_snapshot_computed_before_the_fix_is_not_served(observe_project, seed):
    """Dev failure: dev serves a 30D snapshot published by pre-fix code (old
    semantics) for 30 days, until someone presses Reload; this test plants one
    under the exact key dev uses, holding a sentinel on the trace day, and dev
    returns it.
    Fixed: a snapshot computed under the pre-fix semantics is never served
    (for example by bumping ``_CACHE_VERSION`` or marking the identity), so the
    request computes the trace-day score, 0.2, which equals the custom range.
    """

    template = str(uuid4())
    now = datetime.now(UTC)
    trace_day = _day(now - timedelta(days=20))
    trace = seed.trace(observe_project, trace_day + timedelta(hours=10))
    seed.result(trace, template, trace_day + timedelta(hours=11), value=0.2)
    preset_config = _config(
        [observe_project], {"preset": "30D"}, [_eval_metric(template)]
    )

    identity, _fresh = _capture_identity(seed, preset_config)
    stale_payload = _worker_payload(seed, identity)
    planted = 0
    for metric in stale_payload["metrics"]:
        for line in metric["series"]:
            for point in line["data"]:
                stamp = datetime.fromisoformat(
                    str(point["timestamp"]).replace("Z", "+00:00")
                )
                is_trace_day = stamp.replace(tzinfo=stamp.tzinfo or UTC) == trace_day
                point["value"] = _STALE_SENTINEL if is_trace_day else None
                planted += is_trace_day
    assert planted == 1, "precondition: the trace day is a 30D bucket"
    # A plant the probe would not serve falls through to a fresh read and
    # would pass on dev for the wrong reason.
    assert _dashboard_snapshot_is_renderable(stale_payload)
    cache.set(
        _pre_fix_snapshot_key(identity),
        {
            "v": _PRE_FIX_CACHE_VERSION,
            "completed_at": now.isoformat(),
            "payload": stale_payload,
        },
        timeout=3600,
    )

    served = _run_public(seed, preset_config)
    served_values = [
        point["value"]
        for line in _metric(served, "trace_eval_score")["series"]
        for point in line["data"]
    ]
    assert _STALE_SENTINEL not in served_values, "pre-fix snapshot was served"
    assert _points(served) == pytest.approx({trace_day: 0.2})

    custom = _run_worker(
        seed,
        _config(
            [observe_project],
            _custom(trace_day - timedelta(days=2), trace_day + timedelta(days=3)),
            [_eval_metric(template)],
        ),
    )
    assert _points(custom) == _points(served)


def test_reloaded_preset_snapshot_matches_custom_range_after_reevaluation(
    observe_project, seed
):
    """Dev failure: after the re-evaluation and a Reload, the worker publishes
    a 30D snapshot that elects the re-evaluation and plots it on the
    re-evaluation day (0.8 there, the trace day empty), while a custom range
    around the trace day shows 0.2. The cached preset and the custom range
    disagree about the same day.
    Fixed: the served (cached) 30D snapshot and the custom range both show
    0.8 on the trace day and nothing on the re-evaluation day.

    Not encoded: without a Reload, a preset snapshot published before the
    re-evaluation keeps the older value (dashboard snapshots are reload-only
    and live 30 days). That is the cache design, not this fix; see tests.md.
    """

    template = str(uuid4())
    now = datetime.now(UTC)
    trace_day = _day(now - timedelta(days=20))
    trace = seed.trace(observe_project, trace_day + timedelta(hours=10))
    seed.result(trace, template, trace_day + timedelta(hours=11), value=0.2)
    preset_config = _config(
        [observe_project], {"preset": "30D"}, [_eval_metric(template)]
    )

    identity, _fresh = _capture_identity(seed, preset_config)
    _worker_publish(seed, identity)

    seed.result(trace, template, now - timedelta(days=2), value=0.8)
    _worker_publish(seed, identity)  # Reload after the re-evaluation

    served = _run_public(seed, preset_config)
    assert served.get("query_cached") is True, "precondition: served from cache"
    custom = _run_worker(
        seed,
        _config(
            [observe_project],
            _custom(trace_day - timedelta(days=2), trace_day + timedelta(days=3)),
            [_eval_metric(template)],
        ),
    )
    assert _points(served) == pytest.approx({trace_day: 0.8})
    assert _points(custom) == pytest.approx({trace_day: 0.8})


def test_long_trace_date_and_child_membership_do_not_move_with_chart_window(
    observe_project, seed
):
    template = str(uuid4())
    today = _day(datetime.now(UTC))
    monday = today - timedelta(days=today.weekday() + 7)
    thursday = monday + timedelta(days=3)
    trace = seed.trace(observe_project, monday, children=[(timedelta(days=3), None, 1)])
    seed.result(trace, template, monday, value=0.9, level="trace")
    seed.result(trace, template, thursday, value=0.1, span_id=trace.children[0])
    eval_filter = {
        "column_id": template,
        "source": "traces",
        "output_type": "SCORE",
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": 0.5,
        },
    }
    breakdown = {
        "name": template,
        "type": "eval_metric",
        "config_id": template,
        "output_type": "SCORE",
    }

    def query(time_range, metrics, breakdowns=()):
        config = _config([observe_project], time_range, metrics, breakdowns=breakdowns)
        return _run_worker(seed, config)

    for start, end in [
        (monday, monday + timedelta(days=7)),
        (thursday, thursday + timedelta(days=1)),
    ]:
        time_range = _custom(start, end)
        result = query(
            time_range,
            [
                _eval_metric(template),
                _system_metric("trace_count", "count", filters=[eval_filter]),
            ],
        )
        assert _points(result) == ({monday: 0.9} if start == monday else {})
        expected = {monday: 1, thursday: 1} if start == monday else {thursday: 1}
        assert _points(result, "trace_count") == expected
        result = query(
            time_range, [_system_metric("trace_count", "count")], [breakdown]
        )
        assert _points(result, "trace_count", series="90") == expected
        assert not _points(result, "trace_count", series="10")


def test_spanless_legacy_trace_uses_live_trace_creation_date(observe_project, seed):
    template = str(uuid4())
    start = _day(datetime.now(UTC) - timedelta(days=15))
    trace = seed.trace(observe_project, start, root_span=False)
    seed.result(trace, template, start + timedelta(days=5), value=0.75, stamp=None)
    config = _config(
        [observe_project],
        _custom(start, start + timedelta(days=1)),
        [_eval_metric(template)],
    )
    assert _points(_run_worker(seed, config)) == {start: 0.75}


def test_rootless_trace_and_standalone_use_distinct_fallback_dates(
    observe_project, seed
):
    template = str(uuid4())
    day = _day(datetime.now(UTC) - timedelta(days=15))
    child_day = day + timedelta(days=3)
    standalone_day = day + timedelta(days=4)
    trace = seed.trace(
        observe_project, day, root_span=False, children=[(timedelta(days=3), None, 0)]
    )
    seed.result(trace, template, standalone_day, value=0.2, span_id=trace.children[0])
    standalone = SimpleNamespace(id="", root="", project=observe_project)
    seed.result(standalone, template, standalone_day, value=0.8, stamp=None)
    seed.result(standalone, template, day + timedelta(days=8), value=0.4, stamp=None)
    config = _config(
        [observe_project],
        _custom(day, day + timedelta(days=5)),
        [_eval_metric(template)],
    )
    assert _points(_run_worker(seed, config)) == {child_day: 0.2, standalone_day: 0.8}


def test_candidate_lookup_does_not_resurrect_superseded_usage_versions(
    observe_project, seed, request
):
    # Do not let a background merge remove the older versions before the read.
    client, table = seed.usage.ch_client, seed.usage.usage_table
    client.execute(f"SYSTEM STOP MERGES {table}")
    request.addfinalizer(lambda: client.execute(f"SYSTEM START MERGES {table}"))
    template = str(uuid4())
    start = _day(datetime.now(UTC) - timedelta(days=15))
    inside = seed.trace(observe_project, start)
    outside = seed.trace(observe_project, start + timedelta(days=3))
    when = start + timedelta(days=5)
    first = seed.result(inside, template, when, value=0.8)
    seed.result(outside, template, when, value=0.9, usage_id=first, version=2)
    second = seed.result(inside, template, when, value=0.7)
    seed.result(
        inside, template, when, value=0.7, usage_id=second, version=2, tombstone=True
    )
    config = _config(
        [observe_project],
        _custom(start, start + timedelta(days=1)),
        [_eval_metric(template)],
    )
    assert _points(_run_worker(seed, config)) == {}


@pytest.mark.parametrize("output", ["reason", "", None])
@pytest.mark.parametrize("filter_value", ["Passed", "0"])
def test_untyped_text_filter_does_not_fail_or_broaden_widget(
    observe_project, seed, output, filter_value
):
    template = str(uuid4())
    start = _day(datetime.now(UTC) - timedelta(days=5))
    trace = seed.trace(observe_project, start)
    seed.result(trace, template, start, value=0.8)
    # A genuine numeric 0: if the text "0" were ever compiled as a numeric
    # comparison (eval_score = 0), this trace would be counted.
    zero = seed.trace(observe_project, start + timedelta(hours=1))
    seed.result(zero, template, start + timedelta(hours=1), value=0)
    leaf = {
        "column_id": template,
        "source": "traces",
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": filter_value,
        },
    }
    if output is not None:
        leaf["output_type"] = output
    config = _config(
        [observe_project],
        _custom(start, start + timedelta(days=1)),
        [
            _eval_metric(template),
            _system_metric("trace_count", "count", filters=[leaf]),
        ],
    )
    result = _run_worker(seed, config)
    assert _points(result) == pytest.approx({start: 0.4})
    assert _points(result, "trace_count") == {}


@pytest.mark.parametrize("filter_type", ["text", "string", "categorical"])
@pytest.mark.parametrize(
    ("filter_op", "expected"),
    [
        pytest.param("is_not_null", "recorded", id="is_set"),
        pytest.param("is_null", "missing", id="is_not_set"),
    ],
)
def test_untyped_text_presence_filters_use_elected_membership(
    observe_project, seed, filter_type, filter_op, expected
):
    """Previous PR head fails: an untyped text filter emitted ``0 = 1`` for
    presence operators too, so "is set" and "is not set" both returned nothing
    and "is not set" dropped traces that never ran the eval.
    Fixed: presence goes through the elected-result membership. A recorded
    text result and a recorded number are both present; a trace that never ran
    the eval and one whose latest output is null are missing.
    """

    template = str(uuid4())
    day0 = _day(datetime.now(UTC) - timedelta(days=5))
    text = seed.trace(observe_project, day0 + timedelta(hours=10))
    seed.result(text, template, day0 + timedelta(hours=11), value="good")
    number = seed.trace(observe_project, day0 + timedelta(days=1, hours=10))
    seed.result(number, template, day0 + timedelta(days=1, hours=11), value=0.4)
    seed.trace(observe_project, day0 + timedelta(days=2, hours=10))
    null = seed.trace(observe_project, day0 + timedelta(days=3, hours=10))
    seed.result(null, template, day0 + timedelta(days=3, hours=11), value=None)
    leaf = {
        "column_id": template,
        "source": "traces",
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": None,
        },
    }
    result = _run_worker(
        seed,
        _config(
            [observe_project],
            _custom(day0 - timedelta(days=1), day0 + timedelta(days=5)),
            [_system_metric("trace_count", "count", filters=[leaf])],
        ),
    )
    recorded = {day0: 1, day0 + timedelta(days=1): 1}
    missing = {day0 + timedelta(days=2): 1, day0 + timedelta(days=3): 1}
    assert _points(result, "trace_count") == (
        recorded if expected == "recorded" else missing
    )


def test_eval_window_subjects_exclude_empty_and_nil_trace_ids():
    from tracer.services.clickhouse.query_builders.dashboard import (
        _eval_window_subjects_sql,
    )

    sql = _eval_window_subjects_sql(include_spanless=True)
    assert "eval_window_span.trace_id != ''" in sql
    assert "toUUIDOrZero(eval_window_span.trace_id) !=" in sql
    assert "toUUID('00000000-0000-0000-0000-000000000000')" in sql


def test_system_metric_eval_filter_scopes_workspace_scan_by_org(observe_project, seed):
    template = str(uuid4())
    start = _day(datetime.now(UTC) - timedelta(days=5))
    trace = seed.trace(observe_project, start)
    seed.result(trace, template, start, value=0.8)
    leaf = {
        "metric_type": "eval_metric",
        "metric_name": template,
        "operator": "equal_to",
        "value": 0.8,
        "output_type": "SCORE",
    }
    config = _config(
        [observe_project],
        _custom(start, start + timedelta(days=1)),
        [_system_metric("trace_count", "count", filters=[leaf])],
    )
    result = _run_worker(seed, config)
    assert _points(result, "trace_count") == {start: 1}
    query = "\n".join(seed.usage.queries)
    assert (
        "usage_s_eval_filter_scan_0.organization_id = toUUID(%(s_org_scope_id_0)s)"
    ) in query
    assert (
        "usage_s_eval_filter_scan_0.workspace_id = toUUID(%(s_scope_id_0)s)"
    ) in query


def test_system_metric_eval_breakdown_elects_in_the_filters_tenant_scope(
    observe_project, seed
):
    """Previous PR head: the system-metric eval breakdown elected in the
    workspace alone while the eval metric and the eval filter elect in the
    organization and the workspace. Fixed: one tenant scope for every
    election, so a breakdown and a filter on the same template agree.
    """

    template = str(uuid4())
    start = _day(datetime.now(UTC) - timedelta(days=5))
    trace = seed.trace(observe_project, start)
    seed.result(trace, template, start, value=0.8)
    breakdown = {
        "name": template,
        "type": "eval_metric",
        "config_id": template,
        "output_type": "SCORE",
    }
    result = _run_worker(
        seed,
        _config(
            [observe_project],
            _custom(start, start + timedelta(days=1)),
            [_system_metric("trace_count", "count")],
            breakdowns=[breakdown],
        ),
    )
    assert _points(result, "trace_count", series="80") == {start: 1}
    query = "\n".join(seed.usage.queries)
    assert (
        "usage_sys_bd_ev0_scan.organization_id = toUUID(%(_ev_bd_org_scope_0)s) "
        "AND usage_sys_bd_ev0_scan.workspace_id = toUUID(%(_ev_bd_scope_0)s)"
    ) in query


def test_legacy_filter_template_metadata_respects_visibility(observe_project):
    from accounts.models import Organization, Workspace
    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.services.clickhouse.query_builders.dashboard import (
        DashboardQueryBuilder,
    )

    org = observe_project.organization
    workspace = observe_project.workspace
    sibling = Workspace.objects.create(
        name="Sibling", organization=org, created_by_id=workspace.created_by_id
    )
    other = Organization.objects.create(name="Other")
    builder = DashboardQueryBuilder(
        {
            "organization_id": str(org.id),
            "workspace_id": str(workspace.id),
            "project_ids": [str(observe_project.id)],
        }
    )
    for tenant, space, owner, expected in [
        (org, workspace, OwnerChoices.USER.value, "CHOICE"),
        (org, sibling, OwnerChoices.USER.value, None),
        (other, None, OwnerChoices.USER.value, None),
        (None, None, OwnerChoices.USER.value, None),
        (None, None, OwnerChoices.SYSTEM.value, "CHOICE"),
    ]:
        template = EvalTemplate(
            name=str(uuid4()),
            organization=tenant,
            workspace=space,
            owner=owner,
            config={"output": "Choice"},
        )
        EvalTemplate.no_workspace_objects.bulk_create([template])
        assert (
            builder._eval_filter_output_type(
                {"canonical_filter": {"filter_config": {"col_type": "EVAL_METRIC"}}},
                str(template.id),
            )
            == expected
        )
