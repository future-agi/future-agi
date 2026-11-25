from django.db import migrations


class Migration(migrations.Migration):
    """Joins the Instant eval / Insights chain with dev's simulation-project
    chain after merging dev into the Jev evaluator branch. No operations."""

    dependencies = [
        ("tracer", "0121_remove_insightconfig_schedule"),
        ("tracer", "0117_merge_error_feed_and_simulation_project"),
    ]

    operations = []
