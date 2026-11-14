from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0086_hostedharnessattempt_diagnostics"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="authoring_usage_report",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="receipt_history",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="sandbox_runtime",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="usage_report",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
