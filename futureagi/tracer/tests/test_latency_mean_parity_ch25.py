"""Real-ClickHouse proof: every Observe latency chart publishes one mean.

One seeded population is read through every live latency path, each through
its public entry point, on the repository's own v2 schema:

* trace and span graphs (``fetch_system_metric_graph_ch``), inline and on
  the background worker;
* the project ChartsView bundle (``fetch_all_system_metrics_ch``);
* the session graph (``fetch_session_graph_ch``), which is always a
  background snapshot;
* the users graph (``fetch_user_system_metric_graph_ch``), inline and on the
  background worker.

Each is read UNFILTERED (only the date window) and with filters that match
every span: a system column (``model``) and a span attribute
(``customer_tier``). The background runs go through the real scheduling
code: the refresh the dispatcher enqueues runs synchronously on the real
worker function, publishes into the (test) cache, and the public entry point
then serves that snapshot through its cache guard.

**Definitions (the oracle).** "Live" is the latest ``_version`` of each span,
dropping tombstones; only spans starting inside the window count.

* trace, span and ChartsView: the mean ``latency_ms`` of the live spans that
  start in the bucket; the Traffic bars are their count.
* session: dev's exact definition. A session's latency is the mean of its
  live ROOT spans in the window; the bucket (by the session's first root
  start) publishes the mean of those session values; Traffic counts sessions.
* users: the pooled mean. The bucket (by each user trace's first live span in
  the window) publishes sum(latency) / count over every live span of those
  user traces, never a mean of per-user means; Traffic counts users.

**Population.** Skewed latencies (the mean sits far from the median), traces
of one root and several children, sessions and users spanning several traces,
re-versioned spans (a newer version with another latency), tombstoned spans
(roots included), a trace whose root starts before the window while its
children start inside it, children that start after the window, and whole
traces outside the window.

**Two stores.** In ``merged`` every insert is followed by ``OPTIMIZE FINAL``,
so storage holds only the live version of each span; every path must equal
the oracle there. In ``unmerged`` merges are stopped, so the old versions and
the pre-tombstone rows are still physical rows. The paths that collapse
versions (ChartsView, session, users) must still equal the oracle; the trace
and span graphs read physical rows by design (dev's interactive route, not
changed here), so on that store they are only required to publish the same
value unfiltered and with an always-true filter.
"""

from __future__ import annotations

import math
import random
import statistics
import uuid
from collections import defaultdict
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.test import override_settings

from conftest import (
    _ch_test_apply_v2_schema,
    _ch_test_native_client,
    _ch_test_owned_database,
)

pytestmark = pytest.mark.integration

PROJECT_ID = "7c1e5a2b-3d4f-4a6b-8c9d-0e1f2a3b4c5d"
ORGANIZATION_ID = "8d2f6b3c-4e5a-4b7c-9d0e-1f2a3b4c5d6e"
MODEL = "mean-parity-model"
TIER = "gold"
HOUR_A = datetime(2026, 6, 10, 0, tzinfo=UTC)
HOUR_B = HOUR_A + timedelta(hours=1)
HOUR_C = HOUR_A + timedelta(hours=2)
WINDOW_END = HOUR_A + timedelta(hours=3)
HOURS = (HOUR_A, HOUR_B, HOUR_C)
TOLERANCE = 1e-6

_WINDOW_FILTER = {
    "column_id": "created_at",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": [HOUR_A.isoformat(), WINDOW_END.isoformat()],
    },
}
_ALWAYS_TRUE = {
    "model": {
        "column_id": "model",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": MODEL,
        },
    },
    "tier": {
        "column_id": "customer_tier",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": TIER,
        },
    },
}
FILTER_CASES = ("none", *_ALWAYS_TRUE)

_TIMEOUT_MS = 60_000


def _filters(case: str) -> list[dict]:
    if case == "none":
        return [_WINDOW_FILTER]
    return [_WINDOW_FILTER, _ALWAYS_TRUE[case]]


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def _uid(*parts) -> str:
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, "mean-parity/" + "/".join(map(str, parts)))
    )


def _span(trace, index, parent, start, latency, session, user):
    return {
        "trace_id": trace,
        "id": _uid(trace, "span", index),
        "parent_span_id": parent,
        "start_time": start,
        "latency_ms": int(latency),
        "trace_session_id": uuid.UUID(session),
        "end_user_id": uuid.UUID(user),
        "is_deleted": 0,
        "_version": 1,
    }


def _trace(label, root_start, latencies, session, user, *, child_starts=None):
    """One root (the first latency) and its children, all in ``root_start``'s
    hour unless ``child_starts`` places them elsewhere."""

    trace = _uid(label)
    rows = []
    root_id = _uid(trace, "span", 0)
    for index, latency in enumerate(latencies):
        if index == 0:
            start = root_start
        elif child_starts is not None:
            start = child_starts[index - 1]
        else:
            start = root_start + timedelta(milliseconds=17 * index)
        rows.append(
            _span(
                trace,
                index,
                "" if index == 0 else root_id,
                start,
                latency,
                session,
                user,
            )
        )
    return rows


def _skewed(rng, count):
    # Lognormal: the mean sits well above the median.
    return [max(1, int(rng.lognormvariate(4.5, 1.1))) for _ in range(count)]


def _base_rows():
    rng = random.Random(20260926)
    rows = []
    layout = {
        HOUR_A: (12, 5, 3, 2),
        HOUR_B: (20, 6, 4, 3),
        HOUR_C: (90, 8, 6, 5),
    }
    for hour, (traces, base_spans, per_session, per_user) in layout.items():
        for number in range(traces):
            label = f"{hour:%H}-t{number}"
            session = _uid(f"{hour:%H}-s{number // per_session}")
            user = _uid(f"{hour:%H}-u{number // per_user}")
            start = hour + timedelta(seconds=37 * number + 11)
            # Uneven trace sizes, so a pooled mean differs from a mean of
            # per-trace or per-user means.
            spans = base_spans + 3 * (number % 4)
            latencies = _skewed(rng, spans)
            child_starts = None
            if hour == HOUR_C and number % 7 == 0:
                # The last child starts after the window: it never counts.
                child_starts = [
                    start + timedelta(milliseconds=17 * index)
                    for index in range(1, spans - 1)
                ] + [WINDOW_END + timedelta(minutes=10)]
            rows.extend(
                _trace(
                    label,
                    start,
                    latencies,
                    session,
                    user,
                    child_starts=child_starts,
                )
            )
    # A trace whose root starts before the window and whose children start
    # inside it; its session and user also own an in-window trace.
    rows.extend(
        _trace(
            "straddle",
            HOUR_A - timedelta(minutes=30),
            [900, 40, 55, 3_100],
            _uid("00-s0"),
            _uid("00-u0"),
            child_starts=[
                HOUR_A + timedelta(minutes=5, milliseconds=index) for index in range(3)
            ],
        )
    )
    # Whole traces outside the window, with their own session and user.
    rows.extend(
        _trace(
            "before",
            HOUR_A - timedelta(hours=2),
            [5_000, 7_000],
            _uid("before-s"),
            _uid("before-u"),
        )
    )
    rows.extend(
        _trace(
            "after",
            WINDOW_END + timedelta(hours=1),
            [6_000, 8_000],
            _uid("after-s"),
            _uid("after-u"),
        )
    )
    return rows


def _newer_versions(rows):
    """Every 9th span gets a newer version with another latency; every 13th
    (roots included) a newer tombstone. Same replacement key, same hour."""

    newer = []
    for index, row in enumerate(rows):
        if index % 13 == 5:
            newer.append({**row, "is_deleted": 1, "_version": 2})
        elif index % 9 == 4:
            newer.append(
                {**row, "latency_ms": row["latency_ms"] * 4 + 11, "_version": 2}
            )
    return newer


def _live(all_rows):
    latest = {}
    for row in all_rows:
        key = (row["trace_id"], row["id"])
        if key not in latest or row["_version"] > latest[key]["_version"]:
            latest[key] = row
    return [row for row in latest.values() if not row["is_deleted"]]


def _in_window(row):
    return HOUR_A <= row["start_time"] < WINDOW_END


def _hour(moment):
    return moment.replace(minute=0, second=0, microsecond=0)


def span_oracle(live):
    values = defaultdict(list)
    for row in live:
        if _in_window(row):
            values[_hour(row["start_time"])].append(row["latency_ms"])
    return {
        hour: (statistics.fmean(values[hour]), len(values[hour]))
        for hour in HOURS
        if values[hour]
    }


def session_oracle(live):
    roots = defaultdict(list)
    for row in live:
        if _in_window(row) and not row["parent_span_id"]:
            roots[row["trace_session_id"]].append(row)
    buckets = defaultdict(list)
    for session_roots in roots.values():
        start = min(row["start_time"] for row in session_roots)
        buckets[_hour(start)].append(
            statistics.fmean(row["latency_ms"] for row in session_roots)
        )
    return {
        hour: (statistics.fmean(buckets[hour]), len(buckets[hour]))
        for hour in HOURS
        if buckets[hour]
    }


def users_oracle(live):
    traces = defaultdict(list)
    for row in live:
        if _in_window(row):
            traces[(row["end_user_id"], row["trace_id"])].append(row)
    latencies = defaultdict(list)
    users = defaultdict(set)
    for (user, _trace_id), spans in traces.items():
        hour = _hour(min(row["start_time"] for row in spans))
        latencies[hour].extend(row["latency_ms"] for row in spans)
        users[hour].add(user)
    return {
        hour: (statistics.fmean(latencies[hour]), len(users[hour]))
        for hour in HOURS
        if latencies[hour]
    }


def mean_of_user_means(live):
    """The definition the users graph must NOT publish."""

    traces = defaultdict(list)
    for row in live:
        if _in_window(row):
            traces[(row["end_user_id"], row["trace_id"])].append(row)
    per_user = defaultdict(lambda: defaultdict(list))
    for (user, _trace_id), spans in traces.items():
        hour = _hour(min(row["start_time"] for row in spans))
        per_user[hour][user].append(
            statistics.fmean(row["latency_ms"] for row in spans)
        )
    return {
        hour: statistics.fmean(
            statistics.fmean(means) for means in per_user[hour].values()
        )
        for hour in HOURS
        if per_user[hour]
    }


_SPAN_COLUMNS = (
    "project_id",
    "observation_type",
    "start_time",
    "end_time",
    "trace_id",
    "id",
    "parent_span_id",
    "name",
    "latency_ms",
    "status",
    "model",
    "provider",
    "trace_session_id",
    "end_user_id",
    "org_id",
    "attrs_string",
    "created_at",
    "is_deleted",
    "_version",
)


def _span_tuple(row):
    return (
        uuid.UUID(PROJECT_ID),
        "llm",
        row["start_time"],
        row["start_time"] + timedelta(milliseconds=row["latency_ms"]),
        row["trace_id"],
        row["id"],
        row["parent_span_id"],
        "mean-parity",
        row["latency_ms"],
        "ERROR" if int(row["id"][-1], 16) % 3 == 0 else "OK",
        MODEL,
        "mean-parity-provider",
        row["trace_session_id"],
        row["end_user_id"],
        uuid.UUID(ORGANIZATION_ID),
        {"customer_tier": TIER},
        row["start_time"],
        row["is_deleted"],
        row["_version"],
    )


def _load(client, base, newer, *, merged):
    if not merged:
        client.execute("SYSTEM STOP MERGES spans")
    insert = f"INSERT INTO spans ({', '.join(_SPAN_COLUMNS)}) VALUES"
    # Two statements, so the newer versions land in their own parts.
    client.execute(insert, [_span_tuple(row) for row in base])
    client.execute(insert, [_span_tuple(row) for row in newer])
    users = {row["end_user_id"] for row in base}
    client.execute(
        "INSERT INTO end_users"
        " (project_id, end_user_id, organization_id, user_id, first_seen, is_deleted)"
        " VALUES",
        [
            (
                uuid.UUID(PROJECT_ID),
                user,
                uuid.UUID(ORGANIZATION_ID),
                f"user-{user.hex[:8]}",
                HOUR_A - timedelta(days=1),
                0,
            )
            for user in users
        ],
    )
    client.execute("OPTIMIZE TABLE end_users FINAL")
    if merged:
        client.execute("OPTIMIZE TABLE spans FINAL")
    physical = client.execute(
        "SELECT count() FROM spans WHERE project_id = %(project)s",
        {"project": uuid.UUID(PROJECT_ID)},
    )[0][0]
    return physical


# ---------------------------------------------------------------------------
# Live ClickHouse
# ---------------------------------------------------------------------------


class _LiveAnalytics:
    """Run the product's own statements on the test database."""

    supports_per_query_read_settings = True

    def __init__(self, client):
        self._client = client
        self.statements = []

    def execute_ch_query(
        self, query, params=None, *, timeout_ms=None, settings=None, **_
    ):
        del timeout_ms
        bound = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in (params or {}).items()
        }
        self.statements.append(query)
        rows, columns = self._client.execute(
            query, bound, with_column_types=True, settings=dict(settings or {})
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows],
            columns=names,
        )


def _by_hour(points, field="value"):
    values = {}
    for point in points:
        stamp = datetime.fromisoformat(str(point["timestamp"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        values[stamp.astimezone(UTC)] = float(point.get(field) or 0)
    return {hour: values.get(hour) for hour in HOURS}


def _background_harness(patcher, analytics):
    """Run every refresh the dispatchers enqueue synchronously, on the real
    worker function, against the live database; nothing else is faked."""

    from django.core.cache import cache

    from tracer.services import exact_aggregation_cache
    from tracer.services.clickhouse import graph_dispatch
    from tracer.tasks import exact_aggregation

    cache.clear()
    enqueued = []
    patcher.setattr(
        exact_aggregation_cache,
        "_configured_exact_aggregation_task_queue",
        lambda: "exact_aggregation",
    )
    patcher.setattr(
        exact_aggregation, "_exact_observe_analytics", lambda: nullcontext(analytics)
    )
    patcher.setattr(
        exact_aggregation, "_reauthorize_exact_observe_project", lambda _identity: None
    )

    def apply_async(**call):
        kwargs = call["kwargs"]
        enqueued.append((kwargs["namespace"], kwargs["identity"]))
        exact_aggregation.refresh_exact_aggregation_snapshot._original_func(**kwargs)
        return SimpleNamespace(id=f"workflow-{len(enqueued)}")

    patcher.setattr(
        exact_aggregation.refresh_exact_aggregation_snapshot, "apply_async", apply_async
    )
    interactive_ms = graph_dispatch.GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS
    real_raw_fits = graph_dispatch.raw_graph_scan_fits_wall
    real_user_fits = graph_dispatch.user_graph_scan_fits_wall
    state = {"background": False}

    # Background mode: the interactive wall never fits, the worker's does.
    def raw_fits(rows, *, remaining_ms, raw_log_marks=None):
        if state["background"]:
            return remaining_ms > interactive_ms
        return real_raw_fits(
            rows, remaining_ms=remaining_ms, raw_log_marks=raw_log_marks
        )

    def user_fits(rows, *, remaining_ms):
        if state["background"]:
            return remaining_ms > interactive_ms
        return real_user_fits(rows, remaining_ms=remaining_ms)

    real_seed = graph_dispatch._select_raw_trace_seed_candidate

    # ... and no trace-ID witness rescues the read onto the interactive wall.
    def seed(**call):
        if state["background"] and call["timeout_ms"] <= interactive_ms:
            return None, 0
        return real_seed(**call)

    patcher.setattr(graph_dispatch, "raw_graph_scan_fits_wall", raw_fits)
    patcher.setattr(graph_dispatch, "user_graph_scan_fits_wall", user_fits)
    patcher.setattr(graph_dispatch, "_select_raw_trace_seed_candidate", seed)
    return state, enqueued


def _read_every_path(analytics, patcher):
    from tracer.services.clickhouse import graph_dispatch, session_graph

    state, enqueued = _background_harness(patcher, analytics)
    envelopes = {}
    scheduled = {}
    inline_enqueued = {}

    def record(label, envelope, before=None):
        envelopes[label] = envelope
        if before is not None:
            scheduled[label] = len(enqueued) > before

    for case in FILTER_CASES:
        filters = _filters(case)
        for observe_type in ("trace", "span"):
            state["background"] = False
            record(
                f"{observe_type}/inline/{case}",
                graph_dispatch.fetch_system_metric_graph_ch(
                    analytics=analytics,
                    project_id=PROJECT_ID,
                    filters=filters,
                    interval="hour",
                    metric_id="latency",
                    observe_type=observe_type,
                    timeout_ms=_TIMEOUT_MS,
                ),
            )
            state["background"] = True
            before = len(enqueued)
            record(
                f"{observe_type}/background/{case}",
                graph_dispatch.fetch_system_metric_graph_ch(
                    analytics=analytics,
                    project_id=PROJECT_ID,
                    filters=filters,
                    interval="hour",
                    metric_id="latency",
                    observe_type=observe_type,
                    timeout_ms=_TIMEOUT_MS,
                    organization_id=ORGANIZATION_ID,
                ),
                before,
            )
        state["background"] = False
        record(
            f"charts/inline/{case}",
            graph_dispatch.fetch_all_system_metrics_ch(
                analytics=analytics,
                project_id=PROJECT_ID,
                filters=filters,
                interval="hour",
                timeout_ms=_TIMEOUT_MS,
            ),
        )
        if case == "none":
            # The unfiltered chart's root estimate is tiny here: it is
            # computed inline, and nothing is enqueued. (A span-level filter
            # keeps the background path, so only this case has an inline row.)
            before = len(enqueued)
            record(
                f"session/inline/{case}",
                session_graph.fetch_session_graph_ch(
                    analytics=analytics,
                    project_id=PROJECT_ID,
                    filters=filters,
                    interval="hour",
                    req_data_config={"type": "SYSTEM_METRIC", "id": "latency"},
                    organization_id=ORGANIZATION_ID,
                ),
            )
            inline_enqueued[f"session/inline/{case}"] = len(enqueued) > before
        before = len(enqueued)
        # Inline off, so the same chart runs on the real worker.
        with override_settings(SESSION_GRAPH_INLINE_MAX_ESTIMATED_ROWS=0):
            record(
                f"session/background/{case}",
                session_graph.fetch_session_graph_ch(
                    analytics=analytics,
                    project_id=PROJECT_ID,
                    filters=filters,
                    interval="hour",
                    req_data_config={"type": "SYSTEM_METRIC", "id": "latency"},
                    organization_id=ORGANIZATION_ID,
                ),
                before,
            )
        record(
            f"users/inline/{case}",
            graph_dispatch.fetch_user_system_metric_graph_ch(
                analytics=analytics,
                project_id=PROJECT_ID,
                filters=filters,
                interval="hour",
                metric_id="latency",
                timeout_ms=_TIMEOUT_MS,
            ),
        )
        state["background"] = True
        before = len(enqueued)
        record(
            f"users/background/{case}",
            graph_dispatch.fetch_user_system_metric_graph_ch(
                analytics=analytics,
                project_id=PROJECT_ID,
                filters=filters,
                interval="hour",
                metric_id="latency",
                timeout_ms=_TIMEOUT_MS,
                organization_id=ORGANIZATION_ID,
            ),
            before,
        )
        state["background"] = False
    return envelopes, enqueued, scheduled, inline_enqueued


def _series(label, envelope):
    """``{hour: (value, traffic)}`` of one envelope."""

    if label.startswith("charts/"):
        values = _by_hour(envelope["latency"])
        traffic = _by_hour(envelope["traffic"], field="traffic")
    else:
        values = _by_hour(envelope["data"])
        traffic = _by_hour(envelope["data"], field="primary_traffic")
    return {hour: (values[hour], traffic[hour]) for hour in HOURS}


def _store(merged):
    base = _base_rows()
    newer = _newer_versions(base)
    live = _live([*base, *newer])
    prefix = "test_latency_mean_" + ("merged_" if merged else "unmerged_")
    with _ch_test_owned_database(prefix) as database:
        _ch_test_apply_v2_schema(database)
        with _ch_test_native_client(database=database) as client:
            physical = _load(client, base, newer, merged=merged)
            analytics = _LiveAnalytics(client)
            with pytest.MonkeyPatch.context() as patcher:
                envelopes, enqueued, scheduled, inline_enqueued = _read_every_path(
                    analytics, patcher
                )
    return SimpleNamespace(
        base=base,
        newer=newer,
        live=live,
        physical=physical,
        envelopes=envelopes,
        series={label: _series(label, env) for label, env in envelopes.items()},
        enqueued=enqueued,
        scheduled=scheduled,
        inline_enqueued=inline_enqueued,
    )


@pytest.fixture(scope="module")
def merged():
    return _store(merged=True)


@pytest.fixture(scope="module")
def unmerged():
    return _store(merged=False)


def _oracle_for(label, live):
    surface = label.split("/", 1)[0]
    if surface in {"trace", "span", "charts"}:
        return span_oracle(live)
    if surface == "session":
        return session_oracle(live)
    return users_oracle(live)


def _assert_matches_oracle(label, series, oracle):
    for hour in HOURS:
        value, traffic = series[hour]
        expected, expected_traffic = oracle.get(hour, (0.0, 0))
        assert value is not None, f"{label} published no {hour:%H}:00 bucket"
        assert abs(value - expected) <= TOLERANCE, (label, hour, value, expected)
        assert traffic == expected_traffic, (label, hour, traffic, expected_traffic)


# ---------------------------------------------------------------------------
# The seed exercises what it claims to
# ---------------------------------------------------------------------------


def test_seed_holds_versions_tombstones_and_out_of_window_rows(merged):
    base, newer, live = merged.base, merged.newer, merged.live
    tombstones = [row for row in newer if row["is_deleted"]]
    reversioned = [row for row in newer if not row["is_deleted"]]
    assert len(tombstones) >= 20 and len(reversioned) >= 30
    assert any(not row["parent_span_id"] for row in tombstones), "no root tombstone"
    assert sum(not _in_window(row) for row in live) >= 10
    assert len(live) == len(base) - len(tombstones)
    # Merged storage holds one row per span (tombstones are kept as rows).
    assert merged.physical == len(base)


def test_unmerged_store_still_holds_every_physical_version(unmerged):
    assert unmerged.physical == len(unmerged.base) + len(unmerged.newer)


@pytest.mark.parametrize("hour", HOURS, ids=["a", "b", "c"])
def test_seed_separates_mean_median_and_the_rejected_definitions(merged, hour):
    live = merged.live
    in_hour = [
        row["latency_ms"]
        for row in live
        if _in_window(row) and _hour(row["start_time"]) == hour
    ]
    mean = statistics.fmean(in_hour)
    assert abs(mean - statistics.median(in_hour)) > 0.2 * mean
    # The users graph's pooled mean differs from a mean of per-user means.
    assert abs(users_oracle(live)[hour][0] - mean_of_user_means(live)[hour]) > 0.5
    # Dev's session definition differs from pooling every span of a session.
    assert abs(session_oracle(live)[hour][0] - users_oracle(live)[hour][0]) > 0.5


# ---------------------------------------------------------------------------
# Merged store: every path is the oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("store", ["merged", "unmerged"])
def test_every_path_completes_and_background_paths_ran_on_the_worker(request, store):
    observed = request.getfixturevalue(store)
    for label, envelope in observed.envelopes.items():
        assert envelope.get("query_status") == "complete", (label, envelope)
    assert observed.scheduled, "no background path was read"
    for label, ran_on_worker in observed.scheduled.items():
        assert ran_on_worker, f"{label} did not run on the background worker"


def test_every_latency_envelope_names_the_mean(merged):
    for label, envelope in merged.envelopes.items():
        if label.startswith("charts/"):
            continue  # The bundle's statistics are stamped by the view.
        assert envelope.get("metric_statistic") == "mean", label


def test_unfiltered_latency_requests_run_on_an_empty_filter_set(merged):
    """The unfiltered request schedules exactly the date window, nothing else,
    and every scheduled latency snapshot carries the mean marker."""

    unfiltered = [
        identity
        for namespace, identity in merged.enqueued
        if len(identity["filters"]) == 1
    ]
    assert {identity["metric_id"] for identity in unfiltered} == {"latency"}
    assert len(unfiltered) == 4  # trace, span, session, users
    for identity in unfiltered:
        assert identity["filters"][0]["column_id"] == "created_at"


@pytest.mark.parametrize("case", FILTER_CASES)
@pytest.mark.parametrize(
    "path",
    [
        "trace/inline",
        "trace/background",
        "span/inline",
        "span/background",
        "charts/inline",
        "session/background",
        "users/inline",
        "users/background",
    ],
)
def test_merged_store_every_path_publishes_the_oracle_mean(merged, path, case):
    label = f"{path}/{case}"
    _assert_matches_oracle(label, merged.series[label], _oracle_for(label, merged.live))


@pytest.mark.parametrize("store", ["merged", "unmerged"])
def test_an_affordable_session_chart_is_inline_and_equals_the_worker(request, store):
    """The unfiltered Sessions latency chart of a small scope runs inline:
    nothing is enqueued, it is the oracle, and it equals the same chart the
    worker computes, bucket by bucket (same statement, same numbers)."""

    observed = request.getfixturevalue(store)
    assert observed.inline_enqueued == {"session/inline/none": False}
    label = "session/inline/none"
    assert observed.envelopes[label]["query_status"] == "complete"
    assert observed.envelopes[label]["metric_statistic"] == "mean"
    _assert_matches_oracle(label, observed.series[label], session_oracle(observed.live))
    inline, background = (
        observed.series[label],
        observed.series["session/background/none"],
    )
    for hour in HOURS:
        assert abs(inline[hour][0] - background[hour][0]) <= 1e-9, hour
        assert inline[hour][1] == background[hour][1], hour


@pytest.mark.parametrize("case", list(_ALWAYS_TRUE))
@pytest.mark.parametrize(
    "path",
    [
        "trace/inline",
        "trace/background",
        "span/inline",
        "span/background",
        "charts/inline",
        "session/background",
        "users/inline",
        "users/background",
    ],
)
@pytest.mark.parametrize("store", ["merged", "unmerged"])
def test_unfiltered_equals_an_always_true_filter(request, store, path, case):
    observed = request.getfixturevalue(store)
    unfiltered = observed.series[f"{path}/none"]
    filtered = observed.series[f"{path}/{case}"]
    for hour in HOURS:
        (value, traffic), (other, other_traffic) = unfiltered[hour], filtered[hour]
        assert value is not None and other is not None, (hour, unfiltered, filtered)
        assert abs(value - other) <= TOLERANCE, (hour, value, other)
        assert traffic == other_traffic, (hour, traffic, other_traffic)


def test_skewed_bucket_is_not_the_median(merged):
    in_hour = [
        row["latency_ms"]
        for row in merged.live
        if _in_window(row) and _hour(row["start_time"]) == HOUR_C
    ]
    median = statistics.median(in_hour)
    for label, series in merged.series.items():
        if label.startswith(("trace/", "span/", "charts/")):
            value = series[HOUR_C][0]
            assert not math.isclose(value, median, rel_tol=0.05), (label, value)


# ---------------------------------------------------------------------------
# Unmerged store: the version-collapsing paths are still the oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", FILTER_CASES)
@pytest.mark.parametrize(
    "path",
    ["charts/inline", "session/background", "users/inline", "users/background"],
)
def test_unmerged_store_version_collapsing_paths_publish_the_oracle_mean(
    unmerged, path, case
):
    label = f"{path}/{case}"
    _assert_matches_oracle(
        label, unmerged.series[label], _oracle_for(label, unmerged.live)
    )
