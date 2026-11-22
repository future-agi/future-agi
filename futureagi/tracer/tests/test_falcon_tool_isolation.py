"""Background refusal and independent project scoping of the registered tools."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from ai_tools.base import ToolContext
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail
from tracer.tasks.error_analysis import _falcon_analyze_single_trace
from tracer.tests._falcon_analysis import (
    finding_params,
    install_reader,
    read_call,
    score_params,
    scripted_tool_stream,
)
from tracer.tests._falcon_hold import (
    assert_activity_refused,
    assert_tool_refused,
    assert_worker_refused,
    no_background_work,
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
def test_held_scoring_leaves_local_and_foreign_analyses_untouched(
    project,
    other_project,
    explicit_analysis_id,
    existing_analysis,
):
    trace_id = str(uuid.uuid4())
    if existing_analysis:
        TraceErrorAnalysis.objects.create(trace_id=trace_id, project=project)
    foreign = TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=other_project, insights="Other project analysis"
    )
    before = TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get()
    params = score_params(trace_id)
    if explicit_analysis_id:
        params["analysis_id"] = str(foreign.id)
    stream = scripted_tool_stream(read_call(trace_id), ("submit_trace_scores", params))
    with no_background_work(trace_id, stream=stream):
        result = _falcon_analyze_single_trace(trace_id, str(project.id))
        assert_worker_refused(result, trace_id)
    assert TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get() == before
    local = TraceErrorAnalysis.objects.filter(project=project, trace_id=trace_id)
    assert local.count() == int(existing_analysis)


@pytest.mark.django_db(transaction=True)
def test_held_worker_does_not_score_an_accessible_colliding_root(
    project,
    other_project,
    monkeypatch,
):
    from ai_tools.tools.tracing.access import get_trace_project

    trace_id = str(uuid.uuid4())
    ctx = ToolContext(
        project.workspace.created_by,
        project.organization,
        project.workspace,
        project=project,
    )
    # Keep this resolver stub out of the imports that the worker probes observe.
    with monkeypatch.context() as resolver_patch:
        reader = install_reader(resolver_patch, project, trace_id, foreign=other_project)
        assert get_trace_project(trace_id, ctx) == project
        reader.root_ids_by_trace_ids.assert_called_once_with(
            [trace_id], project_ids=[str(project.id)]
        )
    stream = scripted_tool_stream(
        read_call(trace_id), ("submit_trace_scores", score_params(trace_id))
    )
    with no_background_work(trace_id, stream=stream):
        result = _falcon_analyze_single_trace(trace_id, str(project.id))
        assert_worker_refused(result, trace_id)
    assert not TraceErrorAnalysis.objects.filter(trace_id=trace_id).exists()


@pytest.mark.django_db(transaction=True)
def test_held_worker_never_starts_interruptible_scoring(project):
    trace_id = str(uuid.uuid4())
    stream = scripted_tool_stream(
        read_call(trace_id),
        ("submit_trace_scores", score_params(trace_id)),
        fail_after_tool=True,
    )
    with no_background_work(trace_id, stream=stream):
        result = _falcon_analyze_single_trace(trace_id, str(project.id))
        assert_worker_refused(result, trace_id)
    assert not TraceErrorAnalysis.objects.filter(trace_id=trace_id).exists()


@pytest.mark.django_db(transaction=True)
def test_held_activity_preserves_prior_scored_interruption_in_feed(
    project, queued_trace_pair
):
    from tracer.models.trace_error_analysis import ErrorClusterTraces
    from tracer.queries import deep_analysis_state, feed
    from tracer.tests.test_error_analysis import make_feed_group

    assigned, other, _, _ = queued_trace_pair
    project_id = str(project.id)
    group = make_feed_group(project, "queued-score")
    ErrorClusterTraces.objects.create(cluster=group, trace_id=assigned)
    root = f"root-{assigned}"
    saved = TraceErrorAnalysis.objects.create(
        project=project, trace_id=assigned, overall_score=2.0
    )
    TraceErrorDetail.objects.create(
        analysis=saved, error_id="E001", category="Own finding", location_spans=[root]
    )
    deep_analysis_state.set_failed(assigned, project_id=project_id)
    stream = scripted_tool_stream(
        read_call(assigned, root),
        ("submit_trace_finding", finding_params(assigned) | {"location_spans": [root]}),
        ("submit_trace_scores", score_params(assigned)),
        fail_after_tool=True,
    )
    with no_background_work(assigned, other, stream=stream):
        assert_queued_pair_refused(project_id, queued_trace_pair)
    status = feed.get_deep_analysis(group.cluster_id, assigned, [project_id])
    assert status.status == "failed"
    assert not deep_analysis_state.is_running(assigned, project_id=project_id)
    assert not status.recommendations


@pytest.mark.django_db(transaction=True)
def test_held_worker_never_invokes_unscoped_registry_tools(project, monkeypatch):
    from ai_tools.registry import registry

    tool = registry.get("analyze_project_traces")
    execute = Mock()
    monkeypatch.setattr(tool, "execute", execute)
    stream = scripted_tool_stream(
        ("analyze_project_traces", {"project_id": str(project.id)})
    )
    trace_id = str(uuid.uuid4())
    with no_background_work(trace_id, stream=stream):
        result = _falcon_analyze_single_trace(trace_id, str(project.id))
        assert_worker_refused(result, trace_id)
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


@pytest.fixture
def outside_workspace(project):
    from accounts.models.workspace import Workspace

    return Workspace.objects.create(
        name="Outside the active workspace",
        organization=project.organization,
        created_by=project.workspace.created_by,
        is_default=False,
    )


@pytest.mark.django_db
def test_held_dispatch_still_scopes_legacy_project_to_default_workspace(
    project, outside_workspace
):
    context = ToolContext(
        user=project.workspace.created_by,
        organization=project.organization,
        workspace=project.workspace,
    )
    assert context.workspace.is_default
    project.workspace = None
    project.save(update_fields=["workspace"])
    trace_id = str(uuid.uuid4())
    with no_background_work(trace_id):
        assert_tool_refused(context, outside_workspace, str(project.id), [trace_id])


@pytest.fixture
def queued_trace_pair(project, settings, outside_workspace):
    """Two real CH roots and an existing competing claim; do not enqueue a job in setup."""
    from django.utils import timezone

    from ee.usage.deployment import _detect_mode
    from tracer.queries import deep_analysis_state
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
    ctx = ToolContext(
        user=project.workspace.created_by,
        organization=project.organization,
        workspace=project.workspace,
    )
    try:
        seed_ch_spans(roots)
        assert deep_analysis_state.set_running(other, project_id=project_id)
        yield assigned, other, ctx, outside_workspace
    finally:
        _detect_mode.cache_clear()
        for tid in (assigned, other):
            deep_analysis_state.clear(tid, project_id=project_id)
        delete_ch_spans([row["id"] for row in roots])


def assert_queued_pair_refused(project_id, pair):
    from tracer.queries import deep_analysis_state

    assigned, other, ctx, outside = pair
    assert_tool_refused(ctx, outside, project_id, [assigned, other])
    # Even an entirely claimed batch must not get an already_running success.
    assert_tool_refused(ctx, outside, project_id, [other])
    # A pre-hold Temporal message can contain a trace with a live claim.
    # Refusing that message must preserve even the claim for its own input.
    assert deep_analysis_state.is_running(other, project_id=project_id)
    assert_activity_refused(project_id, [assigned, other])
    assert deep_analysis_state.is_running(other, project_id=project_id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "case", ["text_only", "missing_span", "incomplete_stream", "summary_unavailable", "explored"]
)
def test_held_activity_never_publishes_an_inspection(project, queued_trace_pair, case):
    from tracer.models.trace_error_analysis import ErrorClusterTraces
    from tracer.queries import feed
    from tracer.tests.test_error_analysis import make_feed_group

    assigned, other, _, _ = queued_trace_pair
    group = make_feed_group(project, "inspection")
    ErrorClusterTraces.objects.create(cluster=group, trace_id=assigned)
    summary = MagicMock()
    summary.return_value.choices[0].message.content = (
        '{"sub_flows":[{"name":"answer","summary":"Inspected the root"}]}'
    )
    if case == "summary_unavailable":
        summary.side_effect = RuntimeError("Provider unavailable")
    steps = {
        "text_only": [],
        "missing_span": [read_call(assigned, "no-such-span")],
        "incomplete_stream": [read_call(assigned, f"root-{assigned}")],
        "summary_unavailable": [("explore_trace", {"trace_id": assigned})],
        "explored": [("explore_trace_legacy", {"trace_id": assigned})],
    }[case]
    finish = None if case == "incomplete_stream" else "stop"
    stream = scripted_tool_stream(*steps, finish=finish)
    with no_background_work(assigned, other, stream=stream, summary=summary):
        assert_queued_pair_refused(str(project.id), queued_trace_pair)
    summary.assert_not_called()
    status = feed.get_deep_analysis(group.cluster_id, assigned, [str(project.id)]).status
    assert status == "idle"
    assert not TraceErrorAnalysis.objects.filter(project=project, trace_id=assigned).exists()


@pytest.mark.django_db(transaction=True)
def test_held_activity_leaves_sibling_trace_and_claim_untouched(
    project, queued_trace_pair
):
    from tracer.queries import deep_analysis_state

    assigned, other, _, _ = queued_trace_pair
    existing = TraceErrorAnalysis.objects.create(
        project=project, trace_id=other, insights="Keep me"
    )
    before = TraceErrorAnalysis.objects.filter(pk=existing.pk).values().get()
    summary = Mock()
    finding = finding_params(assigned) | {"location_spans": [f"root-{assigned}"]}
    other_handle = {"analysis_id": str(existing.pk)}
    stream = scripted_tool_stream(
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
    with no_background_work(assigned, other, stream=stream, summary=summary):
        assert_queued_pair_refused(str(project.id), queued_trace_pair)

    analyses = TraceErrorAnalysis.objects.filter(project=project)
    assert not analyses.filter(trace_id=assigned).exists()
    assert list(analyses.filter(trace_id=other).values()) == [before]
    assert not TraceErrorDetail.objects.filter(analysis=existing).exists()
    assert deep_analysis_state.is_running(other, project_id=str(project.id))
    state = deep_analysis_state.status(assigned, project_id=str(project.id), has_analysis=False)
    assert state == "idle"
    summary.assert_not_called()
