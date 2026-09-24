import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("simulate", "0092_harness_scenario_queryable")]

    operations = [
        migrations.AlterField(
            model_name="hostedharnessscenario",
            name="scenario",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="hosted_registrations",
                to="simulate.scenarios",
            ),
        ),
    ]
