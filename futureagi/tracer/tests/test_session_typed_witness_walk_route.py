"""A Session-list page filtered by a typed span attribute takes the bounded walk.

Number and boolean leaves used to keep the candidate lane, whose statement is
seeded by an any-span witness over the whole request window. On production's
high-volume tenant at twelve months that witness scan alone read 238 M rows in
9.4 s on the product's two threads and named about 1.44 M sessions, and
ClickHouse materialises every ``IN`` set built from it while planning - so the
statement died at the 30 s wall before reading a byte, at every window of
thirty days and longer. The walk is exact for the same predicate and bounded
per statement: root-ordered seeds, then a classifier that replays only the
seeded sessions' spans and stops at the first ordered prefix of survivors.

These tests pin three things: the route, the shapes the route must not touch,
and the statements a typed leaf issues on the walk - the fused classifier
(two ``spans`` scans, root-ness from the all-span replay) and the plain root
seed, with the witness gate engaging only when its setting turns it on.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.test import override_settings

from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
USER_ID = "00000000-0000-4000-8000-000000000003"
END = datetime(2026, 9, 12, 0, 0)
START = END - timedelta(days=365)
CANDIDATES = (
    "00000000-0000-4000-8000-0000000000aa",
    "00000000-0000-4000-8000-0000000000ab",
)


def _window() -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _attribute(key: str, kind: str, operation: str, value) -> dict:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _user() -> dict:
    return {
        "column_id": "end_user_id",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": [USER_ID],
        },
    }


def _session_field() -> dict:
    return {
        "column_id": "total_cost",
        "filter_config": {
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": 0,
        },
    }


def _builder(filters: list[dict]) -> SessionListQueryBuilderV2:
    return SessionListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        page_number=0,
        page_size=25,
        bounded_internal_scan=True,
    )


def _cte(sql: str, name: str) -> str:
    return sql.split(f"{name} AS (", 1)[1].split("\n        )", 1)[0]


@pytest.mark.parametrize(
    ("kind", "operation", "value", "typed_map"),
    [
        ("boolean", "equals", True, "span_attr_bool"),
        ("boolean", "equals", False, "span_attr_bool"),
        ("number", "greater_than", 7, "span_attr_num"),
        ("number", "equals", 0, "span_attr_num"),
        ("text", "equals", "Rejected", "span_attr_str"),
    ],
)
def test_every_typed_map_witness_takes_the_walk(kind, operation, value, typed_map):
    builder = _builder([_window(), _attribute("flag", kind, operation, value)])
    plans = builder._candidate_scalar_page_plans()
    assert plans is not None and len(plans) == 1
    assert plans[0].raw_witness_predicate is not None
    assert typed_map in (plans[0].raw_key_witness_predicate or "")
    assert builder.prefers_bounded_filter_page() is True
    assert builder.supports_bounded_filter_scan() is True
    # Nothing is deleted: the candidate statements still render for shapes
    # that reach them by another door (user-detail, org scope).
    sql, _ = builder.build_candidate_cursor_page_query()
    assert "candidate_witness_session_ids" in sql


def test_a_number_leaf_beside_a_text_leaf_takes_the_walk_too():
    builder = _builder(
        [
            _window(),
            _attribute("account", "text", "in", ["a", "b"]),
            _attribute("retries", "number", "greater_than", 2),
        ]
    )
    assert builder.prefers_bounded_filter_page() is True


@pytest.mark.parametrize(
    ("filters", "why"),
    [
        ([_window()], "date-only shape keeps the exact latest-root selector"),
        (
            [_window(), _attribute("account", "text", "not_in", ["a"])],
            "a negated leaf has no positive witness",
        ),
        (
            [_window(), _user(), _attribute("flag", "boolean", "equals", True)],
            "the user-detail page keeps its fused candidate lane",
        ),
        (
            [
                _window(),
                _session_field(),
                _attribute("flag", "boolean", "equals", True),
            ],
            "a session aggregate predicate keeps its specialized path",
        ),
    ],
)
def test_shapes_outside_the_walk_are_unchanged(filters, why):
    assert _builder(filters).prefers_bounded_filter_page() is False, why


def test_the_walk_classifier_for_a_boolean_leaf_reads_the_sessions_spans_once():
    builder = _builder([_window(), _attribute("flag", "boolean", "equals", True)])
    sql, params = builder.build_filter_match_query(list(CANDIDATES))

    # One session-keyed identity scan and one replay; no root scan, no root
    # replay, so the bloom scatter over the seeded sessions is paid once.
    assert sql.count("FROM spans") == 2
    assert "candidate_root_identities AS (" not in sql
    assert "latest_roots AS (" not in sql
    assert "resolved_root_sessions" not in sql
    replay = _cte(sql, "latest_candidate_scalar_spans")
    assert (
        "argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id" in replay
    )
    assert (
        "argMax(attrs_bool[%(latest_filter_key_0)s], _version) AS latest_attr_value_0"
        in replay
    )
    sessions = sql.split("sessions AS (", 1)[1].split("FROM sessions", 1)[0]
    assert "minIf(latest_start_time, is_root) AS session_start" in sessions
    assert "countIf(is_root) > 0" in sessions
    assert (
        "countIf(latest_attr_exists_0 AND latest_attr_value_0 = %(latest_filter_param_0)s) > 0"
        in sessions
    )
    # Membership evidence is gathered over the whole request window.
    identities = _cte(sql, "candidate_scalar_span_identities")
    assert "%(start_date_us)s" in identities and "%(end_date_us)s" in identities
    assert params["candidate_filter_session_ids"] == CANDIDATES
    # The boolean binds as the map value type, an integer.
    assert params["latest_filter_param_0"] == 1


def test_the_walk_classifier_for_a_number_leaf_applies_the_value_on_latest_state():
    builder = _builder([_window(), _attribute("retries", "number", "greater_than", 7)])
    sql, params = builder.build_filter_match_query(list(CANDIDATES))
    assert sql.count("FROM spans") == 2
    assert "countIf(is_root) > 0" in sql
    assert (
        "countIf(latest_attr_exists_0 AND latest_attr_value_0 > %(latest_filter_param_0)s) > 0"
        in sql
    )
    assert params["latest_filter_param_0"] == 7


@pytest.mark.parametrize("kind,value", [("boolean", True), ("number", 7)])
def test_the_walk_seed_for_a_typed_leaf_is_the_root_seed_until_the_gate_turns_on(
    kind, value
):
    builder = _builder([_window(), _attribute("flag", kind, "equals", value)])
    slice_start = END - timedelta(days=2)
    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=-1):
        sql, params = builder.build_filter_seed_page(
            slice_start=slice_start, slice_end=END, limit=31
        )
    assert "witness_spans" not in sql
    assert "seed_spans.parent_span_id IS NULL" in sql
    assert params["filter_seed_limit"] == 31
    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=1):
        gated, gated_params = builder.build_filter_seed_page(
            slice_start=slice_start, slice_end=END, limit=31
        )
    assert "seed_spans.trace_session_id IN (" in gated
    assert "witness_spans" in gated
    assert "filter_witness_start_us" in gated_params
