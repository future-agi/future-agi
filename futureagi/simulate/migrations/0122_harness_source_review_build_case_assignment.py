from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("simulate", "0121_merge_source_generation_and_scenario_lifecycle")]
    operations = [
        migrations.AddField(
            model_name="harnesssourcereview",
            name="build_case_assignment",
            field=models.JSONField(null=True, editable=False),
        ),
    ]
