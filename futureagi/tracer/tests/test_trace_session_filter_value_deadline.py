"""Request-wall wiring for session labels in the filter-value picker."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django import db as django_db

from tracer.models.trace_session import TraceSessionOverlay
from tracer.services.clickhouse.v2 import query_service as query_service_module
from tracer.services.clickhouse.v2 import trace_session_dict_reader

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
SESSION_ID = "00000000-0000-4000-8000-000000000002"


def test_session_label_ch_read_consumes_the_picker_deadline(monkeypatch):
    calls = []

    class Deadline:
        def remaining_ms(self):
            calls.append("remaining")
            return 3_425

    class Analytics:
        def execute_ch_query(self, query, params, **kwargs):
            calls.append((query, params, kwargs))
            return SimpleNamespace(data=[])

    monkeypatch.setattr(
        query_service_module,
        "V2AnalyticsQueryService",
        Analytics,
    )

    assert (
        trace_session_dict_reader.resolve_session_fields(
            [SESSION_ID],
            project_id=PROJECT_ID,
            deadline=Deadline(),
        )
        == {}
    )
    assert calls[0] == "remaining"
    _query, params, kwargs = calls[1]
    assert params["pid"] == PROJECT_ID
    assert "ts.project_id = %(pid)s" in _query
    assert kwargs["timeout_ms"] == 3_425
    assert kwargs["settings"]["max_result_rows"] == 1


def test_session_label_overlay_inside_outer_transaction_only_sets_local(
    monkeypatch,
):
    statements = []

    class Deadline:
        def remaining_ms(self):
            return 3_125

    class Analytics:
        def execute_ch_query(self, _query, _params, **_kwargs):
            return SimpleNamespace(
                data=[
                    {
                        "input_id": SESSION_ID,
                        "resolved_id": SESSION_ID,
                        "external_session_id": "external-session",
                        "first_seen": datetime(2026, 8, 1, tzinfo=UTC),
                        "project_id": PROJECT_ID,
                    }
                ]
            )

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, params=None):
            statements.append((statement, params))

    class OverlayQueryset:
        def filter(self, **kwargs):
            assert kwargs == {"project_id__in": {PROJECT_ID}}
            return self

        def values_list(self, *_args):
            return [(SESSION_ID, True, "renamed session")]

    class OverlayManager:
        def filter(self, **_kwargs):
            return OverlayQueryset()

    monkeypatch.setattr(query_service_module, "V2AnalyticsQueryService", Analytics)
    monkeypatch.setattr(TraceSessionOverlay, "objects", OverlayManager())
    monkeypatch.setattr(
        django_db,
        "connection",
        SimpleNamespace(
            vendor="postgresql",
            in_atomic_block=True,
            cursor=Cursor,
        ),
    )
    monkeypatch.setattr(
        django_db,
        "transaction",
        SimpleNamespace(
            atomic=lambda: pytest.fail(
                "an existing transaction must not open a nested savepoint"
            )
        ),
    )

    resolved = trace_session_dict_reader.resolve_session_fields(
        [SESSION_ID],
        project_id=PROJECT_ID,
        deadline=Deadline(),
    )

    assert resolved[SESSION_ID]["bookmarked"] is True
    assert resolved[SESSION_ID]["display_name"] == "renamed session"
    assert statements == [("SET LOCAL statement_timeout = %s", ["3125ms"])]


def test_session_label_multi_project_scope_reaches_ch_and_overlay(monkeypatch):
    second_project_id = "00000000-0000-4000-8000-000000000003"
    captured = {}

    class Analytics:
        def execute_ch_query(self, query, params, **_kwargs):
            captured["query"] = query
            captured["params"] = params
            return SimpleNamespace(
                data=[
                    {
                        "input_id": SESSION_ID,
                        "resolved_id": SESSION_ID,
                        "external_session_id": "external-session",
                        "first_seen": datetime(2026, 8, 1, tzinfo=UTC),
                        "project_id": PROJECT_ID,
                    }
                ]
            )

    class OverlayQueryset:
        def filter(self, **kwargs):
            captured.setdefault("overlay_filters", []).append(kwargs)
            return self

        def values_list(self, *_args):
            return []

    class OverlayManager:
        def filter(self, **kwargs):
            captured.setdefault("overlay_filters", []).append(kwargs)
            return OverlayQueryset()

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(query_service_module, "V2AnalyticsQueryService", Analytics)
    monkeypatch.setattr(TraceSessionOverlay, "objects", OverlayManager())
    monkeypatch.setattr(
        django_db,
        "connection",
        SimpleNamespace(vendor="postgresql", in_atomic_block=True, cursor=Cursor),
    )

    assert (
        trace_session_dict_reader.resolve_session_fields(
            [SESSION_ID],
            project_ids=[PROJECT_ID, second_project_id],
            deadline=SimpleNamespace(remaining_ms=lambda: 3_000),
        )[SESSION_ID]["external_session_id"]
        == "external-session"
    )
    assert "ts.project_id IN %(pids)s" in captured["query"]
    assert captured["params"]["pids"] == tuple(sorted((PROJECT_ID, second_project_id)))
    assert captured["overlay_filters"] == [
        {"trace_session_id__in": {SESSION_ID}},
        {"project_id__in": {PROJECT_ID, second_project_id}},
    ]
