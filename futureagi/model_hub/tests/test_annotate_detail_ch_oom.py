"""F3 — the annotator workspace (annotate-detail) must not surface a raw
ClickHouse out-of-memory error (server code 241) to the user.

The span-notes target for a TRACE-source queue item is resolved by reading the
trace's ROOT span from ClickHouse. Previously this fetched EVERY span in the
trace (with its wide input/output/metadata JSON blobs) via ``list_by_trace``
and filtered in Python — a whole-trace materialization that can blow CH's
per-query memory budget (code 241) on a big trace, 500-ing the annotator step.

The fix:
  * ``CHSpanReader.get_root_span_for_trace`` selects only the three id/type
    columns with a ``LIMIT 1`` (no wide blobs) — bounded by construction.
  * ``_span_notes_target_for_queue_item`` catches a CH memory error and
    degrades to "no span-notes target" instead of raising; non-CH errors still
    propagate.

These tests pin the CH-OOM detector and the graceful-degradation contract.
"""

from types import SimpleNamespace
from unittest import mock

import pytest

from model_hub.utils.annotation_queue_helpers import is_clickhouse_memory_error


class TestIsClickhouseMemoryError:
    def test_detects_code_241(self):
        exc = Exception(
            "Code: 241. DB::Exception: Memory limit (total) exceeded: "
            "would use 9.31 GiB ... (MEMORY_LIMIT_EXCEEDED)"
        )
        assert is_clickhouse_memory_error(exc) is True

    def test_detects_memory_limit_exceeded_token(self):
        assert is_clickhouse_memory_error(Exception("MEMORY_LIMIT_EXCEEDED")) is True

    def test_detects_wrapped_cause(self):
        inner = Exception("Code: 241 ... (MEMORY_LIMIT_EXCEEDED)")
        outer = RuntimeError("ch read failed")
        outer.__cause__ = inner
        assert is_clickhouse_memory_error(outer) is True

    def test_detects_wrapped_context(self):
        inner = Exception("memory limit ... exceeded")
        outer = RuntimeError("wrapper")
        outer.__context__ = inner
        assert is_clickhouse_memory_error(outer) is True

    def test_ignores_unrelated_error(self):
        assert is_clickhouse_memory_error(ValueError("bad input")) is False
        assert is_clickhouse_memory_error(Exception("Code: 60 unknown table")) is False

    def test_no_infinite_loop_on_self_referential_cause(self):
        exc = Exception("plain")
        exc.__cause__ = exc  # pathological self-reference
        assert is_clickhouse_memory_error(exc) is False


class TestSpanNotesTargetCHResilience:
    """``_span_notes_target_for_queue_item`` degrades gracefully on a CH OOM."""

    def _trace_item(self, trace_id="11111111-1111-1111-1111-111111111111"):
        # Minimal duck-typed QueueItem: a trace source with a trace_id.
        return SimpleNamespace(
            source_type="trace",
            trace_id=trace_id,
            observation_span_id=None,
            observation_span=None,
        )

    def test_ch_memory_error_degrades_to_none(self):
        from model_hub.views import annotation_queues as aq

        item = self._trace_item()
        boom = Exception("Code: 241. DB::Exception: Memory limit exceeded (MEMORY_LIMIT_EXCEEDED)")

        reader_cm = mock.MagicMock()
        reader = reader_cm.__enter__.return_value
        reader.get_root_span_for_trace.side_effect = boom

        with mock.patch("tracer.services.clickhouse.v2.get_reader", return_value=reader_cm):
            # Must NOT raise — the OOM is swallowed and the target is None.
            result = aq._span_notes_target_for_queue_item(item)

        assert result is None
        reader.get_root_span_for_trace.assert_called_once_with(str(item.trace_id))

    def test_non_ch_error_still_propagates(self):
        from model_hub.views import annotation_queues as aq

        item = self._trace_item()
        reader_cm = mock.MagicMock()
        reader = reader_cm.__enter__.return_value
        reader.get_root_span_for_trace.side_effect = ValueError("genuine bug")

        with mock.patch("tracer.services.clickhouse.v2.get_reader", return_value=reader_cm):
            with pytest.raises(ValueError, match="genuine bug"):
                aq._span_notes_target_for_queue_item(item)

    def test_returns_root_span_ref_on_success(self):
        from model_hub.views import annotation_queues as aq
        from tracer.services.clickhouse.v2.span_reader import RootSpanRef

        item = self._trace_item()
        ref = RootSpanRef(
            id="span-123", parent_span_id="", observation_type="conversation"
        )
        reader_cm = mock.MagicMock()
        reader = reader_cm.__enter__.return_value
        reader.get_root_span_for_trace.return_value = ref

        with mock.patch("tracer.services.clickhouse.v2.get_reader", return_value=reader_cm):
            result = aq._span_notes_target_for_queue_item(item)

        assert result is ref
        assert result.id == "span-123"

    def test_span_source_item_skips_ch_entirely(self):
        """An observation_span source returns its FK span without touching CH."""
        from model_hub.views import annotation_queues as aq

        span = SimpleNamespace(id="pg-span-1")
        item = SimpleNamespace(
            source_type="observation_span",
            observation_span_id="pg-span-1",
            observation_span=span,
            trace_id=None,
        )
        with mock.patch("tracer.services.clickhouse.v2.get_reader") as get_reader:
            result = aq._span_notes_target_for_queue_item(item)
        assert result is span
        get_reader.assert_not_called()
