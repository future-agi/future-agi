"""The observed index must never report history it does not hold as complete.

Upgrading onto the observed read path leaves the index empty for everything
ingested before it existed. These tests pin the contract that makes that state
visible instead of silently indistinguishable from "this project has no custom
attributes".
"""

from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.coverage import (
    _PROBE_SETTINGS,
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

    def __init__(self, below=(), raises=None):
        self.below = set(below)
        self.raises = raises
        self.calls = []

    def query(self, sql, parameters=None, settings=None):
        self.calls.append({"sql": sql, "parameters": parameters, "settings": settings})
        if self.raises:
            raise self.raises
        hit = (parameters or {}).get("project_id") in self.below
        return SimpleNamespace(result_rows=[(1,)] if hit else [])


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
def test_project_absent_from_index_is_not_vouched_for():
    """An unindexed project may have unindexed spans; we cannot tell cheaply."""
    result = _coverage(rows=[], scope=SCOPE)
    assert result.complete is False
    assert result.reason == "project_unindexed"


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
    _coverage(rows=[{"project_id": "p1", "floor": "2026-01-01 00:00:00"}], client=client)

    assert client.calls, "the source probe never ran"
    settings = client.calls[0]["settings"]
    for key, value in _PROBE_SETTINGS.items():
        assert settings[key] == value, f"{key} must stay pinned to {value}"
    assert _PROBE_SETTINGS == {"max_threads": 1, "max_block_size": 1024}


@pytest.mark.unit
def test_probe_is_scoped_and_bounded_by_construction():
    """The probe must ask only about one project, below its own floor, LIMIT 1."""
    client = _Client(below=())
    _coverage(rows=[{"project_id": "p1", "floor": "2026-01-01 00:00:00"}], client=client)

    call = client.calls[0]
    assert "LIMIT 1" in call["sql"]
    assert "project_id = %(project_id)s" in call["sql"]
    assert "start_time < toDateTime64(%(floor)s, 6, 'UTC')" in call["sql"]
    # Values are bound, never interpolated into SQL text.
    assert call["parameters"] == {"project_id": "p1", "floor": "2026-01-01 00:00:00"}
    assert "p1" not in call["sql"]


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
        [{"project_id": "p1", "floor": datetime(2026, 1, 1, 12, 30, 45, 123456, tzinfo=UTC)}]
    )
    result = _coverage(observed=observed, client=client)

    floor = client.calls[0]["parameters"]["floor"]
    assert floor == "2026-01-01 12:30:45.123456"
    assert "+00:00" not in floor and "T" not in floor and not floor.endswith("Z")
    # The surfaced floor is rendered the same way, so callers see one format.
    assert result.floor == "2026-01-01 12:30:45.123456"


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
