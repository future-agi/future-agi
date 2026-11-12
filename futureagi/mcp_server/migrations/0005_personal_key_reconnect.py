from django.db import DEFAULT_DB_ALIAS, migrations, models, transaction
from django.utils import timezone

from tfc.migration_database import executor_alias_scope

FLAGS = ("is_active", "reconnect_required")


def fingerprint(row):
    result = {field: getattr(row, field) for field in FLAGS}
    result.update(deleted=row.deleted, updated_at=row.updated_at.isoformat())
    for field in ("user_id", "organization_id", "api_key_id"):
        value = getattr(row, field)
        result[field] = str(value) if value is not None else None
    return result


def flag_reconnect(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        alias = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        connections = apps.get_model("mcp_server", "MCPConnection")._base_manager.using(
            alias
        )
        journals = apps.get_model("accounts", "RetirementManifest").objects.using(alias)
        ids = connections.filter(api_key__retired_reason="733").values_list("pk", flat=True)
        for pk in ids.iterator():
            with transaction.atomic(using=alias):
                row = connections.select_for_update().get(pk=pk)
                saved = (
                    journals.select_for_update()
                    .filter(service="mcp", action="reconnect", object_id=str(pk))
                    .first()
                )
                if saved is not None and saved.superseded_by:
                    continue
                if saved is not None and saved.reverted_at is None:
                    if saved.applied_state != fingerprint(row):
                        saved.superseded_by = "object_changed"
                        saved.save(using=alias, update_fields=["superseded_by"])
                    continue
                prior = {field: getattr(row, field) for field in FLAGS}
                row.is_active, row.reconnect_required = False, True
                row.updated_at = timezone.now()
                # Sender-specific live handlers are bypassed; global signals inherit this scope.
                row.save(using=alias, update_fields=[*FLAGS, "updated_at"])
                values = {
                    "prior_state": prior,
                    "applied_state": fingerprint(row),
                    "applied_at": timezone.now(),
                    "reverted_at": None,
                    "superseded_by": "",
                    "confirmed": True,
                }
                if saved is None:
                    journals.create(
                        service="mcp", action="reconnect", object_id=str(pk), **values
                    )
                else:
                    for field, value in values.items():
                        setattr(saved, field, value)
                    saved.save(using=alias, update_fields=list(values))


def restore_connections(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        alias = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        connections = apps.get_model("mcp_server", "MCPConnection")._base_manager.using(
            alias
        )
        journals = apps.get_model("accounts", "RetirementManifest").objects.using(alias)
        ids = connections.filter(api_key__retired_reason="733").values_list("pk", flat=True)
        for pk in ids.iterator():
            with transaction.atomic(using=alias):
                row = connections.select_for_update().get(pk=pk)
                saved = (
                    journals.select_for_update()
                    .filter(service="mcp", action="reconnect", object_id=str(pk))
                    .first()
                )
                if saved is None:
                    raise RuntimeError(
                        "Cannot restore a connection without its retirement journal"
                    )
                if saved.superseded_by or saved.reverted_at is not None:
                    continue
                if (
                    not saved.confirmed
                    or not isinstance(saved.prior_state, dict)
                    or set(saved.prior_state) != set(FLAGS)
                    or any(type(saved.prior_state[field]) is not bool for field in FLAGS)
                ):
                    raise RuntimeError(
                        "Cannot restore an incomplete connection retirement journal"
                    )
                if saved.applied_state != fingerprint(row):
                    saved.superseded_by = "object_changed"
                    saved.save(using=alias, update_fields=["superseded_by"])
                    continue
                for field, value in saved.prior_state.items():
                    setattr(row, field, value)
                row.updated_at = timezone.now()
                row.save(using=alias, update_fields=[*FLAGS, "updated_at"])
                saved.reverted_at = timezone.now()
                saved.save(using=alias, update_fields=["reverted_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("mcp_server", "0004_remove_retired_tool_groups"),
        ("accounts", "0034_retirement_manifest"),
    ]
    operations = [
        migrations.AddField(
            model_name="mcpconnection",
            name="reconnect_required",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(flag_reconnect, restore_connections),
    ]
