import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0105_evidence_import_transcript"),
        ("model_hub", "0122_backfill_queueitem_source_preview"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="evidenceimport",
            name="evidence_import_project_by_kind",
        ),
        migrations.AlterField(
            model_name="evidenceimport",
            name="source_kind",
            field=models.CharField(
                choices=[
                    ("session", "Session"),
                    ("call", "Call"),
                    ("transcript", "Transcript"),
                    ("dataset", "Dataset rows"),
                ],
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="evidenceimport",
            name="dataset",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="model_hub.dataset",
            ),
        ),
        migrations.AddField(
            model_name="evidenceimport",
            name="dataset_columns",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="evidenceimport",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("dataset__isnull", True),
                        ("project__isnull", True),
                        ("source_kind", "transcript"),
                    ),
                    models.Q(
                        ("dataset__isnull", False),
                        ("project__isnull", True),
                        ("source_kind", "dataset"),
                    ),
                    models.Q(
                        models.Q(
                            ("source_kind__in", ("transcript", "dataset")),
                            _negated=True,
                        ),
                        ("dataset__isnull", True),
                        ("project__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="evidence_import_source_by_kind",
            ),
        ),
    ]
