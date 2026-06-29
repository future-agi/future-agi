"""Tests for the scanner's terminal-marker path.

Two mechanisms keep a sweep-dispatched trace from silently pinning the watermark
forever:

  (A) ``mark_traces_failed`` — the durable FAILED write the sweep uses to abandon
      a trace stuck past the lag bound. This is the linchpin of the data-loss
      fix, so its real PG behaviour (partial-unique-index conflict, idempotency)
      is pinned here, not mocked.
  (B) ``scan_and_write(mark_unresolved=True)`` — the at-source marker for a
      CH-confirmed root that resolves no spans (deletion race), so it terminates
      immediately instead of pinning the cursor for the full lag bound. Gated by
      the flag so the inline path (which may see PeerDB-lagged emptiness) is
      unaffected.
"""

import uuid
from unittest.mock import patch

import pytest

from tracer.models.trace_scan import TraceScanResult, TraceScanStatus
from tracer.queries.trace_scanner import mark_traces_failed
from tracer.types.scan_types import ScanConfig, TraceData


@pytest.mark.django_db
class TestMarkTracesFailed:
    def test_writes_one_failed_marker_per_trace(self, project):
        t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
        n = mark_traces_failed([t1, t2], str(project.id), "stuck")

        assert n == 2
        rows = TraceScanResult.no_workspace_objects.filter(project_id=project.id)
        assert {str(r.trace_id) for r in rows} == {t1, t2}
        assert all(r.status == TraceScanStatus.FAILED for r in rows)
        assert all(r.error_message == "stuck" for r in rows)

    def test_is_idempotent_on_second_call(self, project):
        t1 = str(uuid.uuid4())
        mark_traces_failed([t1], str(project.id), "stuck")
        # Second call: the partial unique index (trace, deleted=False) +
        # filter_already_scanned re-entry must make this a no-op, not a dup row.
        n = mark_traces_failed([t1], str(project.id), "stuck again")

        assert n == 0
        assert TraceScanResult.no_workspace_objects.filter(trace_id=t1).count() == 1

    def test_skips_ids_that_already_have_any_result(self, project):
        done, new = str(uuid.uuid4()), str(uuid.uuid4())
        TraceScanResult.objects.create(
            trace_id=done, project_id=project.id, status=TraceScanStatus.COMPLETED
        )
        n = mark_traces_failed([done, new], str(project.id), "stuck")

        assert n == 1  # only the genuinely-unscanned id
        assert (
            TraceScanResult.no_workspace_objects.get(trace_id=new).status
            == TraceScanStatus.FAILED
        )
        # The pre-existing COMPLETED result is left intact.
        assert (
            TraceScanResult.no_workspace_objects.get(trace_id=done).status
            == TraceScanStatus.COMPLETED
        )


class TestScanAndWriteMarksUnresolved:
    """``mark_unresolved`` is the sweep's at-source terminal mark for a
    CH-confirmed root with no resolvable spans — gated so the inline path is
    untouched."""

    _MOD = "tracer.utils.trace_scanner"

    def _run(self, *, mark_unresolved, resolved_data):
        from tracer.utils.trace_scanner import scan_and_write

        tids = [str(uuid.uuid4()), str(uuid.uuid4())]
        with (
            patch(
                f"{self._MOD}.get_scan_config",
                return_value=ScanConfig(sampling_rate=1.0, scan_version="v7.2", enabled=True),
            ),
            patch(f"{self._MOD}.apply_sampling", side_effect=lambda x, r: x),
            patch(f"{self._MOD}.filter_already_scanned", side_effect=lambda x: x),
            patch(f"{self._MOD}.fetch_trace_data", return_value=resolved_data(tids)),
            patch(f"{self._MOD}.TraceScanner") as scanner_cls,
            patch(f"{self._MOD}.write_scan_results", return_value=0),
            patch(f"{self._MOD}._emit_scanner_billing"),
            patch(f"{self._MOD}.mark_traces_failed") as mock_mark,
        ):
            scanner_cls.return_value.scan_batch.return_value = []
            scan_and_write(tids, "proj-1", mark_unresolved=mark_unresolved)
        return tids, mock_mark

    def test_marks_all_when_none_resolve(self):
        tids, mark = self._run(mark_unresolved=True, resolved_data=lambda t: [])
        mark.assert_called_once()
        assert set(mark.call_args.args[0]) == set(tids)

    def test_marks_only_the_unresolved_subset(self):
        # First trace resolves spans; the second is the deletion-race gap.
        tids, mark = self._run(
            mark_unresolved=True,
            resolved_data=lambda t: [TraceData(trace_id=t[0], spans=[])],
        )
        mark.assert_called_once()
        assert mark.call_args.args[0] == [tids[1]]

    def test_inline_path_never_marks(self):
        # mark_unresolved=False (inline): an unresolved trace may just be
        # PeerDB-lagged; leave it for the sweep, never mark it terminal.
        _, mark = self._run(mark_unresolved=False, resolved_data=lambda t: [])
        mark.assert_not_called()
