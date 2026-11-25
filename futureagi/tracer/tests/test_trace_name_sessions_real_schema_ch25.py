"""Sessions filtered by trace name match through their traces' live roots.

Before 039 every span stored its trace's name, so the bounded Sessions list
served a trace_name leaf as an ordinary span plan. Now a child has no stored
name and the leaf names the root: the Sessions lane must test live roots, not
refuse the leaf (503 unsupported_relational_session_filter) and not let a
child's empty name satisfy a negated comparison.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests._users_live_ch import lane_client

pytestmark = pytest.mark.integration

START = datetime(2026, 8, 24, tzinfo=UTC)
END = START + timedelta(days=1)
PROJECT = str(uuid.uuid4())
CHECKOUT_SESSION = str(uuid.uuid4())
BROWSE_SESSION = str(uuid.uuid4())


def _leaf(operation="equals", value="checkout", kind="SYSTEM_METRIC"):
    return {
        "column_id": "trace_name",
        "filter_config": {
            "col_type": kind,
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _date():
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _builder(*filters):
    return SessionListQueryBuilderV2(
        project_id=PROJECT,
        filters=[_date(), *filters],
        annotation_label_ids=[],
        annotation_label_ids_by_project={PROJECT: []},
        eval_config_ids=[],
        bounded_internal_scan=True,
    )


def test_bounded_sessions_accept_a_trace_name_leaf():
    for kind in ("SYSTEM_METRIC", "NORMAL", ""):
        builder = _builder(_leaf(kind=kind))
        assert builder.bounded_filter_degraded_error_code() is None, kind
        plans, residual = builder._bounded_span_filter_parts()  # noqa: SLF001
        assert not residual
        (plan,) = plans
        assert "parent_span_id" in plan.predicate or "root" in plan.predicate
        assert "parent_span_id" in plan.seed_predicate


@pytest.fixture(scope="module")
def seeded():
    with lane_client() as ch:
        columns = (
            "project_id, observation_type, service_name, start_time, trace_id, "
            "id, parent_span_id, name, trace_session_id"
        )
        rows = []
        for hour, session, root_name in (
            (1, CHECKOUT_SESSION, "checkout"),
            (3, BROWSE_SESSION, "browse"),
        ):
            trace = str(uuid.uuid4())
            root = uuid.uuid4().hex
            # Collector order: spans first, no traces row, no dictionary.
            rows.append(
                (
                    uuid.UUID(PROJECT),
                    "span",
                    "svc",
                    START + timedelta(hours=hour),
                    trace,
                    root,
                    "",
                    root_name,
                    uuid.UUID(session),
                )
            )
            rows.append(
                (
                    uuid.UUID(PROJECT),
                    "span",
                    "svc",
                    START + timedelta(hours=hour, minutes=5),
                    trace,
                    uuid.uuid4().hex,
                    root,
                    f"{root_name} child",
                    uuid.UUID(session),
                )
            )
        ch.execute(f"INSERT INTO spans ({columns}) VALUES", rows)
        yield ch


@pytest.mark.parametrize(
    ("leaf", "expected"),
    [
        (_leaf(), {CHECKOUT_SESSION}),
        (_leaf("contains", "check"), {CHECKOUT_SESSION}),
        (_leaf("in", ["checkout", "browse"]), {CHECKOUT_SESSION, BROWSE_SESSION}),
        # Children have no name: they must not make the checkout session match.
        (_leaf("not_equals"), {BROWSE_SESSION}),
        (_leaf("not_contains", "check"), {BROWSE_SESSION}),
        (_leaf("is_null"), set()),
    ],
)
def test_bounded_session_page_filters_by_root_trace_name(seeded, leaf, expected):
    builder = _builder(leaf)
    query, params = builder.build_filter_match_query([CHECKOUT_SESSION, BROWSE_SESSION])
    rows = seeded.execute(query, params)
    sessions = {
        str(value)
        for row in rows
        for value in row
        if str(value) in {CHECKOUT_SESSION, BROWSE_SESSION}
    }
    assert sessions == expected
