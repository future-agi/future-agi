"""Unit tests for the Linear issue description builder.

The view itself (POST /tracer/feed/issues/{cluster_id}/create-linear-issue/)
is exercised end-to-end elsewhere; this file pins the description shape
so Linear tickets keep landing with a backlink to the cluster and — when
a deep analysis has run — the root causes + immediate fixes inline.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings

from tracer.types.feed_types import (
    DeepAnalysisResponse,
    Recommendation,
    RootCause,
)
from tracer.views.feed.linear_issue_view import (
    _build_issue_description,
    _cluster_url,
)


# ---------------------------------------------------------------------------
# _cluster_url helper
# ---------------------------------------------------------------------------


@override_settings(APP_URL="app.futureagi.com", ssl="https://")
class TestClusterUrl:
    def test_builds_url_from_app_url_and_scheme(self):
        url = _cluster_url("E-ABC123")
        assert url == "https://app.futureagi.com/dashboard/error-feed/E-ABC123"


@override_settings(APP_URL=None)
class TestClusterUrlWithoutAppUrl:
    def test_returns_empty_when_app_url_unset(self):
        # No APP_URL configured (some envs); helper returns "" so the
        # description builder can fall back to a non-link mention.
        assert _cluster_url("E-ABC123") == ""


# ---------------------------------------------------------------------------
# _build_issue_description
# ---------------------------------------------------------------------------


def _cluster(cluster_id="E-CAFEBABE"):
    return SimpleNamespace(cluster_id=cluster_id)


def _done_analysis(trace_id="trace-123", with_findings=True):
    if not with_findings:
        return DeepAnalysisResponse(status="done", trace_id=trace_id)
    return DeepAnalysisResponse(
        status="done",
        trace_id=trace_id,
        root_causes=[
            RootCause(
                rank=1,
                title="Missing null check",
                description="response.data may be None when upstream times out",
            ),
            RootCause(rank=2, title="Stale cache key", description=""),
        ],
        recommendations=[
            Recommendation(
                id="E001",
                title="Add null guard",
                description="...",
                priority="high",
                immediate_fix="`if response.data is None: return early`",
            ),
            Recommendation(
                id="E002",
                title="Cache without immediate fix",
                description="...",
                priority="medium",
                immediate_fix=None,
            ),
        ],
    )


@override_settings(APP_URL="app.futureagi.com", ssl="https://")
class TestBuildIssueDescriptionBacklink:
    """The backlink must always be the first line — it's the only piece
    of context that lets a Linear assignee actually find the cluster."""

    def test_backlink_present_without_trace_id(self):
        body = _build_issue_description(_cluster("E-1"), trace_id=None)
        assert "[View in Future AGI](" in body
        assert "/dashboard/error-feed/E-1" in body
        assert "`E-1`" in body

    def test_no_findings_when_trace_id_missing(self):
        body = _build_issue_description(_cluster("E-1"), trace_id=None)
        assert "## Root causes" not in body
        assert "## Immediate fixes" not in body


@override_settings(APP_URL=None)
class TestBuildIssueDescriptionWithoutAppUrl:
    def test_falls_back_to_plain_cluster_mention(self):
        body = _build_issue_description(_cluster("E-1"), trace_id=None)
        # No URL → no markdown link, but cluster_id still mentioned.
        assert "[View in Future AGI](" not in body
        assert "E-1" in body


@override_settings(APP_URL="app.futureagi.com", ssl="https://")
class TestBuildIssueDescriptionWithDeepAnalysis:
    @patch("tracer.views.feed.linear_issue_view.feed_service.get_deep_analysis")
    def test_done_analysis_renders_root_causes_and_fixes(self, mock_get):
        mock_get.return_value = _done_analysis()

        body = _build_issue_description(_cluster("E-1"), trace_id="trace-123")

        # Both section headers present
        assert "## Root causes" in body
        assert "## Immediate fixes" in body
        # Root cause titles + descriptions rendered
        assert "Missing null check" in body
        assert "response.data may be None when upstream times out" in body
        # Only the recommendation with a non-null immediate_fix is included
        assert "Add null guard" in body
        assert "Cache without immediate fix" not in body

    @patch("tracer.views.feed.linear_issue_view.feed_service.get_deep_analysis")
    def test_done_analysis_without_findings_omits_sections(self, mock_get):
        mock_get.return_value = _done_analysis(with_findings=False)

        body = _build_issue_description(_cluster("E-1"), trace_id="trace-123")

        assert "## Root causes" not in body
        assert "## Immediate fixes" not in body
        # Backlink still present.
        assert "[View in Future AGI](" in body

    @patch("tracer.views.feed.linear_issue_view.feed_service.get_deep_analysis")
    def test_running_analysis_omits_findings(self, mock_get):
        # Don't include partial findings when the analysis hasn't
        # converged yet — wait for it to flip to "done".
        mock_get.return_value = DeepAnalysisResponse(
            status="running", trace_id="trace-123"
        )

        body = _build_issue_description(_cluster("E-1"), trace_id="trace-123")

        assert "## Root causes" not in body
        assert "## Immediate fixes" not in body

    @patch("tracer.views.feed.linear_issue_view.feed_service.get_deep_analysis")
    def test_lookup_failure_is_best_effort(self, mock_get):
        # If the deep-analysis lookup raises, the ticket creation must
        # still proceed with just the backlink — context is best-effort.
        mock_get.side_effect = RuntimeError("CH outage")

        body = _build_issue_description(_cluster("E-1"), trace_id="trace-123")

        assert "[View in Future AGI](" in body
        assert "## Root causes" not in body

    @patch("tracer.views.feed.linear_issue_view.feed_service.get_deep_analysis")
    def test_no_lookup_when_trace_id_is_none(self, mock_get):
        body = _build_issue_description(_cluster("E-1"), trace_id=None)

        # Skip the lookup entirely — saves a DB round-trip when the FE
        # doesn't have a trace selected.
        mock_get.assert_not_called()
        assert "[View in Future AGI](" in body
