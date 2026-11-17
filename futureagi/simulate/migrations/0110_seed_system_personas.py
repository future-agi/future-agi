"""Create the built-in personas on every install, with no operator step.

Each built-in persona is matched on its stable key: a system persona with the
same persona_id. Only the missing ones are created. A row that already exists
(for example from an earlier manual run, edited since, or soft-deleted) is
kept exactly as it is: this migration never updates, duplicates or deletes a
row. Reversing it keeps the rows, because scenarios may already use them.
"""

from django.db import migrations

from simulate.services.system_personas import SYSTEM_PERSONAS
from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:simulate_personas"],
    "mitigation": (
        "forward reads persona_id from the simulate_personas rows with persona_type "
        "system, soft-deleted rows included, and bulk-creates only the built-in "
        "personas in simulate.services.system_personas.SYSTEM_PERSONAS whose "
        "persona_id is missing, as system rows with no organization or workspace. It "
        "never updates, duplicates or deletes a row, so an existing persona stays "
        "exactly as it is. Safe to re-run: a second run finds every built-in persona "
        "present and creates nothing, and a failed run rolls back whole. The reverse "
        "is a no-op and keeps the rows, because scenarios may already use them. Row "
        "scale: the read is bounded by the number of system personas, and the write "
        "is at most one row per built-in persona in SYSTEM_PERSONAS, once."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def forward(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        using = schema_editor.connection.alias
        Persona = apps.get_model("simulate", "Persona")
        personas = Persona._base_manager.using(using)
        existing = set(
            personas.filter(persona_type="system").values_list("persona_id", flat=True)
        )
        personas.bulk_create(
            [
                Persona(
                    persona_type="system",
                    persona_id=data["persona_id"],
                    name=data["name"],
                    description=data.get("description", ""),
                    gender=data.get("gender", []),
                    age_group=data.get("age_group", []),
                    location=data.get("location", []),
                    occupation=data.get("occupation", []),
                    personality=data.get("personality", []),
                    communication_style=data.get("communication_style", []),
                    accent=data.get("accent", []),
                    multilingual=data.get("multilingual", False),
                    languages=data.get("languages", []),
                    conversation_speed=data.get("conversation_speed", []),
                    background_sound=data.get("background_sound", False),
                    finished_speaking_sensitivity=data.get(
                        "finished_speaking_sensitivity", []
                    ),
                    interrupt_sensitivity=data.get("interrupt_sensitivity", []),
                    keywords=data.get("keywords", []),
                    metadata=data.get("custom_properties", {}),
                    additional_instruction=data.get("additional_instruction", ""),
                    organization=None,
                    workspace=None,
                    is_default=True,
                )
                for data in SYSTEM_PERSONAS
                if data["persona_id"] not in existing
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0109_agent_fix_model_reserved_usd"),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
