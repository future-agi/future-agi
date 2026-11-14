import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:simulate_harness_suite_version"],
    "mitigation": (
        "Only nullable or defaulted columns and one index are added. The data step sets lifecycle_legacy on the "
        "active and candidate suite versions, a small per-environment table, in one UPDATE."
    ),
}


def mark_legacy(apps, schema_editor):
    """Suites active or candidate at deploy were validated before proofs were recorded."""
    HarnessSuiteVersion = apps.get_model("simulate", "HarnessSuiteVersion")
    HarnessSuiteVersion._base_manager.filter(status__in=["active", "candidate"]).update(
        lifecycle_legacy=True
    )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("simulate", "0107_merge_certification_files_and_version_candidate"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="archived_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="archive_reason",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="archived_content",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="sources",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="hostedharnessscenario",
            index=models.Index(
                fields=["job", "archived_at"], name="idx_harness_scenario_archived"
            ),
        ),
        migrations.AddField(
            model_name="harnesssuiteversion",
            name="lifecycle_legacy",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_legacy, migrations.RunPython.noop),
    ]
