"""r3b5 PostgreSQL publication/report contracts. NOT_RUN by offline writer."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import close_old_connections
from django.utils import timezone

from ee.agenthub.traceerroragent.analysis_service import TraceErrorAnalysisService
from tracer.models.trace_error_analysis import (
    AnalysisErrorGroup,
    ErrorPattern,
    TraceErrorAnalysis,
)
from tracer.queries import background_analysis_state as state
from tracer.queries.error_analysis import TraceErrorAnalysisDB
from tracer.services import analysis_result_snapshot as snapshots
from tracer.services.analysis_result_binding import attempt_scope
from tracer.tests.test_analysis_result_binding import candidate
from tracer.tests.test_analysis_result_binding import identity as identity
from tracer.tests.test_analysis_result_binding import landing as landing
from tracer.tests.test_analysis_result_binding import scope_no_egress as scope_no_egress

pytestmark = pytest.mark.django_db(transaction=True)


def judge_result():
    return {
        "summary": {"error_count": 1, "overall_score": 3},
        "errors": [
            {
                "error_id": "E1",
                "category": "Accuracy > Reasoning",
                "description": "learned finding",
                "recommendation": "fix",
                "immediate_fix": "retry",
                "root_causes": ["cause"],
            }
        ],
    }


def save_judge(project, trace, generation):
    with attempt_scope(project.pk, trace.pk, generation):
        return TraceErrorAnalysisService(project.pk).save_analysis_result(
            trace.pk, judge_result()
        )


def test_judge_publication_teaches_once_and_excludes_aborted_drafts(identity):
    project, trace = identity
    old = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, old)
    save_judge(project, trace, old)
    patterns = ErrorPattern.objects.filter(project=project)
    assert not patterns.exists()
    assert state.abort(project.pk, trace.pk, old, "analysis_failed")
    assert not state.complete(project.pk, trace.pk, old)
    new = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, new)
    save_judge(project, trace, new)
    assert not state.complete(project.pk, trace.pk, old)
    assert not patterns.exists()
    assert state.complete(project.pk, trace.pk, new)
    assert patterns.get().frequency == 1
    assert not state.complete(project.pk, trace.pk, new)
    assert patterns.get().frequency == 1
    assert list(TraceErrorAnalysis.objects.values_list("pk", flat=True)) == [
        state.get_state(trace.pk).result_analysis_id
    ]


def test_legacy_judge_keeps_immediate_pattern_learning(identity):
    project, trace = identity
    TraceErrorAnalysisService(project.pk).save_analysis_result(trace.pk, judge_result())
    assert state.get_state(trace.pk) is None
    assert ErrorPattern.objects.get(project=project).frequency == 1


def test_pattern_failure_rolls_back_publication_and_retry_does_not_double_count(
    identity,
):
    project, trace = identity
    generation = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, generation)
    save_judge(project, trace, generation)
    original = TraceErrorAnalysisService._update_error_patterns

    def fail_after_write(service, *args, **kwargs):
        original(service, *args, **kwargs)
        raise RuntimeError("synthetic pattern failure")

    with patch.object(
        TraceErrorAnalysisService, "_update_error_patterns", fail_after_write
    ):
        with pytest.raises(RuntimeError, match="synthetic pattern failure"):
            state.complete(project.pk, trace.pk, generation)
    assert state.get_state(trace.pk).status == "running"
    assert not ErrorPattern.objects.filter(project=project).exists()
    assert not TraceErrorAnalysis.objects.filter(trace=trace).exists()
    assert state.complete(project.pk, trace.pk, generation)
    assert ErrorPattern.objects.get(project=project).frequency == 1


@pytest.mark.parametrize(
    "reader",
    [
        "get_recent_trace_error_analyses",
        "get_analyses_for_similarity",
        "get_analysis_by_trace",
        "get_recent_analyses",
        "search_analyses",
    ],
)
def test_report_parent_selection_pins_children_and_reverse_managers_during_retry(
    identity, reader
):
    project, trace = identity
    generation = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, generation)
    analysis = candidate(project, trace, generation)
    with attempt_scope(project.pk, trace.pk, generation):
        group = AnalysisErrorGroup.drafts.create(
            analysis=analysis, cluster_id="C1", error_type="test", error_ids=["E001"]
        )
    assert state.complete(project.pk, trace.pk, generation)
    hydrate = snapshots.materialize_analysis

    def retry():
        close_old_connections()
        try:
            new = state.begin(project.pk, trace.pk)
            assert new and state.admit(project.pk, trace.pk, new)
        finally:
            close_old_connections()

    def race(parent):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(retry).result(timeout=2)
        return hydrate(parent)

    db = TraceErrorAnalysisDB()
    kwargs = {
        "project_id": project.pk,
        "cutoff_date": timezone.now() - timedelta(days=1),
    }
    if reader == "get_recent_trace_error_analyses":
        kwargs["limit"] = 10
    service = TraceErrorAnalysisService(project.pk)
    with patch.object(snapshots, "materialize_analysis", race):
        if reader == "get_analysis_by_trace":
            parents = [service.get_analysis_by_trace(trace.pk)]
        elif reader == "get_recent_analyses":
            parents = service.get_recent_analyses()
        elif reader == "search_analyses":
            parents = service.search_analyses({})
        else:
            parents = list(getattr(db, reader)(**kwargs))
    assert state.get_state(trace.pk).status == "running"
    assert len(parents) == 1
    parent = parents[0]
    assert parent.pk == analysis.pk
    assert [d.error_id for d in db.get_error_details_for_analysis(parent)] == ["E001"]
    assert [d.error_id for d in parent.error_details.all()] == ["E001"]
    assert [g.pk for g in db.get_error_groups_for_analysis(parent)] == [group.pk]
    assert [g.pk for g in parent.analysis_error_groups.all()] == [group.pk]
    assert not TraceErrorAnalysis.objects.filter(pk=parent.pk).exists()


def test_worker_stream_error_quarantines_unscored_partial_result(identity, landing):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock

    from tracer.services.falcon_analysis_guard import AnalysisAborted
    from tracer.tasks.error_analysis import _falcon_analyze_single_trace

    project, trace = identity
    context = SimpleNamespace(
        user=landing.user, organization=landing.org, workspace=landing.default
    )
    guard = Mock(remaining=5)
    guard.abort.side_effect = lambda code: (_ for _ in ()).throw(AnalysisAborted(code))
    guard.run_sync.side_effect = lambda fn, **kw: fn()
    generation = state.begin(project.pk, trace.pk)

    def finding():
        close_old_connections()
        try:
            analysis = candidate(project, trace, generation)
            with attempt_scope(project.pk, trace.pk, generation):
                analysis.overall_score = None
                analysis.save()
        finally:
            close_old_connections()

    async def run(**kwargs):
        await asyncio.to_thread(finding)
        await kwargs["send_callback"](
            {"type": "error", "data": {"message": "LLM error: synthetic"}}
        )
        return {"content": "partial"}

    conversation = SimpleNamespace(id="offline", pk="offline", metadata={})
    with (
        patch("ee.usage.deployment.DeploymentMode.is_oss", return_value=False),
        patch("ee.falcon_ai.agent.AgentLoop", return_value=SimpleNamespace(run=run)),
        patch("ee.falcon_ai.models.Skill.objects") as skills,
        patch("ee.falcon_ai.models.Conversation") as conversations,
        patch(
            "tracer.services.falcon_analysis_scope.resolve_analysis_scope",
            return_value=(project, context),
        ),
        patch(
            "tracer.services.falcon_analysis_guard.AnalysisGuard", return_value=guard
        ),
    ):
        skills.filter.return_value.first.return_value = None
        conversations.objects.create.return_value = conversation
        result = _falcon_analyze_single_trace(
            str(trace.pk),
            str(project.pk),
            requesting_user_id=str(landing.user.pk),
            organization_id=str(landing.org.pk),
            workspace_id=str(landing.default.pk),
            generation=generation,
        )
    assert result["outcome"] == "aborted"
    receipt = state.get_state(trace.pk)
    assert receipt.status == "aborted" and receipt.result_analysis_id is None
    assert not TraceErrorAnalysis.objects.filter(trace=trace).exists()
    assert TraceErrorAnalysis._base_manager.get(pk=generation).overall_score is None
    assert TraceErrorAnalysisDB().get_analysis_by_trace_id(str(trace.pk)) is None
