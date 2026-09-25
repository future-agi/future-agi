"""Real-ClickHouse proof: every Observe latency path publishes one median.

One seeded population is read through every live latency path. Each path
must publish the same per-hour value, the t-digest p50 of the seeded
latencies:

* U   unfiltered trace/span graph (hourly aggregate states, via the public
      ``fetch_system_metric_graph_ch``);
* F   filtered trace/span graph, interactive statement;
* B   filtered trace/span graph, background worker statement;
* X   project ChartsView bundle (``read_exact_all_system_metrics``);
* S-U session graph on the session rollup (public ``fetch_session_graph_ch``);
* S-E session graph on the exact snapshot statement;
* Us  users aggregate graph (public ``fetch_user_system_metric_graph_ch``).

The filter is an always-true ``model`` predicate: every seeded span carries
the model, so the filtered paths see the same rows as the unfiltered ones.

**Population.** Every trace is one root span plus child spans, and every
span carries its trace's session and end user. Every path, the session paths
included, pools the latency of every span: the session rollup digests all
spans of a session, so the exact session statement must too (it once read
root spans only, and a no-op filter moved the session chart about 5x). The
roots alone have a different median in every hour, so a root-only producer
cannot pass. Every row is inserted once and merged (``OPTIMIZE FINAL``): the
hourly states count unmerged versions and tombstones, which is a separate,
documented approximation (``test_hourly_aggregate_state_exactness_ch25``).

``spans.latency_ms`` is ``Int32 DEFAULT 0`` in the real schema, so no path
can see a NULL latency here; the finite guard for empty buckets is pinned by
the unit tests.

**Expected values.**

* Hours A and B hold at most 64 values per bucket, where ClickHouse's
  t-digest keeps every value as its own centroid and returns the lower
  median, even after merging per-status, per-session or per-user states.
  Every path must equal ``statistics.median_low`` exactly.
* Hour C holds 5,000 skewed values. Every path must agree with the others
  within max(1 ms, 0.5%), lie between the 49th and 51st percentiles, and
  differ from the arithmetic mean by more than 5% (so a mean producer
  cannot hide behind the label).

The schema is the repository's own v2 schema, applied to a database this
module creates and drops.
"""

from __future__ import annotations

import os
import random
import statistics
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import (
    _ch_test_http_port,
    _ch_test_native_client,
    _ch_test_owned_database,
    _open_ch_test_http_client,
)
from tracer.services.clickhouse import exact_graph_reads, graph_dispatch, session_graph

pytestmark = pytest.mark.integration

PROJECT_ID = "5e1f7a2c-9b3d-4c6e-8f10-2a3b4c5d6e7f"
ORGANIZATION_ID = uuid.UUID("6f2a8b3d-0c4e-4d7f-9a21-3b4c5d6e7f80")
MODEL = "parity-model"
HOUR_A = datetime(2026, 6, 10, 0, tzinfo=UTC)
HOUR_B = HOUR_A + timedelta(hours=1)
HOUR_C = HOUR_A + timedelta(hours=2)
WINDOW_END = HOUR_A + timedelta(hours=3)

_WINDOW_FILTER = {
    "column_id": "created_at",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": [HOUR_A.isoformat(), WINDOW_END.isoformat()],
    },
}
_ALWAYS_TRUE_FILTER = {
    "column_id": "model",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": MODEL,
    },
}

_SCHEMA_DIR = (
    Path(__file__).resolve().parents[1] / "services" / "clickhouse" / "v2" / "schema"
)
_TIMEOUT_MS = 60_000


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def _hour_a_latencies() -> list[int]:
    # 40 distinct values; the lower median is unambiguous.
    return [7 * index + 3 for index in range(40)]


def _hour_b_latencies() -> list[int]:
    # 64 values with repeats and zeros (the column's default).
    return [0, 0, 0] + [(index * 37) % 500 for index in range(61)]


def _hour_c_latencies() -> list[int]:
    # 5,000 lognormal-ish values: the mean sits well above the median.
    rng = random.Random(20260610)
    return [max(1, int(rng.lognormvariate(5.0, 1.0))) for _ in range(5_000)]


def _span_id(hour, index) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{hour:%H}-span-{index}"))


def _hour_seed(
    hour, latencies, *, spans_per_trace, traces_per_session, traces_per_user
):
    """Rows for one hour: each trace's first span is its root, the rest are
    its children; every trace is in one session and one user."""

    # A root span covers its children, so it is its trace's longest span. The
    # multiset of latencies (and so every all-span median) is unchanged.
    ordered = []
    for first in range(0, len(latencies), spans_per_trace):
        ordered.extend(sorted(latencies[first : first + spans_per_trace], reverse=True))
    rows = []
    users = set()
    for index, latency in enumerate(ordered):
        trace_index = index // spans_per_trace
        root_index = trace_index * spans_per_trace
        trace_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{hour:%H}-trace-{trace_index}"))
        session_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"{hour:%H}-session-{trace_index // traces_per_session}"
        )
        user_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"{hour:%H}-user-{trace_index // traces_per_user}"
        )
        users.add(user_id)
        start = hour + timedelta(seconds=index % 3_000, microseconds=index)
        rows.append(
            {
                "trace_id": trace_id,
                "id": _span_id(hour, index),
                "parent_span_id": (
                    "" if index == root_index else _span_id(hour, root_index)
                ),
                "start_time": start,
                "latency_ms": int(latency),
                # Mixed statuses, so the hourly per-status states must merge.
                "status": "ERROR" if index % 3 == 0 else "OK",
                "trace_session_id": session_id,
                "end_user_id": user_id,
            }
        )
    return rows, users


def _seed():
    hours = {
        HOUR_A: _hour_seed(
            HOUR_A,
            _hour_a_latencies(),
            spans_per_trace=8,
            traces_per_session=4,
            traces_per_user=2,
        ),
        HOUR_B: _hour_seed(
            HOUR_B,
            _hour_b_latencies(),
            spans_per_trace=4,
            traces_per_session=4,
            traces_per_user=3,
        ),
        HOUR_C: _hour_seed(
            HOUR_C,
            _hour_c_latencies(),
            spans_per_trace=10,
            traces_per_session=10,
            traces_per_user=20,
        ),
    }
    return hours


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
    "created_at",
    "is_deleted",
    "_version",
)


def _load(client, hours) -> None:
    project = uuid.UUID(PROJECT_ID)
    span_rows = []
    user_rows = []
    for rows, users in hours.values():
        for row in rows:
            span_rows.append(
                (
                    project,
                    "llm",
                    row["start_time"],
                    row["start_time"] + timedelta(milliseconds=row["latency_ms"]),
                    row["trace_id"],
                    row["id"],
                    row["parent_span_id"],
                    "parity",
                    row["latency_ms"],
                    row["status"],
                    MODEL,
                    "parity-provider",
                    row["trace_session_id"],
                    row["end_user_id"],
                    ORGANIZATION_ID,
                    row["start_time"],
                    0,
                    1,
                )
            )
        for user in users:
            user_rows.append(
                (project, user, ORGANIZATION_ID, f"user-{user.hex[:8]}", HOUR_A, 0)
            )
    client.execute(f"INSERT INTO spans ({', '.join(_SPAN_COLUMNS)}) VALUES", span_rows)
    client.execute(
        "INSERT INTO end_users"
        " (project_id, end_user_id, organization_id, user_id, first_seen, is_deleted)"
        " VALUES",
        user_rows,
    )
    for table in ("spans", "spans_per_session", "end_users"):
        client.execute(f"OPTIMIZE TABLE {table} FINAL")


# ---------------------------------------------------------------------------
# Live ClickHouse
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ch_database():
    with _ch_test_owned_database("test_latency_median_") as database:
        from tracer.services.clickhouse.v2 import apply_schema

        # The schema runner opens its own HTTP connection. Prove that port is
        # the test sidecar first (the native port was proved above).
        _open_ch_test_http_client(database=database).close()

        rc = apply_schema.main(
            [
                "--schema-dir",
                str(_SCHEMA_DIR),
                "--ch-host",
                os.environ.get("CH25_HOST", "127.0.0.1"),
                "--ch-http-port",
                str(_ch_test_http_port().port),
                "--ch-user",
                os.environ.get("CH25_USER")
                or os.environ.get("CH_USERNAME")
                or "default",
                "--ch-password",
                os.environ.get("CH25_PASSWORD") or os.environ.get("CH_PASSWORD") or "",
                "--ch-database",
                database,
            ]
        )
        assert rc == 0, f"v2 schema apply failed with rc={rc}"
        yield database


@pytest.fixture(scope="module")
def seeded(ch_database):
    hours = _seed()
    with _ch_test_native_client(database=ch_database) as client:
        _load(client, hours)
    return hours


class _LiveAnalytics:
    """Run the product's own statements on the test database."""

    supports_per_query_read_settings = True

    def __init__(self, client):
        self._client = client

    def execute_ch_query(self, query, params=None, *, timeout_ms=None, settings=None):
        del timeout_ms
        bound = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in (params or {}).items()
        }
        rows, columns = self._client.execute(
            query, bound, with_column_types=True, settings=dict(settings or {})
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows],
            columns=names,
        )


@pytest.fixture(scope="module")
def analytics(ch_database, seeded):
    with _ch_test_native_client(database=ch_database) as client:
        yield _LiveAnalytics(client)


def _by_hour(points, field="value"):
    values = {}
    for point in points:
        stamp = datetime.fromisoformat(str(point["timestamp"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        values[stamp.astimezone(UTC)] = float(point.get(field) or 0)
    return {hour: values.get(hour) for hour in (HOUR_A, HOUR_B, HOUR_C)}


def _all_paths(analytics):
    """Every live latency path's per-hour value, keyed by path label."""

    window = [_WINDOW_FILTER]
    filtered = [_WINDOW_FILTER, _ALWAYS_TRUE_FILTER]
    paths = {}
    envelopes = {}
    for observe_type in ("trace", "span"):
        unfiltered = graph_dispatch.fetch_system_metric_graph_ch(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=window,
            interval="hour",
            metric_id="latency",
            observe_type=observe_type,
            timeout_ms=_TIMEOUT_MS,
        )
        envelopes[f"U-{observe_type}"] = unfiltered
        paths[f"U-{observe_type}"] = _by_hour(unfiltered["data"])
        paths[f"F-{observe_type}"] = _by_hour(
            graph_dispatch._fetch_direct_raw_system_metric_graph(
                analytics=analytics,
                project_id=PROJECT_ID,
                filters=filtered,
                interval="hour",
                metric_id="latency",
                observe_type=observe_type,
                timeout_ms=_TIMEOUT_MS,
            )["data"]
        )
        paths[f"B-{observe_type}"] = _by_hour(
            graph_dispatch.fetch_background_raw_system_metric_graph(
                analytics=analytics,
                project_id=PROJECT_ID,
                filters=filtered,
                interval="hour",
                metric_id="latency",
                observe_type=observe_type,
            )["data"]
        )
    paths["X"] = _by_hour(
        exact_graph_reads.read_exact_all_system_metrics(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=filtered,
            interval="hour",
        )["latency"],
        # The bundle's points carry the value under "value"; their "latency"
        # key is a zero-filled alias (pre-existing, see the lane notes).
        field="value",
    )
    session_rollup = session_graph.fetch_session_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=window,
        interval="hour",
        req_data_config={"type": "SYSTEM_METRIC", "id": "latency"},
    )
    envelopes["S-U"] = session_rollup
    paths["S-U"] = _by_hour(session_rollup["data"])
    paths["S-E"] = _by_hour(
        exact_graph_reads.read_exact_session_system_graph(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=filtered,
            interval="hour",
            metric_id="latency",
        )["data"]
    )
    users = graph_dispatch.fetch_user_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=window,
        interval="hour",
        metric_id="latency",
        timeout_ms=_TIMEOUT_MS,
    )
    envelopes["Us"] = users
    paths["Us"] = _by_hour(users["data"])
    return paths, envelopes


@pytest.fixture(scope="module")
def observed(analytics):
    return _all_paths(analytics)


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def _latencies(seeded, hour):
    rows, _users = seeded[hour]
    return [row["latency_ms"] for row in rows]


def test_every_envelope_names_the_median(observed):
    _paths, envelopes = observed
    for label, envelope in envelopes.items():
        assert envelope.get("query_status") == "complete", (label, envelope)
        assert envelope.get("metric_statistic") == "median", label


@pytest.mark.parametrize("hour", [HOUR_A, HOUR_B], ids=["hour_a_40", "hour_b_64"])
def test_small_buckets_are_the_exact_lower_median_on_every_path(observed, seeded, hour):
    paths, _envelopes = observed
    expected = float(statistics.median_low(_latencies(seeded, hour)))
    published = {label: values[hour] for label, values in paths.items()}
    for label, value in published.items():
        assert value is not None, f"{label} published no {hour:%H}:00 bucket"
        assert abs(value - expected) <= 0.01, (label, value, expected, published)


def test_large_skewed_bucket_is_a_median_on_every_path_not_a_mean(observed, seeded):
    paths, _envelopes = observed
    values = sorted(_latencies(seeded, HOUR_C))
    mean = statistics.fmean(values)
    p49 = values[int(0.49 * (len(values) - 1))]
    p51 = values[int(0.51 * (len(values) - 1))]
    published = {label: values_[HOUR_C] for label, values_ in paths.items()}
    reference = published["U-trace"]
    for label, value in published.items():
        assert value is not None, f"{label} published no 02:00 bucket"
        assert abs(value - reference) <= max(1.0, 0.005 * reference), (
            label,
            value,
            reference,
            published,
        )
        assert p49 <= value <= p51, (label, value, p49, p51)
        assert abs(value - mean) > 0.05 * mean, (label, value, mean)


def _root_latencies(seeded, hour):
    rows, _users = seeded[hour]
    return [row["latency_ms"] for row in rows if not row["parent_span_id"]]


@pytest.mark.parametrize("hour", [HOUR_A, HOUR_B, HOUR_C], ids=["a", "b", "c"])
def test_seed_separates_all_span_and_root_only_medians(seeded, hour):
    """A root-only session producer must be detectable in every hour."""

    all_spans = sorted(_latencies(seeded, hour))
    roots = _root_latencies(seeded, hour)
    assert len(roots) < len(all_spans)
    root_median = statistics.median_low(roots)
    p49 = all_spans[int(0.49 * (len(all_spans) - 1))]
    p51 = all_spans[int(0.51 * (len(all_spans) - 1))]
    assert root_median != statistics.median_low(all_spans)
    assert not (p49 <= root_median <= p51), (root_median, p49, p51)


@pytest.mark.parametrize("hour", [HOUR_A, HOUR_B, HOUR_C], ids=["a", "b", "c"])
def test_session_latency_is_unchanged_by_an_always_true_filter(observed, seeded, hour):
    """The unfiltered rollup and the filtered exact statement pool the same
    spans (every span of the session), so a filter that removes no row moves
    the session latency chart by at most the stated t-digest tolerance."""

    paths, _envelopes = observed
    unfiltered = paths["S-U"][hour]
    filtered = paths["S-E"][hour]
    assert unfiltered is not None and filtered is not None, (unfiltered, filtered)
    assert abs(filtered - unfiltered) <= max(1.0, 0.005 * unfiltered), (
        hour,
        unfiltered,
        filtered,
        statistics.median_low(_root_latencies(seeded, hour)),
    )


_ALWAYS_TRUE_SESSION_FILTERS = [
    # HAVING filters on root-span aggregates: every seeded session has at
    # least one trace, and no seeded root carries an input message.
    {
        "column_id": "traces_count",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": "greater_than_or_equal",
            "filter_value": 1,
        },
    },
    {
        "column_id": "first_message",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "is_null",
        },
    },
]


def _sessions_per_hour(seeded, hour):
    rows, _users = seeded[hour]
    return len({row["trace_session_id"] for row in rows})


def test_session_aggregate_filters_keep_sessions_and_the_all_span_median(
    analytics, observed, seeded
):
    """The latency statement reads every live span but keeps each session's
    aggregates (and the HAVING filters on them) on its root spans: always-true
    session filters change nothing, and each session is counted once."""

    paths, _envelopes = observed
    payload = exact_graph_reads.read_exact_session_system_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_WINDOW_FILTER, _ALWAYS_TRUE_FILTER, *_ALWAYS_TRUE_SESSION_FILTERS],
        interval="hour",
        metric_id="latency",
    )
    assert _by_hour(payload["data"]) == paths["S-E"]
    sessions = _by_hour(payload["data"], field="primary_traffic")
    for hour in (HOUR_A, HOUR_B, HOUR_C):
        assert sessions[hour] == _sessions_per_hour(seeded, hour), hour
