"""Revert-catchers for the scanner's per-query ClickHouse guardrails.

Each scanner activity that reads spans through the v2 reader must run that read
inside ``scan_ch_guardrails()`` so the memory/time/sort caps are baked into the
reader's client. These tests drive the real (undecorated) activity body and
snapshot ``current_settings()`` at the moment the wrapped CH work begins — if the
``with`` is dropped, or a refactor slips a context-dropping hop in front of the
read, the snapshot is empty and the test fails. DB-free: every collaborator is
mocked. ``cluster_scan_issues_task`` is intentionally uncovered — it reads only
through ``ClickHouseVectorDB`` (a separate client that never sees the contextvar).
"""

import contextlib
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from tracer.services.clickhouse.v2.query_settings import current_settings
from tracer.tasks import trace_scanner as scanner

_NOW = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)


def _reader_cm():
    """A ``with get_reader() as reader`` context manager over a mock reader."""
    reader = MagicMock()
    reader.ch_now.return_value = _NOW
    reader.root_trace_candidates.return_value = []
    cm = MagicMock()
    cm.__enter__.return_value = reader
    cm.__exit__.return_value = False
    return cm


def test_scan_traces_task_runs_scan_and_write_inside_guardrails():
    captured = {}

    def spy(*_a, **_k):
        captured["settings"] = current_settings()
        return []  # empty results are fine — we only need the wrapped call to run

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(scanner, "SCAN_DELAY_SECONDS", 0))
        stack.enter_context(patch.object(scanner, "scan_and_write", side_effect=spy))
        # Load-bearing: the activity dispatches embed unconditionally after the
        # scan, so this mock keeps the real Temporal start_activity out of the test.
        stack.enter_context(patch.object(scanner, "embed_trace_inputs_task"))
        scanner.scan_traces_task._original_func(["t1"], "p1", False)

    assert captured["settings"] == scanner.SCAN_CH_GUARDRAILS


def test_embed_trace_inputs_task_runs_embed_inside_guardrails():
    captured = {}

    def spy(*_a, **_k):
        captured["settings"] = current_settings()
        return 0  # stored count

    with patch.object(scanner, "embed_trace_inputs", side_effect=spy):
        # trigger_clustering=False → no cluster chain to mock.
        scanner.embed_trace_inputs_task._original_func(["t1"], "p1", False)

    assert captured["settings"] == scanner.SCAN_CH_GUARDRAILS


def test_sweep_builds_reader_inside_guardrails():
    captured = {}

    def spy_get_reader():
        captured["settings"] = current_settings()
        return _reader_cm()

    cfg = MagicMock()
    cfg.no_workspace_objects.filter.return_value.order_by.return_value.values.return_value = [
        {"project_id": "p1", "sampling_rate": 1.0, "last_swept_at": None}
    ]

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch.object(scanner, "get_reader", side_effect=spy_get_reader)
        )
        stack.enter_context(patch.object(scanner, "TraceScanConfig", cfg))
        stack.enter_context(
            patch.object(scanner, "filter_already_scanned", side_effect=lambda x: x)
        )
        stack.enter_context(
            patch.object(
                scanner, "is_trace_sampled", side_effect=lambda tid, rate: True
            )
        )
        stack.enter_context(patch.object(scanner, "scan_traces_task"))
        scanner.sweep_scannable_traces._original_func()

    assert captured["settings"] == scanner.SCAN_CH_GUARDRAILS
