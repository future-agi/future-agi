from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0106_evidence_import_dataset"),
    ]

    operations = [
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
                ],
                default="pending",
                max_length=16,
            ),
        ),
    ]
