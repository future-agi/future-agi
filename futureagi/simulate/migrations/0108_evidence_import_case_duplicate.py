import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0107_evidence_import_case_discarded"),
    ]

    operations = [
        migrations.AddField(
            model_name="evidenceimportcase",
            name="content_digest",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=64
            ),
        ),
        migrations.AddField(
            model_name="evidenceimportcase",
            name="duplicate_of",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="duplicates",
                to="simulate.evidenceimportcase",
            ),
        ),
        migrations.AlterField(
            model_name="evidenceimportcase",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending review"),
                    ("unavailable", "Unavailable"),
                    ("drafted", "Drafted"),
                    ("blocked", "Blocked"),
                    ("added", "Added to the suite"),
                    ("discarded", "Discarded"),
                    ("duplicate", "Already imported"),
                ],
                default="pending",
                max_length=16,
            ),
        ),
    ]
