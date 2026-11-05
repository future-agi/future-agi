# Schema 037 uses the existing metrics-only immutable batch receipt ledger.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0128_base_list_current_stores"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="metricattemptbatch",
            name="metric_batch_table",
        ),
        migrations.AlterField(
            model_name="metricattemptbatch",
            name="table_name",
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
        migrations.AddConstraint(
            model_name="metricattemptbatch",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "table_name__in",
                        [
                            "field_values",
                            "bucket_totals",
                            "unit_state",
                            "source_dependencies",
                            "bucket_retired_parts",
                            "list_current_root_facts",
                            "list_current_members",
                            "list_current_user_activity",
                        ],
                    )
                ),
                name="metric_batch_table",
            ),
        ),
    ]
