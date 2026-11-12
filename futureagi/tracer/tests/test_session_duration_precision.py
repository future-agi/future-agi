"""Session duration SQL contracts; no live ClickHouse execution.

The projections must measure elapsed microseconds in seconds, not count
second boundaries: 00.100000 -> 00.800000 is 0.7s, while 00.900000 ->
01.100000 is only 0.2s. Pin the emitted expression independently of the
production constant, including the live V2 rewrite and all four emitters.
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
START = datetime(2026, 9, 5, 10, tzinfo=UTC)
BUILDERS = (
    ("legacy", SessionListQueryBuilder),
    ("v2", SessionListQueryBuilderV2),
)
DURATION_PROJECTION = (
    "dateDiff('microsecond', min(start_time), max(end_time)) / 1000000.0 AS duration"
)
QUERY_METHODS = (
    "build",
    "build_id_query",
    "build_count_query",
    "build_candidate_page_query",
    "build_candidate_count_query",
    "build_page_hydration_query",
)


def _builder(cls, *, duration_threshold=None, direction=None):
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    START.isoformat(),
                    (START + timedelta(hours=1)).isoformat(),
                ],
            },
        }
    ]
    if duration_threshold is not None:
        filters.append(
            {
                "column_id": "duration",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": duration_threshold,
                },
            }
        )
    return cls(
        project_id=PROJECT,
        filters=filters,
        sort_params=(
            [{"column_id": "duration", "direction": direction}] if direction else []
        ),
        page_size=25,
    )


def _assert_elapsed_seconds_projection(sql):
    sql = " ".join(sql.split())
    assert sql.count(DURATION_PROJECTION) == 1
    assert "dateDiff('second', min(start_time), max(end_time))" not in sql
    return sql


@pytest.mark.parametrize(
    ("cls", "method"),
    [
        pytest.param(cls, method, id=f"{name}-{method}")
        for name, cls in BUILDERS
        for method in QUERY_METHODS
    ],
)
def test_duration_projection_and_filter_keep_fractional_seconds(cls, method):
    builder = _builder(cls, duration_threshold=0)
    if method == "build_page_hydration_query":
        sql, params = builder.build_page_hydration_query([SESSION])
    else:
        sql, params = getattr(builder, method)()

    sql = _assert_elapsed_seconds_projection(sql)
    if method == "build_page_hydration_query":
        assert "root_page.duration AS duration" in sql
        assert params["candidate_session_ids"] == (SESSION,)
    else:
        # A nonzero session within one second must not be dropped. Counts and
        # bulk-selection IDs must use the same fractional aggregate as the page.
        assert "HAVING duration > %(having_901)s" in sql
        assert params["having_901"] == 0


@pytest.mark.parametrize(
    ("cls", "direction"),
    [
        pytest.param(cls, direction, id=f"{name}-{direction}")
        for name, cls in BUILDERS
        for direction in ("asc", "desc")
    ],
)
def test_duration_sort_without_filter_uses_elapsed_seconds(cls, direction):
    builder = _builder(cls, direction=direction)
    assert builder.supports_candidate_first_page()
    sql, params = builder.build_candidate_page_query()

    sql = _assert_elapsed_seconds_projection(sql)
    order = direction.upper()
    assert f"ORDER BY duration {order}, session_id {order}" in sql
    assert "HAVING duration" not in sql
    assert "having_901" not in params


@pytest.mark.parametrize("cls", [pytest.param(cls, id=name) for name, cls in BUILDERS])
def test_unfiltered_hydration_keeps_fractional_duration_payload(cls):
    sql, _params = _builder(cls).build_page_hydration_query([SESSION])
    sql = _assert_elapsed_seconds_projection(sql)
    assert "root_page.duration AS duration" in sql

    # Formatter controls only: these model the returned numeric column, not
    # execution of the SQL above. Include the reproduction and a one-us span.
    cases = (
        (100_000, 800_000, 0.7),
        (900_000, 1_100_000, 0.2),
        (123_456, 123_457, 0.000001),
        (0, 1_000_000, 1.0),
        (0, 0, 0.0),
    )
    columns = ["session_id", "session_start", "session_end", "duration"]
    rows = [
        (
            str(UUID(int=100 + index)),
            START + timedelta(microseconds=start_us),
            START + timedelta(microseconds=end_us),
            seconds,
        )
        for index, (start_us, end_us, seconds) in enumerate(cases)
    ]
    sessions = cls.format_sessions(rows, columns)

    assert [session["duration"] for session in sessions] == [
        seconds for _start_us, _end_us, seconds in cases
    ]
    for session, row in zip(sessions, rows, strict=True):
        assert session["duration"] == (row[2] - row[1]).total_seconds()
