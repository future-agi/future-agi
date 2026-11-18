"""Candidate scoping of the user-detail Traces classifier.

The user-detail Traces page is one ``end_user_id`` leaf, one span-attribute
leaf and the request window. The attribute leaf is a typed plan; the user leaf
is a residual, compiled by the shared filter compiler in trace mode into a
membership subquery over ``spans`` that the classifier consumes as
``trace_id IN (SELECT ...)``. That subquery is whole-history by contract (a
child may be written days after its root), so the only thing that bounds it
is the candidate batch - and the batch bounds nothing when the column is
hidden behind ``toString``: ``trace_id`` is a ``String`` column with a bloom
filter and a place in the sorting key, and the cast made the subquery read the
project's entire retained span history per classify chunk. On production's
high-volume tenant that read died at the 9.5 s wall before its first progress
packet, on eighteen candidates, at every window from fourteen days up.

What this module pins:

* the residual membership subquery binds ``trace_id`` bare against the same
  ``candidate_trace_ids`` literals the statement's own replay binds bare, for
  each of the four typed siblings the sweep measured;
* the subquery keeps its whole-history contract - no event-time envelope was
  smuggled in as a substitute for the index;
* the external-identifier shape (``user_id`` through the curated end-user
  dimension) takes the same bare form;
* every other column the candidate filter scopes keeps its cast.

How ``end_user_id`` itself is compared belongs to the shared column compiler
and is deliberately not pinned here.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_bounded_trace_filter_reads import (
    END,
    PROJECT_ID,
    _render_driver_sql,
    _time_filter,
)

pytestmark = pytest.mark.unit

CANONICAL_USER = "50f8845d-e410-5ceb-9bb5-a0d5e7ca6773"
REQUEST_START = END - timedelta(days=30)
CANDIDATES = [
    "1c1f6f4a-3f7a-4b2e-9c4d-0a1b2c3d4e5f",
    "2d2f7f5b-4f8b-4c3f-8d5e-1b2c3d4e5f60",
]
LONG_TEXT = "a-value-long-enough-to-anchor-the-ngram-index-0001"

TYPED_SIBLINGS = {
    "boolean_equals": {
        "filter_type": "boolean",
        "filter_op": "equals",
        "filter_value": True,
    },
    "number_greater_than": {
        "filter_type": "number",
        "filter_op": "greater_than",
        "filter_value": 3,
    },
    "short_text_equals": {
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "ok",
    },
    "long_text_in": {
        "filter_type": "text",
        "filter_op": "in",
        "filter_value": [LONG_TEXT],
    },
}


def _user_filter(value, operation="in", column_id="end_user_id"):
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _attribute_filter(config):
    return {
        "column_id": "lk.attribute",
        "filter_config": {"col_type": "SPAN_ATTRIBUTE", **config},
    }


def _flat(sql: str) -> str:
    return re.sub(r"\s+", " ", sql)


def _classify(builder):
    assert builder.supports_bounded_filter_scan() is True
    sql, params = builder.build_filter_match_query(list(CANDIDATES))
    _render_driver_sql(sql, params)
    assert params["candidate_trace_ids"] == tuple(CANDIDATES)
    return _flat(sql)


def _residual_subquery(flat_sql: str) -> str:
    """The user leaf's membership subquery, as the classifier consumes it."""

    match = re.search(
        r"WHERE trace_id IN \((SELECT trace_id FROM spans .*?)\) ORDER BY", flat_sql
    )
    assert match is not None, flat_sql
    return match.group(1)


@pytest.mark.parametrize("sibling", sorted(TYPED_SIBLINGS))
def test_user_leaf_membership_binds_trace_id_bare_beside_every_typed_sibling(sibling):
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(REQUEST_START, END),
            _attribute_filter(TYPED_SIBLINGS[sibling]),
            _user_filter([CANONICAL_USER]),
        ],
    )
    flat = _classify(builder)
    residual = _residual_subquery(flat)

    # The same literals the replay binds bare, bound bare here too.
    assert "AND trace_id IN %(candidate_trace_ids)s" in residual
    assert "toString(trace_id)" not in flat
    assert flat.count("trace_id IN %(candidate_trace_ids)s") == 2
    # The user leaf is still the residual - it never became a typed plan.
    assert "end_user_id" in residual
    assert "is_deleted = 0" in residual


def test_user_leaf_membership_keeps_its_whole_history_contract():
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(REQUEST_START, END),
            _attribute_filter(TYPED_SIBLINGS["boolean_equals"]),
            _user_filter([CANONICAL_USER]),
        ],
    )
    residual = _residual_subquery(_classify(builder))

    # Once a candidate is known, non-root membership inspects every current
    # child version. The batch is the bound; the window is not.
    assert "INTERVAL 1 DAY" not in residual
    assert "start_date" not in residual
    assert "end_date" not in residual
    assert "filter_slice" not in residual


def test_external_user_identifier_membership_binds_trace_id_bare():
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(REQUEST_START, END),
            _attribute_filter(TYPED_SIBLINGS["boolean_equals"]),
            _user_filter("10000004", operation="equals", column_id="user_id"),
        ],
    )
    flat = _classify(builder)

    # Resolved through the curated end-user dimension, the spans side of the
    # membership read is scoped to the batch by the same bare column.
    assert "FROM end_user_id_remap AS remap_match FINAL" in flat
    assert "toString(trace_id)" not in flat
    assert flat.count("trace_id IN %(candidate_trace_ids)s") == 2


def test_legacy_builder_shares_the_bare_form():
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(REQUEST_START, END),
            _attribute_filter(TYPED_SIBLINGS["boolean_equals"]),
            _user_filter([CANONICAL_USER]),
        ],
    )
    flat = _classify(builder)

    assert "toString(trace_id)" not in flat
    assert flat.count("trace_id IN %(candidate_trace_ids)s") == 2


def test_candidate_filter_unwraps_only_the_spans_trace_id():
    compiler = ClickHouseFilterBuilder(
        table="spans",
        query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE,
        project_id=PROJECT_ID,
        candidate_ids_param="candidate_trace_ids",
    )

    assert compiler._candidate_filter("trace_id") == (
        " AND trace_id IN %(candidate_trace_ids)s"
    )
    # The trace-tags table's ``id`` and every Score-side expression keep the
    # cast: they are not the spans table's ``trace_id``.
    assert (
        compiler._candidate_filter("id")
        == " AND toString(id) IN %(candidate_trace_ids)s"
    )
    assert compiler._candidate_filter("eval_scan.trace_id") == (
        " AND toString(eval_scan.trace_id) IN %(candidate_trace_ids)s"
    )
    assert (
        compiler._candidate_trace_filter() == " AND trace_id IN %(candidate_trace_ids)s"
    )
    assert compiler._candidate_trace_filter("id") == (
        " AND toString(id) IN %(candidate_trace_ids)s"
    )

    unscoped = ClickHouseFilterBuilder(
        table="spans",
        query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE,
        project_id=PROJECT_ID,
    )
    assert unscoped._candidate_filter("trace_id") == ""


def test_span_mode_candidate_scoping_is_unchanged():
    compiler = ClickHouseFilterBuilder(
        table="spans",
        query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
        project_id=PROJECT_ID,
        candidate_ids_param="candidate_span_ids",
    )

    # Span candidates resolve to their traces through a subquery keyed on the
    # span id; that shape is another surface's and is not touched here.
    fragment = compiler._candidate_trace_filter()
    assert fragment.startswith(
        " AND toString(trace_id) IN (SELECT toString(trace_id) FROM spans"
    )
    assert "toString(id) IN %(candidate_span_ids)s" in fragment
