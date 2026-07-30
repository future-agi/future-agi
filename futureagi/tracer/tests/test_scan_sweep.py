"""Unit tests for the periodic scan sweep (``sweep_scannable_traces``).

The sweep is the only scanner trigger for collector-ingested (CH-only) traces,
so these pin its contract: pre-sampled batched dispatch, the watermark parked on
the oldest still-unscanned sampled-in trace (so the cursor never passes work
that lacks a durable marker), the lag bound + FAILED-marker terminal state for a
stuck trace, the cold-start floor, and per-project fail-open. DB-free — every
collaborator (CH reader, config model, anti-join, sampler, abandon-write) is
mocked, so a regression fails here without Postgres/ClickHouse/Temporal.
"""

import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tracer.queries.trace_scanner import is_trace_sampled
from tracer.tasks import trace_scanner as sweep

# Undecorated function: skip the activity wrapper's close_old_connections (DB).
_run = sweep.sweep_scannable_traces._original_func

_NOW = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)
_UPPER = _NOW - timedelta(seconds=sweep._SWEEP_GRACE_SECONDS)
_COLD_FLOOR = _NOW - timedelta(seconds=sweep._SWEEP_COLD_START_SECONDS)
_LAG_FLOOR = _NOW - timedelta(seconds=sweep._SWEEP_MAX_LAG_SECONDS)


def _candidates(n, base=None):
    """n ``(trace_id, created_at)`` pairs, 1s apart from ``base`` (default 8m ago)."""
    base = base or (_NOW - timedelta(minutes=8))
    return [(f"t{i}", base + timedelta(seconds=i)) for i in range(n)]


def _reader(candidates=None, side_effect=None):
    reader = MagicMock()
    reader.ch_now.return_value = _NOW
    if side_effect is not None:
        reader.root_trace_candidates.side_effect = side_effect
    else:
        reader.root_trace_candidates.return_value = candidates or []
    cm = MagicMock()
    cm.__enter__.return_value = reader
    cm.__exit__.return_value = False
    return cm, reader


def _config_model(rows):
    """Mock TraceScanConfig: ``no_workspace_objects.filter(...).order_by(...)
    .values(...)`` returns ``rows``; the same shared ``filter.return_value``
    carries the ``.update(...)`` watermark write."""
    m = MagicMock()
    m.no_workspace_objects.filter.return_value.order_by.return_value.values.return_value = rows
    return m


def _run_sweep(
    candidates=None,
    side_effect=None,
    rows=None,
    unscanned=None,
    sampled=None,
    abandoned=0,
):
    """Run the sweep with every collaborator mocked; return the mock handles."""
    cm, reader = _reader(candidates=candidates, side_effect=side_effect)
    cfg = _config_model(rows or [])
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(sweep, "get_reader", return_value=cm))
        stack.enter_context(patch.object(sweep, "TraceScanConfig", cfg))
        stack.enter_context(
            patch.object(
                sweep,
                "filter_already_scanned",
                side_effect=(lambda x: x) if unscanned is None else (lambda x: unscanned),
            )
        )
        stack.enter_context(
            patch.object(
                sweep,
                "is_trace_sampled",
                side_effect=(lambda tid, rate: True) if sampled is None else sampled,
            )
        )
        abandon = stack.enter_context(
            patch.object(sweep, "mark_traces_failed", return_value=abandoned)
        )
        task = stack.enter_context(patch.object(sweep, "scan_traces_task"))
        _run()
    return SimpleNamespace(reader=reader, cfg=cfg, task=task, abandon=abandon)


def _update(r):
    return r.cfg.no_workspace_objects.filter.return_value.update


def test_dispatches_in_batches_and_parks_watermark_on_oldest_pending():
    cands = _candidates(20)
    r = _run_sweep(
        candidates=cands,
        rows=[{"project_id": "p1", "sampling_rate": 1.0, "last_swept_at": None}],
    )

    # last_swept_at None → cold-start floor is the lower bound.
    r.reader.root_trace_candidates.assert_called_once_with("p1", _COLD_FLOOR, _UPPER)
    # 20 ids → batches of _SWEEP_BATCH_SIZE (15) → 2 dispatches (15 + 5).
    assert r.task.apply_async.call_count == 2
    batches = [c.kwargs["args"][0] for c in r.task.apply_async.call_args_list]
    assert [len(b) for b in batches] == [sweep._SWEEP_BATCH_SIZE, 5]
    assert all(c.kwargs["args"][1] == "p1" for c in r.task.apply_async.call_args_list)
    # Watermark parks on the OLDEST candidate's created_at, NOT `upper` — the
    # cursor must not pass traces whose scan hasn't durably landed yet (that was
    # the data-loss bug: advancing to `upper` on dispatch).
    oldest = min(ca for _, ca in cands)
    _update(r).assert_called_once_with(last_swept_at=oldest)
    r.abandon.assert_not_called()


def test_uses_last_swept_at_as_lower_bound_when_set():
    swept = _NOW - timedelta(minutes=5)
    r = _run_sweep(
        candidates=_candidates(1, base=_NOW - timedelta(minutes=4)),
        rows=[{"project_id": "p1", "sampling_rate": 1.0, "last_swept_at": swept}],
    )
    r.reader.root_trace_candidates.assert_called_once_with("p1", swept, _UPPER)


def test_already_scanned_filtered_before_dispatch():
    r = _run_sweep(
        candidates=_candidates(5),
        rows=[{"project_id": "p1", "sampling_rate": 1.0, "last_swept_at": None}],
        unscanned=["t1", "t3"],  # filter_already_scanned returns the UNSCANNED subset
    )
    assert r.task.apply_async.call_count == 1
    assert r.task.apply_async.call_args.kwargs["args"][0] == ["t1", "t3"]


def test_sampled_out_traces_are_not_dispatched_and_dont_pin_cursor():
    cands = _candidates(4)
    r = _run_sweep(
        candidates=cands,
        rows=[{"project_id": "p1", "sampling_rate": 0.5, "last_swept_at": None}],
        sampled=lambda tid, rate: tid in {"t0", "t2"},
    )
    assert r.task.apply_async.call_count == 1
    assert r.task.apply_async.call_args.kwargs["args"][0] == ["t0", "t2"]
    # Cursor parks on the oldest *sampled-in* trace (t0). A sampled-out trace has
    # no marker; if it pinned the cursor it would re-roll forever.
    _update(r).assert_called_once_with(last_swept_at=dict(cands)["t0"])


def test_stuck_trace_past_lag_bound_is_abandoned_and_cursor_advances():
    t_old = ("t_old", _LAG_FLOOR - timedelta(hours=1))  # unscanned past the bound
    t_new = ("t_new", _NOW - timedelta(minutes=2))  # in-bound, within window
    r = _run_sweep(
        candidates=[t_old, t_new],
        rows=[
            {
                "project_id": "p1",
                "sampling_rate": 1.0,
                "last_swept_at": _LAG_FLOOR - timedelta(hours=2),
            }
        ],
        abandoned=1,
    )
    # Only the in-bound trace is dispatched; the stuck one is FAILED-marked.
    assert r.task.apply_async.call_count == 1
    assert r.task.apply_async.call_args.kwargs["args"][0] == ["t_new"]
    r.abandon.assert_called_once()
    assert r.abandon.call_args.args[0] == ["t_old"]
    # Cursor advances to the bound (not the stuck trace) — so a permanently-stuck
    # trace can't pin the cursor or grow the per-tick scan window without end.
    _update(r).assert_called_once_with(last_swept_at=_LAG_FLOOR)


def test_no_pending_advances_watermark_to_upper():
    # All candidates already scanned → nothing pending → cursor jumps to `upper`
    # so a caught-up project doesn't re-scan its window every tick.
    r = _run_sweep(
        candidates=_candidates(3),
        rows=[{"project_id": "p1", "sampling_rate": 1.0, "last_swept_at": None}],
        unscanned=[],
    )
    r.task.apply_async.assert_not_called()
    _update(r).assert_called_once_with(last_swept_at=_UPPER)
    r.abandon.assert_not_called()


def test_per_project_fail_open_isolates_a_failing_project():
    # p1's CH read raises; p2 succeeds. p1 must not abort the tick, and p1's
    # watermark must NOT advance (so the next tick retries its window).
    r = _run_sweep(
        side_effect=[RuntimeError("ch down"), _candidates(1, base=_NOW - timedelta(minutes=3))],
        rows=[
            {"project_id": "p1", "sampling_rate": 1.0, "last_swept_at": None},
            {"project_id": "p2", "sampling_rate": 1.0, "last_swept_at": None},
        ],
    )
    assert r.task.apply_async.call_count == 1  # only p2 dispatched
    assert _update(r).call_count == 1  # exactly one advance (p2); p1 skipped


def test_no_sampling_projects_short_circuits_before_clickhouse():
    cfg = _config_model([])
    with (
        patch.object(sweep, "get_reader") as mock_reader,
        patch.object(sweep, "TraceScanConfig", cfg),
    ):
        _run()
    mock_reader.assert_not_called()  # no CH round-trip when nothing samples


class TestSamplingDeterminism:
    """``is_trace_sampled`` must be a stable per-trace decision — the sweep
    reproduces ``scan_and_write``'s verdict to lag its watermark without a
    marker per skipped trace."""

    def test_full_rate_takes_everything(self):
        assert is_trace_sampled("any-id", 1.0) is True

    def test_zero_rate_takes_nothing(self):
        assert is_trace_sampled("any-id", 0.0) is False

    def test_same_trace_same_verdict_across_calls(self):
        # Stable across processes (hashlib, not Python's per-process-salted
        # hash()) — without this the sweep and the scan task would disagree.
        verdicts = {is_trace_sampled("trace-abc", 0.5) for _ in range(50)}
        assert len(verdicts) == 1

    def test_rate_is_approximately_honoured(self):
        ids = [f"trace-{i}" for i in range(2000)]
        kept = sum(is_trace_sampled(t, 0.25) for t in ids)
        assert 0.20 * len(ids) < kept < 0.30 * len(ids)
