import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("simulate", "0100_certification_case_ordering")]

    operations = [
        migrations.AddField(
            model_name="certificationcase",
            name="run_job",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="certification_run_cases",
                to="simulate.hostedharnessjob",
            ),
        ),
    ]
