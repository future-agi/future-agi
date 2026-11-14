"""Exact tag target and deadline regressions, separate from HTTP authorization."""

from datetime import UTC, datetime, timedelta
from traceback import format_exception
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.entity_tag_targets import (
    TagTargetError,
    resolve_tag_target,
)

PROJECT = "11111111-1111-1111-1111-111111111111"
TRACE = "22222222-2222-2222-2222-222222222222"
HOUR = datetime(2020, 1, 1, tzinfo=UTC)
START = HOUR + timedelta(minutes=30, microseconds=1)


def reference(**changes):
    return {
        "project_id": PROJECT,
        "trace_id": TRACE,
        "span_id": "0123456789abcdef",
        "start_hour": HOUR,
        "observation_type": "span",
        "service_name": "svc",
        "expected_start_time": START,
        "expected_version": "2",
        **changes,
    }


def winner(**changes):
    ref = reference()
    return {
        **{
            k: ref[k]
            for k in (
                "project_id",
                "trace_id",
                "start_hour",
                "observation_type",
                "service_name",
            )
        },
        "id": ref["span_id"],
        "start_time": START,
        "_version": "2",
        "is_deleted": 0,
        "parent_span_id": "",
        "tags": '["sdk"]',
        **changes,
    }


def reader(rows=None, **changes):
    return SimpleNamespace(
        supports_per_query_read_settings=True,
        execute_ch_query=Mock(
            return_value=SimpleNamespace(
                data=[winner()] if rows is None else rows, complete=True
            )
        ),
        **changes,
    )


def resolve(analytics, **changes):
    return resolve_tag_target(
        analytics=analytics,
        authorized_project_id=PROJECT,
        reference=reference(),
        trace_id=None,
        deadline=ReadDeadline.start(1000, enforce_on_server=True),
        **changes,
    )


def test_root_span_post_and_trace_patch_share_one_logical_assignment():
    for trace_id in (None, TRACE):
        target = resolve_tag_target(
            analytics=reader(),
            authorized_project_id=PROJECT,
            reference=reference(),
            trace_id=trace_id,
            deadline=ReadDeadline.start(1000),
        )
        assert target.key == (
            PROJECT,
            "trace",
            TRACE,
            "",
            HOUR.replace(year=1970),
            "",
            "",
        )
        assert target.row["tags"] == '["sdk"]'


def test_child_identity_includes_project_trace_and_complete_physical_key():
    target = resolve(reader([winner(parent_span_id="parent")]))
    assert target.key == (
        PROJECT,
        "span",
        TRACE,
        "0123456789abcdef",
        HOUR,
        "span",
        "svc",
    )


def test_old_target_uses_full_hour_and_real_server_cap_not_timeout_hint():
    analytics = reader()
    resolve(analytics)
    sql, params = analytics.execute_ch_query.call_args.args
    kwargs = analytics.execute_ch_query.call_args.kwargs
    assert "project_id = %(physical_project_id)s" in sql
    assert "start_time >= fromUnixTimestamp64Micro(%(physical_hour_us)s, 'UTC')" in sql
    assert (
        "start_time < fromUnixTimestamp64Micro(%(physical_hour_us)s, 'UTC') + INTERVAL 1 HOUR"
        in sql
    )
    assert "expected" not in sql and "parent_span_id =" not in sql
    assert "is_deleted =" not in sql
    assert params["physical_hour_us"] == 1577836800000000
    assert 25 <= kwargs["server_execution_cap_ms"] <= 1000
    assert kwargs["timeout_ms"] is None


@pytest.mark.parametrize(
    "rows,code",
    [
        ([], "tag_target_not_found"),
        ([winner(is_deleted=1)], "tag_target_not_found"),
        ([winner(_version="3")], "tag_target_changed"),
        ([winner(start_time=START - timedelta(minutes=1))], "tag_target_changed"),
        ([winner(), winner()], "tag_target_unavailable"),
        ([winner(project_id="foreign")], "tag_target_unavailable"),
        ([winner(parent_span_id=7)], "tag_target_unavailable"),
        ([winner(parent_span_id=[])], "tag_target_unavailable"),
    ],
)
def test_absence_change_and_incomplete_resolution_are_distinct(rows, code):
    with pytest.raises(TagTargetError, match=code):
        resolve(reader(rows))


def test_root_check_happens_after_latest_resolution():
    with pytest.raises(TagTargetError, match="tag_target_changed"):
        resolve_tag_target(
            analytics=reader([winner(parent_span_id="parent")]),
            authorized_project_id=PROJECT,
            reference=reference(),
            trace_id=TRACE,
            deadline=ReadDeadline.start(1000),
        )


def test_scope_mismatch_prevents_clickhouse_call():
    analytics = reader()
    with pytest.raises(TagTargetError, match="tag_target_not_found"):
        resolve_tag_target(
            analytics=analytics,
            authorized_project_id="foreign",
            reference=reference(),
            trace_id=TRACE,
            deadline=ReadDeadline.start(1000),
        )
    analytics.execute_ch_query.assert_not_called()


def test_trace_url_mismatch_prevents_clickhouse_call():
    analytics = reader()
    with pytest.raises(TagTargetError, match="tag_scope_required"):
        resolve_tag_target(
            analytics=analytics,
            authorized_project_id=PROJECT,
            reference=reference(),
            trace_id="wrong-trace",
            deadline=ReadDeadline.start(1000),
        )
    analytics.execute_ch_query.assert_not_called()


@pytest.mark.parametrize("supported", [False, None])
def test_unqualified_locked_profile_fails_closed_without_a_query(supported):
    analytics = reader()
    analytics.supports_per_query_read_settings = supported
    with pytest.raises(TagTargetError, match="tag_target_unavailable"):
        resolve(analytics)
    analytics.execute_ch_query.assert_not_called()


def test_exhausted_wall_is_rejected_before_query_admission():
    analytics = reader()
    with pytest.raises(TagTargetError, match="tag_target_unavailable"):
        resolve_tag_target(
            analytics=analytics,
            authorized_project_id=PROJECT,
            reference=reference(),
            trace_id=None,
            deadline=ReadDeadline(total_ms=1, started=0),
        )
    analytics.execute_ch_query.assert_not_called()


def test_transport_error_is_sanitized_not_not_found(monkeypatch):
    analytics = reader()
    analytics.execute_ch_query.side_effect = ConnectionError("private diagnostic")
    logger = Mock()
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.entity_tag_targets.logger", logger
    )
    with pytest.raises(TagTargetError, match="tag_target_unavailable") as error:
        resolve(analytics)
    assert "private" not in str(error.value)
    logger.exception.assert_called_once()
    fields = logger.exception.call_args.kwargs
    assert fields["error_type"] == "ConnectionError"
    error_type, logged_error, traceback = fields["exc_info"]
    assert error_type is TagTargetError and traceback is not None
    assert str(logged_error) == "tag_target_unavailable"
    assert "private diagnostic" not in "".join(format_exception(*fields["exc_info"]))


def test_partial_empty_result_is_unavailable_not_absence():
    analytics = reader([])
    analytics.execute_ch_query.return_value.complete = False
    with pytest.raises(TagTargetError, match="tag_target_unavailable"):
        resolve(analytics)


@pytest.mark.parametrize(
    "rows",
    [
        [winner()],
        [],
        [winner(is_deleted=1)],
        [winner(_version="3")],
        [winner(start_time=START - timedelta(minutes=1))],
    ],
)
def test_late_resolution_is_unavailable_even_for_absent_or_changed_target(
    rows, monkeypatch
):
    clock = Mock(return_value=100.0)
    monkeypatch.setattr("tracer.services.clickhouse.read_budget.time.monotonic", clock)
    deadline = ReadDeadline.start(1000)
    analytics = reader()

    def late_query(*args, **kwargs):
        clock.return_value = 102.0
        return SimpleNamespace(data=rows, complete=True)

    analytics.execute_ch_query.side_effect = late_query
    with pytest.raises(TagTargetError, match="tag_target_unavailable") as error:
        resolve_tag_target(
            analytics=analytics,
            authorized_project_id=PROJECT,
            reference=reference(),
            trace_id=None,
            deadline=deadline,
        )
    assert error.value.status_code == 503
    analytics.execute_ch_query.assert_called_once()
