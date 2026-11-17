"""ORM + actual polling GET regressions; require the approved Django runner.

The licensed-project/cluster access seams are stubbed, not the worker outcome,
analysis ORM query, response serializer or GET handler. No provider/CH egress.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.tasks import error_analysis
from tracer.tests import test_falcon_analysis_revocation as revocation
from tracer.views.feed.tab_views import FeedRootCauseView

landing = revocation.landing
scope_no_egress = revocation.scope_no_egress
running_analysis = revocation.running_analysis
pytestmark = pytest.mark.django_db(transaction=True)


def poll(state, monkeypatch):
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", Mock())
    monkeypatch.setattr(
        "tracer.views.feed.tab_views._accessible_project_ids_or_response",
        lambda *a: ([str(state.project.pk)], None),
    )
    monkeypatch.setattr(
        "tracer.queries.feed._cluster_qs_for_access",
        lambda *a: SimpleNamespace(exists=lambda: True),
    )
    monkeypatch.setattr("tracer.queries.feed._cluster_has_trace", lambda *a: True)
    request = APIRequestFactory().get(
        "/tracer/feed/issues/synthetic/root-cause/", {"trace_id": state.trace_id}
    )
    force_authenticate(request, user=state.landing.user)
    response = FeedRootCauseView.as_view()(request, cluster_id="synthetic")
    assert response.status_code == 200, response.data
    return response.data["result"]


def persist(state):
    # Simulate a historical/pre-deploy partial row, deliberately unbound.
    analysis = TraceErrorAnalysis(
        trace_id=state.trace_id,
        project=state.project,
        overall_score=4,
        total_errors=0,
        recommended_priority="LOW",
        agent_version="falcon-skill-1.0",
    )
    TraceErrorAnalysis._base_manager.bulk_create([analysis])
    return analysis


@pytest.mark.parametrize(
    "reason", ["analysis_access_revoked", "analysis_deadline_exceeded"]
)
def test_aborted_persisted_analysis_get_is_retryable(
    running_analysis, monkeypatch, reason
):
    state = running_analysis
    analysis = persist(state)
    if reason == "analysis_access_revoked":
        # A viewer can still read the analysis, but may not continue writing it.
        state.mutation = lambda: revocation.revoke(state, "role")
    else:
        state.stall = True
        monkeypatch.setattr(error_analysis, "FALCON_ANALYSIS_TIMEOUT_SECONDS", 0.05)
    outcome = revocation.run(state)
    assert not outcome["success"]
    assert outcome["code"] == reason
    assert TraceErrorAnalysis._base_manager.filter(pk=analysis.pk).exists()
    response = poll(state, monkeypatch)
    assert response["status"] == "failed"
    assert response["code"] == reason
    assert response["root_causes"] == []


def test_completed_analysis_get_remains_done(running_analysis, monkeypatch):
    state = running_analysis
    persist(state)
    assert revocation.run(state)["success"]
    response = poll(state, monkeypatch)
    assert response["status"] == "done"
    assert response["code"] is None
