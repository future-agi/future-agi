"""Unit tests for the Linear issue description builder.

The view itself (POST /tracer/feed/issues/{cluster_id}/create-linear-issue/)
is exercised end-to-end elsewhere; this file pins the description shape so
Linear tickets keep landing with a backlink to the cluster and — when a
cluster RCA has run — its synthesis, fix, confidence, and (UUID-only)
evidence traces inline. When a trace is supplied, the evaluator's reasoning
for that sampled trace is appended best-effort.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from tracer.views.feed.linear_issue_view import (
    _build_issue_description,
    _cluster_url,
)

# ---------------------------------------------------------------------------
# _cluster_url helper
# ---------------------------------------------------------------------------


@override_settings(APP_URL="app.futureagi.com", ssl="https://")
class TestClusterUrl(SimpleTestCase):
    def test_builds_url_from_app_url_and_scheme(self):
        url = _cluster_url("E-ABC123")
        assert url == "https://app.futureagi.com/dashboard/error-feed/E-ABC123"


@override_settings(APP_URL=None)
class TestClusterUrlWithoutAppUrl(SimpleTestCase):
    def test_returns_empty_when_app_url_unset(self):
        # No APP_URL configured (some envs); helper returns "" so the
        # description builder can fall back to a non-link mention.
        assert _cluster_url("E-ABC123") == ""


# ---------------------------------------------------------------------------
# _build_issue_description
# ---------------------------------------------------------------------------


def _cluster(
    cluster_id="E-CAFEBABE",
    priority="high",
    unique_traces=12,
    issue_group="timeout",
    rca_synthesis=None,
    rca_fix=None,
    rca_confidence=None,
    rca_evidence_trace_ids=None,
):
    """Cluster fixture with every field _build_issue_description reads."""
    return SimpleNamespace(
        cluster_id=cluster_id,
        priority=priority,
        unique_traces=unique_traces,
        issue_group=issue_group,
        rca_synthesis=rca_synthesis,
        rca_fix=rca_fix,
        rca_confidence=rca_confidence,
        rca_evidence_trace_ids=rca_evidence_trace_ids,
    )


@override_settings(APP_URL="app.futureagi.com", ssl="https://")
class TestBuildIssueDescriptionBacklink(SimpleTestCase):
    """The backlink must always be the first line — it's the only piece
    of context that lets a Linear assignee actually find the cluster."""

    def test_backlink_is_first_line(self):
        body = _build_issue_description(_cluster("E-1"), trace_id=None)
        first = body.splitlines()[0]
        assert "[View in Future AGI](" in first
        assert "/dashboard/error-feed/E-1" in first
        assert "`E-1`" in first

    def test_no_rca_sections_when_cluster_has_no_synthesis(self):
        body = _build_issue_description(_cluster("E-1"), trace_id=None)
        assert "## Root cause analysis" not in body
        assert "## Recommended fix" not in body
        assert "Confidence:" not in body
        assert "Evidence traces:" not in body


@override_settings(APP_URL=None)
class TestBuildIssueDescriptionWithoutAppUrl(SimpleTestCase):
    def test_falls_back_to_plain_cluster_mention(self):
        body = _build_issue_description(_cluster("E-1"), trace_id=None)
        first = body.splitlines()[0]
        # No URL → no markdown link, but cluster_id still mentioned.
        assert "[View in Future AGI](" not in body
        assert "`E-1`" in first


@override_settings(APP_URL="app.futureagi.com", ssl="https://")
class TestBuildIssueDescriptionRca(SimpleTestCase):
    def test_synthesis_fix_and_confidence_rendered(self):
        cluster = _cluster(
            "E-1",
            rca_synthesis="Upstream returns null on timeout, unchecked.",
            rca_fix="Guard response.data before use.",
            rca_confidence="high",
        )
        body = _build_issue_description(cluster, trace_id=None)

        assert "## Root cause analysis" in body
        assert "Upstream returns null on timeout, unchecked." in body
        assert "## Recommended fix" in body
        assert "Guard response.data before use." in body
        assert "Confidence: **high**" in body

    def test_fix_omitted_without_rca_fix(self):
        cluster = _cluster("E-1", rca_synthesis="Synthesis only.")
        body = _build_issue_description(cluster, trace_id=None)

        assert "## Root cause analysis" in body
        assert "## Recommended fix" not in body
        assert "Confidence:" not in body

    def test_evidence_uuid_filter_drops_aliases_and_caps_at_five(self):
        # Pre-alias-fix runs persisted LLM-facing labels (T01, Sp01) mixed
        # in with real trace UUIDs; only the UUIDs are meaningful outside
        # the run, and at most five should ship.
        real = [str(uuid.uuid4()) for _ in range(6)]
        cluster = _cluster(
            "E-1",
            rca_synthesis="Synthesis.",
            rca_evidence_trace_ids=[
                "T01",
                real[0],
                "Sp01",
                real[1],
                real[2],
                real[3],
                real[4],
                real[5],
            ],
        )
        body = _build_issue_description(cluster, trace_id=None)

        assert "Evidence traces:" in body
        # Aliases never appear.
        assert "T01" not in body
        assert "Sp01" not in body
        # Capped at five — the first five UUIDs ship, the sixth does not.
        for tid in real[:5]:
            assert tid in body
        assert real[5] not in body


@override_settings(APP_URL="app.futureagi.com", ssl="https://")
class TestBuildIssueDescriptionTraceJudge(SimpleTestCase):
    @patch("tracer.views.feed.linear_issue_view.trace_judge")
    def test_evaluator_reasoning_rendered_with_score(self, mock_judge):
        mock_judge.return_value = ("Output drifted from the rubric.", 0.42)

        trace_id = uuid.uuid4()
        body = _build_issue_description(_cluster("E-1"), trace_id=trace_id)

        mock_judge.assert_called_once_with(str(trace_id))
        assert "## Evaluator reasoning — sampled trace (0.42/1.00)" in body
        assert "Output drifted from the rubric." in body

    @patch("tracer.views.feed.linear_issue_view.trace_judge")
    def test_no_score_suffix_when_score_is_none(self, mock_judge):
        mock_judge.return_value = ("Reasoning without a score.", None)

        body = _build_issue_description(_cluster("E-1"), trace_id=uuid.uuid4())

        assert "## Evaluator reasoning — sampled trace" in body
        assert "/1.00" not in body
        assert "Reasoning without a score." in body

    @patch("tracer.views.feed.linear_issue_view.trace_judge")
    def test_judge_raising_is_best_effort(self, mock_judge):
        # The lookup failing must not abort ticket creation: the
        # description is still built (backlink present), the evaluator
        # section is dropped, and the exception does not propagate.
        mock_judge.side_effect = RuntimeError("CH outage")

        body = _build_issue_description(_cluster("E-1"), trace_id=uuid.uuid4())

        assert "[View in Future AGI](" in body
        assert "## Evaluator reasoning" not in body

    @patch("tracer.views.feed.linear_issue_view.trace_judge")
    def test_no_judge_call_when_trace_id_is_none(self, mock_judge):
        body = _build_issue_description(_cluster("E-1"), trace_id=None)

        # No trace selected → skip the evaluator lookup entirely.
        mock_judge.assert_not_called()
        assert "[View in Future AGI](" in body
        assert "## Evaluator reasoning" not in body
