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
                    ("provider_call_open", "Provider call still open"),
                    ("provider_unchecked", "Provider calls not checked"),
                    ("unchecked_no_key", "Not checked: no provider key"),
                    (
                        "unchecked_key_rejected",
                        "Not checked: provider rejected the key",
                    ),
                    ("unchecked_no_target", "Not checked: no provider agent ID"),
                    ("unchecked_no_calls", "Not checked: no calls listed"),
                    (
                        "unchecked_too_many_calls",
                        "Not checked: more calls than one page",
                    ),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
