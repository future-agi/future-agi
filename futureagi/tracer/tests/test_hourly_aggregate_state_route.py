"""The unfiltered graph and dashboard reads must emit one -State/-Merge shape.

These are the guards for issue #2839. The unfiltered system-metric graphs used
to read ``spans_hourly_rollup``, a materialized view fed once per insert
delivery, while ``spans`` collapses replayed deliveries back to one row per
dedup key. The two drifted apart permanently, so "All" rendered at a multiple
of the sum of its own filtered parts.

What makes the replacement readable at all is easy to break by tidying, so the
mechanical rules are pinned here rather than only at the call sites:

* aggregates written as ``-State`` inside and ``-Merge`` outside, because a
  ``PROJECTION`` body applies ``-State`` implicitly and these were declared
  with a second explicit one — a plainly-written ``count()`` looks for
  ``AggregateFunction(count)``, finds ``AggregateFunction(countState)``, and
  can never match;
* no cast around the token columns, in any spelling — ``toInt64`` is the one
  the retired view body used, but ``toUInt64`` and ``CAST`` un-route it just
  as well;
* the window predicate on ``toStartOfHour(start_time)``, the key expression;
* no projection named anywhere, because the optimiser chooses and with a real
  ``WHERE`` the cost model may legitimately prefer a different one. That rule
  is checked over every product file on these two read paths and over the
  *code*, not the rendered SQL: the realistic way to pin one is
  ``force_optimize_projection_name`` in a read-settings dict, which never
  reaches a statement at all.

Whether the optimiser then *accepts* the shape is a different question, and no
amount of text matching can answer it — ``test_hourly_aggregate_state_route_
live_ch`` plans it against a real ClickHouse and asserts which target it picked.
"""

import ast
import pathlib
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

import pytest

import tracer
from tracer.services.clickhouse import graph_dispatch
from tracer.services.clickhouse.query_builders.hourly_aggregate_states import (
    hourly_aggregate_state_source,
)
from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)
from tracer.views import dashboard as dashboard_view
from tracer.views.dashboard import _read_dashboard_rollup_fast_path

PROJECT_ID = "3f1d4b7a-0c2e-4a58-9f6b-1d2c3e4f5a6b"

_REPO_ROOT = pathlib.Path(tracer.__file__).resolve().parent.parent

# Every product file on the two unfiltered read paths. The rule "no projection
# named in product code" has to be checked over all of them, not only the
# shared source: a projection can be pinned at a *call site*, and pinned as a
# read *setting* rather than in SQL, where no rendered-statement assertion can
# ever see it.
_PRODUCT_SOURCES = (
    "tracer/services/clickhouse/query_builders/hourly_aggregate_states.py",
    "tracer/services/clickhouse/query_builders/time_series.py",
    "tracer/services/clickhouse/graph_dispatch.py",
    "tracer/views/dashboard.py",
)

# Cast wrappers of every spelling. The retired rollup's view body cast the
# token columns with ``toInt64``; the stored states are over the raw columns,
# and any cast makes the aggregate signature stop matching. Pinning only the
# one spelling would leave ``toUInt64`` and ``CAST`` free to un-route it.
_CAST_CALL = re.compile(r"\b(?:to(?:U?Int|Float|Decimal)\d*|CAST|_CAST)\s*\(")

_GRAPH_COLUMNS = [
    "time_bucket",
    "avg_latency",
    "total_tokens",
    "avg_cost",
    "traffic_count",
    "prompt_tokens",
    "completion_tokens",
    "error_rate",
]

_STATE_PAIRS = (
    ("countState() AS n", "countMerge(n)"),
    ("sumState(cost) AS cost_sum", "sumMerge(cost_sum)"),
    ("sumState(total_tokens) AS total_tokens_sum", "sumMerge(total_tokens_sum)"),
    ("sumState(prompt_tokens) AS prompt_tokens_sum", "sumMerge(prompt_tokens_sum)"),
    (
        "sumState(completion_tokens) AS completion_tokens_sum",
        "sumMerge(completion_tokens_sum)",
    ),
    (
        "quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms) AS latency_q",
        "quantilesTDigestMerge(0.5, 0.95, 0.99)(latency_q)",
    ),
)


def _unfiltered_graph_sql(interval="day"):
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=[],
        interval=interval,
        start_date=datetime(2026, 6, 1, tzinfo=UTC),
        end_date=datetime(2026, 7, 1, tzinfo=UTC),
    )
    query, _ = builder.build()
    return query


@pytest.mark.unit
@pytest.mark.parametrize("interval", ["hour", "day", "week", "month"])
def test_unfiltered_graph_pairs_every_state_with_its_merge(interval):
    query = _unfiltered_graph_sql(interval)
    for state, merge in _STATE_PAIRS:
        assert state in query, state
        assert merge in query, merge


@pytest.mark.unit
def test_unfiltered_graph_reads_spans_and_not_the_retired_rollup():
    query = _unfiltered_graph_sql()
    assert "FROM spans\n" in query
    assert "spans_hourly_rollup" not in query
    assert query.count("FROM spans") == 1


@pytest.mark.unit
def test_unfiltered_graph_keeps_status_in_the_inner_grouping():
    """`error_rate` is merged conditionally, so `status` must survive inward."""

    query = _unfiltered_graph_sql()
    assert "GROUP BY project_id, hour, status" in query
    assert "countMergeIf(n, status = 'ERROR')" in query


@pytest.mark.unit
def test_unfiltered_graph_windows_on_the_hour_key_expression():
    query = _unfiltered_graph_sql()
    assert "toStartOfHour(start_time) >= %(start_date)s" in query
    assert "toStartOfHour(start_time) < %(end_date)s" in query


@pytest.mark.unit
def test_unfiltered_graph_casts_nothing_and_pins_no_projection():
    query = _unfiltered_graph_sql()
    assert "toInt64(" not in query
    assert _CAST_CALL.search(query) is None, (
        "the unfiltered graph statement casts a column. The projections store"
        " sumState over the raw columns, so any cast — toInt64, toUInt64,"
        " toFloat64, CAST — changes the aggregate signature and the query"
        f" stops matching them: {_CAST_CALL.search(query).group(0)!r} in\n{query}"
    )
    assert "proj_" not in query
    assert "FINAL" not in query.upper()
    assert "SAMPLE" not in query.upper()


def _code_without_docstrings_or_comments(path: pathlib.Path) -> str:
    """Product source with comments and docstrings dropped, values kept.

    Docstrings are exempt because the mechanism is worth explaining by name.
    A projection named in a *value* is not the same thing, and a by-name pin
    can only ever be a value — which is why this reads code rather than SQL.
    """

    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


@pytest.mark.unit
@pytest.mark.parametrize("relative", _PRODUCT_SOURCES)
def test_no_source_file_names_a_projection(relative):
    """The optimiser picks; naming one would freeze a cost-model decision.

    Checked over every product file on these read paths, and over the code
    rather than the rendered SQL, because the realistic way to pin a
    projection is ``force_optimize_projection_name`` in the read-settings dict
    handed to the client — which never appears in a statement, so no
    rendered-SQL assertion anywhere can see it.
    """

    code = _code_without_docstrings_or_comments(_REPO_ROOT / relative)
    assert "proj_metrics_hourly" not in code
    assert "proj_" not in code, (
        f"{relative} names a projection outside its docstring. Which"
        " projection answers this query is the cost model's choice and may"
        " legitimately change with the data; pinning one turns a preference"
        " into a hard dependency. The by-name detector also raises on 'not"
        " chosen', which is indistinguishable from 'not a candidate' — it is"
        " a test instrument at best, never a product setting."
    )
    assert "force_optimize_projection_name" not in code, (
        f"{relative} pins a projection by name. See above: 'not chosen' and"
        " 'not a candidate' are the same exception."
    )


@pytest.mark.unit
def test_state_source_scopes_by_the_predicate_it_is_given():
    single = hourly_aggregate_state_source("project_id = %(project_id)s")
    many = hourly_aggregate_state_source("project_id IN %(project_ids)s")
    assert "WHERE project_id = %(project_id)s" in single
    assert "WHERE project_id IN %(project_ids)s" in many
    for rendered in (single, many):
        # ``is_deleted`` is not a projection column: a predicate on it would
        # stop the states matching. Tombstones are therefore counted, which is
        # one reason the route never publishes ``query_exact``.
        assert "is_deleted" not in rendered
        assert "PREWHERE" not in rendered


@pytest.mark.unit
@pytest.mark.parametrize(
    "metric_id",
    [
        "traffic",
        "tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "cost",
        "error_rate",
        "latency",
    ],
)
def test_graph_never_reports_the_aggregate_states_as_exact(metric_id):
    """The states are per part, not latest-live; latency is also a tDigest.

    ``test_hourly_aggregate_state_exactness_ch25`` shows the numbers that
    make this false on a real ClickHouse.
    """

    analytics = mock.Mock()
    analytics.supports_per_query_read_settings = True
    analytics.execute_ch_query.return_value = SimpleNamespace(
        data=[
            {
                "time_bucket": datetime(2026, 6, 1, tzinfo=UTC),
                "avg_latency": 12.5,
                "total_tokens": 900,
                "avg_cost": 0.002,
                "traffic_count": 11,
                "prompt_tokens": 600,
                "completion_tokens": 300,
                "error_rate": 9.0,
            }
        ],
        columns=list(_GRAPH_COLUMNS),
    )

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[],
        interval="day",
        metric_id=metric_id,
        observe_type="trace",
    )

    assert response["query_status"] == "complete"
    assert response["query_exact"] is False
    assert "spans_hourly_rollup" not in analytics.execute_ch_query.call_args.args[0]


class _CapturingAnalytics:
    supports_per_query_read_settings = True

    def __init__(self):
        self.queries = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.queries.append(query)
        aliases = [
            part.split()[0].rstrip(",")
            for part in query.split(" AS ")
            if part.startswith("metric_")
        ]
        row = {"time_bucket": params["start_date"]}
        row.update(dict.fromkeys(aliases, 1))
        return SimpleNamespace(data=[row], columns=["time_bucket", *aliases])


def _widget_config(metric_id, aggregation):
    return {
        "project_ids": [PROJECT_ID],
        "time_range": {"preset": "30D"},
        "granularity": "day",
        "metrics": [
            {
                "id": metric_id,
                "name": metric_id,
                "type": "system_metric",
                "source": "traces",
                "aggregation": aggregation,
                "filters": [],
            }
        ],
        "filters": [],
        "breakdowns": [],
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("metric_id", "aggregation"),
    [
        ("tokens", "sum"),
        ("cost", "avg"),
        ("error_rate", "avg"),
        ("span_count", "count"),
        ("project", "count_distinct"),
        ("latency", "avg"),
        ("latency", "p95"),
    ],
)
def test_dashboard_widget_routes_and_reports_inexact(
    monkeypatch, metric_id, aggregation
):
    analytics = _CapturingAnalytics()
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: analytics)

    result = _read_dashboard_rollup_fast_path(_widget_config(metric_id, aggregation))

    assert result["query_status"] == "complete"
    assert result["query_exact"] is False
    assert result["metrics"][0]["query_exact"] is False
    query = analytics.queries[0]
    assert "spans_hourly_rollup" not in query
    assert "FROM spans\n" in query
    assert "countState() AS n" in query
    assert "toStartOfHour(start_time) >= %(start_date)s" in query
    assert "toStartOfHour(start_time) < %(end_date)s" in query
    assert "proj_" not in query
    assert "FINAL" not in query.upper()
    assert "SAMPLE" not in query.upper()
    # The widget selects only the metrics asked for, so its -Merge list varies;
    # the inner states do not. They are the half that has to keep matching.
    for state, _merge in _STATE_PAIRS:
        assert state in query, f"{state} missing from the widget statement"
    assert "toInt64(" not in query
    assert _CAST_CALL.search(query) is None, (
        "the dashboard widget statement casts a column, which changes the"
        " aggregate signature and stops it matching the stored states:"
        f" {_CAST_CALL.search(query).group(0)!r} in\n{query}"
    )


@pytest.mark.unit
def test_dashboard_widget_scopes_the_inner_read_to_the_requested_projects():
    """The scope has to sit inside, on the states' own key column."""

    analytics = _CapturingAnalytics()
    with mock.patch.object(
        dashboard_view, "V2AnalyticsQueryService", lambda: analytics
    ):
        _read_dashboard_rollup_fast_path(_widget_config("tokens", "sum"))

    query = analytics.queries[0]
    assert "WHERE project_id IN %(project_ids)s" in query
    assert "PREWHERE" not in query
