"""Existing-pin compiler seam, not a production Sessions authority/provider.

Runs inside PinCapture's dedicated primary connection, after capture_pin and
before PREPARE. Its query is executed only by the separately qualified local
reader transport; no HTTP endpoint binds it and CurrentFactsRead stays gated.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PinnedStoreCount:
    organization_id: str
    project_id: str
    table_name: str
    shard: tuple[str, str]

    def __call__(self, manifest, definition):
        from tracer.models.project import Project
        from tracer.services.derived_metrics.publication import committed_rows_select
        from tracer.services.derived_metrics.reader_lifetime import IncapableReadLane
        from tracer.services.derived_metrics.storage import TABLES

        if (
            str(manifest.project_id) != self.project_id
            or manifest.revoked_at is not None
            or manifest.visibility_receipts.get("shard") != list(self.shard)
            or "batches" not in manifest.visibility_receipts
            or not Project.no_workspace_objects.using("default")
            .filter(
                pk=self.project_id,
                organization_id=self.organization_id,
                deleted=False,
            )
            .exists()
        ):
            raise IncapableReadLane("pinned local manifest unavailable")
        sql, params = committed_rows_select(
            TABLES[self.table_name],
            manifest,
            org_id=self.organization_id,
            project_id=self.project_id,
        )
        return f"SELECT count() FROM ({sql})", params
