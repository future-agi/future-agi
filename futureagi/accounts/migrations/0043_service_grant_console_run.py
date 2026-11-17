# Rolling back refuses while a console-run row exists: the older schema's kind
# constraint does not allow it. Each such grant lives at most an hour; delete
# the expired rows (or wait an hour) before rolling back.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0042_merge_line_and_dev_1fc7d403"),
    ]

    operations = [
        migrations.AddField(
            model_name="servicegrant",
            name="model_ids",
            field=models.CharField(blank=True, default="", max_length=1700),
        ),
        migrations.RemoveConstraint(
            model_name="servicegrant",
            name="service_grant_registered_kind",
        ),
        migrations.AddConstraint(
            model_name="servicegrant",
            constraint=models.CheckConstraint(
                condition=models.Q(kind__in=["sandbox", "batch", "console-run"]),
                name="service_grant_registered_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="servicegrant",
            constraint=models.CheckConstraint(
                condition=(models.Q(kind="console-run") & ~models.Q(model_ids=""))
                | (~models.Q(kind="console-run") & models.Q(model_ids="")),
                name="service_grant_console_run_models",
            ),
        ),
    ]
