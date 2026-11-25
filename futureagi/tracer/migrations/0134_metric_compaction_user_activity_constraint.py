from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0133_metric_compaction_user_activity_choice")]

    operations = [
        migrations.RemoveConstraint(
            model_name="metriccompactionrange",
            name="metric_range_table",
        ),
        migrations.AddConstraint(
            model_name="metriccompactionrange",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    table_kind__in=[
                        "field_values",
                        "bucket_totals",
                        "unit_state",
                        "source_dependencies",
                        "bucket_retired_parts",
                        "list_current_root_facts",
                        "list_current_members",
                        "list_current_user_activity",
                    ]
                ),
                name="metric_range_table",
            ),
        ),
    ]
