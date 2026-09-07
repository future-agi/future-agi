"""Native dataset vocabulary visibility, independent of catalog activation.

The CDC landing tables in ``clickhouse/schema.py`` carry two distinct deletion
flags: PeerDB physical tombstones and the application's soft-delete flag. A
live cell also needs its current row and dataset. Resolve each table with FINAL
before filtering; never recover values from an older version of a deleted row.
"""


def live_dataset_column_queryset(*, workspace, dataset_id, column_id):
    """Bind explicit dataset/column IDs to a live, authorized PostgreSQL parent."""
    from model_hub.models.develop_dataset import Column

    return Column.objects.select_related("dataset").filter(
        id=column_id,
        dataset_id=dataset_id,
        dataset__workspace=workspace,
        dataset__organization_id=workspace.organization_id,
        dataset__deleted=False,
        deleted=False,
    )


def dataset_cell_visibility_sql(*, dataset_scoped=False):
    """WHERE fragment for cell alias ``c``; scope values remain bound parameters.

    The tuple membership prevents a row UUID from another dataset authorizing
    this cell. Dataset-scoped reads also restrict the parent scans to that ID.
    Workspace-wide system pickers use the same liveness checks.
    """
    row_scope = "AND r.dataset_id = toUUID(%(dataset_id)s)" if dataset_scoped else ""
    dataset_scope = "AND d.id = toUUID(%(dataset_id)s)" if dataset_scoped else ""
    return f"""c._peerdb_is_deleted = 0
AND c.deleted = 0
AND (c.dataset_id, c.row_id) IN (
    SELECT r.dataset_id, r.id
    FROM model_hub_row AS r FINAL
    WHERE r._peerdb_is_deleted = 0
      AND r.deleted = 0
      {row_scope}
      AND r.dataset_id IN (
          SELECT d.id
          FROM model_hub_dataset AS d FINAL
          WHERE d._peerdb_is_deleted = 0
            AND d.deleted = 0
            AND d.workspace_id = toUUID(%(workspace_id)s)
            {dataset_scope}
      )
)
""".strip()
