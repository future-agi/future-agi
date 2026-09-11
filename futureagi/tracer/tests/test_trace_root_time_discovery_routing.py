"""Public CH25 trace-cursor wiring; no database or telemetry calls."""

import ast
import inspect
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tracer.selectors import trace_filter_reads
from tracer.services.clickhouse.list_cursor import ListCursor
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_bounded_trace_filter_reads import _attribute_filter, _time_filter

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
END = datetime(2026, 9, 5, 7)
START = END - timedelta(days=7)


class _ReachedRead(Exception):
    pass


def _setup(monkeypatch):
    from tracer.views import trace as view_module

    metadata = MagicMock()
    metadata.select_related.return_value = []
    monkeypatch.setattr(
        view_module.CustomEvalConfig.objects, "filter", lambda **_kwargs: metadata
    )
    monkeypatch.setattr(
        view_module, "get_annotation_labels_for_project", lambda *_args: []
    )
    request = SimpleNamespace(
        query_params={},
        user=SimpleNamespace(pk="principal"),
        organization=SimpleNamespace(pk="organization"),
    )
    view = view_module.TraceView()
    view.request = request
    view._gm = MagicMock()
    validated = {
        "project_id": PROJECT,
        "filters": [
            _time_filter(START, END),
            _attribute_filter("company_id", "company"),
        ],
        "page_number": 0,
        "page_size": 25,
        "cursor_mode": True,
    }
    deadline = SimpleNamespace(remaining_ms=lambda *_args: 5000)
    return view_module, view, request, validated, deadline


@pytest.mark.parametrize(
    "case,enabled",
    [
        ("new_cursor", True),
        ("empty_slice_checkpoint", True),
        ("unfinished_seed_keyset", False),
        ("public_result_keyset", False),
        ("legacy_numbered", False),
        ("numbered_page_n", False),
        ("explicit_exact_total", False),
        ("allow_sampled_false", False),
        ("session_detail", False),
        ("org_user_detail", False),
        ("builder_not_qualified", False),
        ("short_window", False),
        ("annotation_relation", False),
        ("annotator_relation", False),
    ],
)
def test_public_trace_cursor_passes_discovery_only_for_qualified_safe_boundaries(
    monkeypatch, case, enabled
):
    module, view, request, validated, deadline = _setup(monkeypatch)
    cursor = None
    if case in {
        "empty_slice_checkpoint",
        "unfinished_seed_keyset",
        "public_result_keyset",
    }:
        scan_end = None if case == "public_result_keyset" else END - timedelta(days=1)
        cursor = ListCursor(
            window_start=START,
            window_end=END,
            order=(END - timedelta(days=1), "trace-z"),
            scan_slice_end=scan_end,
            scan_slice_start=scan_end - timedelta(hours=2) if scan_end else None,
            scan_before_start_time=scan_end - timedelta(minutes=5)
            if case == "unfinished_seed_keyset"
            else None,
            scan_before_id="trace-m" if case == "unfinished_seed_keyset" else None,
        )
        validated["cursor"] = "signed-test-cursor"
        monkeypatch.setattr(
            module, "decode_list_cursor", lambda *_args, **_kwargs: cursor
        )
    elif case in {"legacy_numbered", "numbered_page_n"}:
        validated["cursor_mode"] = False
        validated["page_number"] = 1 if case == "numbered_page_n" else 0
    elif case == "explicit_exact_total":
        validated["require_exact_total"] = True
    elif case == "allow_sampled_false":
        validated["allow_sampled"] = False
        request.query_params["allow_sampled"] = "false"
    elif case == "session_detail":
        validated["session_id"] = "22222222-2222-4222-8222-222222222222"
    elif case == "org_user_detail":
        validated["filters"].append(
            {
                "column_id": "user_id",
                "filter_config": {
                    "col_type": "TRACE_END_USER",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "guest",
                },
            }
        )
    elif case == "builder_not_qualified":
        monkeypatch.setattr(
            TraceListQueryBuilderV2,
            "supports_filter_root_time_discovery",
            lambda _self: False,
        )
    elif case == "short_window":
        validated["filters"][0] = _time_filter(END - timedelta(minutes=30), END)
    elif case in {"annotation_relation", "annotator_relation"}:
        validated["filters"][1] = {
            "column_id": "22222222-2222-4222-8222-222222222222"
            if case == "annotation_relation"
            else "annotator",
            "filter_config": {
                "col_type": "ANNOTATION",
                "filter_type": "text" if case == "annotation_relation" else "annotator",
                "filter_op": "equals",
                "filter_value": "yes" if case == "annotation_relation" else PROJECT,
            },
        }

    captured = {}

    def stop_at_selector(**kwargs):
        captured.update(kwargs)
        raise _ReachedRead

    monkeypatch.setattr(
        trace_filter_reads, "read_bounded_filter_page", stop_at_selector
    )
    analytics = MagicMock()
    with pytest.raises(_ReachedRead):
        view._list_traces_of_session_clickhouse(
            request,
            None if case == "org_user_detail" else PROJECT,
            validated,
            analytics,
            org_project_ids=[PROJECT] if case == "org_user_detail" else None,
            org=request.organization,
            read_deadline=deadline,
        )
    assert captured["root_time_discovery"] is enabled
    assert type(captured["builder"]) is TraceListQueryBuilderV2
    assert captured["analytics"] is analytics
    assert captured["builder"].project_id == (
        None if case == "org_user_detail" else PROJECT
    )
    assert captured["bounded_continuation"] is validated["cursor_mode"]
    if cursor is not None:
        assert captured["continuation_slice_end"] == cursor.scan_slice_end
        assert (
            captured["continuation_before_start_time"] == cursor.scan_before_start_time
        )
        assert captured["continuation_before_id"] == cursor.scan_before_id
    analytics.execute_ch_query.assert_not_called()


def test_unbounded_fallback_never_calls_discovery_or_the_bounded_selector(monkeypatch):
    _, view, request, validated, deadline = _setup(monkeypatch)
    validated["cursor_mode"] = False
    monkeypatch.setattr(
        TraceListQueryBuilderV2, "supports_bounded_filter_scan", lambda _self: False
    )
    monkeypatch.setattr(
        TraceListQueryBuilderV2, "build", lambda _self: ("unbounded", {})
    )
    qualification = MagicMock(side_effect=AssertionError("unbounded discovery"))
    monkeypatch.setattr(
        TraceListQueryBuilderV2, "supports_filter_root_time_discovery", qualification
    )
    bounded_reader = MagicMock(side_effect=AssertionError("unexpected bounded read"))
    monkeypatch.setattr(trace_filter_reads, "read_bounded_filter_page", bounded_reader)
    analytics = MagicMock()
    analytics.execute_ch_query.side_effect = _ReachedRead
    with pytest.raises(_ReachedRead):
        view._list_traces_of_session_clickhouse(
            request, PROJECT, validated, analytics, read_deadline=deadline
        )
    qualification.assert_not_called()
    bounded_reader.assert_not_called()
    assert analytics.execute_ch_query.call_args.args[0] == "unbounded"


def test_only_observe_trace_view_callsite_opts_in_other_public_surfaces_unchanged():
    from tracer.views import observation_span, trace, trace_session

    enabled_calls = []
    for module in (trace, observation_span, trace_session):
        tree = ast.parse(inspect.getsource(module))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "read_bounded_filter_page"
                    and any(
                        keyword.arg == "root_time_discovery"
                        for keyword in node.keywords
                    )
                ):
                    enabled_calls.append((module.__name__, function.name))
    assert enabled_calls == [
        ("tracer.views.trace", "_list_traces_of_session_clickhouse")
    ]
