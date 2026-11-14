from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("simulate", "0088_expand_hosted_harness_scenario_limit")]

    operations = [
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="number",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="instruction",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="use_case",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="branch",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="tests",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="folder",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="persona",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="coverage",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="sub_goals",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="background_noise",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="max_turns",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="hostedharnessscenario",
            index=models.Index(
                fields=["job", "number"], name="idx_harness_scenario_order"
            ),
        ),
        migrations.AddIndex(
            model_name="hostedharnessscenario",
            index=GinIndex(fields=["persona"], name="idx_harness_scenario_persona"),
        ),
        migrations.AddIndex(
            model_name="hostedharnessscenario",
            index=GinIndex(fields=["coverage"], name="idx_harness_scenario_coverage"),
        ),
    ]
