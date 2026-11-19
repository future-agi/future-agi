"""Full-state span classification resolves trace names within its batch's hours.

Continuous eval-task classification has no read window. Its trace-name root
lookup is bounded by the batch's own start times (round 3 of PR #4097), so it
prunes partitions and the primary key instead of reading the project's whole
history. The bound must still find a root that started up to a day before
its child, in an earlier date partition.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests._users_live_ch import lane_client

pytestmark = pytest.mark.integration

PROJECT = str(uuid.uuid4())
CHILD_AT = datetime(2026, 8, 25, 2, 30, tzinfo=UTC)
CHECKOUT_CHILD = uuid.uuid4().hex
BROWSE_CHILD = uuid.uuid4().hex


def _leaf():
    return {
        "column_id": "trace_name",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "checkout",
        },
    }


@pytest.fixture(scope="module")
def seeded():
    with lane_client() as ch:
        columns = (
            "project_id, observation_type, service_name, start_time, trace_id, "
            "id, parent_span_id, name"
        )
        rows = []
        for child, root_name, root_offset in (
            # The root started 20 hours before its child, the previous day.
            (CHECKOUT_CHILD, "checkout", timedelta(hours=20)),
            (BROWSE_CHILD, "browse", timedelta(minutes=5)),
        ):
            trace = str(uuid.uuid4())
            root = uuid.uuid4().hex
            rows.append(
                (
                    uuid.UUID(PROJECT),
                    "span",
                    "svc",
                    CHILD_AT - root_offset,
                    trace,
                    root,
                    "",
                    root_name,
                )
            )
            rows.append(
                (
                    uuid.UUID(PROJECT),
                    "span",
                    "svc",
                    CHILD_AT,
                    trace,
                    child,
                    root,
                    f"{root_name} child",
                )
            )
        ch.execute(f"INSERT INTO spans ({columns}) VALUES", rows)
        yield ch


def test_full_state_classifier_finds_a_root_from_the_day_before(seeded):
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=[], bounded_identity_only=True
    )
    builder.filters = [_leaf()]
    query, params = builder.build_filter_match_query(
        [CHECKOUT_CHILD, BROWSE_CHILD], candidate_full_state=True
    )
    assert (
        "(SELECT (min(start_time), max(start_time)) FROM "
        "resolved_annotation_candidates)"
    ) in query

    rows = seeded.execute(query, params)

    matched = {
        str(value)
        for row in rows
        for value in row
        if str(value) in {CHECKOUT_CHILD, BROWSE_CHILD}
    }
    assert matched == {CHECKOUT_CHILD}
