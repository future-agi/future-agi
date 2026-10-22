from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "integrations",
            "0004_integrationconnection_uq_intconn_org_ws_action_only_active",
        )
    ]

    operations = [
        migrations.AlterField(
            model_name="integrationconnection",
            name="platform",
            field=models.CharField(
                max_length=50,
                choices=[
                    ("langfuse", "Langfuse"),
                    ("datadog", "Datadog"),
                    ("posthog", "PostHog"),
                    ("pagerduty", "PagerDuty"),
                    ("mixpanel", "Mixpanel"),
                    ("cloud_storage", "Cloud Storage"),
                    ("message_queue", "Message Queue"),
                    ("linear", "Linear"),
                    ("slack", "Slack"),
                ],
            ),
        ),
        migrations.RemoveConstraint(
            model_name="integrationconnection",
            name="uq_intconn_org_ws_action_only_active",
        ),
        migrations.AddConstraint(
            model_name="integrationconnection",
            constraint=models.UniqueConstraint(
                fields=("organization", "workspace", "platform"),
                condition=models.Q(platform__in=["linear", "slack"])
                & models.Q(deleted=False),
                name="uq_intconn_org_ws_action_only_active",
            ),
        ),
    ]
