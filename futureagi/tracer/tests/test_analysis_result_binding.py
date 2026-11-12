"""Real PostgreSQL contracts; NOT_RUN in the offline writer.

Run alongside test_background_analysis_lifecycle on the approved DB runner.
No provider calls: all records are local fixture rows; embedding tests stop
before constructing an embedding client.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest

from accounts.tests.test_workspace_landing import landing as landing
from accounts.tests.workspace_write_support import scope_no_egress as scope_no_egress
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail
from tracer.queries import background_analysis_state as state
from tracer.queries.error_analysis import TraceErrorAnalysisDB
from tracer.services.analysis_result_binding import (
    ResultOwnershipError,
    attempt_scope,
    legacy_attempt,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def identity(landing, scope_no_egress):
    project = Project._base_manager.create(
        name="Bound results",
        organization=landing.org,
        workspace=landing.default,
        user=landing.user,
        model_type="generative_llm",
        trace_type="observe",
    )
    trace = Trace._base_manager.create(project=project)
    return project, trace


def candidate(project, trace, generation, *, finding=True):
    with attempt_scope(project.pk, trace.pk, generation):
        analysis = TraceErrorAnalysis.drafts.create(
            project=project,
            trace=trace,
            overall_score=4,
            total_errors=int(finding),
        )
        if finding:
            TraceErrorDetail.drafts.create(
                analysis=analysis,
                error_id="E001",
                category="test",
                impact="LOW",
                urgency_to_fix="LOW",
                description="generation-owned finding",
                root_causes=["generation-owned cause"],
                recommendation="fix",
            )
    return analysis


def test_late_aborted_writer_cannot_replace_current_result(identity):
    project, trace = identity
    old = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, old)
    stale = candidate(project, trace, old)
    assert state.abort(project.pk, trace.pk, old, "analysis_failed")
    new = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, new)
    good = candidate(project, trace, new)
    assert state.complete(project.pk, trace.pk, new)
    with attempt_scope(project.pk, trace.pk, old), pytest.raises(ResultOwnershipError):
        stale.overall_score = 1
        stale.save()
    # Simulate a write by an old deployed process bypassing the new model mixin:
    # such a row is quarantined even if it is newer than the completed result.
    TraceErrorAnalysis._base_manager.bulk_create(
        [
            TraceErrorAnalysis(project=project, trace=trace, overall_score=1),
        ]
    )
    assert list(TraceErrorAnalysis.objects.values_list("pk", flat=True)) == [good.pk]
    db = TraceErrorAnalysisDB()
    assert db.get_analysis_by_trace_id(str(trace.pk)).pk == good.pk
    assert [a.pk for a in db.get_analyses_for_traces([str(trace.pk)])] == [good.pk]
    assert {
        d.analysis_id for d in db.get_error_details_for_traces([str(trace.pk)])
    } == {good.pk}
    assert TraceErrorAnalysis.objects.filter(project=project).count() == 1
    assert stale.error_details.count() == 0
    assert good.error_details.count() == 1


def test_retry_without_a_new_candidate_aborts_instead_of_adopting_partial(identity):
    project, trace = identity
    old = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, old)
    candidate(project, trace, old)
    assert state.abort(project.pk, trace.pk, old, "analysis_failed")
    new = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, new)
    assert not state.complete(project.pk, trace.pk, new)
    row = state.get_state(trace.pk)
    assert row.status == "aborted"
    assert row.result_analysis_id is None
    assert not TraceErrorAnalysis.objects.filter(trace=trace).exists()
    assert TraceErrorAnalysisDB().ingest_trace_error_embeddings(str(trace.pk)) == 0


def test_empty_success_is_new_result_not_old_findings(identity):
    project, trace = identity
    old = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, old)
    candidate(project, trace, old)
    assert state.abort(project.pk, trace.pk, old, "analysis_failed")
    new = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, new)
    candidate(project, trace, new, finding=False)
    assert state.complete(project.pk, trace.pk, new)
    assert str(TraceErrorAnalysis.objects.get(trace=trace).pk) == new
    assert not TraceErrorDetail.objects.filter(analysis__trace=trace).exists()


def test_predeploy_force_cannot_delete_completed_result(identity):
    project, trace = identity
    generation = state.begin(project.pk, trace.pk)
    assert state.admit(project.pk, trace.pk, generation)
    good = candidate(project, trace, generation)
    assert state.complete(project.pk, trace.pk, generation)
    from tracer.tasks.error_analysis import run_deep_analysis_on_demand

    with patch("tracer.tasks.error_analysis.analyze_single_trace") as analyze:
        result = run_deep_analysis_on_demand(str(trace.pk), force=True)
    assert not result["success"]
    analyze.assert_not_called()
    assert TraceErrorAnalysis.objects.get(trace=trace).pk == good.pk
    with pytest.raises(ResultOwnershipError), legacy_attempt(project.pk, trace.pk):
        pytest.fail("unversioned writer was admitted")
    with pytest.raises(ResultOwnershipError):
        TraceErrorAnalysis.objects.filter(trace=trace).delete()


def test_unowned_write_and_cross_attempt_detail_are_rejected(identity):
    project, trace = identity
    legacy = TraceErrorAnalysis.objects.create(project=project, trace=trace)
    assert TraceErrorAnalysis.objects.get(pk=legacy.pk) == legacy
    generation = state.begin(project.pk, trace.pk)
    with pytest.raises(ResultOwnershipError):
        TraceErrorAnalysis.objects.create(project=project, trace=trace)
    assert state.admit(project.pk, trace.pk, generation)
    with (
        attempt_scope(project.pk, trace.pk, generation),
        pytest.raises(ResultOwnershipError),
    ):
        TraceErrorDetail.drafts.create(analysis_id=uuid4(), error_id="E001")
