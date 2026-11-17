"""PostgreSQL r3b4 contracts. NOT_RUN on the offline writer."""

from unittest.mock import patch

import pytest

from tracer.models.trace_error_analysis import (
    AnalysisErrorGroup,
    TraceErrorAnalysis,
    TraceErrorDetail,
)
from tracer.queries import background_analysis_state as state
from tracer.queries.error_analysis import TraceErrorAnalysisDB
from tracer.services.analysis_result_binding import attempt_scope
from tracer.tests.test_analysis_result_binding import candidate
from tracer.tests.test_analysis_result_binding import identity as identity
from tracer.tests.test_analysis_result_binding import landing as landing
from tracer.tests.test_analysis_result_binding import scope_no_egress as scope_no_egress

pytestmark = pytest.mark.django_db(transaction=True)


def test_historical_judge_is_visible_in_feed_drawer_and_cluster(identity):
    project, trace = identity
    # Simulate records present before migration 0121, without calling new saves.
    analysis = TraceErrorAnalysis(project=project, trace=trace, overall_score=4)
    TraceErrorAnalysis._base_manager.bulk_create([analysis])
    detail = TraceErrorDetail(
        analysis=analysis,
        error_id="E001",
        category="test",
        root_causes=["historical cause"],
        recommendation="fix",
    )
    TraceErrorDetail._base_manager.bulk_create([detail])
    group = AnalysisErrorGroup(
        analysis=analysis, cluster_id="C001", error_type="test", error_ids=["E001"]
    )
    AnalysisErrorGroup._base_manager.bulk_create([group])
    assert state.get_state(trace.pk) is None
    from tracer.queries import feed

    with (
        patch.object(feed, "_cluster_qs_for_access") as access,
        patch.object(feed, "_cluster_has_trace", return_value=True),
    ):
        access.return_value.exists.return_value = True
        response = feed.get_deep_analysis("cluster", str(trace.pk), [str(project.pk)])
    assert response.status == "done"
    assert response.root_causes
    db = TraceErrorAnalysisDB()
    selected = db.get_analysis_by_trace_id(str(trace.pk), str(project.organization_id))
    assert selected.pk == analysis.pk
    assert [d.pk for d in db.get_error_details_for_analysis(selected)] == [detail.pk]
    assert [g.pk for g in db.get_error_groups_for_analysis(selected)] == [group.pk]
    assert [a.pk for a in db.get_analyses_for_traces([str(trace.pk)])] == [analysis.pk]
    assert [d.pk for d in db.get_error_details_for_traces([str(trace.pk)])] == [
        detail.pk
    ]


def test_interactive_multiple_findings_and_scores_have_no_lifecycle(identity):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from ai_tools.tools.tracing.submit_trace_finding import (
        SubmitTraceFindingInput,
        SubmitTraceFindingTool,
    )
    from ai_tools.tools.tracing.submit_trace_scores import (
        SubmitTraceScoresInput,
        SubmitTraceScoresTool,
    )

    project, trace = identity
    context = SimpleNamespace(organization=project.organization)

    @contextmanager
    def reader():
        yield SimpleNamespace(
            root_ids_by_trace_ids=lambda ids: {str(trace.pk): ("root", str(project.pk))}
        )

    analysis_id = None
    with (
        patch("tracer.services.clickhouse.v2.get_reader", reader),
        patch.object(
            TraceErrorAnalysisDB, "ingest_trace_error_embeddings", return_value=0
        ),
        patch("tracer.tasks.error_analysis.cluster_project_errors.delay"),
    ):
        for index in range(3):
            result = SubmitTraceFindingTool().execute(
                SubmitTraceFindingInput(
                    trace_id=trace.pk,
                    analysis_id=analysis_id,
                    category="test",
                    location_spans=[],
                    evidence_snippets=["evidence"],
                    description=str(index),
                    impact="LOW",
                    root_causes=["cause"],
                    recommendation="fix",
                    confidence=0.9,
                ),
                context,
            )
            assert not result.is_error
            analysis_id = result.data["analysis_id"]
            assert state.get_state(trace.pk) is None
        scores = {
            "trace_id": trace.pk,
            "analysis_id": analysis_id,
            "overall_score": 4,
            "insights": "done",
            "recommended_priority": "LOW",
        }
        for name in (
            "factual_grounding",
            "privacy_and_safety",
            "instruction_adherence",
            "optimal_plan_execution",
        ):
            scores[name + "_score"] = 4
            scores[name + "_reason"] = "reason"
        result = SubmitTraceScoresTool().execute(
            SubmitTraceScoresInput(**scores), context
        )
        assert not result.is_error
    assert state.get_state(trace.pk) is None
    analysis = TraceErrorAnalysis.objects.get(pk=analysis_id)
    assert analysis.overall_score == 4
    assert analysis.total_errors == 3
    assert analysis.error_details.count() == 3


def test_watchdog_abort_does_not_wait_for_stalled_result_save(identity):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import close_old_connections

    from tfc.utils.base_model import BaseModel

    project, trace = identity
    generation = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, generation)
    analysis = candidate(project, trace, generation)
    stalled, release = Event(), Event()
    original_save = BaseModel.save

    def save(model, *args, **kwargs):
        if isinstance(model, TraceErrorAnalysis):
            stalled.set()
            assert release.wait(5)
        return original_save(model, *args, **kwargs)

    def mutate():
        close_old_connections()
        try:
            with attempt_scope(project.pk, trace.pk, generation):
                analysis.overall_score = 1
                analysis.save()
        finally:
            close_old_connections()

    def abort():
        close_old_connections()
        try:
            return state.abort(
                project.pk, trace.pk, generation, "analysis_deadline_exceeded"
            )
        finally:
            close_old_connections()

    with (
        patch.object(BaseModel, "save", save),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        mutation = pool.submit(mutate)
        try:
            assert stalled.wait(2)
            # The finalizer already holds its candidate lock and is still blocked.
            assert pool.submit(abort).result(timeout=0.25)
            assert state.get_state(trace.pk).status == "aborted"
        finally:
            release.set()
        mutation.result(timeout=2)
    assert not TraceErrorAnalysis.objects.filter(trace=trace).exists()


def test_feed_and_drawer_pin_children_before_concurrent_force_retry(identity):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    from tracer.queries import feed
    from tracer.services import analysis_result_snapshot as snapshots

    project, trace = identity
    old = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, old)
    analysis = candidate(project, trace, old)
    assert state.complete(project.pk, trace.pk, old)
    hydrate = snapshots.materialize_analysis

    def force_retry():
        close_old_connections()
        try:
            new = state.begin(project.pk, trace.pk)
            assert new and state.admit(project.pk, trace.pk, new)
        finally:
            close_old_connections()

    def race(selected):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(force_retry).result(timeout=2)
        return hydrate(selected)

    with (
        patch.object(feed, "_cluster_qs_for_access") as access,
        patch.object(feed, "_cluster_has_trace", return_value=True),
        patch.object(snapshots, "materialize_analysis", race),
    ):
        access.return_value.exists.return_value = True
        response = feed.get_deep_analysis("cluster", str(trace.pk), [str(project.pk)])
    assert response.status == "done"
    assert response.root_causes
    assert state.get_state(trace.pk).status == "running"
    assert not TraceErrorAnalysis.objects.filter(pk=analysis.pk).exists()
    assert len(hydrate(analysis)._analysis_snapshot_details) == 1

    # Complete the new attempt, then retry during the drawer's parent/child read.
    new = str(state.get_state(trace.pk).generation)
    candidate(project, trace, new)
    assert state.complete(project.pk, trace.pk, new)
    with patch.object(snapshots, "materialize_analysis", race):
        db = TraceErrorAnalysisDB()
        selected = db.get_analysis_by_trace_id(
            str(trace.pk), str(project.organization_id)
        )
    assert str(selected.pk) == new
    assert len(db.get_error_details_for_analysis(selected)) == 1
