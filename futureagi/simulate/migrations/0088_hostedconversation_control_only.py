from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0087_hosted_harness_conversations"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessconversationlease",
            name="control_only",
            field=models.BooleanField(default=False),
        ),
    ]
