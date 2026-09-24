from django.db import migrations, models
from django.db.models.expressions import RawSQL


def remove_retired_tool_groups(apps, schema_editor):
    config = apps.get_model("mcp_server", "MCPToolGroupConfig")
    configs = config._base_manager.using(schema_editor.connection.alias)
    retired = configs.filter(enabled_groups__has_any_keys=["users", "docs"])
    while ids := list(retired.order_by("pk").values_list("pk", flat=True)[:500]):
        # Subtract from the current DB value so concurrent edits are preserved.
        # Include soft-deleted rows: restoring a connection must remain usable.
        configs.filter(pk__in=ids).update(
            enabled_groups=RawSQL(
                "enabled_groups - %s - %s",
                ("users", "docs"),
                output_field=models.JSONField(),
            )
        )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("mcp_server", "0003_streamable_http_transport")]
    operations = [
        # Rollback must not grant permissions that a user may have disabled.
        migrations.RunPython(remove_retired_tool_groups, migrations.RunPython.noop),
    ]
