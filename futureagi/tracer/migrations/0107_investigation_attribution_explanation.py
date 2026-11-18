from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0106_grouping_severity")]

    operations = [
        migrations.AddField(
            model_name="traceinvestigationattribution",
            name="explanation",
            field=models.CharField(blank=True, default="", max_length=600),
        ),
    ]
