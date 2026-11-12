"""Persist new row/cell activity without backfilling invented history.

PostgreSQL transition tables keep bulk writes set-based. Triggers see actual
changed/inserted rows (including ON CONFLICT), run in the write transaction,
and compare full values before storing bounded previews. No per-cell SELECTs.
"""

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


_COLUMNS = """
    row_id, at, subject, action, actor_id, actor, origin,
    cell_id, column_id, column_name, before_value, after_value,
    before_truncated, after_truncated
"""
_ACTOR = """
    nullif(actor->>'id', ''),
    coalesce(nullif(left(actor->>'name', 255), ''), 'System'),
    coalesce(nullif(left(actor->>'origin', 16), ''), 'write')
"""


def _trigger(name, table, event, references, select):
    return f"""
    CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE actor jsonb := coalesce(
        nullif(current_setting('futureagi.row_activity_actor', true), '')::jsonb,
        '{{}}'::jsonb
    );
    BEGIN
        INSERT INTO model_hub_rowactivity ({_COLUMNS}) {select};
        RETURN NULL;
    END;
    $$;
    CREATE TRIGGER {name} AFTER {event} ON {table}
    REFERENCING {references}
    FOR EACH STATEMENT EXECUTE FUNCTION {name}();
    """


_ROW_CREATE = _trigger(
    "mh_row_activity_create", "model_hub_row", "INSERT", "NEW TABLE AS new_rows",
    f"""
    SELECT n.id, clock_timestamp(), 'row', 'create', {_ACTOR},
           NULL, NULL, '', NULL, NULL, false, false
    FROM new_rows n WHERE NOT n.deleted
    """,
)
_ROW_UPDATE = _trigger(
    "mh_row_activity_update", "model_hub_row", "UPDATE",
    "OLD TABLE AS old_rows NEW TABLE AS new_rows",
    f"""
    SELECT n.id, clock_timestamp(), 'row',
           CASE WHEN n.deleted THEN 'delete' ELSE 'restore' END, {_ACTOR},
           NULL, NULL, '', NULL, NULL, false, false
    FROM new_rows n JOIN old_rows o ON o.id = n.id
    WHERE n.deleted IS DISTINCT FROM o.deleted
    """,
)
_CELL_CREATE = _trigger(
    "mh_cell_activity_create", "model_hub_cell", "INSERT", "NEW TABLE AS new_cells",
    f"""
    SELECT n.row_id, clock_timestamp(), 'cell', 'create', {_ACTOR},
           n.id, n.column_id, c.name, NULL, left(n.value, 2048),
           false, coalesce(length(n.value) > 2048, false)
    FROM new_cells n
    JOIN model_hub_row r ON r.id = n.row_id
    JOIN model_hub_column c ON c.id = n.column_id AND c.dataset_id = r.dataset_id
    WHERE NOT n.deleted AND NOT r.deleted
      AND (n.dataset_id IS NULL OR n.dataset_id = r.dataset_id)
    """,
)
_CELL_UPDATE = _trigger(
    "mh_cell_activity_update", "model_hub_cell", "UPDATE",
    "OLD TABLE AS old_cells NEW TABLE AS new_cells",
    f"""
    SELECT n.row_id, clock_timestamp(), 'cell',
           CASE WHEN n.deleted THEN 'delete'
                WHEN o.deleted THEN 'restore' ELSE 'edit' END, {_ACTOR},
           n.id, n.column_id, c.name,
           CASE WHEN o.deleted THEN NULL ELSE left(o.value, 2048) END,
           CASE WHEN n.deleted THEN NULL ELSE left(n.value, 2048) END,
           NOT o.deleted AND coalesce(length(o.value) > 2048, false),
           NOT n.deleted AND coalesce(length(n.value) > 2048, false)
    FROM new_cells n JOIN old_cells o ON o.id = n.id
    JOIN model_hub_row r ON r.id = n.row_id
    JOIN model_hub_column c ON c.id = n.column_id AND c.dataset_id = r.dataset_id
    WHERE NOT r.deleted
      AND (n.dataset_id IS NULL OR n.dataset_id = r.dataset_id)
      AND (n.deleted IS DISTINCT FROM o.deleted
           OR (NOT n.deleted AND n.value IS DISTINCT FROM o.value))
    """,
)
_CELL_DELETE = _trigger(
    "mh_cell_activity_delete", "model_hub_cell", "DELETE", "OLD TABLE AS old_cells",
    f"""
    SELECT o.row_id, clock_timestamp(), 'cell', 'delete', {_ACTOR},
           o.id, o.column_id, c.name, left(o.value, 2048), NULL,
           coalesce(length(o.value) > 2048, false), false
    FROM old_cells o
    JOIN model_hub_row r ON r.id = o.row_id
    JOIN model_hub_column c ON c.id = o.column_id AND c.dataset_id = r.dataset_id
    WHERE NOT o.deleted AND NOT r.deleted
      AND (o.dataset_id IS NULL OR o.dataset_id = r.dataset_id)
    """,
)
# Django's deletion collector can delete old history before deleting cells.
# Remove any cell-deletion events added during that cascade before checking the
# deferred row FK. Hard-deleted rows have no readable history; soft deletes do.
_ROW_PURGE = """
CREATE FUNCTION mh_row_activity_purge() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM model_hub_rowactivity
    WHERE row_id IN (SELECT id FROM old_rows);
    RETURN NULL;
END;
$$;
CREATE TRIGGER mh_row_activity_purge AFTER DELETE ON model_hub_row
REFERENCING OLD TABLE AS old_rows
FOR EACH STATEMENT EXECUTE FUNCTION mh_row_activity_purge();
"""
_FORWARD = _ROW_CREATE + _ROW_UPDATE + _CELL_CREATE + _CELL_UPDATE + _CELL_DELETE + _ROW_PURGE
_REVERSE = "\n".join(
    f"DROP TRIGGER {name} ON {table}; DROP FUNCTION {name}();"
    for name, table in (
        ("mh_row_activity_create", "model_hub_row"),
        ("mh_row_activity_update", "model_hub_row"),
        ("mh_cell_activity_create", "model_hub_cell"),
        ("mh_cell_activity_update", "model_hub_cell"),
        ("mh_cell_activity_delete", "model_hub_cell"),
        ("mh_row_activity_purge", "model_hub_row"),
    )
)


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0142_experiment_description")]

    operations = [
        migrations.CreateModel(
            name="RowActivity",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("at", models.DateTimeField(default=timezone.now, editable=False)),
                ("subject", models.CharField(max_length=4, choices=[("row", "Row"), ("cell", "Cell")])),
                ("action", models.CharField(max_length=7, choices=[
                    ("create", "Create"), ("edit", "Edit"),
                    ("delete", "Delete"), ("restore", "Restore"),
                ])),
                ("actor_id", models.CharField(max_length=64, null=True)),
                ("actor", models.CharField(max_length=255)),
                ("origin", models.CharField(default="write", max_length=16)),
                ("cell_id", models.UUIDField(null=True)),
                ("column_id", models.UUIDField(null=True)),
                ("column_name", models.CharField(max_length=2000, blank=True, default="")),
                ("before_value", models.TextField(null=True)),
                ("after_value", models.TextField(null=True)),
                ("before_truncated", models.BooleanField(default=False)),
                ("after_truncated", models.BooleanField(default=False)),
                ("row", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="activity_events", to="model_hub.row",
                )),
            ],
            options={
                "ordering": ("-at", "-id"),
                "indexes": [models.Index(
                    fields=["row", "-at", "-id"], name="row_activity_page_idx"
                )],
            },
        ),
        migrations.RunSQL(_FORWARD, reverse_sql=_REVERSE),
    ]
