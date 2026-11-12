from django.db import migrations, models
from tfc.migration_bridge import SkipDatabaseIfLegacyApplied


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0037_merge_c26_dev_candidate"),
    ]

    # c2.5 recorded this as accounts.0037_organizationmembership_removed_at.
    operations = [SkipDatabaseIfLegacyApplied(legacy=('accounts', '0037_organizationmembership_removed_at'), operations=[
        migrations.AddField(
            model_name="organizationmembership",
            name="removed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ])]
