"""Project scoping through the real background agent and registered tools."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, PropertyMock

import pytest

from ai_tools.base import ToolContext
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail
from tracer.tasks.error_analysis import _falcon_analyze_single_trace
from tracer.tests._falcon_analysis import (
    finding_params,
    install_reader,
    install_tool_stream,
    read_call,
    score_params,
)
from tracer.tests.test_falcon_error_analysis import (
    falcon_boundaries as falcon_boundaries,
    global_analysis_skill as global_analysis_skill,
)

pytestmark = pytest.mark.requires_ee


@pytest.fixture(params=["organization", "workspace", "project"])
def other_project(request, project):
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from tracer.models.project import Project

    org = project.organization
    workspace = project.workspace
    if request.param == "organization":
        org = Organization.objects.create(name="Other organization")
    if request.param != "project":
        user = User.objects.create_user(
            email=f"other-{uuid.uuid4().hex}@example.invalid", organization=org
        )
        workspace = Workspace.objects.create(
            name="Other workspace", organization=org, created_by=user
        )
    return Project.objects.create(
        name="Other project",
        organization=org,
        workspace=workspace,
        model_type=project.model_type,
        trace_type=project.trace_type,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("explicit_analysis_id", [False, True])
@pytest.mark.parametrize("existing_analysis", [False, True])
def test_background_scoring_never_updates_foreign_analysis(
    project,
    other_project,
    falcon_boundaries,
    monkeypatch,
    explicit_analysis_id,
    existing_analysis,
):
    trace_id = str(uuid.uuid4())
    own = None
    if existing_analysis:
        own = TraceErrorAnalysis.objects.create(trace_id=trace_id, project=project)
    foreign = TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=other_project, insights="Other project analysis"
    )
    before = TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get()
    install_reader(monkeypatch, project, trace_id)
    ingest = Mock()
    monkeypatch.setattr(
        "tracer.queries.error_analysis.TraceErrorAnalysisDB.ingest_trace_error_embeddings",
        ingest,
    )
    monkeypatch.setattr(
        "tracer.tasks.error_analysis.cluster_project_errors.delay", Mock()
    )
    params = score_params(trace_id)
    if explicit_analysis_id:
        params["analysis_id"] = str(foreign.id)
    calls = install_tool_stream(
        monkeypatch, read_call(trace_id), ("submit_trace_scores", params)
    )

    result = _falcon_analyze_single_trace(trace_id, str(project.id))

    assert result["success"] is (not explicit_analysis_id)
    assert len(calls) == 3
    assert TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get() == before
    if explicit_analysis_id:
        assert "failed to save" in result["error"]
        assert "Not Found" in calls[-1][-1]["content"]
        if own:
            own.refresh_from_db()
            assert own.overall_score is None
        else:
            assert not TraceErrorAnalysis.objects.filter(
                trace_id=trace_id, project=project
            ).exists()
        return
    analysis = TraceErrorAnalysis.objects.get(trace_id=trace_id, project=project)
    assert analysis.overall_score == 2.0
    assert analysis.insights == params["insights"]
    if own:
        assert analysis.pk == own.pk


@pytest.mark.django_db(transaction=True)
def test_background_scoring_resolves_colliding_root_in_requesting_project(
    project,
    other_project,
    falcon_boundaries,
    monkeypatch,
):
    trace_id = str(uuid.uuid4())
    reader = install_reader(monkeypatch, project, trace_id, foreign=other_project)
    monkeypatch.setattr(
        "tracer.queries.error_analysis.TraceErrorAnalysisDB.ingest_trace_error_embeddings",
        Mock(),
    )
    monkeypatch.setattr(
        "tracer.tasks.error_analysis.cluster_project_errors.delay", Mock()
    )
    install_tool_stream(
        monkeypatch, read_call(trace_id), ("submit_trace_scores", score_params(trace_id))
    )

    result = _falcon_analyze_single_trace(trace_id, str(project.id))

    assert result["success"] is True
    analysis = TraceErrorAnalysis.objects.get(trace_id=trace_id, project=project)
    assert analysis.overall_score == 2.0
    assert not TraceErrorAnalysis.objects.filter(project=other_project).exists()
    for call in reader.root_ids_by_trace_ids.call_args_list:
        assert call.kwargs["project_ids"] == [str(project.id)]


@pytest.mark.django_db(transaction=True)
def test_failed_stream_after_scoring_does_not_publish_completion(
    project,
    falcon_boundaries,
    monkeypatch,
):
    trace_id = str(uuid.uuid4())
    install_reader(monkeypatch, project, trace_id)
    ingest = Mock()
    cluster = Mock()
    monkeypatch.setattr(
        "tracer.queries.error_analysis.TraceErrorAnalysisDB.ingest_trace_error_embeddings",
        ingest,
    )
    monkeypatch.setattr(
        "tracer.tasks.error_analysis.cluster_project_errors.delay", cluster
    )
    install_tool_stream(
        monkeypatch,
        read_call(trace_id),
        ("submit_trace_scores", score_params(trace_id)),
        fail_after_tool=True,
    )

    result = _falcon_analyze_single_trace(trace_id, str(project.id))

    assert result["success"] is False
    assert "Interrupted after scoring" in result["error"]
    clear, failed, clickhouse = falcon_boundaries
    clear.assert_not_called()
    failed.assert_called_once_with(trace_id, project_id=str(project.id))
    ingest.assert_not_called()
    cluster.assert_not_called()
    clickhouse.return_value.insert.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_queued_activity_keeps_scored_interruption_failed_in_feed(
    project, queued_trace_pair, monkeypatch
):
    from agentic_eval.core.embeddings.embedding_manager import model_manager
    from tracer.models.trace_error_analysis import ErrorClusterTraces
    from tracer.queries import deep_analysis_state, feed
    from tracer.tasks.error_analysis import analyze_traces_on_demand
    from tracer.tests.test_error_analysis import make_feed_group

    assigned, _, args, clustered, insert = queued_trace_pair
    project_id = str(project.id)
    group = make_feed_group(project, "queued-score")
    ErrorClusterTraces.objects.create(cluster=group, trace_id=assigned)
    root = f"root-{assigned}"
    install_tool_stream(
        monkeypatch,
        read_call(assigned, root),
        ("submit_trace_finding", finding_params(assigned) | {"location_spans": [root]}),
        ("submit_trace_scores", score_params(assigned)),
        fail_after_tool=True,
    )
    # Fail at provider access, not by replacing the worker's ingestion logic.
    provider = PropertyMock(side_effect=AssertionError("Unexpected embedding request"))
    monkeypatch.setattr(type(model_manager), "text_model", provider)
    analyze_traces_on_demand(*args)
    saved = TraceErrorAnalysis.objects.get(trace_id=assigned, project=project)
    assert saved.overall_score == 2.0
    assert TraceErrorDetail.objects.get(analysis=saved).location_spans == [root]
    status = feed.get_deep_analysis(group.cluster_id, assigned, [project_id])
    assert status.status == "failed"
    assert not deep_analysis_state.is_running(assigned, project_id=project_id)
    assert not status.recommendations
    provider.assert_not_called()
    clustered.assert_not_called()
    insert.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_background_cannot_invoke_unscoped_registry_tools(
    project, falcon_boundaries, monkeypatch
):
    from ai_tools.registry import registry

    tool = registry.get("analyze_project_traces")
    execute = Mock()
    monkeypatch.setattr(tool, "execute", execute)
    calls = install_tool_stream(
        monkeypatch, ("analyze_project_traces", {"project_id": str(project.id)})
    )

    result = _falcon_analyze_single_trace(str(uuid.uuid4()), str(project.id))

    assert result["success"] is False
    assert "did not inspect" in result["error"]
    assert len(calls) == 2
    assert any(
        message.get("role") == "tool"
        and "Tool 'analyze_project_traces' not found" in message["content"]
        for message in calls[1]
    )
    execute.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "tool_name", ["read_trace_span", "explore_trace_legacy", "submit_trace_finding"]
)
def test_analysis_tools_use_project_scoped_ch_and_analysis_rows(
    project,
    other_project,
    monkeypatch,
    tool_name,
):
    from ai_tools.registry import registry

    trace_id = str(uuid.uuid4())
    foreign = TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=other_project
    )
    before = TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get()
    context = ToolContext(
        user=project.workspace.created_by,
        organization=project.organization,
        workspace=project.workspace,
    )
    context.project = project
    reader = install_reader(monkeypatch, project, trace_id, foreign=other_project)
    if tool_name == "explore_trace_legacy":
        reader.list_by_trace.return_value = []
    params = {"trace_id": trace_id}
    if tool_name == "read_trace_span":
        params["span_id"] = "shared-span"
    elif tool_name == "submit_trace_finding":
        params = finding_params(trace_id) | {"analysis_id": str(foreign.id)}

    result = registry.get(tool_name).run(params, context)

    assert not result.is_error, result.content
    assert TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get() == before
    reader.root_ids_by_trace_ids.assert_called_once_with(
        [trace_id], project_ids=[str(project.id)]
    )
    if tool_name == "submit_trace_finding":
        own = TraceErrorAnalysis.objects.get(project=project, trace_id=trace_id)
        assert own.total_errors == 1
        assert TraceErrorDetail.objects.get(analysis=own).description == "Own finding"
        assert not TraceErrorDetail.objects.filter(analysis=foreign).exists()
    else:
        reader.list_by_trace.assert_called_once_with(
            trace_id, project_id=str(project.id)
        )
        if tool_name == "read_trace_span":
            assert "Own evidence" in result.content


@pytest.mark.django_db
def test_embedding_ingestion_selects_only_requesting_project(
    project, other_project, monkeypatch
):
    from agentic_eval.core.embeddings.embedding_manager import (
        EmbeddingManager,
        model_manager,
    )
    from ee.agenthub.traceerroragent.error_cluster import ErrorEmbeddingClusterer
    from tracer.queries.error_analysis import TraceErrorAnalysisDB

    trace_id = str(uuid.uuid4())
    own = TraceErrorAnalysis.objects.create(trace_id=trace_id, project=project)
    foreign = TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=other_project
    )
    detail = TraceErrorDetail.objects.create(
        analysis=own, error_id="E001", category="Own", description="Own evidence"
    )
    TraceErrorDetail.objects.create(
        analysis=foreign, error_id="E001", category="Foreign", description="Private"
    )
    embed = Mock(return_value=[0.1, 0.2])
    monkeypatch.setattr(type(model_manager), "text_model", property(lambda self: embed))
    db_client = MagicMock()
    monkeypatch.setattr(
        EmbeddingManager, "__init__", lambda self: setattr(self, "db_client", db_client)
    )
    monkeypatch.setattr(EmbeddingManager, "close", Mock())
    monkeypatch.setattr(
        TraceErrorAnalysisDB, "_embedding_exists_for_detail", lambda *args: False
    )
    build = Mock(side_effect=lambda row: row.description)
    monkeypatch.setattr(
        ErrorEmbeddingClusterer, "_build_embedding_text_from_detail", build
    )

    count = TraceErrorAnalysisDB().ingest_trace_error_embeddings(
        trace_id,
        project_id=str(project.id),
        organization_id=str(project.organization_id),
    )

    assert count == 1
    embed.assert_called_once_with("Own evidence")
    assert build.call_args.args[0].id == detail.id
    assert db_client.bulk_upsert_vectors.call_args.kwargs["eval_id"] == str(project.id)
    assert db_client.bulk_upsert_vectors.call_args.kwargs["unique_keys"] == [
        "project_id",
        "error_detail_pk",
    ]


def test_project_markers_do_not_collide_and_failure_wins_over_partial_analysis():
    from django.core.cache import cache

    from tracer.queries import deep_analysis_state as state

    trace_id = str(uuid.uuid4())
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        assert state.set_running(trace_id, project_id=first)
        assert state.set_running(trace_id, project_id=second)
        state.set_failed(trace_id, project_id=first)
        assert state.status(trace_id, project_id=first, has_analysis=True) == "failed"
        assert (
            state.status(trace_id, project_id=second, has_analysis=False) == "running"
        )
        state.clear(trace_id, project_id=second)
        assert state.status(trace_id, project_id=first, has_analysis=False) == "failed"
    finally:
        cache.clear()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "missing_scope", ["organization", "workspace", "foreign_project"]
)
def test_trace_access_fails_closed_before_clickhouse(
    project, other_project, monkeypatch, missing_scope
):
    from ai_tools.tools.tracing.access import get_trace_project

    context = ToolContext(
        user=project.workspace.created_by,
        organization=project.organization,
        workspace=project.workspace,
        project=project,
    )
    if missing_scope == "foreign_project":
        context.organization = other_project.organization
        context.workspace = other_project.workspace
        # Even in the same workspace, bind an unavailable project explicitly.
        context.project = SimpleNamespace(pk=uuid.uuid4())
    else:
        setattr(context, missing_scope, None)
    reader = Mock()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", reader)

    assert get_trace_project(str(uuid.uuid4()), context) is None
    reader.assert_not_called()


@pytest.mark.django_db
def test_dispatch_preserves_default_workspace_legacy_project(project, monkeypatch):
    from ai_tools.tools.tracing.analyze_project_traces import AnalyzeProjectTracesTool

    context = ToolContext(
        user=project.workspace.created_by,
        organization=project.organization,
        workspace=project.workspace,
    )
    project.workspace = None
    project.save(update_fields=["workspace"])
    trace_id = str(uuid.uuid4())
    install_reader(monkeypatch, project, trace_id)
    dispatch = Mock()
    running = Mock()
    monkeypatch.setattr(
        "tracer.tasks.error_analysis.analyze_traces_on_demand.delay", dispatch
    )
    monkeypatch.setattr("tracer.queries.deep_analysis_state.set_running", running)

    result = AnalyzeProjectTracesTool().run(
        {"project_id": str(project.id), "trace_ids": [trace_id]}, context
    )

    assert not result.is_error, result.content
    dispatch.assert_called_once_with(str(project.id), [trace_id])
    running.assert_called_once_with(trace_id, project_id=str(project.id))


@pytest.fixture
def queued_trace_pair(project, global_analysis_skill, settings, monkeypatch):
    """Two real CH roots in one project. Only the queue and log transports are stubbed."""
    from django.utils import timezone

    from ai_tools.registry import registry
    from ee.usage.deployment import _detect_mode
    from tracer.queries import deep_analysis_state
    from tracer.tasks.error_analysis import (
        analyze_traces_on_demand,
        cluster_project_errors,
    )
    from tracer.tests._ch_seed import delete_ch_spans, seed_ch_spans

    settings.CLOUD_DEPLOYMENT = ""
    settings.EE_LICENSE_KEY = "test"
    _detect_mode.cache_clear()
    project_id = str(project.id)
    assigned, other = str(uuid.uuid4()), str(uuid.uuid4())
    roots = [
        {
            "id": f"root-{tid}",
            "trace_id": tid,
            "project_id": project_id,
            "name": "root",
            "observation_type": "llm",
            "start_time": timezone.now(),
            "input": {"prompt": tid},
            "metadata": {"goal": "test goal"},
        }
        for tid in (assigned, other)
    ]
    queued, clustered, log = Mock(), Mock(), Mock()
    monkeypatch.setattr(analyze_traces_on_demand, "delay", queued)
    monkeypatch.setattr(cluster_project_errors, "delay", clustered)
    monkeypatch.setattr("tracer.services.clickhouse.client.ClickHouseClient", log)
    ctx = ToolContext(
        user=project.workspace.created_by,
        organization=project.organization,
        workspace=project.workspace,
    )
    tool = registry.get("analyze_project_traces")
    try:
        seed_ch_spans(roots)
        # Simulate a competing dispatcher that already owns the second trace.
        assert deep_analysis_state.set_running(other, project_id=project_id)
        args = {"project_id": project_id, "trace_ids": [assigned, other]}
        first = tool.run(args, ctx)
        assert not first.is_error, first.content
        assert first.data["trace_ids"] == [assigned]
        assert first.data["trace_count"] == 1
        second = tool.run(args, ctx)
        assert not second.is_error, second.content
        assert second.data["status"] == "already_running"
        assert second.data["trace_count"] == 0
        queued.assert_called_once_with(project_id, [assigned])
        yield assigned, other, queued.call_args.args, clustered, log.return_value.insert
    finally:
        _detect_mode.cache_clear()
        for tid in (assigned, other):
            deep_analysis_state.clear(tid, project_id=project_id)
        delete_ch_spans([row["id"] for row in roots])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "case", ["text_only", "missing_span", "incomplete_stream", "summary_unavailable", "explored"]
)
def test_queued_analysis_requires_completed_inspection(
    project, queued_trace_pair, case, monkeypatch
):
    from tracer.models.trace_error_analysis import ErrorClusterTraces
    from tracer.queries import feed
    from tracer.tasks.error_analysis import analyze_traces_on_demand
    from tracer.tests.test_error_analysis import make_feed_group

    assigned, _, args, clustered, insert = queued_trace_pair
    group = make_feed_group(project, "inspection")
    ErrorClusterTraces.objects.create(cluster=group, trace_id=assigned)
    summary = MagicMock()
    summary.return_value.choices[0].message.content = (
        '{"sub_flows":[{"name":"answer","summary":"Inspected the root"}]}'
    )
    if case == "summary_unavailable":
        summary.side_effect = RuntimeError("Provider unavailable")
    monkeypatch.setattr("litellm.completion", summary)
    steps = {
        "text_only": [],
        "missing_span": [read_call(assigned, "no-such-span")],
        "incomplete_stream": [read_call(assigned, f"root-{assigned}")],
        "summary_unavailable": [("explore_trace", {"trace_id": assigned})],
        "explored": [("explore_trace_legacy", {"trace_id": assigned})],
    }[case]
    finish = None if case == "incomplete_stream" else "stop"
    calls = install_tool_stream(monkeypatch, *steps, finish=finish)
    analyze_traces_on_demand(*args)

    assert len(calls) == len(steps) + 1
    status = feed.get_deep_analysis(group.cluster_id, assigned, [str(project.id)]).status
    rows = TraceErrorAnalysis.objects.filter(project=project, trace_id=assigned)
    if case in ("summary_unavailable", "explored"):
        summary.assert_called_once()
        assert "test goal" in summary.call_args.kwargs["messages"][-1]["content"]
    if case == "explored":
        assert "Inspected the root" in calls[-1][-1]["content"]
        assert status == "done"
        assert rows.get().overall_score == 4.0
        insert.assert_called_once()
        clustered.assert_called_once_with(str(project.id))
    else:
        if case == "missing_span":
            assert "not found in trace" in calls[-1][-1]["content"]
        elif case == "incomplete_stream":
            assert f"root-{assigned}" in calls[-1][-1]["content"]
        elif case == "summary_unavailable":
            assert "Trace summary unavailable" in calls[-1][-1]["content"]
        assert status == "failed"
        assert not rows.exists()
        insert.assert_not_called()
        clustered.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_queued_analysis_rejects_other_trace_in_same_project(
    project, queued_trace_pair, monkeypatch
):
    from tracer.queries import deep_analysis_state
    from tracer.tasks.error_analysis import analyze_traces_on_demand

    assigned, other, args, clustered, insert = queued_trace_pair
    existing = TraceErrorAnalysis.objects.create(
        project=project, trace_id=other, insights="Keep me"
    )
    before = TraceErrorAnalysis.objects.filter(pk=existing.pk).values().get()
    summary = Mock()
    monkeypatch.setattr("litellm.completion", summary)
    finding = finding_params(assigned) | {"location_spans": [f"root-{assigned}"]}
    other_handle = {"analysis_id": str(existing.pk)}
    calls = install_tool_stream(
        monkeypatch,
        ("explore_trace", {"trace_id": other}),
        ("explore_trace_legacy", {"trace_id": other}),
        read_call(other, f"root-{other}"),
        ("submit_trace_scores", score_params(other)),
        ("submit_trace_finding", finding_params(other)),
        # An analysis handle alone must not redirect an otherwise scoped write.
        ("submit_trace_scores", score_params(assigned) | other_handle),
        ("submit_trace_finding", finding | other_handle),
        ("submit_trace_finding", finding | {"location_spans": [f"root-{other}"]}),
    )
    analyze_traces_on_demand(*args)

    results = [msg["content"] for msg in calls[-1] if msg["role"] == "tool"]
    assert len(results) == 8
    assert all("Not Found" in text for text in results[:7])
    assert "outside the assigned trace" in results[7]
    analyses = TraceErrorAnalysis.objects.filter(project=project)
    assert not analyses.filter(trace_id=assigned).exists()
    assert list(analyses.filter(trace_id=other).values()) == [before]
    assert not TraceErrorDetail.objects.filter(analysis=existing).exists()
    assert deep_analysis_state.is_running(other, project_id=str(project.id))
    state = deep_analysis_state.status(assigned, project_id=str(project.id), has_analysis=False)
    assert state == "failed"
    summary.assert_not_called()
    insert.assert_not_called()
    clustered.assert_not_called()
