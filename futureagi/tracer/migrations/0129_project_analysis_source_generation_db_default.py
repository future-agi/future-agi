from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0128_instantevalrow_source_session_ids")]

    operations = [
        migrations.AlterField(
            model_name="project",
            name="analysis_source_generation",
            field=models.PositiveBigIntegerField(
                default=0, db_default=0, editable=False
            ),
        ),
    ]
