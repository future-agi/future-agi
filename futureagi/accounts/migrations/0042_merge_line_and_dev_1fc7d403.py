from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0041_default_workspace_for_every_organization"),
        ("accounts", "0032_merge_expiry_and_coding_agent"),
    ]

    operations = []
