"""Pure contract tests for resumable annotation-queue filter selection."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest

from model_hub.serializers.annotation_queues import (
    AddItemsSerializer,
    QueueAddItemsResponseSerializer,
)
from model_hub.services.bulk_selection import (
    ResolveResult,
    _read_bounded_bulk_page,
    _resumable_bounded_result,
)
from model_hub.views.annotation_queues import QueueItemViewSet
from tracer.services.clickhouse.list_cursor import ListCursor

pytestmark = pytest.mark.unit


class _Builder:
    def __init__(self, start, end):
        self.start = start
        self.end = end

    def parse_time_range(self, filters):
        return self.start, self.end

    @staticmethod
    def bounded_filter_row_order_token(row):
        return str(row["trace_id"])

    @staticmethod
    def bounded_filter_degraded_error_code():
        return None

    @staticmethod
    def supports_bounded_filter_scan():
        return True


def _row(trace_id, start_time):
    return {"trace_id": trace_id, "start_time": start_time}


def test_resumable_result_advances_exact_batches_without_dropping_sentinel():
    end = datetime(2026, 8, 30, tzinfo=UTC)
    start = end - timedelta(days=1)
    rows = [
        _row("trace-3", end - timedelta(minutes=1)),
        _row("trace-2", end - timedelta(minutes=2)),
        _row("trace-1", end - timedelta(minutes=3)),
    ]
    page = SimpleNamespace(rows=rows, complete=True, has_more=False)

    first = _resumable_bounded_result(
        builder=_Builder(start, end),
        filters=[],
        page=page,
        rows=rows,
        selected_rows=rows,
        key_field="trace_id",
        cap=2,
        cursor=None,
    )

    assert first.ids == ["trace-3", "trace-2"]
    assert first.total_matching == 3
    assert first.truncated is True
    assert first.continuation is not None
    assert first.continuation.order == (rows[1]["start_time"], "trace-2")
    assert first.continuation.seen_rows == 2

    terminal = _resumable_bounded_result(
        builder=_Builder(start, end),
        filters=[],
        page=SimpleNamespace(rows=[rows[2]], complete=True, has_more=False),
        rows=[rows[2]],
        selected_rows=[rows[2]],
        key_field="trace_id",
        cap=2,
        cursor=first.continuation,
    )

    assert terminal.ids == ["trace-1"]
    assert terminal.total_matching == 3
    assert terminal.truncated is False
    assert terminal.continuation is None


def test_sparse_post_filter_continuation_consumes_the_checked_raw_prefix():
    end = datetime(2026, 8, 30, tzinfo=UTC)
    start = end - timedelta(days=1)
    rows = [
        _row("selected", end - timedelta(minutes=1)),
        _row("not-selected-1", end - timedelta(minutes=2)),
        _row("not-selected-2", end - timedelta(minutes=3)),
    ]

    result = _resumable_bounded_result(
        builder=_Builder(start, end),
        filters=[],
        page=SimpleNamespace(rows=rows, complete=True, has_more=True),
        rows=rows,
        selected_rows=rows[:1],
        key_field="trace_id",
        cap=2,
        cursor=None,
    )

    assert result.ids == ["selected"]
    assert result.total_matching == 1
    assert result.continuation is not None
    assert result.continuation.order == (rows[-1]["start_time"], "not-selected-2")


def test_bounded_reader_forwards_signed_scan_checkpoint(monkeypatch):
    end = datetime(2026, 8, 30, tzinfo=UTC)
    start = end - timedelta(days=1)
    cursor = ListCursor(
        window_start=start,
        window_end=end,
        order=(end - timedelta(hours=1), "trace-9"),
        seen_rows=5,
        scan_slice_start=start,
        scan_slice_end=end - timedelta(hours=2),
        scan_before_start_time=end - timedelta(hours=3),
        scan_before_id="trace-8",
    )
    captured = {}

    def fake_reader(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            rows=[],
            complete=False,
            has_more=False,
            error_code="read_budget_exceeded",
            continuation_slice_start=start,
            continuation_slice_end=end - timedelta(hours=4),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
        fake_reader,
    )

    page = _read_bounded_bulk_page(
        builder=_Builder(start, end),
        analytics=object(),
        filters=[],
        key_field="trace_id",
        cap=10,
        cursor=cursor,
        resumable=True,
    )

    assert page.complete is False
    assert captured["cursor_start_time"] == cursor.order[0]
    assert captured["cursor_order_token"] == "trace-9"
    assert captured["continuation_slice_start"] == cursor.scan_slice_start
    assert captured["continuation_slice_end"] == cursor.scan_slice_end
    assert captured["continuation_before_start_time"] == (cursor.scan_before_start_time)
    assert captured["continuation_before_id"] == "trace-8"
    assert captured["include_incomplete_rows"] is True
    assert captured["bounded_continuation"] is True
    assert captured["retry_wide_read_budget"] is False


def test_full_window_bulk_seed_jumps_to_safe_slice_and_signs_checkpoint(
    monkeypatch,
):
    from tracer.services.clickhouse.list_cursor import (
        decode_list_cursor,
        encode_list_cursor,
    )
    from tracer.services.clickhouse.query_service import QueryResult
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    start = datetime(1971, 1, 1, tzinfo=UTC)
    end = datetime(2026, 8, 30, tzinfo=UTC)
    project_id = "a2f1c9d0-0000-4000-8000-000000000001"
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
            },
        }
    ]
    builder = TraceListQueryBuilderV2(
        project_id=project_id,
        filters=filters,
        columns=["trace_id"],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
    )

    class FakeClock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

        def advance_ms(self, milliseconds):
            self.now += milliseconds / 1000

    clock = FakeClock()
    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.monotonic",
        clock,
    )

    class BudgetThenEmptyAnalytics:
        def __init__(self):
            self.seed_widths = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            del query, settings
            width = params["filter_slice_end"] - params["filter_slice_start"]
            self.seed_widths.append(width)
            if width > timedelta(minutes=5):
                clock.advance_ms(timeout_ms)
                raise ReadDeadlineExceeded("Code: 159. Timeout exceeded")
            return QueryResult([], 0, "clickhouse", 1.0)

    analytics = BudgetThenEmptyAnalytics()

    page = _read_bounded_bulk_page(
        builder=builder,
        analytics=analytics,
        filters=filters,
        key_field="trace_id",
        cap=10,
        resumable=True,
    )

    full_width = end - start
    expected_checkpoint = end - timedelta(minutes=5)
    assert analytics.seed_widths[:2] == [full_width, timedelta(minutes=5)]
    assert clock.now < 15
    assert page.complete is False
    assert page.error_code == "read_budget_exceeded"
    assert page.continuation_slice_end is not None
    assert page.continuation_slice_end <= expected_checkpoint.replace(tzinfo=None)

    result = _resumable_bounded_result(
        builder=builder,
        filters=filters,
        page=page,
        rows=page.rows,
        selected_rows=page.rows,
        key_field="trace_id",
        cap=10,
        cursor=None,
    )
    assert result.continuation is not None
    assert result.continuation.scan_slice_end == page.continuation_slice_end

    cursor_kwargs = {
        "resource": "annotation_queue_trace_selection",
        "scope": {"project_id": project_id},
        "query": {"filters": filters},
        "page_size": 10,
    }
    token = encode_list_cursor(
        **cursor_kwargs,
        window_start=result.continuation.window_start,
        window_end=result.continuation.window_end,
        order=result.continuation.order,
        seen_rows=result.continuation.seen_rows,
        scan_slice_start=result.continuation.scan_slice_start,
        scan_slice_end=result.continuation.scan_slice_end,
        scan_before_start_time=result.continuation.scan_before_start_time,
        scan_before_id=result.continuation.scan_before_id,
    )
    decoded = decode_list_cursor(token, **cursor_kwargs)

    assert decoded.scan_slice_end == page.continuation_slice_end.replace(tzinfo=UTC)
    assert decoded.scan_slice_end < end


def test_add_items_contract_accepts_and_returns_continuation():
    request = AddItemsSerializer(
        data={
            "selection": {
                "mode": "filter",
                "source_type": "trace",
                "project_id": "a2f1c9d0-0000-4000-8000-000000000001",
                "cursor": "opaque-token",
            }
        }
    )
    assert request.is_valid(), request.errors

    response = QueueAddItemsResponseSerializer(
        data={
            "status": True,
            "result": {
                "added": 10000,
                "duplicates": 0,
                "errors": [],
                "queue_status": "pending",
                "total_matching": 10001,
                "total_matching_is_lower_bound": True,
                "has_more": True,
                "next_cursor": "opaque-token",
            },
        }
    )
    assert response.is_valid(), response.errors


def test_filter_mode_api_returns_signed_continuation_and_accepts_it(monkeypatch):
    end = datetime(2026, 8, 30, tzinfo=UTC)
    start = end - timedelta(days=1)
    queue = SimpleNamespace(
        id="a2f1c9d0-0000-4000-8000-000000000010",
        workspace=None,
    )
    organization = SimpleNamespace(id="a2f1c9d0-0000-4000-8000-000000000020")
    user = SimpleNamespace(
        id="a2f1c9d0-0000-4000-8000-000000000030",
        pk="a2f1c9d0-0000-4000-8000-000000000030",
        organization=organization,
        default_workspace_id=None,
    )
    request = SimpleNamespace(
        organization=organization,
        user=user,
        workspace=None,
        auth=None,
    )
    selection = {
        "mode": "filter",
        "source_type": "trace",
        "project_id": "a2f1c9d0-0000-4000-8000-000000000001",
        "filter": [],
        "exclude_ids": [],
        "is_voice_call": False,
        "remove_simulation_calls": False,
    }
    calls = []

    def resolver(**kwargs):
        calls.append(kwargs)
        if kwargs["cursor"] is None:
            return ResolveResult(
                ids=[],
                total_matching=0,
                truncated=True,
                continuation=ListCursor(
                    window_start=start,
                    window_end=end,
                    order=(end - timedelta(minutes=1), "trace-1"),
                    seen_rows=0,
                ),
            )
        return ResolveResult(ids=[], total_matching=0, truncated=False)

    monkeypatch.setitem(
        __import__(
            "model_hub.views.annotation_queues", fromlist=["FILTER_MODE_RESOLVERS"]
        ).FILTER_MODE_RESOLVERS,
        "trace",
        resolver,
    )
    monkeypatch.setattr(
        "model_hub.views.annotation_queues.filter_available_source_ids_for_annotation",
        lambda *args, **kwargs: ([], 0, None, {}),
    )
    manager = mock.MagicMock()
    manager.filter.return_value.values_list.return_value = []
    manager.filter.return_value.order_by.return_value.values_list.return_value.first.return_value = 0
    monkeypatch.setattr(
        "model_hub.views.annotation_queues.QueueItem.objects",
        manager,
    )
    monkeypatch.setattr(
        "model_hub.views.annotation_queues._finalize_bulk_add",
        lambda *args, **kwargs: (0, "pending"),
    )

    view = QueueItemViewSet()
    first = view._add_items_filter_mode(request, queue, selection)

    assert first.status_code == 200
    assert first.data["result"]["has_more"] is True
    assert first.data["result"]["next_cursor"]
    assert len(first.data["result"]["next_cursor_fingerprint"]) == 64
    assert calls[0]["resumable"] is True
    assert calls[0]["cursor"] is None

    continued = {
        **selection,
        "cursor": first.data["result"]["next_cursor"],
    }
    second = view._add_items_filter_mode(request, queue, continued)

    assert second.status_code == 200
    assert second.data["result"]["has_more"] is False
    assert second.data["result"]["next_cursor"] is None
    assert second.data["result"]["next_cursor_fingerprint"] is None
    assert isinstance(calls[1]["cursor"], ListCursor)


def test_filter_mode_request_locks_queue_before_mutation(monkeypatch):
    import model_hub.views.annotation_queues as views_mod

    events = []
    queue = SimpleNamespace(id="queue-1")
    organization = SimpleNamespace(id="organization-1")
    request = SimpleNamespace(organization=organization)
    deadline = mock.MagicMock()
    manager = mock.MagicMock()
    locked_manager = mock.MagicMock()

    def select_for_update(**kwargs):
        assert kwargs == {"of": ("self",)}
        events.append("lock")
        return locked_manager

    manager.select_for_update.side_effect = select_for_update
    locked_manager.get.return_value = queue
    monkeypatch.setattr(views_mod.AnnotationQueue, "objects", manager)
    monkeypatch.setattr(views_mod.transaction, "atomic", nullcontext)
    monkeypatch.setattr(
        views_mod,
        "_bounded_add_items_postgres",
        lambda _deadline: nullcontext(),
    )

    view = QueueItemViewSet()
    monkeypatch.setattr(view, "_require_queue_manager", lambda *_args: None)

    def mutate(*_args, **_kwargs):
        events.append("mutation")
        return "response"

    monkeypatch.setattr(view, "_add_items_filter_mode", mutate)

    response = view._add_items_filter_mode_request(
        request,
        queue.id,
        {"source_type": "trace"},
        deadline=deadline,
    )

    assert response == "response"
    assert events == ["lock", "mutation"]
    locked_manager.get.assert_called_once_with(
        pk=queue.id,
        organization=organization,
        deleted=False,
    )
