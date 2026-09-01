"""Tests for annotation-label discovery sources.

helper.get_annotation_labels_for_project swapped an inline PG query for a
registry-routed PG-or-CH source. These pin the backend behavior contract that
swap changes:

  * the v2 (CH) source scopes ``model_hub_score`` by ``spans`` and gates the
    same delete predicates as the annotation render (``build_annotation_query``),
  * the CH source returns the same label set the PG source did,
  * a CDC-tombstoned score (``_peerdb_is_deleted = 1``) is excluded by BOTH
    discovery and the render — the divergence that produced ghost labels.

The behavior tests seed CH directly (no CDC in the test path) via ``_ch_seed``.
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from model_hub.models.choices import AnnotationTypeChoices, QueueItemSourceType
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tracer.models.observation_span import ObservationSpan


# --------------------------------------------------------------------------- #
# Cheap query-contract guard (no DB): runs in the unit lane.
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestDiscoveryQueryContract:
    """Discovery and the annotation render must keep the same model_hub_score
    delete predicates, else discovery drifts from what actually renders."""

    def _discovery_query(self) -> str:
        from tracer.services.annotation_label_source import AnnotationLabelScoresCH

        return AnnotationLabelScoresCH._QUERY

    def _render_query(self) -> str:
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(project_id="p", annotation_label_ids=["l"])
        query, _ = builder.build_annotation_query(["s"])
        return query

    def test_discovery_scopes_model_hub_score_via_spans(self):
        q = self._discovery_query()
        assert "model_hub_score" in q
        assert "FROM spans" in q

    @pytest.mark.parametrize("predicate", ["deleted = false", "_peerdb_is_deleted = 0"])
    def test_delete_predicates_match_render(self, predicate):
        assert predicate in self._discovery_query()
        assert predicate in self._render_query()

    def test_registry_routes_annotation_labels(self):
        from tracer.services.annotation_label_source import (
            AnnotationLabelScoresPG,
            AnnotationLabelScoresProjectPG,
        )
        from tracer.services.clickhouse.v2.dispatch import get_v1_class, get_v2_class

        # v2 discovery now answers from the denormalized tracer_project_id in PG
        # (the CH spans-scan class is kept only for the dashboard filter reads).
        assert get_v1_class("ANNOTATION_LABELS") is AnnotationLabelScoresPG
        assert get_v2_class("ANNOTATION_LABELS") is AnnotationLabelScoresProjectPG


# --------------------------------------------------------------------------- #
# Behavior tests (Postgres + ClickHouse): seed both stores, run the sources.
# --------------------------------------------------------------------------- #
def _make_label(organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name=f"Label {uuid.uuid4().hex[:8]}",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
        project=project,
    )


def _make_span(project, trace):
    return ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="Span",
        observation_type="llm",
        start_time=timezone.now(),
        end_time=timezone.now(),
        status="OK",
    )


def _make_score(*, label, span, organization, workspace, user):
    # Score.project points at model_hub.DevelopAI (a different id space), so it
    # is left unset — both sources scope via the span's tracer.Project instead.
    return Score.objects.create(
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span=span,
        label=label,
        value={"rating": 4.0},
        score_source="HUMAN",
        annotator=user,
        organization=organization,
        workspace=workspace,
        deleted=False,
    )


def _seed_tombstoned_ch_score(score):
    """Seed a CH model_hub_score row with _peerdb_is_deleted = 1 (CDC tombstone)
    while the PG row stays deleted = false — the ghost-label condition."""
    from tracer.tests._ch_seed import (
        _SCORE_INSERT_COLUMNS,
        _get_ch_client,
        _score_row_from_django,
    )

    row = list(_score_row_from_django(score))
    row[_SCORE_INSERT_COLUMNS.index("_peerdb_is_deleted")] = 1
    client = _get_ch_client()
    try:
        client.insert("model_hub_score", [tuple(row)], column_names=_SCORE_INSERT_COLUMNS)
    finally:
        client.close()


def _labels_with_rendered_annotations(project_id, span_ids, label_ids):
    """Run the annotation render query and return the set of label_ids that
    actually come back — i.e. the labels the render would display."""
    from tracer.services.clickhouse.client import get_clickhouse_client
    from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

    builder = SpanListQueryBuilder(
        project_id=str(project_id), annotation_label_ids=[str(x) for x in label_ids]
    )
    query, params = builder.build_annotation_query([str(s) for s in span_ids])
    rows, _types, _ms = get_clickhouse_client().execute_read(query, params)
    return {str(r[1]) for r in rows if r}


@pytest.mark.django_db
class TestAnnotationLabelSourceBehavior:
    def test_pg_and_ch_sources_return_same_labels(
        self, organization, workspace, project, trace, user
    ):
        from tracer.services.annotation_label_source import (
            AnnotationLabelScoresCH,
            AnnotationLabelScoresPG,
        )
        from tracer.tests._ch_seed import seed_ch_score, seed_ch_span

        labels, spans, scores = [], [], []
        for _ in range(2):
            label = _make_label(organization, workspace, project)
            span = _make_span(project, trace)
            seed_ch_span(span)
            score = _make_score(
                label=label,
                span=span,
                organization=organization,
                workspace=workspace,
                user=user,
            )
            seed_ch_score(score)
            labels.append(str(label.id))
            spans.append(span)
            scores.append(score)

        pg = set(AnnotationLabelScoresPG().label_ids_for_project(project.id))
        ch = set(AnnotationLabelScoresCH().label_ids_for_project(project.id))

        assert pg == set(labels)
        assert ch == pg

    def test_cdc_tombstoned_label_excluded_by_discovery_and_render(
        self, organization, workspace, project, trace, user
    ):
        from tracer.services.annotation_label_source import AnnotationLabelScoresCH
        from tracer.tests._ch_seed import seed_ch_score, seed_ch_span

        # Visible: normal score (CH _peerdb_is_deleted = 0).
        visible_label = _make_label(organization, workspace, project)
        visible_span = _make_span(project, trace)
        seed_ch_span(visible_span)
        visible_score = _make_score(
            label=visible_label,
            span=visible_span,
            organization=organization,
            workspace=workspace,
            user=user,
        )
        seed_ch_score(visible_score)

        # Ghost: CDC-tombstoned in CH (_peerdb_is_deleted = 1) but deleted=false in PG.
        ghost_label = _make_label(organization, workspace, project)
        ghost_span = _make_span(project, trace)
        seed_ch_span(ghost_span)
        ghost_score = _make_score(
            label=ghost_label,
            span=ghost_span,
            organization=organization,
            workspace=workspace,
            user=user,
        )
        _seed_tombstoned_ch_score(ghost_score)

        discovered = set(AnnotationLabelScoresCH().label_ids_for_project(project.id))
        rendered = _labels_with_rendered_annotations(
            project.id,
            [visible_span.id, ghost_span.id],
            [visible_label.id, ghost_label.id],
        )

        # Discovery agrees with the render: visible in both, ghost in neither.
        assert str(visible_label.id) in discovered
        assert str(ghost_label.id) not in discovered
        assert discovered == rendered


def _make_span_score(*, label, span, organization, workspace, user, project):
    """A span Score stamped with tracer_project_id (as the write-sites now do)."""
    score = _make_score(
        label=label,
        span=span,
        organization=organization,
        workspace=workspace,
        user=user,
    )
    score.tracer_project_id = project.id
    score.save(update_fields=["tracer_project_id"])
    return score


# --------------------------------------------------------------------------- #
# tracer_project_id_for_source: source-type gating + project_id fast path (no DB).
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestTracerProjectIdForSource:
    def _fn(self):
        from model_hub.utils.annotation_queue_helpers import (
            tracer_project_id_for_source,
        )

        return tracer_project_id_for_source

    def test_non_tracer_source_returns_none(self):
        from types import SimpleNamespace

        obj = SimpleNamespace(project_id=uuid.uuid4())
        for st in ("dataset_row", "call_execution", "prototype_run"):
            assert self._fn()(st, obj) is None

    def test_tracer_source_reads_project_id_fast_path(self):
        from types import SimpleNamespace

        pid = uuid.uuid4()
        obj = SimpleNamespace(project_id=pid)  # no .project → must not hit DB
        for st in ("trace", "observation_span", "trace_session"):
            assert self._fn()(st, obj) == pid

    def test_falls_back_to_project_relation(self):
        from types import SimpleNamespace

        pid = uuid.uuid4()
        obj = SimpleNamespace(project_id=None, project=SimpleNamespace(id=pid))
        assert self._fn()("observation_span", obj) == pid

    def test_no_resolvable_project_returns_none(self):
        from types import SimpleNamespace

        obj = SimpleNamespace(project_id=None, project=None)
        assert self._fn()("trace", obj) is None


# --------------------------------------------------------------------------- #
# AnnotationLabelScoresProjectPG: parity, session exclusion, distinct guard.
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestProjectPGDiscovery:
    def test_parity_with_ch_scope(
        self, organization, workspace, project, trace, user
    ):
        from tracer.services.annotation_label_source import (
            AnnotationLabelScoresCH,
            AnnotationLabelScoresProjectPG,
        )
        from tracer.tests._ch_seed import seed_ch_score, seed_ch_span

        labels = []
        for _ in range(2):
            label = _make_label(organization, workspace, project)
            span = _make_span(project, trace)
            seed_ch_span(span)
            score = _make_span_score(
                label=label,
                span=span,
                organization=organization,
                workspace=workspace,
                user=user,
                project=project,
            )
            seed_ch_score(score)
            labels.append(str(label.id))

        pg = set(AnnotationLabelScoresProjectPG().label_ids_for_project(project.id))
        ch = set(AnnotationLabelScoresCH().label_ids_for_project(project.id))
        assert pg == set(labels)
        assert pg == ch  # PG source matches the CH spans-scope it replaces

    def test_session_only_score_excluded(
        self, organization, workspace, project, user
    ):
        from tracer.models.trace_session import TraceSession
        from tracer.services.annotation_label_source import (
            AnnotationLabelScoresProjectPG,
        )

        session = TraceSession.objects.create(project=project)
        label = _make_label(organization, workspace, project)
        # tracer_project_id populated, but no trace_id / observation_span_id.
        Score.objects.create(
            source_type=QueueItemSourceType.TRACE_SESSION.value,
            trace_session=session,
            label=label,
            value={"rating": 4.0},
            score_source="HUMAN",
            annotator=user,
            organization=organization,
            workspace=workspace,
            tracer_project_id=project.id,
            deleted=False,
        )

        labels = AnnotationLabelScoresProjectPG().label_ids_for_project(project.id)
        assert str(label.id) not in labels  # not-null trace/span predicate excludes it

    def test_distinct_returns_each_label_once(
        self, organization, workspace, project, trace, user
    ):
        # Guards the BaseModel-ordering-defeats-DISTINCT bug: two scores of the
        # same label must collapse to one label id, not one row per score.
        from tracer.services.annotation_label_source import (
            AnnotationLabelScoresProjectPG,
        )

        label = _make_label(organization, workspace, project)
        for _ in range(2):
            span = _make_span(project, trace)
            _make_span_score(
                label=label,
                span=span,
                organization=organization,
                workspace=workspace,
                user=user,
                project=project,
            )

        result = AnnotationLabelScoresProjectPG().label_ids_for_project(project.id)
        assert result == [str(label.id)]


# --------------------------------------------------------------------------- #
# Backfill: idempotency + never overwriting an existing value.
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestBackfillTracerProject:
    def test_tag_is_idempotent_and_never_overwrites(
        self, organization, workspace, project, trace, user
    ):
        from model_hub.management.commands.backfill_score_tracer_project import _tag

        span1 = _make_span(project, trace)
        span2 = _make_span(project, trace)
        s1 = _make_score(
            label=_make_label(organization, workspace, project),
            span=span1,
            organization=organization,
            workspace=workspace,
            user=user,
        )
        s2 = _make_score(
            label=_make_label(organization, workspace, project),
            span=span2,
            organization=organization,
            workspace=workspace,
            user=user,
        )
        # s2 already carries a DIFFERENT project id — must be left untouched.
        other = uuid.uuid4()
        Score.no_workspace_objects.filter(pk=s2.pk).update(tracer_project_id=other)

        n1 = _tag(project.id, "observation_span_id", [str(span1.id), str(span2.id)])
        assert n1 == 1  # only the NULL row (s1)
        s1.refresh_from_db()
        s2.refresh_from_db()
        assert s1.tracer_project_id == project.id
        assert s2.tracer_project_id == other  # not overwritten

        # Re-run: nothing left to stamp.
        assert _tag(project.id, "observation_span_id", [str(span1.id), str(span2.id)]) == 0

    def test_backfill_from_ch_spans_then_noop(
        self, organization, workspace, project, trace, user
    ):
        from model_hub.management.commands.backfill_score_tracer_project import (
            backfill_tracer_project_ids,
        )
        from tracer.tests._ch_seed import seed_ch_span

        span = _make_span(project, trace)
        seed_ch_span(span)
        score = _make_score(
            label=_make_label(organization, workspace, project),
            span=span,
            organization=organization,
            workspace=workspace,
            user=user,
        )
        assert score.tracer_project_id is None

        res = backfill_tracer_project_ids()
        score.refresh_from_db()
        assert score.tracer_project_id == project.id
        assert res["updated"] >= 1

        # Idempotent second run.
        assert backfill_tracer_project_ids()["updated"] == 0
