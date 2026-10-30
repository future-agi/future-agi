from django.db import migrations


class Migration(migrations.Migration):
    """Joins the Instant eval / Insights chain with dev's shared-link resource
    type change after merging dev into the Jev evaluator branch. No
    operations."""

    dependencies = [
        ("tracer", "0122_merge_instant_eval_and_simulation_project"),
        ("tracer", "0118_sharedlink_error_feed_issue_resource_type"),
    ]

    operations = []
