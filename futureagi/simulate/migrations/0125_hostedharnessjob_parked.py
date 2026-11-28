"""A copy whose design paused on an unanswered question waits as `parked` until it is
continued. Choices only: no column changes."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0124_agent_fix_outside_attempts"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hostedharnessjob",
            name="state",
            field=models.CharField(
                choices=[
                    ("received", "Received"),
                    ("queued", "Queued"),
                    ("admitted", "Admitted"),
                    ("provisioning", "Provisioning"),
                    ("running", "Running"),
                    ("finalizing", "Finalizing"),
                    ("cleaning_up", "Cleaning up"),
                    ("retry_wait", "Retry wait"),
                    ("parked", "Parked"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                    ("canceled", "Canceled"),
                ],
                default="received",
                max_length=32,
            ),
        ),
    ]
