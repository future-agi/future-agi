"""
Script to insert or update system personas in the database.
Provides a create-or-update function similar to insert_evals_template.
"""

from django.db import transaction

from simulate.models import Persona
from simulate.services.system_personas import SYSTEM_PERSONAS

# Global flag to control whether to rebuild system personas
build_system_personas = True


def insert_system_personas(personas_data=None):
    """
    Sync system personas in the database (create/update/delete).

    Args:
        personas_data: List of persona dictionaries. If None, uses SYSTEM_PERSONAS from system_personas.py

    The function will:
    - Create new personas if persona_id doesn't exist in DB
    - Update existing personas if persona_id matches (matched by persona_id)
    - Delete personas that exist in DB but not in template
    - All personas are created as system-level (persona_type='system')
    """
    global build_system_personas

    if not build_system_personas:
        return

    build_system_personas = False

    # Use provided data or default to SYSTEM_PERSONAS
    if personas_data is None:
        personas_data = SYSTEM_PERSONAS

    # Get all existing system persona_ids in the database
    existing_persona_ids = set(
        Persona.objects.filter(persona_type=Persona.PersonaType.SYSTEM).values_list(
            "persona_id", flat=True
        )
    )

    # Get all persona_ids from the template
    template_persona_ids = {persona["persona_id"] for persona in personas_data}

    # Fetch all existing system personas in one query and create a dictionary with persona_id as key
    existing_personas = {
        persona.persona_id: persona
        for persona in Persona.objects.filter(
            persona_type=Persona.PersonaType.SYSTEM, persona_id__in=template_persona_ids
        )
    }

    # Separate personas into updates, creates, and deletes
    to_update = []
    to_create = []
    to_delete = []

    # Find personas to delete (exist in DB but not in template)
    personas_to_delete = existing_persona_ids - template_persona_ids
    if personas_to_delete:
        to_delete = list(
            Persona.objects.filter(
                persona_type=Persona.PersonaType.SYSTEM,
                persona_id__in=personas_to_delete,
            )
        )

    for persona_data in personas_data:
        try:
            if persona_data["persona_id"] in existing_persona_ids:
                # Update existing persona
                existing_persona = existing_personas[persona_data["persona_id"]]
                existing_persona.name = persona_data["name"]
                existing_persona.description = persona_data.get("description", "")
                existing_persona.gender = persona_data.get("gender", [])
                existing_persona.age_group = persona_data.get("age_group", [])
                existing_persona.location = persona_data.get("location", [])
                existing_persona.occupation = persona_data.get("occupation", [])
                existing_persona.personality = persona_data.get("personality", [])
                existing_persona.communication_style = persona_data.get(
                    "communication_style", []
                )
                existing_persona.accent = persona_data.get("accent", [])
                existing_persona.multilingual = persona_data.get("multilingual", False)
                existing_persona.languages = persona_data.get("languages", [])
                existing_persona.conversation_speed = persona_data.get(
                    "conversation_speed", []
                )
                existing_persona.background_sound = _parse_boolean(
                    persona_data.get("background_sound", "false")
                )
                existing_persona.finished_speaking_sensitivity = persona_data.get(
                    "finished_speaking_sensitivity", []
                )
                existing_persona.interrupt_sensitivity = persona_data.get(
                    "interrupt_sensitivity", []
                )
                existing_persona.keywords = persona_data.get("keywords", [])
                existing_persona.metadata = persona_data.get("custom_properties", {})
                existing_persona.additional_instruction = persona_data.get(
                    "additional_instruction", ""
                )
                existing_persona.persona_type = Persona.PersonaType.SYSTEM
                existing_persona.organization = None
                existing_persona.workspace = None
                to_update.append(existing_persona)
            else:
                # Prepare for bulk create
                to_create.append(
                    Persona(
                        persona_type=Persona.PersonaType.SYSTEM,
                        persona_id=persona_data["persona_id"],
                        name=persona_data["name"],
                        description=persona_data.get("description", ""),
                        gender=persona_data.get("gender", []),
                        age_group=persona_data.get("age_group", []),
                        location=persona_data.get("location", []),
                        occupation=persona_data.get("occupation", []),
                        personality=persona_data.get("personality", []),
                        communication_style=persona_data.get("communication_style", []),
                        accent=persona_data.get("accent", []),
                        multilingual=persona_data.get("multilingual", False),
                        languages=persona_data.get("languages", []),
                        conversation_speed=persona_data.get("conversation_speed", []),
                        background_sound=_parse_boolean(
                            persona_data.get("background_sound", False)
                        ),
                        finished_speaking_sensitivity=persona_data.get(
                            "finished_speaking_sensitivity", []
                        ),
                        interrupt_sensitivity=persona_data.get(
                            "interrupt_sensitivity", []
                        ),
                        keywords=persona_data.get("keywords", []),
                        metadata=persona_data.get("custom_properties", {}),
                        additional_instruction=persona_data.get(
                            "additional_instruction", ""
                        ),
                        organization=None,
                        workspace=None,
                        is_default=True,  # Mark system personas as default
                    )
                )
        except Exception:
            continue

    # Perform bulk operations within a transaction
    with transaction.atomic():
        # Bulk delete (remove personas not in template)
        if to_delete:
            try:
                deleted_ids = [p.persona_id for p in to_delete]
                len(to_delete)
                Persona.objects.filter(
                    persona_type=Persona.PersonaType.SYSTEM, persona_id__in=deleted_ids
                ).delete()
            except Exception:
                pass

        # Bulk update
        if to_update:
            try:
                Persona.objects.bulk_update(
                    to_update,
                    [
                        "name",
                        "description",
                        "gender",
                        "age_group",
                        "location",
                        "occupation",
                        "personality",
                        "communication_style",
                        "accent",
                        "multilingual",
                        "languages",
                        "conversation_speed",
                        "background_sound",
                        "finished_speaking_sensitivity",
                        "interrupt_sensitivity",
                        "keywords",
                        "metadata",
                        "additional_instruction",
                        "persona_type",
                        "organization",
                        "workspace",
                    ],
                )

            except Exception:
                pass

        # Bulk create
        if to_create:
            try:
                Persona.objects.bulk_create(to_create, ignore_conflicts=True)
            except Exception:
                pass


def _parse_boolean(value):
    """Convert string boolean to actual boolean"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ["true", "1", "yes"]
    return bool(value)


def _parse_int(value, default=5):
    """Convert string to integer with validation"""
    if isinstance(value, int):
        return max(1, min(10, value))  # Clamp between 1-10
    try:
        val = int(value)
        return max(1, min(10, val))  # Clamp between 1-10
    except (ValueError, TypeError):
        return default
