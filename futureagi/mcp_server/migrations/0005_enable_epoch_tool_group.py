from django.db import migrations, models
from django.db.models.expressions import RawSQL


def enable_epoch_tool_group(apps, schema_editor):
    config = apps.get_model("mcp_server", "MCPToolGroupConfig")
    configs = config._base_manager.using(schema_editor.connection.alias)
    # Only where simulation is on: epoch tools can spend credits, and a user who
    # turned simulation off has not agreed to that.
    pending = configs.filter(enabled_groups__contains=["simulation"]).exclude(
        enabled_groups__contains=["epoch"]
    )
    while ids := list(pending.order_by("pk").values_list("pk", flat=True)[:500]):
        # Append to the current DB value so concurrent edits are preserved.
        # Include soft-deleted rows: a restored connection keeps its selection.
        configs.filter(pk__in=ids).exclude(enabled_groups__contains=["epoch"]).update(
            enabled_groups=RawSQL(
                "enabled_groups || %s::jsonb",
                ('["epoch"]',),
                output_field=models.JSONField(),
            )
        )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("mcp_server", "0004_remove_retired_tool_groups")]
    operations = [
        # Rollback must not take away a group a user may have chosen since.
        migrations.RunPython(enable_epoch_tool_group, migrations.RunPython.noop),
    ]
