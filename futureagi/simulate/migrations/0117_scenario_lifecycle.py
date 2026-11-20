import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("simulate", "0116_harness_version_candidate"),
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
            field=models.CharField(
                blank=True, db_default="", default="", max_length=255
            ),
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
        # Every suite version that exists at upgrade was validated, if at all, before proofs were
        # recorded, and so is one an older pod still writes during a rolling deploy: the database
        # default is true. This release always writes the value itself, false for a new version.
        migrations.AddField(
            model_name="harnesssuiteversion",
            name="lifecycle_legacy",
            field=models.BooleanField(db_default=True, default=False),
        ),
    ]
