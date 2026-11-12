"""The label head table (webhooks DESIGN.md section 14.3).

One row per (prompt, label): where the label sits and its revision. The backfill reads today's attachments. Where a
label already sits on more than one live version of a prompt (the race O1 describes), the head points at the most
recently updated one, the count is reported, and no attachment is changed: the next change through the writer repairs
the attachment.
"""

import logging

import django.db.models.deletion
from django.db import migrations, models

from tfc.migration_database import executor_alias_scope

log = logging.getLogger("model_hub.migrations.promptlabelhead")

_LIVE_ATTACHMENTS = """
    FROM model_hub_promptversion_labels t
    JOIN model_hub_promptversion v ON v.id = t.promptversion_id
    WHERE v.deleted = false AND v.original_template_id IS NOT NULL
"""

BACKFILL_SQL = (
    """
    INSERT INTO model_hub_promptlabelhead (template_id, label_id, version_id, revision, updated_at)
    SELECT DISTINCT ON (v.original_template_id, t.promptlabel_id)
           v.original_template_id, t.promptlabel_id, v.id, 0, now()
    """
    + _LIVE_ATTACHMENTS
    + """
    ORDER BY v.original_template_id, t.promptlabel_id, v.updated_at DESC, v.id DESC
    ON CONFLICT (template_id, label_id) DO NOTHING
    """
)

DUPLICATES_SQL = (
    "SELECT count(*) FROM (SELECT 1"
    + _LIVE_ATTACHMENTS
    + " GROUP BY v.original_template_id, t.promptlabel_id HAVING count(*) > 1) d"
)


def backfill(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        with schema_editor.connection.cursor() as cur:
            cur.execute(DUPLICATES_SQL)
            duplicates = cur.fetchone()[0]
            cur.execute(BACKFILL_SQL)
            heads = cur.rowcount
        message = (
            f"promptlabelhead backfill: {heads} heads; {duplicates} (prompt, label) pairs sit on more than one live "
            "version (head = the most recently updated; attachments unchanged until the next change)"
        )
        log.warning(message) if duplicates else log.info(message)
        if duplicates:
            print(message)  # noqa: T201 - shown in the migrate output, as the design asks


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0122_backfill_queueitem_source_preview"),
    ]

    operations = [
        migrations.CreateModel(
            name="PromptLabelHead",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("revision", models.PositiveIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "label",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="heads",
                        to="model_hub.promptlabel",
                    ),
                ),
                (
                    "template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="label_heads",
                        to="model_hub.prompttemplate",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="label_heads",
                        to="model_hub.promptversion",
                    ),
                ),
            ],
            options={
                "db_table": "model_hub_promptlabelhead",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("template", "label"), name="prompt_label_head_unique"
                    )
                ],
            },
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
