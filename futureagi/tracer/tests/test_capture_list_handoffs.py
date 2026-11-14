"""Bounded source witnesses and response companions, not capture certification."""

import pytest

from tracer.serializers.trace import TraceObserveListResultSerializer
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TRACE_ROLLUP_COLUMNS,
    TraceListQueryBuilderV2,
    apply_trace_rollup,
)

pytestmark = pytest.mark.unit
PROJECT = "00000000-0000-0000-0000-000000000001"
SESSION = "00000000-0000-0000-0000-000000000002"


def capture(count):
    return {
        "scope": "observed_requests",
        "source_summary_present": True,
        "request_count": count,
        "coverage_unknown": True,
    }


@pytest.mark.parametrize(
    "count,tokens,cost", [(0, 0, 0.0), (3, 43071, None), (3, 43071, 1.25)]
)
def test_trace_list_hydration_keeps_capture_separate_from_cost(count, tokens, cost):
    row = {"trace_id": "same", "project_id": PROJECT, "cost": 17, "total_tokens": 43071}
    apply_trace_rollup(
        row,
        {
            "trace_rollup_present": 1,
            "trace_rollup_source_summary_present": 1,
            "trace_rollup_request_count": count,
            "trace_rollup_cost": cost,
            "trace_rollup_cost_unknown": cost is None,
            "trace_rollup_total_tokens": tokens,
        },
    )
    assert row["capture_coverage"] == capture(count)
    assert row["cost"] == cost
    assert row["total_tokens"] == tokens
    assert row["trace_id"] == "same"


def test_trace_legacy_and_absent_rollup_do_not_invent_capture():
    row = {"cost": 0, "total_tokens": 0}
    apply_trace_rollup(
        row, {"trace_rollup_present": 1, "trace_rollup_request_count": 1}
    )
    assert row == {"cost": 0, "total_tokens": 0}
    apply_trace_rollup(row, {"trace_rollup_source_summary_present": 1})
    assert row == {"cost": 0, "total_tokens": 0}


@pytest.mark.parametrize("as_tuple", [False, True])
def test_session_formatter_emits_only_explicit_source_witness(as_tuple):
    for witness, count in [(True, 0), (True, 3), (False, 3)]:
        row = {
            "session_id": SESSION,
            "total_cost": None,
            "total_tokens": 43071,
            "source_summary_present": witness,
            "observed_request_count": count,
        }
        actual = SessionListQueryBuilder.format_sessions(
            [tuple(row.values()) if as_tuple else row],
            list(row),
        )[0]
        assert actual["total_cost"] is None
        assert actual["total_tokens"] == 43071
        if witness:
            assert actual["capture_coverage"] == capture(count)
        else:
            assert "capture_coverage" not in actual
    legacy = {"session_id": SESSION, "total_cost": 0, "total_tokens": 0}
    assert (
        "capture_coverage"
        not in SessionListQueryBuilder.format_sessions(
            [tuple(legacy.values()) if as_tuple else legacy],
            list(legacy),
        )[0]
    )


def test_typed_trace_sidecar_retains_dynamic_cells_and_project_coordinates():
    payload = {
        "metadata": {"total_rows": 2},
        "config": [],
        "table": [
            {
                "project_id": PROJECT,
                "trace_id": "same",
                "cost": 0,
                "eval-id": {"pass": 3},
            },
            {
                "project_id": "other",
                "trace_id": "same",
                "cost": None,
                "custom": ["value"],
            },
        ],
    }
    old = TraceObserveListResultSerializer(data=payload)
    assert old.is_valid(), old.errors
    metadata = {
        "total_rows": 2,
        "total_rows_exact": None,
        "next_cursor": None,
        "next_cursor_fingerprint": None,
        "query_error_code": None,
    }
    assert old.data == {**payload, "metadata": metadata}
    typed = {PROJECT: {"same": capture(0)}, "other": {"same": capture(3)}}
    new = TraceObserveListResultSerializer(
        data={**payload, "trace_capture_coverage": typed}
    )
    assert new.is_valid(), new.errors
    assert new.data["table"] == payload["table"]
    assert new.data["metadata"] == metadata
    assert new.data["trace_capture_coverage"] == typed
    invalid = TraceObserveListResultSerializer(
        data={
            **payload,
            "trace_capture_coverage": {
                PROJECT: {"same": {**capture(0), "request_count": -1}}
            },
        }
    )
    assert not invalid.is_valid()


def test_trace_witness_is_inside_same_latest_span_tuple_and_scoped_join():
    builder = TraceListQueryBuilderV2(project_id=PROJECT, filters=[])
    sql = builder._trace_rollup_join_sql("content")
    assert "latest_span.14 AS latest_source_summary_present" in sql
    assert "attrs_string['fi.usage.scope'] = 'turn')), _version)" in sql
    assert "usage_source_summary_present, usage_request_count" in sql
    assert (
        "PREWHERE (toString(project_id), trace_id) IN %(content_rollup_trace_identities)s"
        in sql
    )
    assert (
        "GROUP BY project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        in sql
    )
    assert "trace_rollup_source_summary_present" in TRACE_ROLLUP_COLUMNS
    assert "trace_rollup_request_count" in TRACE_ROLLUP_COLUMNS
    assert (
        "fi.source.summary.gen_ai" not in sql
    )  # no raw source amounts in list metadata


@pytest.mark.parametrize(
    "builder_class", [SessionListQueryBuilder, SessionListQueryBuilderV2]
)
def test_session_source_is_scalar_latest_member_and_same_physical_window(builder_class):
    builder = builder_class(
        project_id=PROJECT,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2026-10-20T12:00:00Z", "2026-10-22T12:00:00Z"],
                },
            }
        ],
    )
    sql, params = builder.build_page_hydration_query([SESSION])
    assert "latest_source_summary_present" in sql
    assert "max(usage_source_summary_present) AS member_source_summary_present" in sql
    assert "sum(usage_request_count) AS member_request_count" in sql
    assert "source_summary_present" in sql
    assert "fi.source.summary.gen_ai" not in sql
    assert (
        "created_day" in sql
        if builder_class is SessionListQueryBuilder
        else "start_hour" in sql
    )
    assert PROJECT in str(params)


def test_session_legacy_positional_rows_do_not_treat_an_id_as_capture():
    row = (SESSION, None, None, 0, 0.0, 0, 1, "input", "output")
    assert (
        "capture_coverage" not in SessionListQueryBuilder.format_sessions([row], [])[0]
    )


def test_new_unmarked_winner_clears_an_old_trace_companion():
    row = {"cost": 0, "capture_coverage": capture(0)}
    apply_trace_rollup(
        row, {"trace_rollup_present": 1, "trace_rollup_source_summary_present": 0}
    )
    assert row == {"cost": 0}
