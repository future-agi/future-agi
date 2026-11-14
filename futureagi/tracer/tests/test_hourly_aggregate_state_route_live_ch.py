"""Ask a live ClickHouse *which* target the shipped unfiltered shape reads.

The rendered-SQL guards in ``test_hourly_aggregate_state_route`` pin the text.
They cannot tell you the optimiser accepts it — and the whole defect behind
issue #2839 was a shape that looked right and silently full-scanned, because
the projections on ``spans`` store a doubled ``-State`` and so never match a
plainly-written ``count()``.

**Why this module asserts on the plan's read target rather than only on
"a projection was used".** ``force_optimize_projection = 1`` raises when *no*
projection is used, and that is much weaker than it sounds on this table.
``spans`` also carries *normal* projections (``proj_root_spans``,
``proj_by_session``, ``proj_by_end_user``), and a query that has fallen back
to reading rows still satisfies the setting by reading them through one of
those. Measured on a live ClickHouse 25.3 carrying the production projection
set: the plainly-written regression plans ``ReadFromMergeTree
(proj_root_spans)`` and does **not** raise. So does a ``toInt64()`` cast on
the token columns, and so does windowing on bare ``start_time``. A guard whose
only assertion is that the plan contains ``ReadFromMergeTree (`` passes
through every one of them — and ``EXPLAIN`` renders that same text for a plain
base-table read, as ``ReadFromMergeTree (<db>.spans)``.

The discriminating question is therefore not *"was a projection used"* but
*"was one of this table's **aggregate** projections used"*, because only those
carry the hourly metric states. The acceptable set is discovered from
``system.projections`` at run time rather than written down, so the cost model
stays free to pick between them — which is exactly why no product code names
one. A test may name what it expects; it must not narrow the optimiser's
choice to a single member.

The by-name detector (``force_optimize_projection_name``) is still deliberately
unused: it raises on "not chosen", which is indistinguishable from "not a
candidate".

**Both sites are planned here.** The Observe system-metric graph and the
dashboard time-series widgets are two separate builders that share one source
module; had only the graph been checked, the widgets could regress alone. Each
statement is taken from the product builder itself, not re-typed, so a change
to the emitted SQL reaches this module unaltered.

Everything below is ``EXPLAIN`` only: no rows are read, nothing is written and
no DDL is issued. The target is still fenced to a loopback ``test_`` database
on a port that is not one of the operator port-forwards, because a live-CH
test that quietly defaults to a forwarded production port has happened here
before.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from conftest import _open_ch_test_http_client
from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)

# ``EXPLAIN`` names the chosen read target in parentheses: a projection name
# when one answers the query, otherwise ``<database>.<table>``.
_PLAN_READ_TARGET = re.compile(r"ReadFromMergeTree \(([^)]*)\)")

# ClickHouse raises this when ``force_optimize_projection = 1`` and the plan
# uses no projection at all. Matched on the exact server text: a bare "584"
# also occurs in byte counts and line numbers, and turning an unrelated driver
# error into "no projection was used" would report the wrong cause.
_PROJECTION_NOT_USED = "Code: 584."


def _client():
    # conftest resolves the HTTP port (no default; a forwarded one is refused
    # with an exception, not an ``assert``), keeps the host to loopback and, on
    # CI's opted-in sidecar, proves the server is the test sidecar.
    pytest.importorskip("clickhouse_connect")
    database = os.getenv("CH25_DATABASE") or os.getenv("CH_DATABASE") or ""
    if not database:
        pytest.skip("no test ClickHouse configured")
    if not database.lower().lstrip("_").startswith("test_"):
        pytest.skip("test ClickHouse database is not a test_* database")
    return _open_ch_test_http_client(
        database=database, connect_timeout=5, send_receive_timeout=30
    )


def _aggregate_projection_names(client) -> frozenset[str]:
    """Names of the aggregate projections on ``spans``, discovered live.

    Only these carry the hourly metric states. The normal projections on the
    same table cover rows, so a plan that reads one of *those* is reading rows.
    """

    rows = client.query(
        "SELECT name FROM system.projections"
        " WHERE database = currentDatabase() AND table = 'spans'"
        "   AND type = 'Aggregate'"
    ).result_rows
    return frozenset(str(row[0]) for row in rows)


def _busiest_project_window(client):
    """Return a project and window that actually has rows, or skip."""

    rows = client.query(
        "SELECT project_id,"
        " toString(toStartOfHour(min(start_time))),"
        " toString(toStartOfHour(max(start_time)) + INTERVAL 1 HOUR)"
        " FROM spans GROUP BY project_id ORDER BY count() DESC LIMIT 1"
    ).result_rows
    if not rows:
        pytest.skip("test ClickHouse has no spans to plan against")
    return rows[0]


def _literal(value) -> str:
    """Render one query parameter as a ClickHouse literal for ``EXPLAIN``."""

    if isinstance(value, datetime):
        return f"toDateTime('{value.strftime('%Y-%m-%d %H:%M:%S')}')"
    if isinstance(value, (list, tuple)):
        return "(" + ", ".join(_literal(item) for item in value) + ")"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    try:
        uuid.UUID(text)
    except ValueError:
        return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return f"toUUID('{text}')"


def _inline(statement: str, params: dict) -> str:
    """Substitute the builder's ``%(name)s`` placeholders for EXPLAIN."""

    for name, value in params.items():
        statement = statement.replace(f"%({name})s", _literal(value))
    assert "%(" not in statement, f"unbound parameter in statement:\n{statement}"
    return statement


class _CapturingAnalytics:
    """Collect the widget's own SQL without executing it."""

    supports_per_query_read_settings = True

    def __init__(self):
        self.calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params))
        aliases = [
            part.split()[0].rstrip(",")
            for part in query.split(" AS ")
            if part.startswith("metric_")
        ]
        row = {"time_bucket": params["start_date"]}
        row.update(dict.fromkeys(aliases, 1))
        return SimpleNamespace(data=[row], columns=["time_bucket", *aliases])


def _graph_statement(project_id, window_start, window_end) -> str:
    """The unfiltered Observe system-metric graph, as the product builds it."""

    query, params = TimeSeriesQueryBuilder(
        project_id=str(project_id),
        filters=[],
        interval="day",
        start_date=datetime.fromisoformat(window_start),
        end_date=datetime.fromisoformat(window_end),
    ).build()
    return _inline(query, params)


def _widget_statement(project_id, window_start, window_end) -> str:
    """The unfiltered dashboard time-series widget, as the product builds it."""

    from tracer.views import dashboard as dashboard_view
    from tracer.views.dashboard import _read_dashboard_rollup_fast_path

    analytics = _CapturingAnalytics()
    config = {
        "project_ids": [str(project_id)],
        "time_range": {"start": window_start, "end": window_end},
        "granularity": "day",
        "metrics": [
            {
                "id": "tokens",
                "name": "tokens",
                "type": "system_metric",
                "source": "traces",
                "aggregation": "sum",
                "filters": [],
            }
        ],
        "filters": [],
        "breakdowns": [],
    }
    with mock.patch.object(
        dashboard_view, "V2AnalyticsQueryService", lambda: analytics
    ):
        _read_dashboard_rollup_fast_path(config)
    query, params = analytics.calls[0]
    return _inline(query, params)


_SITES = {"graph": _graph_statement, "widget": _widget_statement}


def _tidied_into_plain_aggregates(statement: str) -> str:
    """Rewrite the shipped statement the way a later "tidy-up" would.

    This is the regression issue #2839 exists to prevent: the two-level
    ``-State`` / ``-Merge`` shape rewritten into the aggregates anybody would
    reach for first. It returns the same numbers on a base scan, so nothing
    downstream notices — the read just stops being pre-aggregated.
    """

    rewrites = (
        # outer: the merge combinators, before the inner state ones
        ("quantilesTDigestMerge(0.5, 0.95, 0.99)(latency_q)", "max(latency_q)"),
        ("countMergeIf(", "sumIf("),
        ("countMerge(", "sum("),
        ("sumMerge(", "sum("),
        # inner: the state combinators
        ("countState()", "count()"),
        ("sumState(", "sum("),
        ("quantilesTDigestState(", "quantilesTDigest("),
    )
    tidied = statement
    for shipped, plain in rewrites:
        tidied = tidied.replace(shipped, plain)
    # A control that no longer rewrites anything would silently test nothing.
    assert tidied != statement, "the plain-aggregate rewrite matched nothing"
    assert "State(" not in tidied and "Merge(" not in tidied, (
        "the plain-aggregate rewrite left an aggregate-state combinator behind:"
        f"\n{tidied}"
    )
    return tidied


def _explain(client, statement: str, *, force: bool):
    """Return the rendered plan, or ``None`` when no projection was used.

    ``None`` is only reachable under ``force``: that is ClickHouse refusing the
    statement outright rather than planning it.
    """

    query_settings = {"optimize_use_projections": 1}
    if force:
        query_settings["force_optimize_projection"] = 1
    try:
        plan = client.query(
            "EXPLAIN indexes = 1 " + statement, settings=query_settings
        ).result_rows
    except Exception as exc:  # noqa: BLE001 - the code is carried in the text
        if force and _PROJECTION_NOT_USED in str(exc):
            return None
        raise
    return "\n".join(str(row[0]) for row in plan)


def _plan_targets(rendered: str) -> list[str]:
    return _PLAN_READ_TARGET.findall(rendered)


def _explain_failure(site: str, detector: str) -> str:
    return (
        f"the unfiltered {site} statement reads no projection at all under"
        f" {detector}, so it is scanning rows. The -State/-Merge shape has"
        " stopped matching the hourly metric states stored inside `spans`;"
        " see issue #2839."
    )


def _route_failure(site: str, targets, aggregate, rendered: str) -> str:
    return (
        f"the unfiltered {site} statement no longer reads an hourly aggregate"
        f" projection of `spans`.\n"
        f"  the plan reads: {targets or ['nothing recognisable']}\n"
        f"  aggregate projections available here: {sorted(aggregate)}\n"
        "Reading the base table — or one of this table's *normal* projections"
        " (proj_root_spans, proj_by_session, proj_by_end_user), which cover"
        " rows rather than aggregate states — means the two-level"
        " -State/-Merge shape stopped matching and the query is scanning rows"
        " again. It will still return correct-looking numbers, at full-scan"
        " cost, which is precisely how issue #2839 went unnoticed. The usual"
        " causes: aggregates written plainly (count()/sum()), a cast around"
        " the token columns, the window predicate moved off"
        " toStartOfHour(start_time), or a predicate on a column the"
        " projections do not carry, such as is_deleted.\n"
        f"plan:\n{rendered}"
    )


def _prepared(client):
    if not _aggregate_projection_names(client):
        pytest.skip("spans carries no aggregate projections on this ClickHouse")
    return _busiest_project_window(client)


@pytest.mark.integration
@pytest.mark.parametrize("site", sorted(_SITES))
def test_optimiser_selects_a_projection_for_the_shipped_shape(site):
    """The plan the deployed query gets must name an aggregate projection.

    Planned with the settings the product actually sends — it sets neither
    ``optimize_use_projections`` nor ``force_optimize_projection`` and takes
    the server default — so this is the route production takes, not a route
    coaxed out of the optimiser by a test-only setting.
    """

    client = _client()
    project_id, window_start, window_end = _prepared(client)
    aggregate = _aggregate_projection_names(client)

    rendered = _explain(
        client, _SITES[site](project_id, window_start, window_end), force=False
    )

    targets = _plan_targets(rendered)
    assert targets and all(target in aggregate for target in targets), _route_failure(
        site, targets, aggregate, rendered
    )


@pytest.mark.integration
@pytest.mark.parametrize("site", sorted(_SITES))
def test_force_optimize_projection_accepts_the_shipped_shape(site):
    """The original tripwire, kept, and now told which target it accepted.

    ``force_optimize_projection = 1`` raises when no projection is used. That
    alone does not distinguish an aggregate-state read from a row read through
    a normal projection, so the same identity assertion is applied on top.
    """

    client = _client()
    project_id, window_start, window_end = _prepared(client)
    aggregate = _aggregate_projection_names(client)

    rendered = _explain(
        client, _SITES[site](project_id, window_start, window_end), force=True
    )
    if rendered is None:
        pytest.fail(_explain_failure(site, "force_optimize_projection = 1"))

    targets = _plan_targets(rendered)
    assert targets and all(target in aggregate for target in targets), _route_failure(
        site, targets, aggregate, rendered
    )


@pytest.mark.integration
@pytest.mark.parametrize("site", sorted(_SITES))
@pytest.mark.parametrize("force", [False, True], ids=["default", "forced"])
def test_plainly_written_aggregates_do_not_reach_the_hourly_states(site, force):
    """The negative control that gives the guard above its meaning.

    Take the statement the product emits, tidy it into the plainly-written
    aggregates, and confirm the plan stops naming an aggregate projection. If
    this ever passes *and* the shipped shape also passes, the assertion is
    measuring something other than routing.

    The doubled ``-State`` is why: the projections store
    ``AggregateFunction(countState)`` while ``count()`` looks for
    ``AggregateFunction(count)``, so the plain form can never match. Note that
    under ``force_optimize_projection = 1`` this form does *not* raise — it
    falls to a normal projection — which is the reason this module asserts on
    the target's identity.
    """

    client = _client()
    project_id, window_start, window_end = _prepared(client)
    aggregate = _aggregate_projection_names(client)

    tidied = _tidied_into_plain_aggregates(
        _SITES[site](project_id, window_start, window_end)
    )
    rendered = _explain(client, tidied, force=force)
    if rendered is None:
        return  # refused outright: no projection at all, which is the point

    targets = _plan_targets(rendered)
    assert not any(target in aggregate for target in targets), (
        f"the plainly-written {site} aggregates reached an aggregate"
        f" projection ({targets}). The guard in this module asserts that the"
        " shipped -State/-Merge shape routes there and the plain form does"
        " not; if the plain form routes too, that assertion no longer"
        f" separates them.\nplan:\n{rendered}"
    )
