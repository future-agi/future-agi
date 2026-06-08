from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0028_onboarding_quality_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="onboardingqualityaction",
            name="due_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddIndex(
            model_name="onboardingqualityaction",
            index=models.Index(
                fields=["workspace", "status", "due_at"],
                name="onb_qact_ws_status_due",
            ),
        ),
    ]
