"""Real ORM lifecycle contracts for fi-infra; NOT_RUN in the offline writer."""

from uuid import uuid4

import pytest
from django.core.cache import cache
from django.db import IntegrityError, transaction

from accounts.tests.test_workspace_landing import landing as landing
from accounts.tests.workspace_write_support import scope_no_egress as scope_no_egress
from tracer.models.background_trace_analysis_state import BackgroundTraceAnalysisState
from tracer.models.project import Project
from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.queries import background_analysis_state as state
from tracer.queries import deep_analysis_state
from tracer.services.analysis_result_binding import ResultOwnershipError, attempt_scope

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def identity(landing, scope_no_egress):
    project = Project._base_manager.create(
        name="Durable lifecycle",
        organization=landing.org,
        workspace=landing.default,
        user=landing.user,
        model_type="generative_llm",
        trace_type="observe",
    )
    return str(project.pk), str(uuid4())


def test_real_orm_abort_retry_cache_eviction_and_stale_finalizer(identity):
    project, trace = identity
    first = state.begin(project, trace)
    assert state.admit(project, trace, first)
    with attempt_scope(project, trace, first):
        TraceErrorAnalysis.drafts.create(trace_id=trace, project_id=project)
    assert state.abort(project, trace, first, "analysis_access_revoked")
    cache.delete(deep_analysis_state._failed_key(trace))
    cache.delete(deep_analysis_state._running_key(trace))
    assert deep_analysis_state.status(trace, has_analysis=True) == "failed"
    assert not state.complete(project, trace, first)

    second = state.begin(project, trace)
    assert first != second
    assert state.begin(project, trace) is None
    assert deep_analysis_state.status(trace, has_analysis=True) == "running"
    assert not state.admit(project, trace, first)
    assert state.admit(project, trace, second)
    assert not state.admit(project, trace, second)
    assert not state.complete(project, trace, first)
    assert not state.abort(project, trace, first, "analysis_deadline_exceeded")
    assert deep_analysis_state.status(trace, has_analysis=True) == "running"
    with attempt_scope(project, trace, second):
        TraceErrorAnalysis.drafts.create(trace_id=trace, project_id=project)
    assert state.complete(project, trace, second)
    assert not state.abort(project, trace, second, "analysis_deadline_exceeded")
    assert deep_analysis_state.status(trace, has_analysis=True) == "done"


def test_unique_identity_and_findings_deletion_preserve_lifecycle(identity):
    project, trace = identity
    generation = state.begin(project, trace)
    with pytest.raises(IntegrityError), transaction.atomic():
        BackgroundTraceAnalysisState.objects.create(project_id=project, trace_id=trace)
    assert state.admit(project, trace, generation)
    with attempt_scope(project, trace, generation):
        analysis = TraceErrorAnalysis.drafts.create(trace_id=trace, project_id=project)
    with pytest.raises(ResultOwnershipError):
        analysis.delete()
    row = state.get_state(trace, project_ids=[project])
    assert str(row.generation) == generation
    assert row.status == "running"
    assert state.get_state(trace, project_ids=[]) is None
    assert state.get_state(trace, project_ids=[str(uuid4())]) is None
    assert not state.admit(str(uuid4()), trace, generation)
