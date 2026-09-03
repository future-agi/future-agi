"""HAVING-clause operator coverage for the session list builder.

``first_message``/``last_message`` and the numeric session aggregates
(``duration``/``total_cost``/``total_tokens``/``traces_count``) are filtered
post-``GROUP BY`` by the hand-written ``_build_having_clauses`` — not the shared
``ClickHouseFilterBuilder``. These tests pin its operator coverage to the FE
contract so a multi-select (``in``) or range (``between``) filter never silently
collapses to ``HAVING 0 = 1``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)

PROJECT_ID = "11111111-1111-1111-1111-111111111111"


def _builder(filters: list[dict]) -> SessionListQueryBuilder:
    return SessionListQueryBuilder(project_id=PROJECT_ID, filters=filters)


def _msg_filter(col: str, op: str, value: Any) -> dict:
    return {
        "column_id": col,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }


def _num_filter(col: str, op: str, value: Any) -> dict:
    return {
        "column_id": col,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": op,
            "filter_value": value,
        },
    }


# ── message filters: in / not_in (the multi-select regression) ───────────────
@pytest.mark.parametrize("col", ["first_message", "last_message"])
def test_message_in_builds_in_clause(col):
    b = _builder([_msg_filter(col, "in", ["Hello from Alice", "Hello from Carol"])])
    having = b._build_having_clauses()

    assert "0 = 1" not in having
    assert f"{col} IN %(" in having
    bound = [v for v in b.params.values() if isinstance(v, tuple)]
    assert bound == [("Hello from Alice", "Hello from Carol")]


@pytest.mark.parametrize("col", ["first_message", "last_message"])
def test_message_not_in_builds_not_in_clause(col):
    b = _builder([_msg_filter(col, "not_in", ["spam"])])
    having = b._build_having_clauses()

    assert f"{col} NOT IN %(" in having
    assert ("spam",) in b.params.values()


def test_message_in_empty_list_matches_nothing():
    b = _builder([_msg_filter("first_message", "in", [])])
    assert b._build_having_clauses() == "0 = 1"


def test_message_not_in_empty_list_matches_everything():
    b = _builder([_msg_filter("first_message", "not_in", [])])
    assert b._build_having_clauses() == "1 = 1"


def test_message_equals_still_scalar():
    b = _builder([_msg_filter("first_message", "equals", "hi")])
    having = b._build_having_clauses()
    assert "first_message = %(" in having
    assert "hi" in b.params.values()


@pytest.mark.parametrize(
    "op,expected_value",
    [
        ("contains", "%hi%"),
        ("not_contains", "%hi%"),
        ("starts_with", "hi%"),
        ("ends_with", "%hi"),
    ],
)
def test_message_wildcard_ops_wrap_value(op, expected_value):
    b = _builder([_msg_filter("first_message", op, "hi")])
    having = b._build_having_clauses()
    like = "NOT ILIKE" if op == "not_contains" else "ILIKE"
    assert f"first_message {like} %(" in having
    assert expected_value in b.params.values()


@pytest.mark.parametrize("op", ["is_null", "is_not_null"])
def test_message_null_ops(op):
    b = _builder([_msg_filter("first_message", op, None)])
    having = b._build_having_clauses()
    if op == "is_null":
        assert having == "(first_message IS NULL OR first_message = '')"
    else:
        assert having == "(first_message IS NOT NULL AND first_message != '')"


# ── numeric aggregate filters: between / in ──────────────────────────────────
@pytest.mark.parametrize(
    "col", ["duration", "total_cost", "total_tokens", "traces_count"]
)
def test_aggregate_between(col):
    b = _builder([_num_filter(col, "between", [10, 100])])
    having = b._build_having_clauses()

    assert "0 = 1" not in having
    ch_col = SessionListQueryBuilder.SESSION_FILTER_MAP[col]
    assert f"{ch_col} BETWEEN %(" in having
    # lo must bind before hi (else BETWEEN 100 AND 10 matches nothing).
    lo_name, hi_name = re.search(
        rf"{ch_col} BETWEEN %\((\w+)\)s AND %\((\w+)\)s", having
    ).groups()
    assert b.params[lo_name] == 10 and b.params[hi_name] == 100


def test_aggregate_not_between():
    b = _builder([_num_filter("total_cost", "not_between", [1, 2])])
    having = b._build_having_clauses()
    lo_name, hi_name = re.search(
        r"total_cost NOT BETWEEN %\((\w+)\)s AND %\((\w+)\)s", having
    ).groups()
    assert b.params[lo_name] == 1 and b.params[hi_name] == 2


def test_two_having_conditions_get_distinct_params():
    # A message IN and a numeric BETWEEN both live in HAVING; their params
    # (having_*) must not collide.
    b = _builder(
        [
            _msg_filter("first_message", "in", ["a", "b"]),
            _num_filter("total_cost", "between", [0.1, 0.5]),
        ]
    )
    having = b._build_having_clauses()
    assert "first_message IN %(" in having and "total_cost BETWEEN %(" in having
    assert ("a", "b") in b.params.values()
    assert 0.1 in b.params.values() and 0.5 in b.params.values()


def test_aggregate_between_bad_arity_is_no_match():
    b = _builder([_num_filter("duration", "between", [10])])
    assert b._build_having_clauses() == "0 = 1"


def test_aggregate_is_not_null():
    b = _builder([_num_filter("traces_count", "is_not_null", None)])
    assert b._build_having_clauses() == "traces_count IS NOT NULL"


def test_aggregate_is_null():
    b = _builder([_num_filter("duration", "is_null", None)])
    assert b._build_having_clauses() == "duration IS NULL"


def test_aggregate_scalar_ops_unchanged():
    b = _builder([_num_filter("duration", "greater_than", 5)])
    having = b._build_having_clauses()
    assert "duration > %(" in having
    assert 5 in b.params.values()


# ── end-to-end: the failing curl no longer yields HAVING 0 = 1 ───────────────
def test_message_in_end_to_end_does_not_collapse():
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    "2025-08-25T06:38:15.000Z",
                    "2026-08-25T18:30:00.000Z",
                ],
            },
        },
        _msg_filter("first_message", "in", ["Hello from Carol", "Hello from Alice"]),
    ]
    query, params = _builder(filters).build()

    assert "HAVING 0 = 1" not in query
    assert "first_message IN %(" in query
    assert ("Hello from Carol", "Hello from Alice") in params.values()


def test_having_params_do_not_collide_with_span_filter_params():
    """HAVING (having_*) and span-filter (col_*/p_*) params share self.params in
    build(); a message `in` + a span-level `model equals` must not overwrite."""
    filters = [
        _msg_filter("first_message", "in", ["a", "b"]),
        {
            "column_id": "model",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "gpt-4",
            },
        },
    ]
    query, params = _builder(filters).build()

    assert "HAVING 0 = 1" not in query
    assert ("a", "b") in params.values()
    assert "gpt-4" in params.values()
    # No param key silently clobbered: both the IN tuple and the model scalar survive.
    assert sum(1 for v in params.values() if v == ("a", "b")) == 1


def test_count_query_is_self_contained_without_build_first():
    # build_count_query() must bind its own having_* params, not rely on a prior
    # build() call having populated self.params.
    b = _builder([_msg_filter("first_message", "in", ["a", "b"])])
    query, params = b.build_count_query()
    referenced = set(re.findall(r"%\((\w+)\)s", query))
    assert referenced <= set(params), f"unbound params: {referenced - set(params)}"
    assert ("a", "b") in params.values()
