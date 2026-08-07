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
    def test_medoid_is_chosen_over_first_arrival(self, project):
        """The core fix. The outlier arrived first and named the cluster; the
        medoid is the member the group is actually about."""
        outlier = "Hallucinated context regarding UK property worth 500000"
        typical_a = "Queried past month instead of requested quarterly performance"
        typical_b = "Queried past month performance instead of requested quarter"
        cluster = _cluster_with(project, [outlier, typical_a, typical_b])
        assert cluster.title == outlier  # first arrival named it

        # outlier sits far from the other two; centroid sits with the pair
        vectors = {
            outlier: [1.0, 0.0, 0.0],
            typical_a: [0.0, 1.0, 0.0],
            typical_b: [0.0, 0.98, 0.2],
        }
        centroid = [0.0, 1.0, 0.1]

        with patch(
            "tracer.queries.scan_clustering.embed_texts",
            side_effect=lambda briefs: [vectors[b] for b in briefs],
        ):
            _retitle_from_members(cluster, centroid)

        cluster.refresh_from_db()
        assert cluster.title in (typical_a, typical_b)
        assert cluster.title != outlier

    def test_singleton_is_left_alone(self, project):
        """Nothing to be representative of — and re-embedding one brief to
        rename it to itself is pure waste."""
        only = "Agent returned raw empty LLM result"
        cluster = _cluster_with(project, [only])

        with patch("tracer.queries.scan_clustering.embed_texts") as embed:
            _retitle_from_members(cluster, [1.0, 0.0])

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
            _retitle_from_members(cluster, [1.0, 0.0])

        cluster.refresh_from_db()
        assert cluster.title == first

    def test_no_write_when_medoid_is_already_the_title(self, project):
        keep = "Queried past month instead of requested quarterly performance"
        other = "Something quite different about tool errors"
        cluster = _cluster_with(project, [keep, other])
        vectors = {keep: [0.0, 1.0], other: [1.0, 0.0]}

        with patch(
            "tracer.queries.scan_clustering.embed_texts",
            side_effect=lambda briefs: [vectors[b] for b in briefs],
        ):
            _retitle_from_members(cluster, [0.0, 1.0])

        cluster.refresh_from_db()
        assert cluster.title == keep


class TestRetitleThresholds:
    def test_growth_points_are_sparse_and_increasing(self):
        """Recomputing on every assignment would re-embed the whole cluster each
        time; these are the growth points where the medoid can actually shift."""
        assert list(_RETITLE_AT) == sorted(_RETITLE_AT)
        assert _RETITLE_AT[0] == 2
        assert len(_RETITLE_AT) <= 10
