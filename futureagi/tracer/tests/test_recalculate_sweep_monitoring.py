"""A partial recalculation sweep must reach monitoring after all work is tried.

Use the real Sentry reporting helper with its SDK transport doubled. No database,
Temporal server, evaluation provider, or monitoring network calls are needed.
"""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
import sentry_sdk
import structlog
from django.utils import timezone

from tfc.logging import sentry
from tracer.services.eval_tasks import recalculate
from tracer.tasks import eval_task_sweeper as sweeper

_RECOVERY_COUNTS = {
    "kept": 1,
    "restored": 2,
    "left_retired": 3,
    "waiting": 4,
    "busy": 5,
    "errors": 0,
}
_RECHECK_COUNTS = {
    "rechecked": 6,
    "retired": 2,
    "released": 3,
    "busy": 4,
    "errors": 0,
}


@pytest.fixture
def phases(monkeypatch):
    recovery = Mock(return_value=dict(_RECOVERY_COUNTS))
    recheck = Mock(return_value=dict(_RECHECK_COUNTS))
    monkeypatch.setattr(sweeper, "recover_stale_recalculations", recovery)
    monkeypatch.setattr(sweeper, "recheck_put_backs", recheck)
    return SimpleNamespace(recovery=recovery, recheck=recheck)


@pytest.fixture
def monitoring(monkeypatch):
    """Exercise the existing reporting path without sending any Sentry events."""
    scope = Mock()
    capture = Mock(return_value="event-id")
    monkeypatch.setattr(sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(sentry_sdk, "push_scope", lambda: nullcontext(scope))
    monkeypatch.setattr(sentry_sdk, "capture_exception", capture)
    return SimpleNamespace(scope=scope, capture=capture)


@pytest.mark.parametrize(
    ("recovery_fails", "recheck_fails"), [(True, False), (False, True), (True, True)]
)
def test_phase_failures_reach_monitoring_after_both_phases(
    phases, monitoring, recovery_fails, recheck_fails
):
    recovery_error = RuntimeError("recovery query failed")
    recheck_error = RuntimeError("recheck query failed")
    expected_failures = []
    expected_tags = []
    if recovery_fails:
        phases.recovery.side_effect = recovery_error
        expected_failures.append(call(recovery_error))
        expected_tags.append(
            call("recalculate_sweep.phase", "recover_stale_recalculations")
        )
    if recheck_fails:
        phases.recheck.side_effect = recheck_error
        expected_failures.append(call(recheck_error))
        expected_tags.append(call("recalculate_sweep.phase", "recheck_put_backs"))

    completion_snapshots = []

    def record_completed_phases(_exception):
        completion_snapshots.append(
            (
                tuple(phases.recovery.call_args_list),
                tuple(phases.recheck.call_args_list),
            )
        )

    monitoring.capture.side_effect = record_completed_phases
    with structlog.testing.capture_logs() as records:
        result = sweeper.sweep_stale_recalculations._original_func()

    expected = {"recovery_failed": True} if recovery_fails else dict(_RECOVERY_COUNTS)
    expected["put_back_recheck"] = None if recheck_fails else _RECHECK_COUNTS
    assert result == expected
    assert monitoring.capture.call_args_list == expected_failures
    assert completion_snapshots == [((call(),), (call(),))] * len(expected_failures)
    assert monitoring.scope.set_tag.call_args_list == expected_tags
    assert monitoring.scope.set_context.call_args_list == [
        call("recalculate_sweep", expected)
    ] * len(expected_failures)
    [completed] = [r for r in records if r["event"] == "recalculate_sweep_completed"]
    assert {key: completed[key] for key in expected} == expected
    for failure in (c.args[0] for c in expected_failures):
        assert failure.__traceback__ is not None


@pytest.mark.parametrize(
    ("recovery_errors", "recheck_errors"), [(1, 0), (0, 1), (2, 3)]
)
def test_per_entry_error_counts_are_not_success_only_results(
    phases, monitoring, recovery_errors, recheck_errors
):
    phases.recovery.return_value["errors"] = recovery_errors
    phases.recheck.return_value["errors"] = recheck_errors

    completion_snapshots = []

    def record_completed_phases(_exception):
        completion_snapshots.append(
            (
                tuple(phases.recovery.call_args_list),
                tuple(phases.recheck.call_args_list),
            )
        )

    monitoring.capture.side_effect = record_completed_phases
    result = sweeper.sweep_stale_recalculations._original_func()

    assert result == {
        **_RECOVERY_COUNTS,
        "errors": recovery_errors,
        "put_back_recheck": {**_RECHECK_COUNTS, "errors": recheck_errors},
    }
    expected = []
    if recovery_errors:
        expected.append(
            f"recover_stale_recalculations: {recovery_errors} entries failed"
        )
    if recheck_errors:
        expected.append(f"recheck_put_backs: {recheck_errors} entries failed")
    assert [str(c.args[0]) for c in monitoring.capture.call_args_list] == expected
    assert completion_snapshots == [((call(),), (call(),))] * len(expected)
    assert monitoring.scope.set_context.call_args_list == [
        call("recalculate_sweep", result)
    ] * len(expected)


def test_successful_work_and_expected_busy_or_waiting_results_do_not_alert(
    phases, monitoring
):
    result = sweeper.sweep_stale_recalculations._original_func()

    phases.recovery.assert_called_once_with()
    phases.recheck.assert_called_once_with()
    assert result == {**_RECOVERY_COUNTS, "put_back_recheck": _RECHECK_COUNTS}
    monitoring.capture.assert_not_called()


def test_a_phase_failure_does_not_hide_the_other_phases_entry_failures(
    phases, monitoring
):
    failure = RuntimeError("recovery query failed")
    phases.recovery.side_effect = failure
    phases.recheck.return_value["errors"] = 2

    result = sweeper.sweep_stale_recalculations._original_func()

    assert result == {
        "recovery_failed": True,
        "put_back_recheck": {**_RECHECK_COUNTS, "errors": 2},
    }
    assert monitoring.capture.call_count == 2
    assert monitoring.capture.call_args_list[0] == call(failure)
    assert str(monitoring.capture.call_args_list[1].args[0]) == (
        "recheck_put_backs: 2 entries failed"
    )


def test_real_service_loops_finish_every_entry_before_reporting(
    monkeypatch, monitoring
):
    """Keep both real loops, mocking only their database/row-work boundaries."""
    journals = Mock()
    journals.annotate.return_value.filter.return_value.order_by.return_value.values_list.return_value = [
        11,
        12,
        13,
    ]
    monkeypatch.setattr(recalculate, "_journal", lambda: journals)
    recover_one = Mock(
        side_effect=[RuntimeError("journal 11"), RuntimeError("journal 12"), "restored"]
    )
    monkeypatch.setattr(recalculate, "_recover_one", recover_one)

    watches = Mock()
    watches.order_by.return_value.values_list.return_value = [21, 22, 23]
    rows = [Mock(result_id=i, restored_at=timezone.now()) for i in (21, 22, 23)]
    watches.select_for_update.return_value.filter.return_value.first.side_effect = rows
    monkeypatch.setattr(recalculate, "_put_backs", lambda: watches)
    monkeypatch.setattr(
        recalculate,
        "transaction",
        SimpleNamespace(atomic=lambda **_kwargs: nullcontext()),
    )
    is_live = Mock(
        side_effect=[RuntimeError("watch 21"), RuntimeError("watch 22"), True]
    )
    retire = Mock(return_value=True)
    monkeypatch.setattr(recalculate, "_previous_is_live", is_live)
    monkeypatch.setattr(recalculate, "_retire_if_parents_deleted", retire)

    completion_snapshots = []

    def record_completed_entries(_exception):
        completion_snapshots.append(
            (
                tuple(c.args[0] for c in recover_one.call_args_list),
                tuple(is_live.call_args_list),
                tuple(retire.call_args_list),
                tuple(rows[2].delete.call_args_list),
            )
        )

    monitoring.capture.side_effect = record_completed_entries
    with structlog.testing.capture_logs() as records:
        result = sweeper.sweep_stale_recalculations._original_func()

    assert result == {
        "kept": 0,
        "restored": 1,
        "left_retired": 0,
        "waiting": 0,
        "busy": 0,
        "errors": 2,
        "put_back_recheck": {
            "rechecked": 1,
            "retired": 1,
            "released": 1,
            "busy": 0,
            "errors": 2,
        },
    }
    assert [str(c.args[0]) for c in monitoring.capture.call_args_list] == [
        "recover_stale_recalculations: 2 entries failed",
        "recheck_put_backs: 2 entries failed",
    ]
    assert (
        completion_snapshots
        == [((11, 12, 13), (call(21), call(22), call(23)), (call(23),), (call(),))] * 2
    )
    failures = [r for r in records if r["event"] == "recalculate_recovery_failed"]
    assert [r["journal_id"] for r in failures] == [11, 12]
    recheck_failures = [
        r for r in records if r["event"] == "recalculate_put_back_recheck_failed"
    ]
    assert [r["watch_id"] for r in recheck_failures] == [21, 22]
    assert all(r["exc_info"] for r in failures + recheck_failures)
