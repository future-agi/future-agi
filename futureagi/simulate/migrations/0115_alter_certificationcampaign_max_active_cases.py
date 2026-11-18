from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0114_certificationcampaign_certificationcase_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="certificationcampaign",
            name="max_active_cases",
            field=models.PositiveSmallIntegerField(default=2),
        ),
        migrations.AddConstraint(
            model_name="certificationcampaign",
            constraint=models.UniqueConstraint(
                condition=models.Q(("state__in", ("pending", "running", "canceling"))),
                fields=("organization",),
                name="uniq_active_cert_campaign_org",
            ),
        ),
        migrations.AlterField(
            model_name="certificationnotification",
            name="event_kind",
            field=models.CharField(
                choices=[
                    ("campaign_started", "Campaign started"),
                    ("blocking_failure", "Blocking failure"),
                    ("stable_assertion_failure", "Stable assertion failure"),
                    (
                        "exploratory_evaluation_failure",
                        "Exploratory evaluation failure",
                    ),
                    ("campaign_completed", "Campaign completed"),
                ],
                max_length=48,
            ),
        ),
    ]
