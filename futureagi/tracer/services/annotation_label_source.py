"""Routable source for annotation-label discovery (which labels have scores in a project).

Routed via ``_REGISTRY["ANNOTATION_LABELS"]`` (see v2/dispatch.py):

- ``V1_ONLY`` → :class:`AnnotationLabelScoresPG` — joins ``Score`` through
  ``trace``/``observation_span`` (valid only while the legacy PG tables exist).
- ``V2_ONLY``/``V2_PRIMARY`` → :class:`AnnotationLabelScoresProjectPG` — filters
  ``Score`` on the denormalized ``tracer_project_id`` (valid post-CH25, cheap).

:class:`AnnotationLabelScoresCH` scopes ``model_hub_score`` via the CH ``spans``
table. Its ``spans`` scan OOMs at scale, so label discovery moved to the PG
source above; the CH class is retained for the annotator / categorical
filter-value reads that have no PG equivalent.

``label_ids_for_project(project_id) -> list[str]`` is the dispatched entrypoint
(all sources expose it) so the dispatcher stays backend-blind.

Note: ``Score.project``/``model_hub_score.project_id`` point at
``model_hub.DevelopAI`` (a different id space) and are NOT used for scoping; the
denormalized ``Score.tracer_project_id`` carries the ``tracer.Project`` id.
"""
from __future__ import annotations


class AnnotationLabelScoresPG:
    """v1: label ids of scores in a project, via PG joins (legacy tables)."""

    def label_ids_for_project(self, project_id) -> list[str]:
        from django.db.models import Q

        from model_hub.models.score import Score

        return [
            str(lid)
            for lid in Score.objects.filter(
                Q(trace__project_id=project_id)
                | Q(observation_span__project_id=project_id),
                deleted=False,
            )
            .values_list("label_id", flat=True)
            .distinct()
            if lid
        ]


# Scope model_hub_score (s) to tracer projects via spans. Param: project_ids (list[str]).
_CH_PROJECT_SCOPE = """(
        (isNotNull(s.trace_id) AND toString(s.trace_id) IN (
            SELECT DISTINCT trace_id FROM spans
            WHERE project_id IN %(project_ids)s AND is_deleted = 0
        ))
        OR (s.observation_span_id IN (
            SELECT DISTINCT id FROM spans
            WHERE project_id IN %(project_ids)s AND is_deleted = 0
        ))
    )"""


class AnnotationLabelScoresProjectPG:
    """v2: label ids of scores in a project, via denormalized ``Score.tracer_project_id``.

    Replaces the CH ``spans``-scoped scan (see :class:`AnnotationLabelScoresCH`),
    which OOMs at scale. Keeps trace+span parity with that query via the
    ``trace_id``/``observation_span_id`` not-null predicate — session-only and
    other non-trace/span scores stay excluded, matching prior behavior.
    """

    def label_ids_for_project(self, project_id) -> list[str]:
        from django.db.models import Q

        from model_hub.models.score import Score

        return [
            str(lid)
            for lid in Score.objects.filter(
                Q(trace_id__isnull=False) | Q(observation_span_id__isnull=False),
                tracer_project_id=project_id,
                deleted=False,
            )
            .values_list("label_id", flat=True)
            .distinct()
            if lid
        ]


class AnnotationLabelScoresCH:
    """label ids + filter-value reads over ``model_hub_score``, scoped by ``spans``.

    Label discovery for the ANNOTATION_LABELS dispatch now routes to
    :class:`AnnotationLabelScoresProjectPG` (the ``spans`` scan here OOMs at
    scale); this class is retained for the annotator / categorical filter-value
    reads below, which have no PG equivalent.
    """

    _QUERY = f"""
        SELECT DISTINCT toString(label_id) AS label_id
        FROM model_hub_score AS s FINAL
        WHERE s.deleted = false
          AND s._peerdb_is_deleted = 0
          AND {_CH_PROJECT_SCOPE}
    """

    def label_ids_for_project(self, project_id) -> list[str]:
        from tracer.services.clickhouse.client import get_clickhouse_client

        rows, _types, _ms = get_clickhouse_client().execute_read(
            self._QUERY, {"project_ids": [str(project_id)]}, timeout_ms=30000
        )
        return [r[0] for r in rows if r and r[0]]

    def annotator_ids_for_projects(self, project_ids: list[str]) -> list[str]:
        if not project_ids:
            return []
        from tracer.services.clickhouse.client import get_clickhouse_client

        query = f"""
            SELECT DISTINCT toString(annotator_id) AS annotator_id
            FROM model_hub_score AS s FINAL
            WHERE s.deleted = false AND s._peerdb_is_deleted = 0
              AND isNotNull(s.annotator_id)
              AND {_CH_PROJECT_SCOPE}
        """
        rows, _t, _ms = get_clickhouse_client().execute_read(
            query, {"project_ids": [str(p) for p in project_ids]}, timeout_ms=30000
        )
        return [r[0] for r in rows if r and r[0]]

    def categorical_values_for_label(
        self, label_id, project_ids: list[str]
    ) -> list[str]:
        if not project_ids:
            return []
        from tracer.services.clickhouse.client import get_clickhouse_client

        query = f"""
            SELECT value FROM model_hub_score AS s FINAL
            WHERE s.deleted = false AND s._peerdb_is_deleted = 0
              AND toString(s.label_id) = %(label_id)s
              AND {_CH_PROJECT_SCOPE}
            ORDER BY s.updated_at DESC
            LIMIT 5000
        """
        rows, _t, _ms = get_clickhouse_client().execute_read(
            query,
            {"label_id": str(label_id), "project_ids": [str(p) for p in project_ids]},
            timeout_ms=30000,
        )
        return [r[0] for r in rows if r and r[0] not in (None, "")]
