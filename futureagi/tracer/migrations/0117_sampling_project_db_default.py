from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0117_cohort_sampling"),
    ]

    operations = [
        # Existing collectors insert projects outside the Django ORM. Keep a
        # database default so a schema-first rollout does not break ingestion.
        migrations.AlterField(
            model_name="project",
            name="sampling_contact_attribute",
            field=models.CharField(
                blank=True, default="", db_default="", max_length=200
            ),
        ),
    ]
