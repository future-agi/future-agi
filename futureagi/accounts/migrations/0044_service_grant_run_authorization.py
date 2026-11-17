# Rolling back refuses while a run authorization row exists: the older
# schema's constraints do not allow it. Each lives at most 12 hours; revoke or
# delete those rows before rolling back.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0043_service_grant_console_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="servicegrant",
            name="ee",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RemoveConstraint(
            model_name="servicegrant",
            name="service_grant_registered_kind",
        ),
        migrations.RemoveConstraint(
            model_name="servicegrant",
            name="service_grant_console_run_models",
        ),
        migrations.AddConstraint(
            model_name="servicegrant",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    kind__in=["sandbox", "batch", "console-run", "console-run-auth"]
                ),
                name="service_grant_registered_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="servicegrant",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(kind__in=["console-run", "console-run-auth"])
                    & ~models.Q(model_ids="")
                )
                | (
                    ~models.Q(kind__in=["console-run", "console-run-auth"])
                    & models.Q(model_ids="")
                ),
                name="service_grant_console_run_models",
            ),
        ),
        migrations.AddConstraint(
            model_name="servicegrant",
            constraint=models.CheckConstraint(
                condition=(models.Q(kind="console-run-auth") & ~models.Q(ee=""))
                | (~models.Q(kind="console-run-auth") & models.Q(ee="")),
                name="service_grant_run_authorization_ee",
            ),
        ),
    ]
