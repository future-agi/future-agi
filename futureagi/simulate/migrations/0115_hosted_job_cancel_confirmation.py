from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0114_harness_certification_files"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessjob",
            name="cancel_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessjob",
            name="cancel_confirmed_by",
            field=models.CharField(
                blank=True,
                choices=[
                    ("sandbox_absent", "Sandbox absent"),
                    ("provider_call_ended", "Provider call ended"),
                    ("cleanup_failed", "Cleanup failed"),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
