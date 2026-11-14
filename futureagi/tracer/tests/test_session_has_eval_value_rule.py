"""The session list's ``has_eval`` leaf reaches the one boolean value rule.

``SessionListQueryBuilder._bounded_has_eval_values`` used to hand-write the
value half of the boolean meta-filter rule — its own ``bool`` branch, its own
``"true"`` / ``"false"`` coercion — fourteen lines above a sibling
``has_annotation`` branch that calls :func:`parse_boolean_meta_filter`.  The
two halves of one method reached the same rule two different ways, which is
the failure mode this train's own body blames for the defect it repairs, and
no test in the train tied them together: inverting either coercion branch, or
widening the operator gate to ``not_equals``, left every one of the train's
modules green.

These cases pin the lane at the level a user can feel — the membership
operator the compiled statement carries — and tie it to
:func:`resolve_boolean_meta_value`, so a change to the shared rule can no
longer pass unnoticed on this route.  The operator gate stays deliberately
narrower than the published vocabulary: the presence operators have no finite
latest-state compiler here, so they are still refused, and that refusal is
pinned too.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from django.test import override_settings

from tracer.services.clickhouse.query_builders.filters import (
    BooleanMetaFilterShapeError,
    resolve_boolean_meta_value,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

NOW = datetime(2026, 7, 31, 12, 0)
LIVE_EVAL_TRACE_IDS = "(SELECT trace_id FROM live_candidate_eval_trace_ids)"


def _window() -> list[dict]:
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    (NOW - timedelta(days=1)).isoformat(),
                    (NOW + timedelta(days=1)).isoformat(),
                ],
            },
        }
    ]


def _has_eval_leaf(**config) -> dict:
    return {
        "column_id": "has_eval",
        "filter_config": {"filter_type": "boolean", "filter_op": "equals", **config},
    }


def _builder(leaf: dict) -> SessionListQueryBuilderV2:
    return SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[*_window(), leaf],
        eval_config_ids=[str(uuid.uuid4())],
        bounded_internal_scan=True,
    )


def _membership_op(leaf: dict) -> str:
    """Compile the leaf and report the membership operator it rendered."""

    sql, _ = _builder(leaf).build_filter_match_query([str(uuid.uuid4())])
    positive = f"trace_id IN {LIVE_EVAL_TRACE_IDS}" in sql
    negative = f"trace_id NOT IN {LIVE_EVAL_TRACE_IDS}" in sql
    assert positive or negative, "no has_eval membership predicate was rendered"
    return "NOT IN" if negative else "IN"


ACCEPTED_VALUES = [
    (True, "IN"),
    (False, "NOT IN"),
    ("true", "IN"),
    ("false", "NOT IN"),
    ("True", "IN"),
    ("FALSE", "NOT IN"),
    ("  true  ", "IN"),
    ("  False  ", "NOT IN"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("filter_value", "membership_op"), ACCEPTED_VALUES)
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_has_eval_value_compiles_to_the_shared_rule_s_membership(
    filter_value: bool | str,
    membership_op: str,
):
    """Every accepted value renders the operator the shared rule resolves."""

    wanted = resolve_boolean_meta_value("has_eval", filter_value, "equals")
    assert wanted is (membership_op == "IN")
    assert _membership_op(_has_eval_leaf(filter_value=filter_value)) == membership_op


@pytest.mark.unit
@pytest.mark.parametrize(("filter_value", "membership_op"), ACCEPTED_VALUES)
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_has_eval_agrees_with_the_shared_rule_on_every_accepted_value(
    filter_value: bool | str,
    membership_op: str,
):
    """The route's compiled answer is the shared rule's answer, not a copy.

    Stated as an equality against :func:`resolve_boolean_meta_value` rather
    than against a literal, so a change to the one rule moves this assertion
    with it instead of leaving a second opinion standing on this route.
    """

    del membership_op
    compiled_positive = (
        _membership_op(_has_eval_leaf(filter_value=filter_value)) == "IN"
    )
    assert compiled_positive is resolve_boolean_meta_value(
        "has_eval", filter_value, "equals"
    )


def _assert_refused(leaf: dict) -> None:
    """A malformed ``has_eval`` leaf degrades the lane rather than compiling.

    ``bounded_filter_degraded_error_code`` is the gate the session view reads:
    the parser's ``ValueError`` reaches the client as this code and a page that
    says so, never as an unfiltered page.
    """

    builder = _builder(leaf)
    assert builder.bounded_filter_degraded_error_code() == "unsupported_filter_shape"
    assert builder.supports_bounded_filter_scan() is False
    with pytest.raises(ValueError, match="unsupported bounded session filter scan"):
        builder.build_filter_match_query([str(uuid.uuid4())])


@pytest.mark.unit
@pytest.mark.parametrize(
    "filter_value",
    [None, 0, 1, "yes", "no", "", "TRUE_", [], {}, ["true"]],
)
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_has_eval_rejects_every_value_the_shared_rule_rejects(filter_value):
    """A value the shared rule refuses is refused on this route too.

    Stated as a pair so the two can never drift apart: the shared rule raises,
    and the session lane degrades with the code its view maps to a 400.
    """

    with pytest.raises(BooleanMetaFilterShapeError):
        resolve_boolean_meta_value("has_eval", filter_value, "equals")
    _assert_refused(_has_eval_leaf(filter_value=filter_value))


@pytest.mark.unit
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_has_eval_rejects_a_leaf_that_carries_no_value():
    """An absent value is a malformed leaf, not a false."""

    _assert_refused(
        {
            "column_id": "has_eval",
            "filter_config": {"filter_type": "boolean", "filter_op": "equals"},
        }
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "filter_op", ["not_equals", "is_null", "is_not_null", "is", "in", "EQUALS", ""]
)
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_has_eval_admits_only_a_literal_equals(filter_op: str):
    """The operator gate does not widen to the shared rule's vocabulary.

    ``resolve_boolean_meta_value`` accepts ``not_equals``; this lane must not,
    because the presence and negation operators have no finite latest-state
    compiler on the session route.  The gate is what keeps the 400 the train's
    body discloses as a deliberate gap, so it is pinned rather than assumed.
    """

    _assert_refused(_has_eval_leaf(filter_op=filter_op, filter_value=True))


@pytest.mark.unit
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_has_eval_admits_only_a_boolean_filter_type():
    _assert_refused(_has_eval_leaf(filter_type="text", filter_value="true"))


@pytest.mark.unit
@pytest.mark.parametrize(("filter_value", "membership_op"), ACCEPTED_VALUES)
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_an_accepted_has_eval_leaf_does_not_degrade_the_lane(
    filter_value: bool | str,
    membership_op: str,
):
    """The refusals above are the gate firing, not the gate being stuck shut."""

    del membership_op
    builder = _builder(_has_eval_leaf(filter_value=filter_value))
    assert builder.bounded_filter_degraded_error_code() is None
    assert builder.supports_bounded_filter_scan() is True
