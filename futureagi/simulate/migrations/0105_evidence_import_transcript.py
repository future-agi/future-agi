import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0104_evidence_import"),
        ("tracer", "0130_observationspan_internal_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="evidenceimport",
            name="source_kind",
            field=models.CharField(
                choices=[
                    ("session", "Session"),
                    ("call", "Call"),
                    ("transcript", "Transcript"),
                ],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="evidenceimport",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="tracer.project",
            ),
        ),
        migrations.AddField(
            model_name="evidenceimport",
            name="declared_production",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="evidenceimport",
            name="redaction",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="evidenceimportcase",
            name="transcript",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="evidenceimport",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("project__isnull", True), ("source_kind", "transcript")),
                    models.Q(
                        models.Q(("source_kind", "transcript"), _negated=True),
                        ("project__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="evidence_import_project_by_kind",
            ),
        ),
    ]
