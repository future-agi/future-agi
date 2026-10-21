"""The Session page hydrates from ONE statement over the page's roots.

The page used to issue three hydration statements — aggregates, first/last
message, root span attributes — that each replayed the same page-scoped
latest-state roots. They are fused here: same rows, same order, same columns,
one read.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT = str(UUID(int=1))
SESSION = str(UUID(int=100))
START = datetime(2026, 8, 1, 12, 30, tzinfo=UTC)
BUILDERS = [SessionListQueryBuilder, SessionListQueryBuilderV2]


def _builder(cls, days=7):
    return cls(
        project_id=PROJECT,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        START.isoformat(),
                        (START + timedelta(days=days)).isoformat(),
                    ],
                },
            }
        ],
        page_number=0,
        page_size=25,
    )


@pytest.mark.parametrize("cls", BUILDERS)
def test_page_hydration_carries_the_union_of_the_three_payloads(cls):
    sql, params = _builder(cls).build_page_hydration_query([SESSION])

    # Aggregates.
    for projection in (
        "min(start_time) AS session_start",
        "max(end_time) AS session_end",
        "sum(cost) AS total_cost",
        "sum(total_tokens) AS total_tokens",
        "uniqExact(trace_id) AS traces_count",
    ):
        assert projection in sql
    # Messages.
    assert "argMin(input, start_time) AS first_message" in sql
    assert "argMax(input, start_time) AS last_message" in sql
    # Root attributes, one array per payload the merge reads back.
    for array_column, _key in cls.PAGE_ATTRIBUTE_ARRAY_COLUMNS:
        assert f"has_span_attributes) AS {array_column}" in sql
    assert params["candidate_session_ids"] == (SESSION,)


@pytest.mark.parametrize("cls", BUILDERS)
def test_page_hydration_reads_the_page_roots_once(cls):
    sql, _params = _builder(cls).build_page_hydration_query([SESSION])

    # One candidate acquisition and one latest-state replay for the whole
    # page, where three statements each ran their own pair.
    assert sql.count("candidate_root_identities AS (") == 1
    assert sql.count("latest_roots AS (") == 1
    assert sql.count("GROUP BY session_id") == 1
    # The hydrations this replaces are gone, not merely unused.
    for removed in (
        "build_page_metrics_query",
        "build_content_query",
        "build_span_attributes_query",
    ):
        assert not hasattr(cls, removed)


@pytest.mark.parametrize("cls", BUILDERS)
def test_empty_page_still_issues_no_statement(cls):
    assert _builder(cls).build_page_hydration_query([]) == ("", {})


@pytest.mark.parametrize("cls", BUILDERS)
def test_attribute_arrays_expand_to_the_pre_fusion_row_shape(cls):
    keys = [key for _array, key in cls.PAGE_ATTRIBUTE_ARRAY_COLUMNS]
    row = {"session_id": SESSION}
    for array_column, key in cls.PAGE_ATTRIBUTE_ARRAY_COLUMNS:
        row[array_column] = [f"{key}-first", f"{key}-second"]

    expanded = cls.expand_page_attribute_rows([row])

    assert expanded == [
        {"session_id": SESSION, **{key: f"{key}-first" for key in keys}},
        {"session_id": SESSION, **{key: f"{key}-second" for key in keys}},
    ]


@pytest.mark.parametrize("cls", BUILDERS)
def test_a_session_without_root_attributes_expands_to_no_rows(cls):
    row = {
        "session_id": SESSION,
        **{array: [] for array, _key in cls.PAGE_ATTRIBUTE_ARRAY_COLUMNS},
    }
    assert cls.expand_page_attribute_rows([row]) == []


@pytest.mark.parametrize("cls", BUILDERS)
def test_published_session_payload_is_unchanged_by_the_fusion(cls):
    """The fused row formats to the same session projection as before.

    Pre-fusion the view merged ``first_message``/``last_message`` from the
    content statement onto the metrics row; the fused row already carries
    them, and the attribute arrays are not part of the published projection.
    """
    fused = {
        "session_id": SESSION,
        "session_start": START,
        "session_end": START + timedelta(minutes=30),
        "duration": 1800,
        "total_cost": 12.0,
        "total_tokens": 40,
        "traces_count": 3,
        "first_message": "first",
        "last_message": "last",
        **{array: [{}] for array, _key in cls.PAGE_ATTRIBUTE_ARRAY_COLUMNS},
    }
    pre_fusion = {
        "session_id": SESSION,
        "session_start": fused["session_start"],
        "session_end": fused["session_end"],
        "duration": 1800,
        "total_cost": 12.0,
        "total_tokens": 40,
        "traces_count": 3,
        "first_message": "first",
        "last_message": "last",
    }

    assert cls.format_sessions(
        [list(fused.values())], list(fused.keys())
    ) == cls.format_sessions([list(pre_fusion.values())], list(pre_fusion.keys()))
