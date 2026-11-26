from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0132_merge_metrics_b1_dev")]

    operations = [
        migrations.AlterField(
            model_name="metriccompactionrange",
            name="table_kind",
            field=models.CharField(
                choices=[
                    ("field_values", "Field Values"),
                    ("bucket_totals", "Bucket Totals"),
                    ("unit_state", "Unit State"),
                    ("source_dependencies", "Source Dependencies"),
                    ("bucket_retired_parts", "Bucket Retired Parts"),
                    ("list_current_root_facts", "List Current Roots"),
                    ("list_current_members", "List Current Members"),
                    ("list_current_user_activity", "List User Activity"),
                ],
                max_length=32,
            ),
        ),
    ]
