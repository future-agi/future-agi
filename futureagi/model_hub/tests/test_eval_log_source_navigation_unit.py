"""TH-4804 U01-U35: provenance, bounded reads, and terminal wire states."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from model_hub.services import eval_log_source_navigation as navigation
from model_hub.utils.api_log_config import parse_api_log_config
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailNotFound,
    TraceDetailRead,
    TraceDetailReadUnavailable,
)

PROJECT, TRACE, SPAN, CONFIG, ROOT, OTHER = [str(uuid4()) for _ in range(6)]
STATUSES = (
    "ready",
    "no_reference",
    "unsupported_source",
    "unsupported_target",
    "incomplete_reference",
    "invalid_reference",
    "ambiguous_reference",
    "unavailable",
    "temporarily_unavailable",
)


def _config(**changes):
    return {
        "source": "tracer",
        "custom_eval_config_id": CONFIG,
        "project_id": PROJECT,
        "trace_id": TRACE,
        "span_id": SPAN,
        **changes,
    }


def _span(span_id=SPAN, parent=None, kind="llm", **changes):
    return {
        "id": span_id,
        "project_id": PROJECT,
        "trace_id": TRACE,
        "parent_span_id": parent,
        "observation_type": kind,
        **changes,
    }


def _detail(*spans, project_id=PROJECT):
    return TraceDetailRead(project_id, tuple(spans), (), (), (), 2, 1.0)


@pytest.fixture
def resolve(monkeypatch):
    # Patch only the Project lookup; exercise the production authorization helper.
    manager = Mock()
    manager.filter.return_value.filter.return_value = manager.filter.return_value
    manager.filter.return_value.first.return_value = SimpleNamespace(
        id=PROJECT,
        workspace_id=None,
    )
    monkeypatch.setattr("tracer.views.trace.Project.no_workspace_objects", manager)
    request = SimpleNamespace(organization=None, workspace=None)
    reader = Mock(return_value=_detail(_span()))

    def run(config=None, *, raw=None, source="tracer", **kwargs):
        config = _config() if config is None else config
        raw = json.dumps(config) if raw is None else raw
        log = SimpleNamespace(
            log_id=OTHER,
            config=raw,
            source=source,
            source_id=None,
            workspace_id=None,
        )
        return navigation.resolve_eval_log_source_navigation(
            request=request,
            log_row=log,
            config=parse_api_log_config(raw),
            analytics=kwargs.pop("analytics", sentinel_analytics),
            read_detail=reader,
            **kwargs,
        )

    sentinel_analytics = object()
    run.reader = reader
    run.manager = manager
    run.analytics = sentinel_analytics
    return run


@pytest.mark.parametrize(
    "config,raw,source,expected",
    [
        (
            {"custom_eval_config_id": CONFIG, "mappings": {}},
            None,
            "tracer",
            "no_reference",
        ),
        (_config(trace_id=None), None, "tracer", "incomplete_reference"),
        (_config(span_id=None), None, "tracer", "incomplete_reference"),
        (_config(trace_id=" " + TRACE), None, "tracer", "invalid_reference"),
        ({}, "not json", "tracer", "invalid_reference"),
        ({}, "[1,2]", "tracer", "invalid_reference"),
        (_config(custom_eval_config_id=None), None, "tracer", "incomplete_reference"),
        (_config(custom_eval_config_id="abc"), None, "tracer", "invalid_reference"),
        (_config(target_type="span_group"), None, "tracer", "invalid_reference"),
        (_config(session_id=OTHER), None, "tracer", "invalid_reference"),
        (_config(), None, "dataset", "invalid_reference"),
        (
            {"source": "dataset", "dataset_id": TRACE},
            None,
            "dataset",
            "unsupported_source",
        ),
        ({}, None, "prompt", "unsupported_source"),
        (
            {"source": "tracer", "target_type": "session", "session_id": OTHER},
            None,
            "tracer",
            "unsupported_target",
        ),
        (_config(project_id="not-a-uuid"), None, "tracer", "invalid_reference"),
    ],
    ids=[
        "u01",
        "u02",
        "u03",
        "u04",
        "u05",
        "u06",
        "u07",
        "u08",
        "u09",
        "u10",
        "u11",
        "u12",
        "u13",
        "u14",
        "u31",
    ],
)
def test_reference_classification(resolve, config, raw, source, expected):
    with capture_logs() as logs:
        result = resolve(config, raw=raw, source=source)
    assert result.as_dict() == {
        "status": expected,
        "kind": None,
        "project_id": None,
        "trace_id": None,
        "span_id": None,
        "retryable": False,
    }
    resolve.reader.assert_not_called()
    resolve.manager.filter.assert_not_called()
    assert logs == [
        {
            "event": "eval_log_source_navigation.resolved",
            "log_level": "info",
            "log_id": OTHER,
            "status": expected,
            "error_code": None,
        }
    ]


@pytest.mark.parametrize(
    "source,target",
    [("feedback", None), ("tracer", None), ("tracer", "trace")],
    ids=["u15", "u16", "u17"],
)
def test_ready_trace(resolve, source, target):
    with capture_logs() as logs:
        result = resolve(_config(target_type=target), source=source)
    assert result.as_dict() == {
        "status": "ready",
        "kind": "trace",
        "project_id": PROJECT,
        "trace_id": TRACE,
        "span_id": SPAN,
        "retryable": False,
    }
    assert logs == []


def test_u18_unique_conversation_root(resolve):
    resolve.reader.return_value = _detail(
        _span(ROOT, kind="conversation"), _span(parent=ROOT)
    )
    assert resolve().kind == "voice_call"


@pytest.mark.parametrize(
    "spans",
    [
        [
            _span(ROOT, kind="conversation"),
            _span(OTHER, kind="conversation"),
            _span(parent=ROOT),
        ],
        [_span(ROOT, kind="conversation"), _span()],
        [
            _span(ROOT, kind="conversation"),
            _span(parent=OTHER),
            _span(OTHER, parent=SPAN),
        ],
    ],
    ids=["u19", "u20", "u21"],
)
def test_ambiguous_ancestry(resolve, spans):
    resolve.reader.return_value = _detail(*spans)
    assert resolve().status == "ambiguous_reference"


@pytest.mark.parametrize(
    "exc,status,code",
    [
        (
            TraceDetailReadUnavailable("ambiguous_span_identity"),
            "ambiguous_reference",
            "ambiguous_span_identity",
        ),
        (
            TraceDetailReadUnavailable("ambiguous_project"),
            "ambiguous_reference",
            "ambiguous_project",
        ),
        (TraceDetailNotFound(), "unavailable", None),
        (
            TraceDetailReadUnavailable("project_scope_too_large"),
            "unavailable",
            "project_scope_too_large",
        ),
        (
            TraceDetailReadUnavailable("deadline_exceeded"),
            "temporarily_unavailable",
            "deadline_exceeded",
        ),
        (
            TraceDetailReadUnavailable("incomplete_content_replay"),
            "temporarily_unavailable",
            "incomplete_content_replay",
        ),
        (
            TraceDetailReadUnavailable("read_budget_exceeded"),
            "temporarily_unavailable",
            "read_budget_exceeded",
        ),
        (RuntimeError("boom"), "temporarily_unavailable", None),
    ],
    ids=["u22", "u22b", "u24", "u24b", "u25", "u26", "u27", "u28"],
)
def test_reader_error_mapping(resolve, exc, status, code):
    resolve.reader.side_effect = exc
    with capture_logs() as logs:
        result = resolve()
    assert result.status == status
    assert result.retryable == (status == "temporarily_unavailable")
    resolved = [
        log for log in logs if log["event"] == "eval_log_source_navigation.resolved"
    ]
    assert len(resolved) == 1
    assert resolved[0]["error_code"] == code
    if type(exc) is RuntimeError:
        assert any(
            log["event"] == "eval_log_source_navigation.unexpected" and log["exc_info"]
            for log in logs
        )


def test_u23_missing_referenced_span(resolve):
    resolve.reader.return_value = _detail(_span(OTHER))
    assert resolve().status == "unavailable"


def test_u29_bounded_singleton_read(resolve):
    assert resolve(deadline_ms=1234).status == "ready"
    resolve.reader.assert_called_once_with(
        analytics=resolve.analytics,
        project_ids=[PROJECT],
        trace_id=TRACE,
        eval_config_ids_resolver=None,
        include_annotations=False,
        deadline_ms=1234,
        unique_project=True,
    )


@pytest.mark.parametrize("status", STATUSES)
def test_u30_wire_shape_and_null_untrusted_ids(status):
    result = navigation.SourceNavigation(
        status, "trace", PROJECT, TRACE, SPAN
    ).as_dict()
    assert set(result) == {
        "status",
        "kind",
        "project_id",
        "trace_id",
        "span_id",
        "retryable",
    }
    assert result["status"] == status
    assert result["retryable"] == (status == "temporarily_unavailable")
    assert result["project_id"] == (PROJECT if status == "ready" else None)
    assert result["trace_id"] == (TRACE if status == "ready" else None)
    assert result["span_id"] == (SPAN if status == "ready" else None)
    assert result["kind"] == ("trace" if status == "ready" else None)


def test_u32_stamp_does_not_require_config_row(resolve, monkeypatch):
    lookup = Mock(side_effect=AssertionError("stamped logs must not query config"))
    monkeypatch.setattr(CustomEvalConfig.all_objects, "filter", lookup)
    assert resolve().status == "ready"
    lookup.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"trace_id": ""},
        {"trace_id": 123},
        {"trace_id": []},
        {"span_id": " "},
        {"project_id": ""},
        {"source": 12},
        {"trace_id": TRACE.replace("-", "")},
    ],
)
def test_malformed_tokens_are_never_repaired(resolve, changes):
    result = resolve(_config(**changes))
    assert result.status in ("invalid_reference", "incomplete_reference")
    resolve.reader.assert_not_called()


@pytest.mark.parametrize(
    "detail,status",
    [
        (_detail(_span(), project_id=OTHER), "invalid_reference"),
        (_detail(_span(trace_id=OTHER)), "invalid_reference"),
        (_detail(_span(project_id=OTHER)), "invalid_reference"),
        (_detail(_span(), _span()), "ambiguous_reference"),
        (
            _detail(_span(ROOT, kind="conversation"), _span(parent=OTHER)),
            "ambiguous_reference",
        ),
    ],
)
def test_reader_identity_and_missing_parent_defenses(resolve, detail, status):
    resolve.reader.return_value = detail
    assert resolve().status == status


def test_unexpected_project_lookup_error_is_isolated(resolve):
    resolve.manager.filter.side_effect = RuntimeError("database unavailable")
    assert resolve().status == "temporarily_unavailable"
    resolve.reader.assert_not_called()


@pytest.mark.parametrize(
    "span_id", ["abcdef0123456789", "ABCDEF0123456789", "span_child-1", "s" * 255]
)
def test_collector_and_observe_string_ids_preserve_exact_identity(resolve, span_id):
    resolve.reader.return_value = _detail(_span(span_id))
    result = resolve(_config(span_id=span_id))
    assert result.status == "ready"
    assert result.span_id == span_id


@pytest.mark.parametrize(
    "span_id",
    [" abcdef0123456789", "abcdef0123456789 ", "a\nb", "a\x00b", "s" * 256, 123],
)
def test_invalid_span_string_is_not_repaired(resolve, span_id):
    assert resolve(_config(span_id=span_id)).status == "invalid_reference"
    resolve.reader.assert_not_called()


@pytest.fixture
def legacy(resolve, monkeypatch):
    """An older log with no recorded project; its eval config is never read."""
    scope = resolve.manager.filter.return_value
    scope.order_by.return_value.values_list.return_value = [PROJECT, ROOT]
    config_lookup = Mock(
        side_effect=AssertionError("older logs must not read the config")
    )
    monkeypatch.setattr(CustomEvalConfig.all_objects, "filter", config_lookup)
    resolve.config_lookup = config_lookup
    # By default the organization has no project outside the readable scope.
    organization_projects = Mock(return_value=[PROJECT, ROOT])
    monkeypatch.setattr(
        navigation, "organization_source_project_ids", organization_projects
    )
    resolve.organization_projects = organization_projects
    return resolve


def test_u33_legacy_log_reads_the_scoped_projects_not_the_config(legacy):
    result = legacy(_config(project_id=None))
    assert result.as_dict()["project_id"] == PROJECT
    legacy.config_lookup.assert_not_called()
    assert legacy.reader.call_args.kwargs["project_ids"] == [PROJECT, ROOT]
    assert legacy.reader.call_args.kwargs["unique_project"] is True


def test_u34_legacy_read_outside_the_scope_is_invalid(legacy):
    legacy.reader.return_value = _detail(_span(project_id=OTHER), project_id=OTHER)
    assert legacy(_config(project_id=None)).status == "invalid_reference"


def _outside_analytics(present):
    analytics = Mock()
    analytics.execute_ch_query.return_value = SimpleNamespace(
        data=[{"present": 1}] if present else []
    )
    return analytics


def test_u36_legacy_trace_also_held_outside_the_scope_is_unavailable(legacy):
    """A copy in a deleted or unreadable project: the readable copy may be a
    replay, so the older log is not attributed to it and nothing is read."""
    legacy.organization_projects.return_value = [PROJECT, ROOT, OTHER]
    analytics = _outside_analytics(present=True)
    result = legacy(_config(project_id=None), analytics=analytics)
    assert result.as_dict() == {
        "status": "unavailable",
        "kind": None,
        "project_id": None,
        "trace_id": None,
        "span_id": None,
        "retryable": False,
    }
    legacy.reader.assert_not_called()
    query, params = analytics.execute_ch_query.call_args.args
    assert "PREWHERE project_id IN %(outside_project_ids)s" in query
    assert "id = toUUID(%(trace_id)s)" in query
    assert params == {"outside_project_ids": (OTHER,), "trace_id": TRACE}


def test_u37_legacy_trace_only_in_readable_projects_still_resolves(legacy):
    legacy.organization_projects.return_value = [PROJECT, ROOT, OTHER]
    analytics = _outside_analytics(present=False)
    result = legacy(_config(project_id=None), analytics=analytics)
    assert result.status == "ready"
    assert legacy.reader.call_args.kwargs["project_ids"] == [PROJECT, ROOT]


def test_u38_legacy_log_past_the_organization_bound_fails_closed(legacy):
    legacy.organization_projects.return_value = None
    analytics = _outside_analytics(present=False)
    assert legacy(_config(project_id=None), analytics=analytics).status == (
        "unavailable"
    )
    analytics.execute_ch_query.assert_not_called()
    legacy.reader.assert_not_called()


def test_u39_recorded_project_never_reads_the_organization_scope(resolve, monkeypatch):
    organization_projects = Mock(side_effect=AssertionError("not for recorded logs"))
    monkeypatch.setattr(
        navigation, "organization_source_project_ids", organization_projects
    )
    assert resolve(_config()).status == "ready"
    organization_projects.assert_not_called()


def test_u35_legacy_log_with_no_readable_project_is_unavailable(legacy):
    scope = legacy.manager.filter.return_value
    scope.order_by.return_value.values_list.return_value = []
    assert legacy(_config(project_id=None)).status == "unavailable"
    legacy.reader.assert_not_called()


# Task 761: the typing-only changes in _classify_reference and the scope branch
# must keep every outcome. Each malformed shape maps to one exact status.
@pytest.mark.parametrize(
    "changes,status",
    [
        ({"trace_id": 123}, "invalid_reference"),
        ({"trace_id": 0}, "invalid_reference"),
        ({"trace_id": False}, "invalid_reference"),
        ({"trace_id": []}, "invalid_reference"),
        ({"trace_id": {}}, "invalid_reference"),
        ({"trace_id": "not-a-uuid"}, "invalid_reference"),
        ({"span_id": 123}, "invalid_reference"),
        ({"span_id": ["abcdef0123456789"]}, "invalid_reference"),
        ({"span_id": {"id": SPAN}}, "invalid_reference"),
        ({"span_id": True}, "invalid_reference"),
        ({"trace_id": None, "span_id": 123}, "incomplete_reference"),
        ({"trace_id": 123, "span_id": None}, "incomplete_reference"),
        ({"trace_id": None, "span_id": None}, "no_reference"),
        ({"custom_eval_config_id": 123}, "invalid_reference"),
        ({"custom_eval_config_id": []}, "invalid_reference"),
        ({"custom_eval_config_id": "not-a-uuid"}, "invalid_reference"),
        ({"custom_eval_config_id": ""}, "incomplete_reference"),
        ({"custom_eval_config_id": None}, "incomplete_reference"),
        ({"project_id": 123}, "invalid_reference"),
    ],
)
def test_t761_malformed_ids_keep_their_exact_status(resolve, changes, status):
    assert resolve(_config(**changes)).as_dict() == {
        "status": status,
        "kind": None,
        "project_id": None,
        "trace_id": None,
        "span_id": None,
        "retryable": False,
    }
    resolve.reader.assert_not_called()


def test_t761_upper_case_ids_are_read_lower_case(resolve):
    result = resolve(
        _config(
            trace_id=TRACE.upper(),
            custom_eval_config_id=CONFIG.upper(),
            project_id=PROJECT.upper(),
        )
    )
    assert result.status == "ready"
    assert result.trace_id == TRACE
    assert resolve.reader.call_args.kwargs["trace_id"] == TRACE
    assert resolve.manager.filter.return_value.filter.call_args.kwargs == {
        "id": PROJECT
    }


def test_t761_terminal_scope_result_skips_the_read(resolve):
    resolve.manager.filter.return_value.first.return_value = None
    assert resolve().as_dict()["status"] == "unavailable"
    resolve.reader.assert_not_called()
