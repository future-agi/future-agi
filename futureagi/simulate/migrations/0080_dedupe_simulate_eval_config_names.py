from django.db import migrations
from django.db.models import Subquery
from django.db.models.functions import Now


def dedupe_simulate_eval_config_names(apps, schema_editor):
    """Prod already holds duplicate (run_test, name) rows from the racy
    get_or_create in _store_reported_evaluations; the constraint added in the
    next migration cannot land over them, so the oldest row per group is kept
    and the rest are soft-deleted.

    Set-based rather than row-by-row: the constraint's index build takes an
    ACCESS EXCLUSIVE lock in the following migration, and this dedupe runs in
    its own transaction so it never inflates that lock window.
    """
    SimulateEvalConfig = apps.get_model("simulate", "SimulateEvalConfig")
    live = SimulateEvalConfig.objects.filter(deleted=False, name__isnull=False)
    keepers = live.order_by("run_test_id", "name", "created_at", "id").distinct(
        "run_test_id", "name"
    )
    live.exclude(id__in=Subquery(keepers.values("id"))).update(
        deleted=True, deleted_at=Now()
    )


class Migration(migrations.Migration):

    dependencies = [
        ('simulate', '0079_rlenvironment_rlcontract_runtest_rl_environment_and_more'),
    ]

    operations = [
        migrations.RunPython(
            dedupe_simulate_eval_config_names, migrations.RunPython.noop
        ),
    ]
