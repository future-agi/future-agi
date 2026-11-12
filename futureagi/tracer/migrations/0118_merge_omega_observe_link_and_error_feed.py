from django.db import migrations


class Migration(migrations.Migration):
    # Integration round 7: the fork's Omega deployment Observe link (0115) and
    # dev's error-feed and simulation-project merge (0117) are separate leaves.
    dependencies = [
        ("tracer", "0115_omega_deployment_observe_link"),
        ("tracer", "0117_merge_error_feed_and_simulation_project"),
    ]

    operations = []
