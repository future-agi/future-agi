"""Add parent-independent erasure identity, audit ancestry and capture fences.

0126 is immutable. Backfill before making organization_key mandatory. Rollback
is safe only before deletion/collection has used the new protocol: refuse to
silently discard orphaned work or frozen detached ancestry on downgrade.
"""

import django.db.models.deletion
from django.db import migrations, models


def freeze_existing(apps, schema_editor):
    alias = schema_editor.connection.alias
    Erasure = apps.get_model("tracer", "MetricErasureRequest")
    for row in Erasure.objects.using(alias).all().iterator():
        Erasure.objects.using(alias).filter(id=row.id).update(
            organization_key=row.organization_id,
            project_key=row.project_id,
        )
    Manifest = apps.get_model("tracer", "MetricReadManifest")
    Attempt = apps.get_model("tracer", "MetricRefreshAttempt")
    for Model, field in ((Manifest, "parent_id"), (Attempt, "parent_manifest_id")):
        for row in Model.objects.using(alias).exclude(**{field: None}).iterator():
            parent_id = getattr(row, field)
            digest = (
                Manifest.objects.using(alias)
                .values_list(
                    "manifest_sha256",
                    flat=True,
                )
                .get(id=parent_id)
            )
            Model.objects.using(alias).filter(id=row.id).update(
                parent_manifest_key=parent_id,
                parent_manifest_sha256=digest,
            )
    # Only the current head's historical remap is provable from 0126 state.
    Refresh = apps.get_model("tracer", "MetricProjectRefresh")
    Pin = apps.get_model("tracer", "MetricGenerationPin")
    for refresh in (
        Refresh.objects.using(alias).exclude(head_manifest_id=None).iterator()
    ):
        Manifest.objects.using(alias).filter(id=refresh.head_manifest_id).update(
            remap_revision=refresh.remap_cursor_ns,
        )
        Pin.objects.using(alias).filter(manifest_id=refresh.head_manifest_id).update(
            remap_revision=refresh.remap_cursor_ns,
        )
    # Old pins cannot prove an old remap/version capture. Expire them, rather
    # than invent a historical revision and make an inconsistent capture usable.
    from django.utils import timezone

    Pin.objects.using(alias).filter(invalidated_at=None).exclude(
        manifest_id__in=Refresh.objects.using(alias)
        .exclude(head_manifest_id=None)
        .values("head_manifest_id")
    ).update(invalidated_at=timezone.now(), invalidation_reason="version_retired")


def guard_downgrade(apps, schema_editor):
    alias = schema_editor.connection.alias
    Erasure = apps.get_model("tracer", "MetricErasureRequest")
    if (
        Erasure.objects.using(alias).filter(organization_id=None).exists()
        or (
            Erasure.objects.using(alias)
            .exclude(scope_kind__in=["organization", "project", "sessions", "users"])
            .exists()
        )
        or Erasure.objects.using(alias)
        .filter(
            scope_kind__in=["project", "sessions"],
            project_id=None,
        )
        .exists()
    ):
        raise RuntimeError("cannot downgrade with parent-independent erasure work")
    for label, field in (
        ("MetricReadManifest", "parent_id"),
        ("MetricRefreshAttempt", "parent_manifest_id"),
    ):
        if (
            apps.get_model("tracer", label)
            .objects.using(alias)
            .filter(**{field: None, "parent_manifest_key__isnull": False})
            .exists()
        ):
            raise RuntimeError("cannot downgrade after ancestry collection")
    if (
        apps.get_model("tracer", "MetricReadManifest")
        .objects.using(alias)
        .exclude(
            revoked_at=None,
        )
        .exists()
        or apps.get_model("tracer", "DerivedMetricVersion")
        .objects.using(alias)
        .exclude(
            pins_revoked_at=None,
        )
        .exists()
    ):
        raise RuntimeError("cannot downgrade after durable revocation")


UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration", "index", "constraint", "not_null"],
    "tables": [
        "postgres:accounts_organization",
        "postgres:tracer_project",
        "postgres:tracer_derivedmetricrun",
        "postgres:tracer_derivedmetricversion",
        "postgres:tracer_metricerasurerequest",
        "postgres:tracer_metricgenerationpin",
        "postgres:tracer_metricprojectrefresh",
        "postgres:tracer_metricreadmanifest",
        "postgres:tracer_metricrefreshattempt",
    ],
    "mitigation": "Runs in one atomic migration on the schema-editor alias. Backfill runs "
    "before NOT NULL; iterator reads do not bound transaction length: "
    "per-row writes and parent lookups span existing erasure/ancestry rows, "
    "plus per-head updates and a set-based pin invalidation. Stable copies "
    "and invalidated_at=NULL make retry after atomic failure safe. The SQL "
    "makes deferred checks immediate before remaining DDL. Downgrade "
    "refuses orphaned/new-scope erasure, detached ancestry or durable "
    "revocation; it does not undo pin invalidations. Use the sealed cold "
    "backup for failed-update recovery.",
    "reviewer": "nik13",
    "review": "https://github.com/future-agi/future-agi/pull/3588#discussion_r4642126126",
    "large_volume_rehearsal": True,
}


class Migration(migrations.Migration):
    dependencies = [("tracer", "0126_derived_metric_refresh")]
    # The forward data step is last, avoiding ALTER TABLE with pending deferred
    # FK trigger events on populated tables in the same migration transaction.
    operations = [
        migrations.RemoveConstraint(
            "metricerasurerequest", "metric_erasure_project_scope"
        ),
        migrations.RemoveConstraint("metricerasurerequest", "metric_erasure_scope"),
        migrations.RemoveConstraint("metricerasurerequest", "metric_erasure_subjects"),
        migrations.AlterField(
            "derivedmetricrun",
            "definition",
            models.ForeignKey(
                "tracer.derivedmetricversion",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="runs",
            ),
        ),
        migrations.AlterField(
            "metricgenerationpin",
            "definition_version",
            models.ForeignKey(
                "tracer.derivedmetricversion",
                on_delete=django.db.models.deletion.CASCADE,
                null=True,
                blank=True,
                related_name="+",
            ),
        ),
        migrations.AlterField(
            "metricerasurerequest",
            "organization",
            models.ForeignKey(
                "accounts.organization",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name="+",
            ),
        ),
        migrations.AlterField(
            "metricerasurerequest",
            "project",
            models.ForeignKey(
                "tracer.project",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name="+",
            ),
        ),
        migrations.AddField(
            "metricerasurerequest", "organization_key", models.UUIDField(null=True)
        ),
        *[
            migrations.AddField(
                "metricerasurerequest", name, models.UUIDField(null=True, blank=True)
            )
            for name in (
                "project_key",
                "metric_key",
                "version_key",
                "run_key",
                "snapshot_key",
                "result_key",
            )
        ],
        migrations.AddField(
            "metricerasurerequest",
            "deletion_key",
            models.CharField(max_length=100, null=True, blank=True, unique=True),
        ),
        migrations.AddField(
            "metricerasurerequest",
            "store_payload",
            models.JSONField(default=dict, blank=True),
        ),
        migrations.AlterField(
            "metricerasurerequest",
            "scope_kind",
            models.CharField(
                max_length=16,
                choices=[
                    (x, x.title())
                    for x in (
                        "organization",
                        "project",
                        "sessions",
                        "users",
                        "metric",
                        "version",
                        "run",
                        "history",
                    )
                ],
            ),
        ),
        migrations.AddField(
            "derivedmetricversion",
            "pins_revoked_at",
            models.DateTimeField(null=True, blank=True),
        ),
        *[
            migrations.AddField(model, name, field)
            for model in ("metricrefreshattempt", "metricreadmanifest")
            for name, field in (
                ("parent_manifest_key", models.UUIDField(null=True, blank=True)),
                (
                    "parent_manifest_sha256",
                    models.CharField(max_length=64, blank=True, default=""),
                ),
            )
        ],
        migrations.AddField(
            "metricreadmanifest", "remap_revision", models.BigIntegerField(default=0)
        ),
        migrations.AddField(
            "metricgenerationpin", "remap_revision", models.BigIntegerField(default=0)
        ),
        migrations.AddField(
            "metricreadmanifest",
            "revoked_at",
            models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            "metricreadmanifest",
            "revocation_reason",
            models.CharField(max_length=32, blank=True, default=""),
        ),
        migrations.RunPython(freeze_existing, guard_downgrade),
        # Make the two constraints mandatory without another table rewrite or FK
        # trigger interaction. SET CONSTRAINTS drains deferred events first.
        migrations.RunSQL("SET CONSTRAINTS ALL IMMEDIATE", migrations.RunSQL.noop),
        migrations.AlterField(
            "metricerasurerequest", "organization_key", models.UUIDField()
        ),
        migrations.AddConstraint(
            "metricerasurerequest",
            models.CheckConstraint(
                condition=models.Q(
                    scope_kind__in=[
                        "organization",
                        "project",
                        "sessions",
                        "users",
                        "metric",
                        "version",
                        "run",
                        "history",
                    ]
                ),
                name="metric_erasure_scope",
            ),
        ),
        migrations.AddConstraint(
            "metricerasurerequest",
            models.CheckConstraint(
                condition=models.Q(
                    scope_kind__in=["sessions", "users"],
                    subject_count__gte=1,
                    subject_count__lte=10000,
                )
                | models.Q(
                    scope_kind__in=[
                        "organization",
                        "project",
                        "metric",
                        "version",
                        "run",
                        "history",
                    ],
                    subject_count=0,
                ),
                name="metric_erasure_subjects",
            ),
        ),
        migrations.AddConstraint(
            "metricerasurerequest",
            models.CheckConstraint(
                condition=~models.Q(
                    scope_kind__in=[
                        "project",
                        "sessions",
                        "metric",
                        "version",
                        "run",
                        "history",
                    ]
                )
                | models.Q(project_key__isnull=False),
                name="metric_erasure_project_scope",
            ),
        ),
        # Run downgrade guard before reversing ANY constraints or dropping fields.
        migrations.RunPython(migrations.RunPython.noop, guard_downgrade),
    ]
