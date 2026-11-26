from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0126_analysis_source_arrival")]

    operations = [
        migrations.AddField(
            model_name="project",
            name="analysis_source_generation",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
    ]
