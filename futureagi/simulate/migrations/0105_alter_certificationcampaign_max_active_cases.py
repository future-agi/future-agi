from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0104_certificationcampaign_certificationcase_and_more"),
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
    ]
