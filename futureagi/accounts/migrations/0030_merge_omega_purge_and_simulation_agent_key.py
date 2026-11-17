from django.db import migrations


class Migration(migrations.Migration):
    # Integration round 7: the fork's Omega purge lifecycle (0029) and dev's
    # authtoken scope and simulation-agent key merge (0028) are separate leaves.
    dependencies = [
        ("accounts", "0028_merge_authtoken_scope_and_simulation_agent_key"),
        ("accounts", "0029_omega_purge_lifecycle"),
    ]

    operations = []
