from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0117_cohort_sampling"),
    ]

    operations = [
        # The database default now ships in 0117_cohort_sampling itself. This
        # AlterField is kept (unchanged field, so no SQL) only so migration
        # graphs that already recorded it stay consistent.
        migrations.AlterField(
            model_name="project",
            name="sampling_contact_attribute",
            field=models.CharField(
                blank=True, default="", db_default="", max_length=200
            ),
        ),
    ]
