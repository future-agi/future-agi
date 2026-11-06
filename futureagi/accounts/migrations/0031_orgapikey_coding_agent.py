import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0030_queue_existing_demo_trace_publications"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orgapikey",
            name="type",
            field=models.CharField(
                choices=[
                    ("system", "System"),
                    ("user", "User"),
                    ("mcp", "MCP"),
                    ("ingest", "Ingest"),
                    ("coding_agent", "Coding agent"),
                ],
                default="system",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="orgapikey",
            name="simulation_environment_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="orgapikey",
            name="minted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="minted_api_keys",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="orgapikey",
            constraint=models.CheckConstraint(
                condition=~models.Q(type="coding_agent")
                | models.Q(
                    simulation_environment_id__isnull=False,
                    user__isnull=True,
                    minted_by__isnull=False,
                ),
                name="coding_agent_api_key_names_an_environment",
                violation_error_message=(
                    "A coding agent key must name its environment and its minter,"
                    " and no user."
                ),
            ),
        ),
    ]
