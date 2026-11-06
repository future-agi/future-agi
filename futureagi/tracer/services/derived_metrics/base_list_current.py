"""Inactive, window-independent current-fact SQL foundation.

A batch receipt proves bytes, not full source coverage or freshness. B04's
finite discovery and a static CTAS/checksum cannot qualify a retention window.
There is deliberately no production authority provider or caller-supplied
qualification flag here. Sessions uses native exact selection and hydration
until BL01-Q supplies and verifies a durable source/remap fence contract.
"""

from uuid import UUID

from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.storage import (
    LIST_CURRENT_MEMBERS,
    LIST_CURRENT_ROOTS,
)


class CurrentFactsRead:
    """SQL seam only; production cannot qualify a read at this foundation stage.

    Static-fixture tests subclass this seam under tracer.tests. Their declared
    assumptions are not production evidence and cannot activate a real request.
    """

    def __init__(self, manifest, org_id):
        self.manifest = manifest
        self.org_id = org_id
        self.active = True

    def permits(self, project_id):
        # No source-fenced provider exists yet. Neither a successful publication,
        # a live pin nor booleans supplied by a caller can change this result.
        return False

    def permits_user_activity(self, project_id: str | UUID) -> bool:
        # Schema 036 coverage cannot qualify the later by-user index. BL01-Q
        # must fence its complete backfill, user-key retractions and live delta
        # on this SAME pin before enabling schema 037 reads.
        return False

    def rows(self, *, members=False):
        if not self.permits(self.manifest.project_id):
            raise ValueError("current facts require an active qualified pin")
        return committed_rows_select(
            LIST_CURRENT_MEMBERS if members else LIST_CURRENT_ROOTS,
            self.manifest,
            org_id=self.org_id,
            project_id=self.manifest.project_id,
        )

    def session_ctes(self, builder, params, *, candidate_session_ids=()):
        """Exact min LIVE root INSIDE this arbitrary half-open request window."""
        sql, bound = self.rows()
        params.update(bound)
        predicate = builder._latest_time_scope_sql(
            params, param_prefix="current_list_time"
        )
        candidate = ""
        if candidate_session_ids:
            params["current_candidate_ids"] = candidate_session_ids
            candidate = " AND canonical_session_id IN %(current_candidate_ids)s"
        return f"""resolved_root_sessions AS (
            SELECT toUUID(canonical_session_id) AS session_id, project_id,
                latest_start_time AS start_time
            FROM (SELECT *, unit_start AS latest_start_time FROM ({sql}))
            WHERE is_deleted = 0
                AND canonical_session_id NOT IN ('', '00000000-0000-0000-0000-000000000000')
                {predicate}{candidate}
        ), sessions AS (
            SELECT session_id, min(start_time) AS session_start
            FROM resolved_root_sessions GROUP BY session_id
        )"""
