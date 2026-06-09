"""
Clear stale `encrypted_check_configs` on existing `__ui_guardrails__` policies.

Prior to the serializer fix, AgentccGuardrailPolicySerializer.update() always
overwrote new credentials with the existing encrypted blob. Existing rows are
stuck on the first credentials ever saved. Clearing forces the next save from
the org-config UI to populate them correctly with the user-supplied values.
"""

from django.db import migrations


def clear_stuck_encrypted_configs(apps, schema_editor):
    AgentccGuardrailPolicy = apps.get_model("prism", "AgentccGuardrailPolicy")
    AgentccGuardrailPolicy.objects.filter(
        name="__ui_guardrails__",
        encrypted_check_configs__isnull=False,
    ).update(encrypted_check_configs=None)


class Migration(migrations.Migration):

    dependencies = [
        ("prism", "0025_rename_indexes_and_related_name_alters"),
    ]

    operations = [
        migrations.RunPython(
            clear_stuck_encrypted_configs,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
