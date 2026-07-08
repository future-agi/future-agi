"""Partial unique index enforcing at most one is_default=True version per template."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0113_backfill_eval_version_is_default"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="evaltemplateversion",
            constraint=models.UniqueConstraint(
                fields=["eval_template"],
                condition=models.Q(is_default=True, deleted=False),
                name="unique_default_version_per_template",
            ),
        ),
    ]
