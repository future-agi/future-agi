# Generated manually for Error Feed email + optional Slack alerts.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0001_initial"),
        ("tracer", "0142_merge_metrics_and_eval_put_back"),
    ]

    operations = [
        migrations.AddField(
            model_name="errorfeedalertrule",
            name="email_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="errorfeedalertrule",
            name="email_recipients",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="errorfeedalertrule",
            name="slack_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="errorfeedalertrule",
            name="slack_channel_id",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AlterField(
            model_name="errorfeedalertrule",
            name="slack_channel_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="errorfeedalertrule",
            name="slack_connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="integrations.integrationconnection",
            ),
        ),
    ]
