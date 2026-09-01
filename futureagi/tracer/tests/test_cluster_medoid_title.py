"""Regression tests for fix 3b — medoid titling.

A cluster's title was `issue.brief` from whichever member arrived FIRST, frozen
forever. First arrival has no claim to being representative, so a 15-trace
cluster ends up named after one accidental member — the "heading doesn't
describe what's inside" complaint that started this audit (S-0F8414EA was titled
after "David Chen" while 0 of 20 visible members mentioned him).

The title is now recomputed from the medoid — the member nearest the centroid —
at a handful of growth points.
"""

from unittest.mock import patch

import pytest

from tracer.models.trace_error_analysis import ClusterSource, TraceErrorGroup
from tracer.models.trace_scan import TraceScanIssue, TraceScanResult, TraceScanStatus
from tracer.queries.scan_clustering import (
    _RETITLE_AT,
    _cosine_distance,
    _retitle_from_members,
)


class TestCosineDistance:
    def test_identical_vectors_are_zero(self):
        assert _cosine_distance([1.0, 0.0], [1.0, 0.0]) == pytest.approx(0.0)

    def test_orthogonal_vectors_are_one(self):
        assert _cosine_distance([1.0, 0.0], [0.0, 1.0]) == pytest.approx(1.0)

    def test_magnitude_does_not_matter(self):
        """Cosine is scale-invariant — a longer vector in the same direction is
        the same distance, which is what makes centroid comparison valid."""
        assert _cosine_distance([1.0, 0.0], [5.0, 0.0]) == pytest.approx(0.0)

    def test_zero_vector_does_not_divide_by_zero(self):
        assert _cosine_distance([0.0, 0.0], [1.0, 0.0]) == 1.0


def _cluster_with(project, briefs):
    cluster = TraceErrorGroup.objects.create(
        project_id=project.id,
        cluster_id="S-MEDOID1",
        source=ClusterSource.SCANNER,
        issue_group="Context Handling Failures",
        issue_category="Context Handling Failures",
        fix_layer="prompt",
        title=briefs[0],  # first-arrival title, the behaviour under test
        error_type="Context Handling Failures",
        total_events=len(briefs),
        unique_traces=len(briefs),
        error_count=len(briefs),
    )
    import uuid

    for brief in briefs:
        sr = TraceScanResult.objects.create(
            trace_id=str(uuid.uuid4()),
            project_id=project.id,
            status=TraceScanStatus.COMPLETED,
            has_issues=True,
            key_moments=[],
            meta={},
        )
        TraceScanIssue.objects.create(
            scan_result=sr,
            category="Context Handling Failures",
            group="Context Handling Failures",
            fix_layer="prompt",
            confidence="H",
            brief=brief,
            cluster=cluster,
        )
    return cluster


@pytest.mark.django_db
class TestRetitleFromMembers:
    """Medoid selection, with title generation held OFF.

    ``_retitle_from_members`` prefers a generated group title and falls back to
    the medoid only when one is unavailable. These tests are about the fallback,
    so they pin the generator to None rather than letting the environment decide
    — otherwise they assert the medoid while silently depending on EE being
    absent, and start failing the moment it is present. That is not theoretical:
    they pass in CI, where the EE symbol is missing, and fail against a checkout
    where it resolves.
    """

    @pytest.fixture(autouse=True)
    def _no_generated_title(self):
        with patch(
            "tracer.ee_boundary.generate_scan_cluster_title", return_value=None
        ):
            yield

    def test_a_generated_title_wins_over_the_medoid(self, project):
        """The generator is the preferred source — the medoid is the fallback.

        Without this, every test here runs with generation off and nothing
        covers the path that actually executes when EE is present.
        """
        medoid = "Queried past month instead of requested quarterly performance"
        cluster = _cluster_with(project, [medoid, "Queried past month, not the quarter"])
        generated = "Agent used the wrong period when answering performance questions"

        with patch(
            "tracer.queries.scan_clustering.embed_texts",
            side_effect=lambda briefs: [[1.0, 0.0] for _ in briefs],
        ), patch(
            "tracer.ee_boundary.generate_scan_cluster_title", return_value=generated
        ):
            _retitle_from_members(cluster)

        cluster.refresh_from_db()
        assert cluster.title == generated

    def test_medoid_is_chosen_over_first_arrival(self, project):
        """The core fix. The outlier arrived first and named the cluster; the
        medoid is the member the group is actually about."""
        outlier = "Hallucinated context regarding UK property worth 500000"
        typical_a = "Queried past month instead of requested quarterly performance"
        typical_b = "Queried past month performance instead of requested quarter"
        cluster = _cluster_with(project, [outlier, typical_a, typical_b])
        assert cluster.title == outlier  # first arrival named it

        # outlier sits far from the other two; the members' own mean sits with the pair
        vectors = {
            outlier: [1.0, 0.0, 0.0],
            typical_a: [0.0, 1.0, 0.0],
            typical_b: [0.0, 0.98, 0.2],
        }

        with patch(
            "tracer.queries.scan_clustering.embed_texts",
            side_effect=lambda briefs: [vectors[b] for b in briefs],
        ):
            _retitle_from_members(cluster)

        cluster.refresh_from_db()
        assert cluster.title in (typical_a, typical_b)
        assert cluster.title != outlier

    def test_medoid_is_measured_among_the_briefs_it_selects_from(self, project):
        """The stored centroid is built from ``embedding_text`` — the DISTILLED
        phrase — while the title must be a raw brief a person would recognise.
        Ranking raw briefs by distance to that centroid compares two different
        embedding spaces, and the winner is then near-arbitrary.

        Here the raw briefs cluster around the pair, so the medoid is one of
        them. Nothing outside this set may decide that.
        """
        outlier = "Agent invented a client named Dana Whitfield out of nothing"
        pair_a = "Answered with text instead of calling the portfolio tool"
        pair_b = "Answered in prose instead of invoking the portfolio tool"
        cluster = _cluster_with(project, [outlier, pair_a, pair_b])
        vectors = {
            outlier: [1.0, 0.0, 0.0],
            pair_a: [0.0, 1.0, 0.0],
            pair_b: [0.0, 0.99, 0.14],
        }

        with patch(
            "tracer.queries.scan_clustering.embed_texts",
            side_effect=lambda briefs: [vectors[b] for b in briefs],
        ):
            _retitle_from_members(cluster)

        cluster.refresh_from_db()
        assert cluster.title in (pair_a, pair_b)
        assert cluster.title != outlier

    def test_singleton_is_left_alone(self, project):
        """Nothing to be representative of — and re-embedding one brief to
        rename it to itself is pure waste."""
        only = "Agent returned raw empty LLM result"
        cluster = _cluster_with(project, [only])

        with patch("tracer.queries.scan_clustering.embed_texts") as embed:
            _retitle_from_members(cluster)

        embed.assert_not_called()
        cluster.refresh_from_db()
        assert cluster.title == only

    def test_embedding_failure_leaves_the_title_intact(self, project):
        """A stale title beats a broken assignment — retitling is best-effort."""
        first = "Queried past month instead of requested quarterly performance"
        cluster = _cluster_with(project, [first, "Some other brief"])

        with patch(
            "tracer.queries.scan_clustering.embed_texts",
            side_effect=RuntimeError("serving down"),
        ):
            _retitle_from_members(cluster)

        cluster.refresh_from_db()
        assert cluster.title == first

    def test_no_write_when_medoid_is_already_the_title(self, project):
        keep = "Queried past month instead of requested quarterly performance"
        near = "Queried past month rather than the requested quarter"
        other = "Something quite different about tool errors"
        # three members, with `keep` sitting between the other two so it really
        # is the medoid — two opposed vectors would tie and let position decide
        cluster = _cluster_with(project, [keep, near, other])
        vectors = {keep: [0.7, 0.714], near: [0.0, 1.0], other: [1.0, 0.0]}

        with patch(
            "tracer.queries.scan_clustering.embed_texts",
            side_effect=lambda briefs: [vectors[b] for b in briefs],
        ):
            _retitle_from_members(cluster)

        cluster.refresh_from_db()
        assert cluster.title == keep


class TestRetitleThresholds:
    def test_growth_points_are_sparse_and_increasing(self):
        """Recomputing on every assignment would re-embed the whole cluster each
        time; these are the growth points where the medoid can actually shift."""
        assert list(_RETITLE_AT) == sorted(_RETITLE_AT)
        assert _RETITLE_AT[0] == 2
        assert len(_RETITLE_AT) <= 10
