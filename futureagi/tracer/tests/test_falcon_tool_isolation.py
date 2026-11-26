"""Refusal, untouched rows and pinned foreground behavior."""

import uuid
from unittest.mock import MagicMock, Mock

import pytest

from ai_tools.base import ToolContext
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail
from tracer.tasks.error_analysis import _falcon_analyze_single_trace
from tracer.tests._falcon_analysis import (
    finding_params,
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
def test_held_worker_does_not_score_a_sibling_analysis(
    project,
    other_project,
):
    trace_id = str(uuid.uuid4())
    foreign = TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=other_project
    )
    stream = scripted_tool_stream(
        read_call(trace_id), ("submit_trace_scores", score_params(trace_id))
    )
    with no_background_work(trace_id, stream=stream):
        result = _falcon_analyze_single_trace(trace_id, str(project.id))
        assert_worker_refused(result, trace_id)
    foreign.refresh_from_db()
    assert foreign.overall_score is None
    assert not TraceErrorAnalysis.objects.filter(
        trace_id=trace_id, project=project
    ).exists()


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
def test_held_activity_preserves_existing_feed_result_and_failure_marker(
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
    deep_analysis_state.set_failed(assigned)
    stream = scripted_tool_stream(
        read_call(assigned, root),
        ("submit_trace_finding", finding_params(assigned) | {"location_spans": [root]}),
        ("submit_trace_scores", score_params(assigned)),
        fail_after_tool=True,
    )
    with no_background_work(assigned, other, stream=stream):
        assert_queued_pair_refused(project_id, queued_trace_pair)
    status = feed.get_deep_analysis(group.cluster_id, assigned, [project_id])
    assert status.status == "done"  # Pinned Feed prefers the stored analysis row.
    assert [r.id for r in status.recommendations] == ["E001"]
    assert deep_analysis_state.status(assigned, has_analysis=False) == "failed"
    assert not deep_analysis_state.is_running(assigned)


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
def test_foreground_trace_tools_require_dev_pg_trace(
    project, other_project, monkeypatch, tool_name
):
    from ai_tools.registry import registry

    trace_id = str(uuid.uuid4())  # Analysis rows do not create a PG Trace.
    foreign = TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=other_project
    )
    before = TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get()
    ctx = ToolContext(
        project.workspace.created_by, project.organization, project.workspace
    )
    reader = Mock(side_effect=AssertionError("Read spans without the PG trace"))
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", reader)
    params = {"trace_id": trace_id}
    if tool_name == "read_trace_span":
        params["span_id"] = "shared-span"
    elif tool_name == "submit_trace_finding":
        params = finding_params(trace_id) | {"analysis_id": str(foreign.pk)}
    result = registry.get(tool_name).run(params, ctx)
    assert result.is_error and result.error_code == "NOT_FOUND"
    assert TraceErrorAnalysis.objects.filter(pk=foreign.pk).values().get() == before
    assert not TraceErrorDetail.objects.filter(analysis=foreign).exists()
    reader.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_refused_batch_does_not_embed_either_project(project, other_project):
    trace_id = str(uuid.uuid4())
    for owner in (project, other_project):
        analysis = TraceErrorAnalysis.objects.create(trace_id=trace_id, project=owner)
        TraceErrorDetail.objects.create(
            analysis=analysis,
            error_id="E001",
            category="Evidence",
            description=owner.name,
        )
    with no_background_work(trace_id):
        assert_activity_refused(str(project.pk), [trace_id])


def test_dev_claim_and_completed_row_precedence():
    from django.core.cache import cache

    from tracer.queries import deep_analysis_state as state

    tid = str(uuid.uuid4())
    try:
        assert state.set_running(tid)
        assert cache.get(f"deep_analysis:running:{tid}") == "1"
        assert not state.set_running(tid)
        assert state.status(tid, has_analysis=False) == "running"
        state.set_failed(tid)
        assert cache.get(f"deep_analysis:failed:{tid}") == "1"
        assert state.status(tid, has_analysis=False) == "failed"
        assert state.status(tid, has_analysis=True) == "done"
        assert state.set_running(tid)
        assert cache.get(f"deep_analysis:failed:{tid}") is None
    finally:
        state.clear(tid)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "missing_scope", ["organization", "workspace", "foreign_project"]
)
def test_foreground_span_uses_dev_trace_lookup(
    project, other_project, monkeypatch, missing_scope
):
    from ai_tools.registry import registry
    from tracer.models.trace import Trace

    tid = uuid.uuid4()
    ctx = ToolContext(
        project.workspace.created_by, project.organization, project.workspace
    )
    if missing_scope == "foreign_project":
        ctx.project = other_project
    else:
        setattr(ctx, missing_scope, None)
    lookup = Mock(side_effect=Trace.DoesNotExist)
    monkeypatch.setattr(Trace.objects, "get", lookup)
    reader = Mock(side_effect=AssertionError("Reader used for a missing trace"))
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", reader)
    result = registry.get("read_trace_span").run(
        {"trace_id": str(tid), "span_id": "absent"}, ctx
    )
    assert result.is_error and result.error_code == "NOT_FOUND"
    lookup.assert_called_once_with(id=tid, project__organization=ctx.organization)
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
def test_producer_refuses_legacy_project_without_disclosing_scope(
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
    from django.core.cache import cache
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
        assert deep_analysis_state.set_running(other)
        yield assigned, other, ctx, outside_workspace
    finally:
        _detect_mode.cache_clear()
        cache.delete(f"deep_analysis:running:{other}")
        for tid in (assigned, other):
            deep_analysis_state.clear(tid)
        delete_ch_spans([row["id"] for row in roots])


def assert_queued_pair_refused(project_id, pair):
    from django.core.cache import cache

    from tracer.queries import deep_analysis_state

    assigned, other, ctx, outside = pair
    assert_tool_refused(ctx, outside, project_id, [assigned, other])
    # Even an entirely claimed batch must not get an already_running success.
    assert_tool_refused(ctx, outside, project_id, [other])
    # A pre-hold Temporal message can contain a trace with a live claim.
    # Refusing that message must preserve even the claim for its own input.
    assert deep_analysis_state.is_running(other)
    assert_activity_refused(project_id, [assigned, other])
    assert cache.get(f"deep_analysis:running:{other}") == "1"
    assert deep_analysis_state.is_running(other)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "case",
    [
        "text_only",
        "missing_span",
        "incomplete_stream",
        "summary_unavailable",
        "explored",
    ],
)
def test_held_activity_never_publishes_an_inspection(project, queued_trace_pair, case):
    from tracer.models.trace_error_analysis import ErrorClusterTraces
    from tracer.queries import feed
    from tracer.tests.test_error_analysis import make_feed_group

    assigned, other, _, _ = queued_trace_pair
    group = make_feed_group(project, "inspection")
    ErrorClusterTraces.objects.create(cluster=group, trace_id=assigned)
    summary = MagicMock()
    summary.return_value.choices[
        0
    ].message.content = (
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
    status = feed.get_deep_analysis(
        group.cluster_id, assigned, [str(project.id)]
    ).status
    assert status == "idle"
    assert not TraceErrorAnalysis.objects.filter(
        project=project, trace_id=assigned
    ).exists()


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
    assert deep_analysis_state.is_running(other)
    state = deep_analysis_state.status(assigned, has_analysis=False)
    assert state == "idle"
    summary.assert_not_called()
