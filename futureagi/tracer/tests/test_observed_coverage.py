"""The observed index must never report history it does not hold as complete.

Upgrading onto the observed read path leaves the index empty for everything
ingested before it existed. These tests pin the contract that makes that state
visible instead of silently indistinguishable from "this project has no custom
attributes".
"""

import re
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.coverage import (
    _INDEXABLE,
    _PROBE_SETTINGS,
    _PROBE_WALL_MS,
    _SETTLED_BEFORE,
    Coverage,
    observed_scope_coverage,
)

SCOPE = {
    "organization_id": "org-1",
    "workspace_id": "ws-1",
    "project_ids": ("p1",),
}
TWO_PROJECTS = {**SCOPE, "project_ids": ("p1", "p2")}


class _Observed:
    """Stands in for the catalog executor, recording the SQL it is given."""

    def __init__(self, rows=None, raises=None):
        self.rows = rows if rows is not None else []
        self.raises = raises
        self.database = "property_catalog"
        self.queries = []

    def execute(self, sql, params, limit):
        self.queries.append((sql, params, limit))
        if self.raises:
            raise self.raises
        return self.rows


class _Client:
    """Stands in for the source span client.

    ``below`` names the projects that have spans older than their index floor.
    """

    def __init__(self, below=(), raises=None, any_span=None, in_flight=(), bare=()):
        self.below = set(below)
        # Projects that have at least one span. Defaults to "the ones below",
        # so an empty project is the default for anything unindexed.
        self.any_span = set(below) if any_span is None else set(any_span)
        # Projects whose every span ARRIVED within the margin (created_at is
        # recent), and projects whose spans carry nothing the catalog indexes.
        # The real table excludes each only under its own predicate, so the
        # double honours a gate only when the statement actually carries it --
        # detected by the module's own constants, not a retyped literal.
        self.in_flight = set(in_flight)
        self.bare = set(bare)
        self.raises = raises
        self.calls = []

    def _counts(self, sql, project):
        if f"created_at < {_SETTLED_BEFORE}" in sql and project in self.in_flight:
            return False
        if _INDEXABLE in sql and project in self.bare:
            return False
        return True

    def execute_read(self, sql, params=None, timeout_ms=None, settings=None):
        self.calls.append(
            {
                "sql": sql,
                "parameters": params,
                "settings": settings,
                "timeout_ms": timeout_ms,
            }
        )
        if self.raises:
            raise self.raises
        params = params or {}
        # The index-less probe binds one array; the floor probe binds one
        # (p<i>, f<i>) pair per arm.
        ids = list(params.get("project_ids") or ()) or [
            params[key] for key in sorted(params) if re.fullmatch(r"p\d+", key)
        ]
        if "start_time" not in sql:
            # The index-less probe asks "do any of these hold a settled span".
            rows = (
                [(1,)]
                if any(p in self.any_span and self._counts(sql, p) for p in ids)
                else []
            )
        else:
            hit = next(
                (p for p in ids if p in self.below and self._counts(sql, p)), None
            )
            rows = [(hit,)] if hit else []
        # execute_read returns (rows, column_types, elapsed).
        return rows, [], 0.0

    def execute(self, *args, **kwargs):  # pragma: no cover - guard
        raise AssertionError(
            "coverage must use execute_read: ClickHouseClient.execute() discards "
            "`settings` on a server-readonly client, silently dropping the "
            "bounded probe settings"
        )


def _coverage(*, rows=None, below=(), scope=SCOPE, observed=None, client=None):
    return observed_scope_coverage(
        scope=scope,
        deadline=SimpleNamespace(remaining_ms=lambda floor_ms=1: 10_000),
        observed=observed or _Observed(rows if rows is not None else []),
        client=client or _Client(below),
    )


@pytest.mark.unit
def test_fully_covered_index_reports_complete():
    """The steady state: nothing in the source predates the index."""
    result = _coverage(rows=[{"project_id": "p1", "floor": "2026-01-01 00:00:00"}])
    assert result.complete is True
    assert result.status == "complete"
    assert result.reason == "covered"


@pytest.mark.unit
def test_source_older_than_index_reports_partial_not_complete():
    """The upgrade case. This is the defect the module exists to prevent."""
    result = _coverage(
        rows=[{"project_id": "p1", "floor": "2026-06-01 00:00:00"}],
        below=("p1",),
    )
    assert result.complete is False
    assert result.status == "partial"
    assert result.reason == "source_predates_index"
    # The floor is surfaced so a caller can say what the answer covers.
    assert result.floor == "2026-06-01 00:00:00"


@pytest.mark.unit
def test_unindexed_project_with_spans_is_a_gap():
    """Spans present but nothing indexed is exactly the un-backfilled upgrade."""
    result = _coverage(rows=[], scope=SCOPE, client=_Client(any_span=("p1",)))
    assert result.complete is False
    assert result.reason == "project_unindexed"


@pytest.mark.unit
def test_unindexed_project_without_spans_is_not_a_gap():
    """An empty project has nothing to index, so it is genuinely covered.

    Regression: treating every floor-less project as suspicious dragged any
    scope containing an empty project to "partial". Workspaces routinely hold
    projects with no traces yet, so this made healthy installs report partial
    and broke dashboard flows asserting completeness.
    """
    result = _coverage(rows=[], scope=SCOPE, client=_Client(any_span=()))
    assert result.complete is True
    assert result.reason == "covered"


@pytest.mark.unit
def test_unindexed_project_with_only_in_flight_spans_is_not_a_gap():
    """A project created seconds ago is ingestion in flight, not a missing backfill.

    Its spans are in the source (the collector wrote them) and not yet in the
    index (the consumer is seconds behind). Reporting that as ``partial`` made
    every freshly created project answer incomplete for the length of the
    consumer lag -- whatever the spans' own timestamps said, because e2e
    fixtures plant yesterday's spans into a project created today. The
    Playwright trace of the failing flow read exactly:

        query_complete=false reason=project_unindexed values=[]

    on the first poll after ingest, and the same flow passed on the next run.
    """
    result = _coverage(
        rows=[], scope=SCOPE, client=_Client(any_span=("p1",), in_flight=("p1",))
    )
    assert result.complete is True
    assert result.reason == "covered"


@pytest.mark.unit
def test_in_flight_project_does_not_mask_a_settled_gap_in_the_same_scope():
    """One IN-query, LIMIT 1: the fresh project must not hide the stale one."""
    scope = {**SCOPE, "project_ids": ["fresh", "stale"]}
    result = _coverage(
        rows=[],
        scope=scope,
        client=_Client(any_span=("fresh", "stale"), in_flight=("fresh",)),
    )
    assert result.complete is False
    assert result.reason == "project_unindexed"


@pytest.mark.unit
def test_late_arrival_below_the_floor_is_not_a_gap_until_it_settles():
    """An indexed project receiving yesterday's trace from a client buffer.

    By its own clock the span is below the floor the moment it lands; by
    arrival it is seconds old and still on its way to the index. Only once it
    has been in the source for the margin without being indexed is it a gap.
    """
    rows = [{"project_id": "p1", "floor": "2026-01-02 00:00:00"}]
    settling = _coverage(rows=rows, client=_Client(below=("p1",), in_flight=("p1",)))
    assert settling.complete is True
    assert settling.reason == "covered"

    settled = _coverage(rows=rows, client=_Client(below=("p1",)))
    assert settled.complete is False
    assert settled.reason == "source_predates_index"


@pytest.mark.unit
def test_a_project_of_spans_the_catalog_would_never_index_is_not_a_gap():
    """Bare spans -- no custom attributes, no model -- produce no index row.

    The collector publishes nothing for them by design, so a project made only
    of such spans has nothing missing at any age. Treating it as a gap turned
    every workspace-scoped picker ``partial`` for good once its spans settled.
    """
    result = _coverage(
        rows=[], scope=SCOPE, client=_Client(any_span=("p1",), bare=("p1",))
    )
    assert (result.complete, result.reason) == (True, "covered")

    attributed = _coverage(rows=[], scope=SCOPE, client=_Client(any_span=("p1",)))
    assert attributed.reason == "project_unindexed"


@pytest.mark.unit
def test_an_old_bare_span_below_the_floor_is_not_a_gap():
    rows = [{"project_id": "p1", "floor": "2026-01-02 00:00:00"}]
    bare = _coverage(rows=rows, client=_Client(below=("p1",), bare=("p1",)))
    assert (bare.complete, bare.reason) == (True, "covered")

    attributed = _coverage(rows=rows, client=_Client(below=("p1",)))
    assert attributed.reason == "source_predates_index"


@pytest.mark.unit
def test_a_wide_scope_is_probed_in_chunks_and_stops_at_the_first_gap():
    from tracer.services.clickhouse.v2.property_catalog.coverage import (
        _FLOOR_ARMS_PER_STATEMENT as per_statement,
    )

    ids = [f"p{n}" for n in range(per_statement * 2 + 5)]
    gap = ids[per_statement + 3]  # second chunk
    client = _Client(below=(gap,))
    result = _coverage(
        rows=[{"project_id": pid, "floor": "2026-01-01 00:00:00"} for pid in ids],
        scope={**SCOPE, "project_ids": ids},
        client=client,
    )
    assert (result.complete, result.reason) == (False, "source_predates_index")
    floor_calls = [c for c in client.calls if "start_time" in c["sql"]]
    assert len(floor_calls) == 2, "the third chunk must not run once a gap is found"
    assert all(
        len(re.findall(r"%\(p\d+\)s", c["sql"])) <= per_statement for c in floor_calls
    )


@pytest.mark.unit
def test_both_probes_gate_on_arrival_not_the_spans_own_clock():
    """The gate is created_at (server-assigned at insert), on both statements.

    start_time is the producer's clock and is routinely days old for a span
    that arrived a second ago, so it cannot tell "in flight" from "missing".
    """
    # p2 is index-less but empty, so the index-less probe finds nothing and the
    # floor probe for p1 still runs: both statements are then on record.
    client = _Client(below=(), any_span=())
    scope = {**SCOPE, "project_ids": ["p1", "p2"]}
    _coverage(
        rows=[{"project_id": "p1", "floor": "2026-01-01 00:00:00"}],
        scope=scope,
        client=client,
    )
    gate = f"created_at < {_SETTLED_BEFORE}"
    assert len(client.calls) == 2
    unindexed, floor = client.calls[0]["sql"], client.calls[1]["sql"]
    assert "start_time" not in unindexed and gate in unindexed
    assert "start_time <" in floor and gate in floor


@pytest.mark.unit
def test_an_empty_project_does_not_mask_a_real_gap_elsewhere():
    """One empty project must not make a genuinely uncovered sibling look fine."""
    result = _coverage(
        rows=[{"project_id": "p2", "floor": "2026-06-01 00:00:00"}],
        scope=TWO_PROJECTS,
        client=_Client(below=("p2",), any_span=("p2",)),
    )
    assert result.complete is False
    assert result.reason == "source_predates_index"


@pytest.mark.unit
def test_one_uncovered_project_makes_the_whole_scope_partial():
    """A multi-project scope is only as complete as its worst project."""
    result = _coverage(
        rows=[
            {"project_id": "p1", "floor": "2026-01-01 00:00:00"},
            {"project_id": "p2", "floor": "2026-06-01 00:00:00"},
        ],
        below=("p2",),
        scope=TWO_PROJECTS,
    )
    assert result.complete is False
    assert result.reason == "source_predates_index"


@pytest.mark.unit
def test_empty_scope_is_genuinely_complete():
    """No projects means no history to miss, so the empty answer is honest."""
    result = _coverage(scope={**SCOPE, "project_ids": ()})
    assert result.complete is True
    assert result.reason == "empty_scope"


@pytest.mark.unit
@pytest.mark.parametrize(
    "observed,client,reason",
    [
        (_Observed(raises=RuntimeError("catalog down")), None, "floor_unavailable"),
        (None, _Client(raises=RuntimeError("source down")), "probe_unavailable"),
    ],
)
def test_a_failed_check_never_claims_completeness(observed, client, reason):
    """Fail closed: if we could not verify coverage, we do not assert it."""
    result = _coverage(
        rows=[{"project_id": "p1", "floor": "2026-01-01 00:00:00"}],
        observed=observed,
        client=client,
    )
    assert result.complete is False
    assert result.status == "partial"
    assert result.reason == reason


@pytest.mark.unit
def test_probe_is_pinned_to_bounded_read_settings():
    """The perf contract, not a style preference.

    Measured on a 2M-row production-shaped fixture, probing a project whose whole
    history sits below the floor: default parallelism reads 409,600 rows / 9.38
    MiB because LIMIT 1 only applies after concurrent granules land, while
    max_threads=1 + max_block_size=1024 reads 2,048 rows / 48 KiB. The
    un-backfilled state runs this probe on every request, so dropping these
    settings reintroduces the cost this feature exists to remove.
    """
    client = _Client(below=())
    _coverage(
        rows=[{"project_id": "p1", "floor": "2026-01-01 00:00:00"}], client=client
    )

    assert client.calls, "the source probe never ran"
    settings = client.calls[-1]["settings"]
    for key, value in _PROBE_SETTINGS.items():
        assert settings[key] == value, f"{key} must stay pinned to {value}"
    assert _PROBE_SETTINGS == {"max_threads": 1, "max_block_size": 1024}


@pytest.mark.unit
def test_the_index_less_probe_is_bounded_and_walled_too():
    """The settings pin must hold on BOTH statements.

    The un-backfilled state runs the index-less probe on every picker open, and
    the settings-drop regression has already happened once in this module; the
    existing pins only ever inspected the floor probe.
    """
    client = _Client(any_span=("p2",))
    scope = {**SCOPE, "project_ids": ["p1", "p2"]}
    _coverage(
        rows=[{"project_id": "p1", "floor": "2026-01-01 00:00:00"}],
        scope=scope,
        client=client,
    )
    index_less = client.calls[0]
    assert "start_time" not in index_less["sql"] and "LIMIT 1" in index_less["sql"]
    for key, value in _PROBE_SETTINGS.items():
        assert index_less["settings"][key] == value, (
            f"{key} must stay pinned to {value}"
        )
    assert index_less["timeout_ms"] == _PROBE_WALL_MS and _PROBE_WALL_MS > 0


@pytest.mark.unit
def test_probe_is_scoped_and_bounded_by_construction():
    """The probe must ask only about one project, below its own floor, LIMIT 1."""
    client = _Client(below=())
    _coverage(
        rows=[{"project_id": "p1", "floor": "2026-01-01 00:00:00"}], client=client
    )

    call = client.calls[-1]
    assert "LIMIT 1" in call["sql"]
    assert "project_id = %(p0)s" in call["sql"]
    assert "INTERVAL 1 HOUR" in call["sql"]
    # Values are bound, never interpolated into SQL text.
    assert call["parameters"]["p0"] == "p1"
    assert call["parameters"]["f0"] == "2026-01-01 00:00:00"
    assert "p1" not in call["sql"] and "2026-01-01" not in call["sql"]


@pytest.mark.unit
def test_tz_aware_floor_is_rendered_without_an_offset():
    """Regression: a tz-aware floor must not reach ClickHouse as "...+00:00".

    The catalog driver returns tz-aware datetimes, and their default string form
    carries a "+00:00" offset that DateTime64 refuses:
    ``Cannot convert string '2026-09-11 06:52:48.031961+00:00' to type
    DateTime64(6, 'UTC')``. That failure was invisible in unit tests, which fed
    plain strings, and invisible in production too, because coverage fails
    closed -- so a broken probe and an un-backfilled index looked identical.
    """
    from datetime import UTC, datetime

    client = _Client(below=())
    observed = _Observed(
        [
            {
                "project_id": "p1",
                "floor": datetime(2026, 1, 1, 12, 30, 45, 123456, tzinfo=UTC),
            }
        ]
    )
    result = _coverage(observed=observed, client=client)

    floor = client.calls[-1]["parameters"]["f0"]
    assert floor == "2026-01-01 12:30:45.123456"
    assert "+00:00" not in floor and "T" not in floor and not floor.endswith("Z")
    # The surfaced floor is rendered the same way, so callers see one format.
    assert result.floor == "2026-01-01 12:30:45.123456"


@pytest.mark.unit
def test_probe_ignores_sub_floor_jitter_but_not_real_absence():
    """Regression: an exact floor comparison reported "partial" on healthy data.

    The floor is the earliest span carrying a catalog-eligible attribute, but
    the probe can only ask about spans, and spans without such attributes
    legitimately predate it. On a real stack the oldest span sat **one
    microsecond** before the floor, so every project reported partial and 11
    E2E flows that assert ``query_complete: true`` broke.

    Measured across 846 indexed projects: 831 had no gap, 15 had a sub-second
    gap, and none fell between 1 second and 1 hour -- so the margin separates
    jitter from absence without masking anything real.
    """
    from tracer.services.clickhouse.v2.property_catalog.coverage import (
        _COVERAGE_MARGIN,
    )

    client = _Client(below=())
    _coverage(
        rows=[{"project_id": "p1", "floor": "2026-01-01 00:00:00"}], client=client
    )

    # The margin is applied in SQL, so a span a microsecond below the floor can
    # no longer match; only data older than the margin can.
    assert _COVERAGE_MARGIN == "INTERVAL 1 HOUR"
    assert f"- {_COVERAGE_MARGIN}" in client.calls[-1]["sql"]


@pytest.mark.unit
def test_probe_cost_is_flat_in_scope_size():
    """Coverage must not cost one round trip per project.

    The first implementation looped per project: measured at 765 ms for one
    project, 2.0 s for five and 5.3 s for fifteen -- linear, on the interactive
    path this feature exists to make fast. Both probes are now set-based, so a
    scope of any size costs at most two queries.
    """
    many = tuple(f"p{i}" for i in range(40))
    client = _Client(below=())
    observed = _Observed(
        [{"project_id": p, "floor": "2026-01-01 00:00:00"} for p in many]
    )
    observed_scope_coverage(
        scope={**SCOPE, "project_ids": many},
        deadline=SimpleNamespace(remaining_ms=lambda floor_ms=1: 10_000),
        observed=observed,
        client=client,
    )

    assert len(client.calls) <= 2, f"{len(client.calls)} queries for 40 projects"
    assert (
        len([k for k in client.calls[-1]["parameters"] if re.fullmatch(r"p\d+", k)])
        == 40
    )


@pytest.mark.unit
def test_every_project_is_its_own_arm_bound_to_its_own_floor():
    """No per-row floor lookup, no shared bound, nothing left unbound.

    An earlier shape located each row's floor with
    ``arrayElement(floors, indexOf(ids, project_id))``. That needed a
    fail-closed backstop for an unmappable row, and -- worse -- it was a
    per-row bound ClickHouse's key analysis cannot prune on, so a covered
    project was read end to end. Explicit arms have neither problem: each
    project's rows are selected by equality and bounded by that project's own
    floor, so there is no row whose floor could be looked up wrongly.
    """
    client = _Client(below=())
    scope = {**SCOPE, "project_ids": ["p1", "p2", "p3"]}
    _coverage(
        rows=[
            {"project_id": "p1", "floor": "2026-01-01 00:00:00"},
            {"project_id": "p2", "floor": "2026-03-01 00:00:00"},
            {"project_id": "p3", "floor": "2026-02-01 00:00:00"},
        ],
        scope=scope,
        client=client,
    )
    sql, params = client.calls[-1]["sql"], client.calls[-1]["parameters"]
    assert "arrayElement" not in sql and "indexOf" not in sql
    arms = re.findall(
        r"\(project_id = %\((p\d+)\)s AND start_time < toDateTime64\(%\((f\d+)\)s, 6, 'UTC'\) - INTERVAL 1 HOUR\)",
        sql,
    )
    assert len(arms) == 3
    bound = {params[p]: params[f] for p, f in arms}
    assert bound == {
        "p1": "2026-01-01 00:00:00",
        "p2": "2026-03-01 00:00:00",
        "p3": "2026-02-01 00:00:00",
    }
    # The cheap predicates sit in PREWHERE; the attribute columns behind the
    # eligibility predicate are read only for rows that pass them.
    assert (
        sql.index("PREWHERE")
        < sql.index(_SETTLED_BEFORE)
        < sql.index(" WHERE ")
        < sql.index(_INDEXABLE)
    )
    assert "is_deleted = 0" in sql[: sql.index(" WHERE ")]


@pytest.mark.unit
def test_probe_uses_the_guarded_read_path_that_preserves_settings():
    """The bounded settings must actually reach ClickHouse, not just be passed.

    Regression, and a lesson about doubles. An earlier revision called
    ``ClickHouseClient.execute()``, which sets ``settings = None``
    unconditionally on a server-readonly client -- so ``max_threads=1``,
    ``max_block_size=1024`` and the wall clock were silently discarded, and the
    200x read amplification they exist to prevent was reintroduced.

    The old test could not see it: it asserted the settings dict handed to a fake
    that faithfully recorded whatever it was given. The fake now refuses
    ``execute()`` outright and mirrors ``execute_read``'s real signature and
    triple return, so the same mistake fails here instead of in production.
    """
    client = _Client(below=())
    _coverage(
        rows=[{"project_id": "p1", "floor": "2026-01-01 00:00:00"}], client=client
    )

    call = client.calls[-1]
    assert call["settings"]["max_threads"] == 1
    assert call["settings"]["max_block_size"] == 1024
    # A wall clock must be supplied to the guarded path, not left unbounded.
    assert call["timeout_ms"] == _PROBE_WALL_MS and _PROBE_WALL_MS > 0


@pytest.mark.unit
def test_source_client_allows_settings_under_server_readonly():
    """The client must be built so its settings survive server-readonly mode."""
    import inspect

    from tracer.services.clickhouse.v2.property_catalog import coverage as mod

    src = inspect.getsource(mod._source_client)
    assert "allow_query_settings_with_server_readonly=True" in src, (
        "without this flag ClickHouseClient drops settings on a readonly client"
    )


@pytest.mark.unit
def test_floor_query_is_tenant_scoped():
    """Coverage must not be derived from another tenant's rows."""
    observed = _Observed([{"project_id": "p1", "floor": "2026-01-01 00:00:00"}])
    _coverage(observed=observed)

    sql, params, _ = observed.queries[0]
    assert "k.organization_id = %(organization_id)s" in sql
    assert "k.workspace_id = %(workspace_id)s" in sql
    assert "k.project_id IN %(project_ids)s" in sql
    assert params["organization_id"] == "org-1"
    assert params["workspace_id"] == "ws-1"


@pytest.mark.unit
def test_status_maps_to_the_frontend_degraded_contract():
    """`query_complete: false` with a non-"sampled" status renders as degraded.

    getQueryReadState (frontend/src/utils/queryReadState.js) treats that pair as
    degraded, so the caveat surfaces without a frontend change.
    """
    assert Coverage(False, "source_predates_index").status == "partial"
    assert Coverage(False, "source_predates_index").status != "sampled"
    assert Coverage(True, "covered").status == "complete"
