"""Reconcile BL02's metrics-only store enum with the pinned range model.

This changes no source table and does not enable range compaction or serving.
"""

from django.db import migrations, models

TABLE_CHOICES = [
    ("field_values", "Field Values"),
    ("bucket_totals", "Bucket Totals"),
    ("unit_state", "Unit State"),
    ("source_dependencies", "Source Dependencies"),
    ("bucket_retired_parts", "Bucket Retired Parts"),
    ("list_current_root_facts", "List Current Roots"),
    ("list_current_members", "List Current Members"),
    ("list_current_user_activity", "List User Activity"),
]


class Migration(migrations.Migration):
    dependencies = [("tracer", "0130_merge_session_components")]

    operations = [
        migrations.RemoveConstraint(
            model_name="metriccompactionrange", name="metric_range_table",
        ),
        migrations.AlterField(
            model_name="metriccompactionrange", name="table_kind",
            field=models.CharField(choices=TABLE_CHOICES, max_length=32),
        ),
        migrations.AddConstraint(
            model_name="metriccompactionrange",
            constraint=models.CheckConstraint(
                condition=models.Q(table_kind__in=[value for value, _ in TABLE_CHOICES]),
                name="metric_range_table",
            ),
        ),
    ]
