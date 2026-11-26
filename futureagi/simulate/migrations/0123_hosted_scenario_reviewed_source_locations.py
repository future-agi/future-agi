from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("simulate", "0122_harness_source_review_build_case_assignment")]
    operations = [
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="reviewed_source_locations",
            field=models.JSONField(null=True, editable=False),
        ),
    ]
