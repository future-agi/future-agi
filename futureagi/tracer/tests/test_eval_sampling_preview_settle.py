"""Preview eligibility uses the closed freeze cutoff, not the request clock."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tracer.models.eval_task import EvalTask, RunType
from tracer.models.project import Project
from tracer.services.eval_tasks.sampling import service
from tracer.services.eval_tasks.sampling.policy import InvalidSamplingPolicy
from tracer.services.eval_tasks.sampling.query import SamplingQuery

NOW = datetime(2026, 10, 21, 12, 0, 0, 654321, tzinfo=UTC)
PROJECT = "ba052ed0-921f-4aa9-972d-7bbbc79e5d30"
MICROSECOND = timedelta(microseconds=1)


def _task(*, mode="closed", end=None):
    return EvalTask(
        project=Project(id=PROJECT, sampling_contact_attribute=""),
        row_type="spans",
        run_type=RunType.CONTINUOUS if mode == "live" else RunType.HISTORICAL,
        filters={},
        sampling_policy={
            "version": 1,
            "sampling_unit": "session",
            "mode": mode,
            "window": {
                "start": (NOW - timedelta(hours=3)).isoformat(),
                "end": None
                if mode == "live"
                else (end or NOW - timedelta(hours=1, minutes=30)).isoformat(),
            },
            # A census isolates arrival eligibility from bottom-k/hash choices.
            "default": {"rate": 100},
        },
    )


def _configure_margin(settings, seconds):
    if seconds is None:
        # Exercise the service's real default, independent of runner overrides.
        if hasattr(settings, "EVAL_SAMPLING_CLOSED_SETTLE_SECONDS"):
            del settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS
    else:
        settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = seconds


def _arrival_rows(cutoff, changed_clock):
    def row(identity, unit, arrival):
        value = {
            "id": identity,
            "unit": unit,
            "project_id": PROJECT,
            "start_time": NOW - timedelta(hours=2),
            "created_at": NOW - timedelta(hours=2),
            "updated_at": NOW - timedelta(hours=2),
            # Decoded _version timestamp, as in the SQL's source_arrival.
            "version_time": NOW - timedelta(hours=2),
        }
        value[changed_clock] = arrival
        return value

    return [
        row("old-a-1", "session:old-a", cutoff - MICROSECOND),
        row("old-a-2", "session:old-a", cutoff - MICROSECOND),
        row("old-b", "session:old-b", cutoff - MICROSECOND),
        row("at-cutoff", "session:at-cutoff", cutoff),
        row("after-cutoff", "session:after-cutoff", cutoff + MICROSECOND),
        row("future", "session:future", NOW + MICROSECOND),
    ]


def _eligible_rows(sql, params, rows):
    """Small DB-boundary fixture for this census, not a query-builder mock.

    Both preview and membership statements must emit the real watermark.
    Evaluate that watermark from their bound parameters against fixture rows;
    never select a canned count based on the expected constructor arguments.
    """
    assert "project_id = %(sp_project)s" in sql
    assert "s.start_time >= %(sp_start)s AND s.start_time < %(sp_end)s" in sql
    assert "created_at < %(sp_snapshot)s AND source_arrival < %(sp_snapshot)s" in sql
    assert (
        "greatest(created_at, updated_at, "
        "fromUnixTimestamp64Nano(toInt64(_version), 'UTC'))"
    ) in sql
    assert params.policy["default"]["rate"] == 100
    assert not params.policy["strata"]
    return [
        row
        for row in rows
        if row["project_id"] == params["sp_project"]
        and params["sp_start"] <= row["start_time"] < params["sp_end"]
        and row["created_at"] < params["sp_snapshot"]
        and max(row["created_at"], row["updated_at"], row["version_time"])
        < params["sp_snapshot"]
    ]


def _preview_stream(rows, statements):
    def stream(sql, params):
        statements.append((sql, params))
        eligible = _eligible_rows(sql, params, rows)
        if not eligible:
            yield []
            return
        units = len({row["unit"] for row in eligible})
        count = len(eligible)
        yield [('["default",[]]', units, count, units, count, units, count)]

    return stream


@pytest.mark.parametrize("seconds", [None, 60], ids=["default", "custom"])
@pytest.mark.parametrize("changed_clock", ["created_at", "updated_at", "version_time"])
def test_closed_preview_counts_match_freeze_eligibility(
    settings, seconds, changed_clock
):
    _configure_margin(settings, seconds)
    margin = timedelta(seconds=15 if seconds is None else seconds)
    assert service.closed_settle_margin() == margin
    cutoff = NOW - margin
    rows = _arrival_rows(cutoff, changed_clock)
    task = _task()
    policy, version = service.task_policy(task, now=NOW)
    # _acquire freezes at request time minus margin; _freeze_closed compiles
    # members with that frozen_at. Exercise the same real membership SQL here.
    execution_sql, execution_params = SamplingQuery(
        task, policy, version, cutoff
    ).members()
    execution_rows = _eligible_rows(execution_sql, execution_params, rows)
    assert {row["id"] for row in execution_rows} == {"old-a-1", "old-a-2", "old-b"}
    statements = []
    with patch.object(service, "stream_query", _preview_stream(rows, statements)):
        result = service.preview_policy(task, now=NOW, evaluator_count=3)

    (cell,) = result["strata"]
    assert cell["eligible_rows"] == len(execution_rows) == 3
    assert cell["eligible_units"] == len({row["unit"] for row in execution_rows}) == 2
    assert cell["expected_rows"] == cell["selected_rows"] == 3
    assert cell["expected_units"] == cell["selected_units"] == 2
    assert result["expected_evaluations"] == result["selected_evaluations"] == 9
    assert len(statements) == 1
    assert statements[0][1]["sp_snapshot"] == execution_params["sp_snapshot"]
    assert result["as_of"] == cutoff
    assert result["policy_version"] == version
    assert result["estimated_cost"] is None
    assert "not a reservation" in result["population_note"]


@pytest.mark.parametrize(
    "seconds", [None, 60, 3600], ids=["default", "custom", "maximum"]
)
def test_closed_preview_as_of_reports_effective_snapshot(settings, seconds):
    _configure_margin(settings, seconds)
    statements = []
    with patch.object(service, "stream_query", _preview_stream([], statements)):
        result = service.preview_policy(_task(), now=NOW)

    expected = NOW - timedelta(seconds=15 if seconds is None else seconds)
    assert result["as_of"] == expected
    assert statements[0][1]["sp_snapshot"] == result["as_of"]
    assert result["preview_window"] == result["window"]
    assert result["strata"] == []
    assert result["selected_evaluations"] is None


def test_closed_preview_reads_request_clock_once_for_effective_snapshot(settings):
    _configure_margin(settings, 60)
    rows = _arrival_rows(NOW - timedelta(seconds=60), "version_time")
    statements = []
    with (
        patch.object(service, "sampling_clock", return_value=NOW) as clock,
        patch.object(service, "stream_query", _preview_stream(rows, statements)),
    ):
        result = service.preview_policy(_task(), evaluator_count=2)

    clock.assert_called_once_with()
    assert result["selected_evaluations"] == 6
    assert result["as_of"] == NOW - timedelta(seconds=60)
    assert statements[0][1]["sp_snapshot"] == result["as_of"]


@pytest.mark.parametrize(
    "mode,seconds", [("closed", 0), ("live", 60)], ids=["closed-zero", "live"]
)
def test_preview_without_closed_margin_keeps_current_snapshot(settings, mode, seconds):
    _configure_margin(settings, seconds)
    rows = _arrival_rows(NOW - timedelta(seconds=60), "updated_at")
    statements = []
    with patch.object(service, "stream_query", _preview_stream(rows, statements)):
        result = service.preview_policy(_task(mode=mode), now=NOW, evaluator_count=3)

    (cell,) = result["strata"]
    assert cell["eligible_rows"] == cell["expected_rows"] == cell["selected_rows"] == 5
    assert (
        cell["eligible_units"] == cell["expected_units"] == cell["selected_units"] == 4
    )
    assert result["expected_evaluations"] == result["selected_evaluations"] == 15
    assert result["as_of"] == statements[0][1]["sp_snapshot"] == NOW
    if mode == "live":
        assert result["window"]["end"] is None
        assert result["preview_window"]["end"] == NOW.isoformat()
        assert statements[0][1]["sp_end"] == NOW
    else:
        assert result["preview_window"] == result["window"]


def test_closed_preview_validates_recent_window_at_request_clock_without_waiting(
    settings,
):
    _configure_margin(settings, 60)
    # Valid at request time, but would be incorrectly rejected at now - margin.
    end = NOW - MICROSECOND
    task = _task(end=end)
    original = deepcopy(task.sampling_policy)
    policy, version = service.task_policy(task, now=NOW)
    statements = []
    with (
        patch.object(
            service, "sampling_clock", side_effect=AssertionError("explicit clock")
        ),
        patch.object(
            service, "_await_closed_settle", side_effect=AssertionError("no wait")
        ),
        patch.object(service, "stream_query", _preview_stream([], statements)),
    ):
        result = service.preview_policy(task, now=NOW)

    params = statements[0][1]
    assert params.policy == policy
    assert params["sp_version"] == result["policy_version"] == version
    assert params["sp_seed"] == version
    assert params["sp_end"] == end
    assert result["window"] == result["preview_window"] == policy["window"]
    assert result["as_of"] == params["sp_snapshot"]
    assert task.sampling_policy == original


def test_closed_preview_rejects_future_window_before_reading(settings):
    _configure_margin(settings, 60)
    with patch.object(service, "stream_query") as stream:
        with pytest.raises(InvalidSamplingPolicy, match="ending in the past"):
            service.preview_policy(_task(end=NOW + MICROSECOND), now=NOW)
    stream.assert_not_called()
