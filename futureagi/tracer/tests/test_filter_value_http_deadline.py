"""Unit wiring for the request-owned system filter-value deadline."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.attribute_cursor_state import AttributeCursorSeenState
from tracer.services.clickhouse.attribute_reads import (
    AttributeReadMetadata,
    AttributeValueCursorPageRead,
)
from tracer.services.clickhouse.filter_value_reads import (
    FilterValueCursorPageRead,
    FilterValueRead,
    SessionFilterValueCursorPageRead,
)
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.views import dashboard as dashboard_view


class _ProjectScopeStub:
    """Minimal bounded QuerySet double for filter-value authorization tests."""

    def __init__(self, project_ids, *, on_read=None):
        self.project_ids = tuple(project_ids)
        self.requested_ids = None
        self.on_read = on_read

    def filter(self, **kwargs):
        requested = kwargs.get("id__in")
        if requested is not None:
            self.requested_ids = {str(value) for value in requested}
        return self

    def order_by(self, *_args):
        return self

    def values_list(self, *_args, **_kwargs):
        if self.on_read is not None:
            self.on_read()
        return [
            project_id
            for project_id in self.project_ids
            if self.requested_ids is None or project_id in self.requested_ids
        ]


def test_filter_value_pg_reads_use_the_remaining_request_wall(monkeypatch):
    events = []
    statements = []

    class Deadline:
        def remaining_ms(self, cap_ms):
            events.append(("remaining", cap_ms))
            return 3_725

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, params=None):
            statements.append((statement, params))

    class Atomic:
        def __enter__(self):
            events.append("atomic")

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        dashboard_view,
        "connection",
        SimpleNamespace(
            vendor="postgresql",
            in_atomic_block=False,
            cursor=Cursor,
        ),
    )
    monkeypatch.setattr(
        dashboard_view,
        "transaction",
        SimpleNamespace(atomic=lambda: Atomic()),
    )

    result = dashboard_view._run_filter_value_pg_read(
        Deadline(),
        lambda: events.append("select") or ["project"],
    )

    assert result == ["project"]
    assert events == [
        ("remaining", dashboard_view._FILTER_VALUES_INTERACTIVE_TIMEOUT_MS),
        "atomic",
        "select",
    ]
    assert statements == [
        ("SET TRANSACTION READ ONLY", None),
        ("SELECT set_config('statement_timeout', %s, true)", ["3725"]),
    ]


def test_filter_value_pg_timeout_fails_closed(monkeypatch):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement, _params=None):
            raise dashboard_view.DatabaseError("statement timeout")

    class Atomic:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        dashboard_view,
        "connection",
        SimpleNamespace(
            vendor="postgresql",
            in_atomic_block=False,
            cursor=Cursor,
        ),
    )
    monkeypatch.setattr(
        dashboard_view,
        "transaction",
        SimpleNamespace(atomic=lambda: Atomic()),
    )

    with pytest.raises(ReadDeadlineExceeded):
        dashboard_view._run_filter_value_pg_read(
            SimpleNamespace(remaining_ms=lambda _cap: 3_500),
            lambda: [],
        )


def test_filter_value_pg_read_inside_outer_transaction_only_sets_local(monkeypatch):
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, params=None):
            statements.append((statement, params))

        def fetchone(self):
            return ("30s",)

    monkeypatch.setattr(
        dashboard_view,
        "connection",
        SimpleNamespace(
            vendor="postgresql",
            in_atomic_block=True,
            needs_rollback=False,
            cursor=Cursor,
        ),
    )
    monkeypatch.setattr(
        dashboard_view,
        "transaction",
        SimpleNamespace(
            atomic=lambda: pytest.fail(
                "an existing transaction must not open a nested savepoint"
            )
        ),
    )

    assert dashboard_view._run_filter_value_pg_read(
        SimpleNamespace(remaining_ms=lambda _cap: 3_250),
        lambda: ["project"],
    ) == ["project"]
    assert statements == [
        ("SELECT current_setting('statement_timeout')", None),
        ("SELECT set_config('statement_timeout', %s, true)", ["3250"]),
        ("SELECT set_config('statement_timeout', %s, true)", ["30s"]),
    ]


def test_filter_value_pg_read_does_not_restore_a_broken_outer_transaction(
    monkeypatch,
):
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, params=None):
            statements.append((statement, params))

        def fetchone(self):
            return ("30s",)

    fake_connection = SimpleNamespace(
        vendor="postgresql",
        in_atomic_block=True,
        needs_rollback=False,
        cursor=Cursor,
    )
    monkeypatch.setattr(dashboard_view, "connection", fake_connection)

    def fail_read():
        fake_connection.needs_rollback = True
        raise dashboard_view.DatabaseError("statement timeout")

    with pytest.raises(ReadDeadlineExceeded):
        dashboard_view._run_filter_value_pg_read(
            SimpleNamespace(remaining_ms=lambda _cap: 3_250),
            fail_read,
        )

    assert statements == [
        ("SELECT current_setting('statement_timeout')", None),
        ("SELECT set_config('statement_timeout', %s, true)", ["3250"]),
    ]


def test_resumed_custom_value_cursor_captures_wall_after_state_restore(monkeypatch):
    project_id = "00000000-0000-4000-8000-000000000001"
    workspace_id = "00000000-0000-4000-8000-000000000002"
    window_start = datetime(2026, 7, 1, tzinfo=UTC)
    window_end = datetime(2026, 8, 1, tzinfo=UTC)
    segment_end = datetime(2026, 7, 31, tzinfo=UTC)
    events = []

    class Deadline:
        def remaining_ms(self, cap_ms):
            events.append(("remaining", cap_ms))
            return 3_175

    cursor_state = SimpleNamespace(
        order=(segment_end, (), (), 0, ()),
        window_start=window_start,
        window_end=window_end,
        scan_slice_start=None,
        scan_slice_end=None,
        seen_rows=0,
    )
    seen_state = AttributeCursorSeenState(digests=(), state_id=None)
    metadata = AttributeReadMetadata(
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        query_window_start=window_start,
        query_window_end=window_end,
        query_count=1,
    )
    page_read = AttributeValueCursorPageRead(
        rows=(),
        metadata=metadata,
        has_more=False,
        next_segment_end=segment_end,
        next_before_identity=None,
        next_resume_identity=None,
        next_resume_member_offset=0,
        seen_value_digests=(),
    )
    selector = Mock()
    selector.read_value_cursor_page.return_value = page_read

    def selector_factory(**kwargs):
        events.append(("selector", kwargs["wall_timeout_ms"]))
        return selector

    def decode_cursor(*_args, **_kwargs):
        events.append("decode")
        return cursor_state, None

    def load_seen_state(*_args, **_kwargs):
        events.append("seen_state")
        return seen_state

    monkeypatch.setattr(
        dashboard_view,
        "ReadDeadline",
        SimpleNamespace(start=lambda _total_ms: Deadline()),
    )
    monkeypatch.setattr(
        dashboard_view,
        "project_queryset_for_request",
        lambda _request: _ProjectScopeStub([project_id]),
    )
    monkeypatch.setattr(
        dashboard_view,
        "_run_filter_value_pg_read",
        lambda _deadline, read: read(),
    )
    monkeypatch.setattr(
        dashboard_view,
        "cursor_scope_for_request",
        lambda *_args, **_kwargs: {"principal": "unit"},
    )
    monkeypatch.setattr(
        dashboard_view,
        "decode_catalog_snapshot_list_cursor",
        decode_cursor,
    )
    monkeypatch.setattr(
        dashboard_view,
        "load_attribute_cursor_seen_state",
        load_seen_state,
    )
    monkeypatch.setattr(dashboard_view, "AttributeReadSelector", selector_factory)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: object())

    request = SimpleNamespace(
        validated_query_data={
            "metric_name": "customer.plan",
            "metric_type": "custom_attribute",
            "source": "traces",
            "project_ids": [project_id],
            "search": "enterprise",
            "page_size": 10,
            "cursor": "opaque-signed-cursor",
        },
        workspace=SimpleNamespace(id=workspace_id),
    )

    response = dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(), request
    )

    assert response.status_code == 200
    assert response.data["result"]["values"] == []
    assert events == [
        "decode",
        "seen_state",
        (
            "remaining",
            dashboard_view.ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS,
        ),
        ("selector", 3_175),
    ]
    selector.read_value_cursor_page.assert_called_once()
    assert (
        selector.read_value_cursor_page.call_args.kwargs["continue_operation"] is False
    )


def test_system_filter_value_cursor_receives_view_owned_deadline(monkeypatch):
    project_id = "00000000-0000-4000-8000-000000000001"
    events = []
    request_deadline = Mock()
    request_deadline.remaining_ms.return_value = 3_900
    deadline_start = Mock(
        side_effect=lambda _total_ms: events.append("deadline") or request_deadline
    )

    retained_start = datetime(2026, 8, 1, 11, 55, tzinfo=UTC)
    selector = Mock()
    selector.retained_window_start.return_value = retained_start
    selector_factory = Mock(return_value=selector)
    page_read = FilterValueCursorPageRead(
        values=("customer-ended-call",),
        query_window_start=retained_start,
        query_window_end=retained_start + timedelta(minutes=5),
        has_more=False,
        next_segment_end=retained_start,
        next_segment_start=None,
        next_value_after=None,
        seen_value_digests=(),
        browse_status="exhausted",
    )

    def read_cursor_page(*_args, **kwargs):
        events.append("selector")
        assert kwargs["deadline"] is request_deadline
        return page_read

    monkeypatch.setattr(
        dashboard_view,
        "ReadDeadline",
        SimpleNamespace(start=deadline_start),
    )
    monkeypatch.setattr(
        dashboard_view,
        "project_queryset_for_request",
        lambda _request: _ProjectScopeStub(
            [project_id], on_read=lambda: events.append("project_scope")
        ),
    )
    monkeypatch.setattr(
        dashboard_view,
        "_run_filter_value_pg_read",
        lambda deadline, read: (
            events.append("pg_budget"),
            read() if deadline is request_deadline else None,
        )[1],
    )
    monkeypatch.setattr(
        dashboard_view,
        "cursor_scope_for_request",
        lambda *_args, **_kwargs: {"principal": "unit"},
    )
    monkeypatch.setattr(
        dashboard_view,
        "AttributeReadSelector",
        selector_factory,
    )
    monkeypatch.setattr(
        dashboard_view,
        "V2AnalyticsQueryService",
        lambda: object(),
    )
    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        read_cursor_page,
    )

    request = SimpleNamespace(
        validated_query_data={
            "metric_name": "ended_reason",
            "metric_type": "system_metric",
            "source": "traces",
            "project_ids": [project_id],
            "search": "",
            "page_size": 20,
        },
        workspace=SimpleNamespace(id="00000000-0000-4000-8000-000000000002"),
    )
    response = dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(), request
    )

    assert response.status_code == 200
    assert response.data["result"]["values"] == [
        {"value": "customer-ended-call", "label": "customer-ended-call"}
    ]
    deadline_start.assert_called_once_with(
        dashboard_view._FILTER_VALUES_INTERACTIVE_TIMEOUT_MS
    )
    assert dashboard_view._FILTER_VALUES_INTERACTIVE_TIMEOUT_MS == 30_000
    selector_factory.assert_called_once_with(
        typed_only=True,
        json_attribute_mode="arrays",
        wall_timeout_ms=3_900,
    )
    request_deadline.remaining_ms.assert_called_once_with(
        dashboard_view.ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS
    )
    assert events == ["deadline", "pg_budget", "project_scope", "selector"]


@pytest.mark.parametrize("source", ["traces", "sessions"])
def test_session_display_label_search_uses_curated_cursor_project_scoped(
    monkeypatch, source
):
    project_id = "00000000-0000-4000-8000-000000000001"
    foreign_project_id = "00000000-0000-4000-8000-000000000099"
    session_id = "00000000-0000-4000-8000-000000000010"
    request_deadline = Mock()
    request_deadline.remaining_ms.return_value = 3_900

    page_read = SessionFilterValueCursorPageRead(
        values=(session_id,),
        has_more=True,
        next_value_after=session_id,
        browse_status="continuation",
    )
    read_cursor_page = Mock(return_value=page_read)
    resolved_calls = []

    monkeypatch.setattr(
        dashboard_view,
        "ReadDeadline",
        SimpleNamespace(start=lambda _total_ms: request_deadline),
    )
    monkeypatch.setattr(
        dashboard_view,
        "project_queryset_for_request",
        lambda _request: _ProjectScopeStub([project_id]),
    )
    monkeypatch.setattr(
        dashboard_view,
        "_run_filter_value_pg_read",
        lambda _deadline, read: read(),
    )
    monkeypatch.setattr(
        dashboard_view,
        "cursor_scope_for_request",
        lambda *_args, **_kwargs: {"principal": "unit"},
    )
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: object())
    monkeypatch.setattr(
        dashboard_view,
        "load_attribute_cursor_seen_state",
        lambda *_args, **_kwargs: AttributeCursorSeenState(digests=(), state_id=None),
    )
    monkeypatch.setattr(
        dashboard_view,
        "persist_attribute_cursor_seen_state",
        lambda *_args, **_kwargs: ("d" * 32,),
    )
    monkeypatch.setattr(
        dashboard_view,
        "encode_list_cursor",
        lambda **_kwargs: "signed-next",
    )
    monkeypatch.setattr(
        dashboard_view,
        "read_session_filter_value_cursor_page",
        read_cursor_page,
    )
    overlay_ids = Mock(return_value=(session_id,))
    monkeypatch.setattr(
        dashboard_view,
        "_session_overlay_filter_value_ids",
        overlay_ids,
    )

    from tracer.services.clickhouse.v2 import trace_session_dict_reader

    def resolve_session_fields(ids, **kwargs):
        resolved_calls.append((ids, kwargs))
        return {
            session_id: {
                "display_name": "Customer Session",
                "external_session_id": "external-customer-123",
            }
        }

    monkeypatch.setattr(
        trace_session_dict_reader,
        "resolve_session_fields",
        resolve_session_fields,
    )

    request = SimpleNamespace(
        validated_query_data={
            "metric_name": "session",
            "metric_type": "system_metric",
            "source": source,
            "project_ids": [project_id, foreign_project_id],
            "search": "external-customer",
            "page_size": 10,
        },
        workspace=SimpleNamespace(id="00000000-0000-4000-8000-000000000002"),
    )
    response = dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(), request
    )

    assert response.status_code == 200
    assert response.data["result"] == {
        "values": [
            {
                "value": session_id,
                "label": "Customer Session",
                "description": "external-customer-123",
            }
        ],
        "query_complete": True,
        "query_status": "complete",
        "has_more": True,
        "browse_status": "continuation",
        "next_cursor": "signed-next",
    }
    assert read_cursor_page.call_args.kwargs["project_ids"] == [project_id]
    assert read_cursor_page.call_args.kwargs["search"] == "external-customer"
    assert read_cursor_page.call_args.kwargs["overlay_session_ids"] == (session_id,)
    overlay_ids.assert_called_once_with(
        project_ids=[project_id],
        search="external-customer",
        value_after=None,
        limit=11,
        deadline=request_deadline,
    )
    assert resolved_calls == [
        (
            (session_id,),
            {"project_ids": [project_id], "deadline": request_deadline},
        )
    ]


def test_project_display_label_search_keeps_raw_cursor_authorized(monkeypatch):
    project_id = "00000000-0000-4000-8000-000000000001"
    foreign_project_id = "00000000-0000-4000-8000-000000000099"
    window_end = datetime(2026, 8, 12, tzinfo=UTC)
    window_start = window_end - timedelta(days=30)
    request_deadline = Mock()
    request_deadline.remaining_ms.return_value = 3_900
    workspace = SimpleNamespace(id="00000000-0000-4000-8000-000000000002")

    project_rows = Mock()
    project_rows.filter.return_value = project_rows
    project_rows.values_list.return_value = [(project_id, "Authorized Project")]
    selector = Mock()
    selector.retained_window_start.return_value = window_start
    page_read = FilterValueCursorPageRead(
        values=(project_id,),
        query_window_start=window_start,
        query_window_end=window_end,
        has_more=False,
        next_segment_end=window_start,
        next_segment_start=None,
        next_value_after=None,
        seen_value_digests=("d" * 32,),
        browse_status="exhausted",
    )
    read_cursor_page = Mock(return_value=page_read)

    monkeypatch.setattr(
        dashboard_view,
        "ReadDeadline",
        SimpleNamespace(start=lambda _total_ms: request_deadline),
    )
    monkeypatch.setattr(
        dashboard_view,
        "project_queryset_for_request",
        lambda _request: _ProjectScopeStub([project_id]),
    )
    monkeypatch.setattr(
        dashboard_view,
        "_run_filter_value_pg_read",
        lambda _deadline, read: read(),
    )
    monkeypatch.setattr(
        dashboard_view,
        "cursor_scope_for_request",
        lambda *_args, **_kwargs: {"principal": "unit"},
    )
    monkeypatch.setattr(
        dashboard_view, "AttributeReadSelector", lambda **_kwargs: selector
    )
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: object())
    monkeypatch.setattr(
        dashboard_view,
        "load_attribute_cursor_seen_state",
        lambda *_args, **_kwargs: AttributeCursorSeenState(digests=(), state_id=None),
    )
    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        read_cursor_page,
    )
    monkeypatch.setattr(
        dashboard_view,
        "Project",
        SimpleNamespace(objects=project_rows),
    )

    request = SimpleNamespace(
        validated_query_data={
            "metric_name": "project",
            "metric_type": "system_metric",
            "source": "traces",
            "project_ids": [project_id, foreign_project_id],
            "search": "authorized project",
            "page_size": 10,
        },
        workspace=workspace,
    )
    response = dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(), request
    )

    assert response.status_code == 200
    assert response.data["result"] == {
        "values": [{"value": project_id, "label": "Authorized Project"}],
        "query_complete": True,
        "query_status": "complete",
        "query_window_start": window_start.isoformat(),
        "query_window_end": window_end.isoformat(),
        "has_more": False,
        "browse_status": "exhausted",
        "next_cursor": None,
    }
    assert read_cursor_page.call_args.kwargs["project_ids"] == [project_id]
    assert read_cursor_page.call_args.kwargs["search"] == ""
    project_rows.filter.assert_called_once_with(
        id__in=[project_id],
        workspace=workspace,
    )


def test_legacy_session_search_stays_raw_and_keeps_sample_metadata(monkeypatch):
    project_id = "00000000-0000-4000-8000-000000000001"
    foreign_project_id = "00000000-0000-4000-8000-000000000099"
    session_id = "abc123-session"
    window_end = datetime(2026, 8, 12, tzinfo=UTC)
    window_start = window_end - timedelta(days=7)
    request_deadline = Mock()
    analytics = object()
    raw_read = Mock(
        return_value=FilterValueRead(
            values=(session_id,),
            query_complete=False,
            query_error_code="sample_limit",
            query_window_start=window_start,
            query_window_end=window_end,
            has_more=True,
        )
    )

    monkeypatch.setattr(
        dashboard_view,
        "ReadDeadline",
        SimpleNamespace(start=lambda _total_ms: request_deadline),
    )
    monkeypatch.setattr(
        dashboard_view,
        "project_queryset_for_request",
        lambda _request: _ProjectScopeStub([project_id]),
    )
    monkeypatch.setattr(
        dashboard_view,
        "_run_filter_value_pg_read",
        lambda _deadline, read: read(),
    )
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: analytics)
    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_values",
        raw_read,
    )

    from tracer.services.clickhouse.v2 import trace_session_dict_reader

    resolve_session_fields = Mock(
        return_value={
            session_id: {
                "display_name": "Customer Session",
                "external_session_id": "external-session",
            }
        }
    )
    monkeypatch.setattr(
        trace_session_dict_reader,
        "resolve_session_fields",
        resolve_session_fields,
    )

    request = SimpleNamespace(
        validated_query_data={
            "metric_name": "session",
            "metric_type": "system_metric",
            "source": "traces",
            "project_ids": [project_id, foreign_project_id],
            "search": "abc123",
        },
        workspace=SimpleNamespace(id="00000000-0000-4000-8000-000000000002"),
    )
    response = dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(), request
    )

    assert response.status_code == 200
    assert response.data["result"] == {
        "values": [
            {
                "value": session_id,
                "label": "Customer Session",
                "description": "external-session",
            }
        ],
        "query_complete": False,
        "query_status": "sampled",
        "query_window_start": window_start.isoformat(),
        "query_window_end": window_end.isoformat(),
        "query_error_code": "sample_limit",
    }
    raw_read.assert_called_once_with(
        analytics,
        project_ids=[project_id],
        metric_name="session",
        search="abc123",
        limit=20,
        lookback_days=dashboard_view.DashboardViewSet.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS,
        deadline=request_deadline,
    )
    resolve_session_fields.assert_called_once_with(
        [session_id],
        project_ids=[project_id],
        deadline=request_deadline,
    )


def test_system_filter_value_cursor_deadline_maps_to_sanitized_503(monkeypatch):
    project_id = "00000000-0000-4000-8000-000000000001"
    request_deadline = Mock()
    request_deadline.remaining_ms.side_effect = ReadDeadlineExceeded(
        "shared filter-value deadline"
    )

    monkeypatch.setattr(
        dashboard_view,
        "ReadDeadline",
        SimpleNamespace(start=lambda _total_ms: request_deadline),
    )
    monkeypatch.setattr(
        dashboard_view,
        "project_queryset_for_request",
        lambda _request: _ProjectScopeStub([project_id]),
    )
    monkeypatch.setattr(
        dashboard_view,
        "_run_filter_value_pg_read",
        lambda _deadline, read: read(),
    )
    monkeypatch.setattr(
        dashboard_view,
        "cursor_scope_for_request",
        lambda *_args, **_kwargs: {"principal": "unit"},
    )
    monkeypatch.setattr(
        dashboard_view,
        "V2AnalyticsQueryService",
        lambda: object(),
    )

    request = SimpleNamespace(
        validated_query_data={
            "metric_name": "ended_reason",
            "metric_type": "system_metric",
            "source": "traces",
            "project_ids": [project_id],
            "search": "customer",
            "page_size": 20,
        },
        workspace=SimpleNamespace(id="00000000-0000-4000-8000-000000000002"),
    )
    response = dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(), request
    )

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
