# The Observe link of a Console agent (learning-service P1-08): an agent kind on
# the deployment link, one live link per EE agent id, and when mistake analysis
# was set up on the agent's project. Existing rows are deployment links.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0118_merge_omega_observe_link_and_error_feed"),
    ]

    operations = [
        migrations.AddField(
            model_name="omegadeploymentobservelink",
            name="kind",
            field=models.CharField(
                choices=[("deployment", "Deployment"), ("agent", "Console agent")],
                default="deployment",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="omegadeploymentobservelink",
            name="ee_deployment_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name="omegadeploymentobservelink",
            name="deployment_name",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="omegadeploymentobservelink",
            name="ee_agent_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="omegadeploymentobservelink",
            name="agent_name",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="omegadeploymentobservelink",
            name="scan_configured_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="omegadeploymentobservelink",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted", False)),
                fields=("ee_agent_id",),
                name="unique_live_omega_agent_observe_link",
            ),
        ),
        migrations.AddConstraint(
            model_name="omegadeploymentobservelink",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("ee_agent_id__isnull", True),
                        ("ee_deployment_id__isnull", False),
                        ("kind", "deployment"),
                    ),
                    models.Q(
                        ("ee_agent_id__isnull", False),
                        ("ee_deployment_id__isnull", True),
                        ("kind", "agent"),
                    ),
                    _connector="OR",
                ),
                name="omega_observe_link_one_ee_id_per_kind",
            ),
        ),
    ]
