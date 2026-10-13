"""TH-4804 U01-U32: provenance, bounded reads, and terminal wire states."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from model_hub.services import eval_log_source_navigation as navigation
from model_hub.utils.api_log_config import parse_api_log_config
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
            analytics=sentinel_analytics,
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
        (TraceDetailNotFound(), "unavailable", None),
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
    ids=["u22", "u24", "u25", "u26", "u27", "u28"],
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
    monkeypatch.setattr(navigation.CustomEvalConfig.all_objects, "filter", lookup)
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
