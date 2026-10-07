from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("simulate", "0093_harness_scenario_authored_before_call")]

    operations = [
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="keywords",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="hostedharnessscenario",
            index=GinIndex(fields=["keywords"], name="idx_harness_scenario_keywords"),
        ),
        # Suites indexed before this point hold their keywords inside the persona document.
        # Lift them so a filter answers the same for an old run as for a new one; the persona
        # copy is left alone, because the artefacts it was read from are never rewritten.
        migrations.RunSQL(
            sql="""
                UPDATE simulate_hosted_harness_scenario
                   SET keywords = persona -> 'keywords'
                 WHERE keywords IS NULL
                   AND persona -> 'keywords' IS NOT NULL
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
