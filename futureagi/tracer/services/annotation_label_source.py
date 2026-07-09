"""Routable source for annotation-label discovery (which labels have scores in a project).

Routed via ``_REGISTRY["ANNOTATION_LABELS"]`` (see v2/dispatch.py): ``V1_ONLY``
uses the PG source (joins ``Score`` through ``trace``/``observation_span`` — valid
while the legacy PG tables exist); ``V2_ONLY``/``V2_PRIMARY`` use the CH source
(scopes ``model_hub_score`` via ``spans`` — valid post-CH25 once those PG tables
are dropped).

Both expose ``label_ids_for_project(project_id) -> list[str]``
so the dispatcher stays backend-blind (self-executing, returns rows not SQL).

Note: ``Score.project``/``model_hub_score.project_id`` point at
``model_hub.DevelopAI`` (a different id space), so neither source filters on it —
project scoping goes through trace/span which carry the ``tracer.Project`` id.
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


class AnnotationLabelScoresCH:
    """v2: label ids of scores in a project, via CH ``model_hub_score`` scoped by ``spans``."""

    _QUERY = """
        SELECT DISTINCT toString(label_id) AS label_id
        FROM model_hub_score AS s FINAL
        WHERE s.deleted = false
          AND s._peerdb_is_deleted = 0
          AND (
            (isNotNull(s.trace_id) AND toString(s.trace_id) IN (
                SELECT DISTINCT trace_id FROM spans
                WHERE project_id = toUUID(%(project_id)s) AND is_deleted = 0
            ))
            OR (s.observation_span_id IN (
                SELECT DISTINCT id FROM spans
                WHERE project_id = toUUID(%(project_id)s) AND is_deleted = 0
            ))
          )
    """

    def label_ids_for_project(self, project_id) -> list[str]:
        from tracer.services.clickhouse.client import get_clickhouse_client

        rows, _types, _ms = get_clickhouse_client().execute_read(
            self._QUERY, {"project_id": str(project_id)}, timeout_ms=30000
        )
        return [r[0] for r in rows if r and r[0]]
