# Omega Hub operations: when each Hub checker last called, so a received or checking submission
# can say that no checker is running.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("omega_hub", "0005_merge_hub_operations_and_erasure"),
    ]

    operations = [
        migrations.CreateModel(
            name="CheckerPresence",
            fields=[
                ("checker_id", models.CharField(max_length=120, primary_key=True, serialize=False)),
                ("last_seen_at", models.DateTimeField()),
            ],
            options={"db_table": "omega_hub_checker_presence"},
        ),
    ]
