"""The candidate seed's raw root population names roots as each builder does.

A filtered trace list seeds its candidates from the traces that have a raw
root inside the slice. The root predicate is the whole WHERE of that subquery:
the global-root builder writes it bare, and Observe writes its project-local
condition as one parenthesized term. The legacy builder has no such subquery
and keeps its parenthesized clause on the ordered roots.
"""

import pytest

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.observe_usage import (
    ObserveTraceListQueryBuilderV2,
)
from tracer.tests.test_trace_text_candidate_seed import SLICE, picker_leaves, subject

pytestmark = pytest.mark.unit

GLOBAL_ROOTS = "parent_span_id IS NULL OR parent_span_id = ''"
SLICE_PARAMS = ("filter_slice_start_us", "filter_slice_end_us")


def _candidate_seed(builder):
    """One candidate seed statement, split where the candidates end."""

    sql, _ = builder.build_filter_candidate_seed_page(
        slice_start=SLICE[0], slice_end=SLICE[1], limit=200
    )
    return sql.split("SELECT trace_id, id AS root_span_id", 1)


def test_global_roots_are_written_bare_in_the_candidate_root_population():
    candidates, roots = _candidate_seed(picker_leaves(1))

    assert f"WHERE {GLOBAL_ROOTS}" in candidates
    assert f"({GLOBAL_ROOTS})" not in candidates
    # The ordered roots keep their own parenthesized clause.
    assert f"AND ({GLOBAL_ROOTS})" in roots


def test_observe_keeps_project_local_roots_in_the_candidate_root_population():
    builder = picker_leaves(1, cls=ObserveTraceListQueryBuilderV2)
    candidates, _ = _candidate_seed(builder)
    local_roots = builder._trace_root_window_condition(*SLICE_PARAMS)
    slice_scope = (
        "start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s) "
        "AND start_time < fromUnixTimestamp64Micro(%(filter_slice_end_us)s)"
    )

    # One parenthesized term: no parent, or a parent this project never stored.
    assert local_roots.startswith(f"(({GLOBAL_ROOTS})")
    assert local_roots.endswith("))")
    assert f"WHERE {local_roots}" in candidates
    assert f"WHERE {GLOBAL_ROOTS}" not in candidates
    assert "OR (parent_span_id IS NOT NULL AND parent_span_id != ''" in candidates
    assert "(toString(project_id), trace_id, parent_span_id) NOT IN (" in candidates
    # The parent lookup reads only the parents this slice's spans name.
    assert f"AND ({slice_scope})" in candidates
    assert "HAVING argMax(is_deleted, _version) = 0" in candidates


def test_legacy_builder_root_clauses_are_unchanged():
    builder = subject(cls=TraceListQueryBuilder)

    # No raw root population subquery, so no bare WHERE to write.
    assert builder._scalar_candidate_root_population_predicate() == ""
    assert builder._trace_root_window_condition(*SLICE_PARAMS) == f"({GLOBAL_ROOTS})"
    sql, _ = builder.build_filter_ordered_seed_page(
        slice_start=SLICE[0], slice_end=SLICE[1], limit=200
    )
    assert f"AND ({GLOBAL_ROOTS})" in sql
    assert f"WHERE {GLOBAL_ROOTS}" not in sql
