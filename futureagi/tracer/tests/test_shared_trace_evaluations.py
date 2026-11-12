"""Shared trace links must retain the canonical detail reader's evaluations."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest

from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
    get_current_workspace,
    set_workspace_context,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.models.shared_link import AccessType
from tracer.models.trace import Trace
from tracer.services.clickhouse import v2
from tracer.services.clickhouse.v2 import query_service, trace_detail_reads
from tracer.services.clickhouse.v2.query_builders import trace_detail
from tracer.services.clickhouse.v2.span_reader import CHSpanReader
from tracer.views import shared_link

pytestmark = pytest.mark.unit


def _context():
    return (
        get_current_workspace(),
        get_current_organization(),
        get_current_user(),
    )


@pytest.fixture
def shared_trace(monkeypatch):
    """Run the real resolver/renderer; replace only storage and CH construction.

    The legacy span reader is also stubbed so the base fails on missing eval
    data, rather than attempting a connection or failing to deserialize spans.

    The fake link and CH stub carry what the shared trace read now touches:
    the link's access type and the trace-level tag read. Without either,
    ``_resolve_resource`` logs the error and returns None.
    """
    previous = _context()
    viewer = SimpleNamespace(workspace=object(), organization=object(), user=object())
    set_workspace_context(
        workspace=viewer.workspace, organization=viewer.organization, user=viewer.user
    )
    organization = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4(), organization=organization, is_default=False)
    link = SimpleNamespace(
        id=uuid4(),
        resource_type="trace",
        resource_id=str(uuid4()),
        organization=organization,
        workspace=workspace,
        created_by=SimpleNamespace(id=uuid4()),
        # A real SharedLink always has an access type, and the shared trace
        # read branches on it (221a6d47). A signed-out viewer can open only a
        # public link; a public link takes no annotation read.
        access_type=AccessType.PUBLIC,
    )
    project_id = str(uuid4())
    config_id = str(uuid4())
    foreign_config_id = str(uuid4())
    started = datetime(2026, 1, 1, tzinfo=UTC)
    spans = [
        {
            "id": span_id,
            "trace_id": link.resource_id,
            "project_id": project_id,
            "parent_span_id": parent_id,
            "name": span_id,
            "observation_type": "CHAIN",
            "start_time": started,
            "end_time": started,
            "status": "OK",
            "input": '{"prompt": "hello"}',
            "output": '{"response": "world"}',
            "metadata_json": "{}",
        }
        for span_id, parent_id in (("root", None), ("child", "root"))
    ]
    data = SimpleNamespace(
        project_id=project_id,
        spans=spans,
        eval_config_ids=(),
        evals=[
            {
                "span_id": span_id,
                "eval_config_id": cid,
                "output_float": score,
                "status": "completed",
                "eval_explanation": explanation,
            }
            for span_id, cid, score, explanation in (
                ("root", config_id, 0.0, "root failed"),
                ("child", config_id, 0.75, "child passed"),
                ("child", foreign_config_id, 0.99, "foreign result"),
            )
        ],
        annotations=[],
        eval_unavailable=False,
    )
    identity = Mock(return_value=project_id)
    monkeypatch.setattr(shared_link, "_get_shared_trace_project_id", identity)
    analytics = Mock()

    def read_trace_tags(query, params, **_options):
        # A trace with no PG row reads its trace-level tags from CH (5b372e75),
        # pinned to the trace's project. No live row: root-span tags are used.
        assert params == {
            "tag_project_ids": (project_id,),
            "tag_trace_id": link.resource_id,
        }
        return SimpleNamespace(data=[])

    analytics.execute_ch_query.side_effect = read_trace_tags
    monkeypatch.setattr(
        query_service, "V2AnalyticsQueryService", Mock(return_value=analytics)
    )

    project_manager = MagicMock()
    project_scope = project_manager.filter.return_value
    project_scope.filter.return_value = project_scope
    project_scope.values_list.return_value = [project_id]
    monkeypatch.setattr(Project, "no_workspace_objects", project_manager)

    trace_manager = Mock()
    trace_manager.filter.return_value.first.return_value = None
    monkeypatch.setattr(Trace, "objects", trace_manager)
    monkeypatch.setattr(Trace, "no_workspace_objects", trace_manager)

    config = SimpleNamespace(id=config_id, name="quality", eval_template=None)
    config_manager = Mock()
    config_manager.filter.return_value.select_related.return_value = [config]
    monkeypatch.setattr(CustomEvalConfig, "objects", config_manager)

    def read_detail(*, analytics, project_ids, trace_id, eval_config_ids_resolver):
        assert analytics is runtime.analytics
        assert project_ids == [project_id]
        assert trace_id == link.resource_id
        assert get_current_workspace() is link.workspace
        assert get_current_organization() is link.organization
        data.eval_config_ids = eval_config_ids_resolver(project_id)
        return data

    reader = Mock(side_effect=read_detail)
    monkeypatch.setattr(trace_detail_reads, "read_trace_detail", reader)
    canonical = Mock(wraps=trace_detail.retrieve_trace_detail_ch)
    monkeypatch.setattr(trace_detail, "retrieve_trace_detail_ch", canonical)

    legacy_reader = Mock()
    legacy_reader.list_by_trace.return_value = spans
    legacy_context = MagicMock()
    legacy_context.__enter__.return_value = legacy_reader
    monkeypatch.setattr(v2, "get_reader", Mock(return_value=legacy_context))
    monkeypatch.setattr(CHSpanReader, "to_django_dict", staticmethod(lambda span: span))

    runtime = SimpleNamespace(
        link=link,
        data=data,
        project_id=project_id,
        config_id=config_id,
        foreign_config_id=foreign_config_id,
        identity=identity,
        analytics=analytics,
        project_scope=project_scope,
        config_manager=config_manager,
        reader=reader,
        canonical=canonical,
        legacy_reader=legacy_reader,
    )
    try:
        yield runtime
    finally:
        set_workspace_context(
            workspace=previous[0], organization=previous[1], user=previous[2]
        )


@pytest.mark.parametrize("viewer", ["signed_out", "other_workspace"])
def test_shared_trace_keeps_project_authorized_root_and_child_evals(
    shared_trace, viewer
):
    runtime = shared_trace
    if viewer == "signed_out":
        set_workspace_context(workspace=None, organization=None, user=None)
    before = _context()

    detail = shared_link._resolve_resource(runtime.link)

    assert detail is not None
    root = detail["observation_spans"][0]
    child = root["children"][0]
    assert [row["score"] for row in root.get("eval_scores", [])] == [0.0]
    assert [row["score"] for row in child.get("eval_scores", [])] == [75.0]
    assert child["eval_scores"][0]["explanation"] == "child passed"
    assert child["eval_scores"][0]["status"] == "completed"
    assert child["eval_scores"][0]["eval_config_id"] == runtime.config_id
    assert runtime.foreign_config_id not in {
        row["eval_config_id"] for entry in (root, child) for row in entry["eval_scores"]
    }
    assert detail["trace"]["id"] == runtime.link.resource_id
    assert detail["trace"]["project_id"] == runtime.project_id
    assert detail["trace"]["created_at"] == str(runtime.data.spans[0]["start_time"])
    assert detail["summary"]["total_spans"] == 2
    assert "graph" in detail
    runtime.identity.assert_called_once_with(
        runtime.link.resource_id, runtime.link.organization, runtime.link.workspace
    )
    runtime.config_manager.filter.assert_called_once_with(
        project_id=runtime.project_id, deleted=False
    )
    runtime.project_scope.filter.assert_called_once_with(id=runtime.project_id)
    runtime.canonical.assert_called_once()
    view, request, trace_id, analytics = runtime.canonical.call_args.args
    assert request.organization is runtime.link.organization
    assert request.workspace is runtime.link.workspace
    assert request.user is runtime.link.created_by
    assert view.request is request
    assert view.kwargs == {"pk": runtime.link.resource_id}
    assert view.format_kwarg is None
    assert trace_id == runtime.link.resource_id
    assert analytics is runtime.analytics
    assert runtime.canonical.call_args.kwargs == {"project_id": runtime.project_id}
    runtime.legacy_reader.list_by_trace.assert_not_called()
    assert _context() == before


def test_shared_trace_preserves_eval_unavailable_rollups(shared_trace):
    runtime = shared_trace
    runtime.data.evals = []
    runtime.data.eval_unavailable = True
    before = _context()

    detail = shared_link._resolve_resource(runtime.link)

    assert detail is not None
    root = detail["observation_spans"][0]
    child = root["children"][0]
    for entry, scope in ((root, "trace"), (child, "span")):
        assert entry.get("eval_rollup") == {"scope": scope, "evals": [], "error": True}
        assert entry["eval_scores"] == []
    assert _context() == before


def test_shared_trace_with_no_configs_keeps_canonical_empty_evals(shared_trace):
    runtime = shared_trace
    runtime.config_manager.filter.return_value.select_related.return_value = []
    runtime.data.evals = []
    before = _context()

    detail = shared_link._resolve_resource(runtime.link)

    assert detail is not None
    root = detail["observation_spans"][0]
    child = root["children"][0]
    for entry in (root, child):
        assert entry["eval_scores"] == []
        assert entry["eval_rollup"]["evals"] == []
        assert not entry["eval_rollup"].get("error", False)
    assert _context() == before


def test_shared_trace_restores_viewer_context_when_canonical_read_fails(shared_trace):
    runtime = shared_trace
    runtime.reader.side_effect = RuntimeError("trace detail unavailable")
    before = _context()

    assert shared_link._resolve_resource(runtime.link) is None

    runtime.reader.assert_called_once()
    runtime.legacy_reader.list_by_trace.assert_not_called()
    assert _context() == before


def test_shared_trace_outside_link_scope_never_reads_detail(shared_trace):
    runtime = shared_trace
    runtime.identity.return_value = None
    before = _context()

    assert shared_link._resolve_resource(runtime.link) is None

    runtime.canonical.assert_not_called()
    runtime.reader.assert_not_called()
    runtime.legacy_reader.list_by_trace.assert_not_called()
    assert _context() == before
