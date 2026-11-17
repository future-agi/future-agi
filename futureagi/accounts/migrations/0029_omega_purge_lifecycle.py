"""Workspace and organization deletion capture, sessions ending with their user, and EE's purge
receipts (READINESS lane 3.22, E02). The triggers are Postgres-only (accounts.lifecycle.capture_sql).
"""

from django.db import migrations, models

from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:accounts_omega_purge_receipt",
        "postgres:accounts_workspace",
        "postgres:accounts_organization",
        "postgres:accounts_user",
        "postgres:accounts_auth_token",
        "postgres:accounts_host_event_outbox",
    ],
    "mitigation": (
        "Creates accounts_omega_purge_receipt and installs four AFTER ... FOR EACH "
        "ROW triggers through DROP TRIGGER IF EXISTS / CREATE TRIGGER, on Postgres "
        "only. No existing row is read or rewritten at migration time. CREATE TRIGGER "
        "takes a SHARE ROW EXCLUSIVE lock on accounts_workspace, "
        "accounts_organization and accounts_user for the length of each statement. "
        "Re-runnable; reverse drops the triggers, the functions and the table. After "
        "it, deactivating or deleting an account also updates that account's token "
        "rows in the same transaction: the rehearsal must measure an account "
        "deactivation with many tokens."
    ),
    "reviewer": "",
    "review": "",
    "large_volume_rehearsal": True,
}


def install_capture(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        from accounts.lifecycle.capture_sql import install

        install(schema_editor)


def uninstall_capture(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        from accounts.lifecycle.capture_sql import uninstall

        uninstall(schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0028_merge_20261019_1400"),
    ]

    operations = [
        migrations.CreateModel(
            name="OmegaPurgeReceipt",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("event_id", models.UUIDField(unique=True)),
                ("kind", models.CharField(max_length=32)),
                ("organization_id", models.UUIDField(db_index=True)),
                ("workspace_id", models.UUIDField(blank=True, null=True)),
                (
                    "outcome",
                    models.CharField(
                        choices=[("purged", "Purged"), ("ignored", "Ignored")],
                        max_length=16,
                    ),
                ),
                ("requested_at", models.DateTimeField()),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("receipt", models.JSONField(default=dict)),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "accounts_omega_purge_receipt",
            },
        ),
        migrations.RunPython(install_capture, uninstall_capture),
    ]
