"""Project-collision controls at the real trace-span reader boundaries.

No ClickHouse or provider calls: the fake returns the foreign project's copy
of the same trace whenever the caller omits or changes its authorized project.
Like test_eval_context_project_scoping, ORM lookups are stubbed, not the code
that decides which project to pass to the reader.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

TRACE_ID = UUID("00000000-0000-4000-8000-000000000001")
PROJECT_ID = UUID("00000000-0000-4000-8000-000000000002")
FOREIGN_PROJECT_ID = UUID("00000000-0000-4000-8000-000000000003")


def _span(project_id, name):
    return SimpleNamespace(
        id="colliding-span",
        trace_id=str(TRACE_ID),
        project_id=str(project_id),
        name=name,
        observation_type="llm",
        parent_span_id="",
        status="ERROR",
        status_message="",
        input=name,
        output=name,
        metadata={},
        span_events=[],
        model="test-model",
        latency_ms=1,
        total_tokens=2,
        cost=0,
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class CollisionReader:
    def __init__(self):
        self.calls = []
        self.project_calls = []
        self.resolved_project = str(PROJECT_ID)
        self.owned = _span(PROJECT_ID, "owned-content")
        self.foreign = _span(FOREIGN_PROJECT_ID, "foreign-content")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def close(self):
        pass

    def newest_trace_project(self, trace_id, project_ids):
        self.project_calls.append((str(trace_id), list(project_ids)))
        if self.resolved_project in project_ids:
            return self.resolved_project
        return None

    def list_by_trace(self, trace_id, *, project_id=None, include_heavy=True):
        self.calls.append((str(trace_id), project_id, include_heavy))
        assert str(trace_id) == str(TRACE_ID)
        if str(project_id) == str(PROJECT_ID):
            return [self.owned]
        return [self.foreign]


@pytest.fixture
def scope(monkeypatch):
    from tracer.models.project import Project
    from tracer.models.trace import Trace

    organization = SimpleNamespace(id="organization")
    trace = SimpleNamespace(
        id=TRACE_ID,
        project_id=PROJECT_ID,
        project=SimpleNamespace(name="authorized-project"),
        created_at=None,
    )
    query = Mock()
    query.select_related.return_value = query
    query.filter.return_value = query
    query.get.return_value = trace
    query.first.return_value = trace
    monkeypatch.setattr(Trace, "objects", query)
    projects = Mock()
    projects.filter.return_value.values_list.return_value = [PROJECT_ID]
    monkeypatch.setattr(Project, "objects", projects)
    monkeypatch.setattr(
        "tfc.middleware.workspace_context.get_current_organization",
        lambda: organization,
    )
    monkeypatch.setattr(
        "tfc.middleware.workspace_context.get_current_workspace", lambda: None
    )
    monkeypatch.setattr(
        "tracer.services.analysis_result_binding.current_attempt", lambda: None
    )
    return SimpleNamespace(
        trace=trace,
        query=query,
        projects=projects,
        organization=organization,
        context=SimpleNamespace(organization=organization, background_analysis=None),
    )


@pytest.fixture
def reader(monkeypatch):
    reader = CollisionReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    # error_analysis imports its reader at module scope.
    monkeypatch.setattr("tracer.queries.error_analysis.get_reader", lambda: reader)
    return reader


def _assert_scoped(reader, *, include_heavy=True):
    assert reader.calls == [(str(TRACE_ID), str(PROJECT_ID), include_heavy)]


def test_graph_import_excludes_coincident_foreign_trace(monkeypatch, scope, reader):
    from django.core.exceptions import ValidationError

    from agent_playground.services import trace_to_graph

    seen = []

    def convert(span):
        seen.append(span.name)
        return {"observation_type": "span"}

    monkeypatch.setattr(trace_to_graph, "_chspan_to_legacy_dict", convert)
    monkeypatch.setattr(
        trace_to_graph, "_legacy_spans_from_postgres", lambda *a, **kw: []
    )
    with pytest.raises(ValidationError, match="no LLM spans"):
        trace_to_graph.convert_trace_to_graph(
            scope.trace, None, scope.organization, None
        )
    assert seen == ["owned-content"]
    _assert_scoped(reader)


def test_graph_pg_fallback_keeps_authorized_project(monkeypatch, scope):
    from django.core.exceptions import ValidationError

    from agent_playground.services import trace_to_graph

    monkeypatch.setattr(
        trace_to_graph, "_legacy_spans_from_clickhouse", lambda *a, **kw: []
    )
    spans = Mock()
    spans.order_by.return_value.values.return_value = [{"observation_type": "span"}]
    query = Mock(return_value=spans)
    monkeypatch.setattr(
        trace_to_graph.ObservationSpan.no_workspace_objects, "filter", query
    )
    with pytest.raises(ValidationError, match="no LLM spans"):
        trace_to_graph.convert_trace_to_graph(
            scope.trace, None, scope.organization, None
        )
    query.assert_called_once_with(
        trace_id=str(TRACE_ID), project_id=str(PROJECT_ID), deleted=False
    )


def test_explore_trace_excludes_coincident_foreign_trace(monkeypatch, scope, reader):
    from ai_tools.tools.tracing.explore_trace import ExploreTraceInput, ExploreTraceTool

    tool = ExploreTraceTool()
    monkeypatch.setattr(tool, "_run_chauffeur", lambda *a, **kw: {"sub_flows": []})
    result = tool.execute(ExploreTraceInput(trace_id=TRACE_ID), scope.context)
    assert "owned-content" in result.content
    assert "foreign-content" not in result.content
    _assert_scoped(reader)
    assert scope.query.get.call_args.kwargs == {
        "id": TRACE_ID,
        "project__organization": scope.organization,
    }


def test_read_trace_span_excludes_coincident_foreign_trace(scope, reader):
    from ai_tools.tools.tracing.read_trace_span import (
        ReadTraceSpanInput,
        ReadTraceSpanTool,
    )

    result = ReadTraceSpanTool().execute(
        ReadTraceSpanInput(trace_id=TRACE_ID, span_id="colliding-span"), scope.context
    )
    assert "owned-content" in result.content
    assert "foreign-content" not in result.content
    _assert_scoped(reader)
    assert scope.query.get.call_args.kwargs == {
        "id": TRACE_ID,
        "project__organization": scope.organization,
    }


def test_web_explorer_pins_one_project_not_all_org_projects(monkeypatch, scope, reader):
    from ai_tools.tools.web.trace_explorer import TraceExplorerTool
    from tracer.models.project import Project

    # Both projects may be in the same organization. Post-filtering by org is
    # insufficient: only the authorized trace's project is the requested copy.
    projects = Mock()
    projects.filter.return_value.values_list.return_value = [
        PROJECT_ID,
        FOREIGN_PROJECT_ID,
    ]
    monkeypatch.setattr(Project, "objects", projects)
    result = TraceExplorerTool()._list_trace_spans(str(TRACE_ID), 10)
    assert "owned-content" in result.content
    assert "foreign-content" not in result.content
    _assert_scoped(reader)
    assert projects.filter.call_args.kwargs["organization"] is scope.organization
    assert reader.project_calls == [
        (str(TRACE_ID), [str(PROJECT_ID), str(FOREIGN_PROJECT_ID)])
    ]


def test_web_explorer_resolves_ch_only_trace(scope, reader):
    from ai_tools.tools.web.trace_explorer import TraceExplorerTool

    scope.query.first.return_value = None
    result = TraceExplorerTool()._list_trace_spans(str(TRACE_ID), 10)
    assert "owned-content" in result.content
    assert "foreign-content" not in result.content
    _assert_scoped(reader)
    scope.query.filter.assert_not_called()


def test_web_explorer_empty_project_scope_never_reads(scope, reader):
    from ai_tools.tools.web.trace_explorer import TraceExplorerTool

    scope.projects.filter.return_value.values_list.return_value = []
    result = TraceExplorerTool()._list_trace_spans(str(TRACE_ID), 10)
    assert "foreign-content" not in result.content
    assert reader.calls == []
    assert reader.project_calls == []


def test_web_explorer_rejects_trace_outside_project_scope(scope, reader):
    from ai_tools.tools.web.trace_explorer import TraceExplorerTool

    reader.resolved_project = str(FOREIGN_PROJECT_ID)
    result = TraceExplorerTool()._list_trace_spans(str(TRACE_ID), 10)
    assert "foreign-content" not in result.content
    assert reader.calls == []
    assert reader.project_calls == [(str(TRACE_ID), [str(PROJECT_ID)])]


def test_imagine_fetch_excludes_coincident_foreign_trace(monkeypatch, scope, reader):
    from tfc.temporal.imagine.activities import fetch_trace_data
    from tfc.temporal.imagine.types import FetchTraceInput

    def sync_as_async(function):
        async def invoke(*args, **kwargs):
            return function(*args, **kwargs)

        return invoke

    monkeypatch.setattr("channels.db.database_sync_to_async", sync_as_async)
    result = asyncio.run(
        fetch_trace_data(
            FetchTraceInput(
                trace_id=str(TRACE_ID),
                org_id=scope.organization.id,
                project_id=str(PROJECT_ID),
            )
        )
    )
    assert "owned-content" in result
    assert "foreign-content" not in result
    _assert_scoped(reader)


def test_error_analysis_all_spans_resolves_authorized_project(scope, reader):
    from tracer.queries.error_analysis import TraceErrorAnalysisDB

    spans = TraceErrorAnalysisDB().get_all_spans(str(TRACE_ID))
    assert spans == [reader.owned]
    _assert_scoped(reader)
    assert scope.query.filter.call_args.kwargs["project__organization"] is (
        scope.organization
    )


def test_error_analysis_error_spans_excludes_foreign_trace(scope, reader):
    from tracer.queries.error_analysis import TraceErrorAnalysisDB

    spans = TraceErrorAnalysisDB().get_error_spans_for_trace(scope.trace)
    assert spans == [reader.owned]
    _assert_scoped(reader)


def test_eval_loader_excludes_coincident_foreign_trace(monkeypatch, reader):
    from tracer.services.clickhouse.v2 import eval_loader

    monkeypatch.setattr(eval_loader, "_construct_from_chspan", lambda span: span)
    with eval_loader.eval_read_source("clickhouse"):
        spans = eval_loader.filter_observation_spans_by_trace(
            str(TRACE_ID), project_id=str(PROJECT_ID)
        )
    assert spans == [reader.owned]
    _assert_scoped(reader, include_heavy=False)


@pytest.mark.parametrize("project_id", [None, ""], ids=["none", "empty"])
def test_eval_loader_missing_project_never_reads_foreign_spans(
    monkeypatch, reader, project_id
):
    from tracer.services.clickhouse.v2 import eval_loader

    monkeypatch.setattr(eval_loader, "_construct_from_chspan", lambda span: span)
    with eval_loader.eval_read_source("clickhouse"):
        spans = eval_loader.filter_observation_spans_by_trace(
            str(TRACE_ID), project_id=project_id
        )
    assert spans == []
    assert reader.calls == []


@pytest.mark.parametrize("fallback", [False, True])
def test_eval_loader_pg_paths_keep_project_scope(monkeypatch, reader, fallback):
    from tracer.models.observation_span import ObservationSpan
    from tracer.services.clickhouse.v2 import eval_loader

    query = Mock(return_value=[reader.owned])
    monkeypatch.setattr(ObservationSpan.objects, "filter", query)
    monkeypatch.setattr(
        eval_loader, "_read_source", lambda: "clickhouse" if fallback else "postgres"
    )
    monkeypatch.setattr(eval_loader, "_forced_clickhouse", lambda: False)
    if fallback:
        monkeypatch.setattr(
            "tracer.services.clickhouse.v2.get_reader", Mock(side_effect=TimeoutError)
        )
    assert eval_loader.filter_observation_spans_by_trace(
        str(TRACE_ID), project_id=str(PROJECT_ID)
    ) == [reader.owned]
    query.assert_called_once_with(
        trace_id=str(TRACE_ID), project_id=str(PROJECT_ID), deleted=False
    )


@pytest.mark.parametrize("project_id", [None, ""], ids=["none", "empty"])
def test_eval_loader_missing_project_skips_pg(monkeypatch, reader, project_id):
    from tracer.models.observation_span import ObservationSpan
    from tracer.services.clickhouse.v2 import eval_loader

    query = Mock(return_value=[reader.foreign])
    monkeypatch.setattr(ObservationSpan.objects, "filter", query)
    with eval_loader.eval_read_source("postgres"):
        assert (
            eval_loader.filter_observation_spans_by_trace(
                str(TRACE_ID), project_id=project_id
            )
            == []
        )
    query.assert_not_called()
    assert reader.calls == []


def test_trace_context_excludes_coincident_foreign_trace(scope, reader):
    from tracer.utils.eval import build_trace_context

    context = build_trace_context(scope.trace)
    assert [span["name"] for span in context["spans"]] == ["owned-content"]
    _assert_scoped(reader)


@pytest.mark.parametrize("project_id", [None, ""], ids=["none", "empty"])
def test_trace_context_missing_project_never_reads_foreign_spans(reader, project_id):
    from tracer.utils.eval import build_trace_context

    context = build_trace_context(SimpleNamespace(id=TRACE_ID, project_id=project_id))
    assert context["spans"] == []
    assert context["span_count"] == 0
    assert reader.calls == []


@pytest.mark.parametrize("entry", ["web", "all", "errors"])
def test_unresolved_trace_never_reaches_reader(scope, reader, entry):
    from ai_tools.tools.web.trace_explorer import TraceExplorerTool
    from tracer.queries.error_analysis import TraceErrorAnalysisDB

    scope.query.first.return_value = None
    reader.resolved_project = None
    if entry == "web":
        result = TraceExplorerTool()._list_trace_spans(str(TRACE_ID), 10)
        assert "foreign-content" not in result.content
    elif entry == "all":
        assert TraceErrorAnalysisDB().get_all_spans(str(TRACE_ID)) == []
    else:
        assert TraceErrorAnalysisDB().get_error_spans_for_trace(str(TRACE_ID)) == []
    assert reader.calls == []


def test_error_analysis_attempt_uses_bound_project(monkeypatch, scope, reader):
    from tracer.queries.error_analysis import TraceErrorAnalysisDB

    monkeypatch.setattr(
        "tracer.services.analysis_result_binding.current_attempt",
        lambda: (str(PROJECT_ID), str(TRACE_ID), "generation"),
    )
    assert TraceErrorAnalysisDB().get_all_spans(str(TRACE_ID)) == [reader.owned]
    _assert_scoped(reader)
    scope.query.filter.assert_not_called()


def test_error_analysis_attempt_cannot_change_trace(monkeypatch, scope, reader):
    from tracer.queries.error_analysis import TraceErrorAnalysisDB

    monkeypatch.setattr(
        "tracer.services.analysis_result_binding.current_attempt",
        lambda: (str(PROJECT_ID), "another-trace", "generation"),
    )
    assert TraceErrorAnalysisDB().get_all_spans(str(TRACE_ID)) == []
    assert reader.calls == []
    scope.query.filter.assert_not_called()


@pytest.mark.parametrize("entry", ["web", "all", "errors"])
def test_missing_organization_never_reaches_reader(monkeypatch, scope, reader, entry):
    from ai_tools.tools.web.trace_explorer import TraceExplorerTool
    from tracer.queries.error_analysis import TraceErrorAnalysisDB

    monkeypatch.setattr(
        "tfc.middleware.workspace_context.get_current_organization", lambda: None
    )
    if entry == "web":
        TraceExplorerTool()._list_trace_spans(str(TRACE_ID), 10)
    elif entry == "all":
        assert TraceErrorAnalysisDB().get_all_spans(str(TRACE_ID)) == []
    else:
        assert TraceErrorAnalysisDB().get_error_spans_for_trace(str(TRACE_ID)) == []
    assert reader.calls == []
    scope.query.filter.assert_not_called()


def _real_reader():
    from tracer.services.clickhouse.v2.span_reader import CHSpanReader

    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = Mock()
    reader._client.query.return_value.result_rows = []
    return reader


def test_list_by_trace_requires_project_keyword():
    reader = _real_reader()
    with pytest.raises(TypeError):
        reader.list_by_trace(str(TRACE_ID))
    reader._client.query.assert_not_called()


@pytest.mark.parametrize("project_id", [None, ""], ids=["none", "empty"])
def test_list_by_trace_rejects_empty_project_before_query(project_id):
    reader = _real_reader()
    with pytest.raises(ValueError, match="project_id"):
        reader.list_by_trace(str(TRACE_ID), project_id=project_id)
    reader._client.query.assert_not_called()


@pytest.mark.parametrize("include_heavy", [True, False])
def test_list_by_trace_keeps_project_predicate_and_oom_guards(include_heavy):
    reader = _real_reader()
    assert (
        reader.list_by_trace(
            str(TRACE_ID), project_id=str(PROJECT_ID), include_heavy=include_heavy
        )
        == []
    )
    args, kwargs = reader._client.query.call_args
    assert "project_id = %(pid)s" in args[0]
    assert "trace_id = %(trace_id)s" in args[0]
    assert "ORDER BY start_time, id" in args[0]
    assert "is_deleted = 0" not in args[0]
    assert kwargs["parameters"] == {"trace_id": str(TRACE_ID), "pid": str(PROJECT_ID)}
    assert kwargs["settings"]["use_skip_indexes_if_final"] == 1
