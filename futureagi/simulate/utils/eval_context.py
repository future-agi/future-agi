"""Eval mapping context helpers.

Shared between the Temporal `run_simulate_evaluations` activity (which
resolves eval-config mapping values at run time) and the call-detail API
serializer (which surfaces the same vocabulary to the frontend dropdown).
"""

import json

# Persona model fields exposed under persona.* via flatten_persona.
PERSONA_LIST_FIELDS = (
    "gender", "age_group", "occupation", "location", "personality",
    "communication_style", "languages", "accent", "conversation_speed",
    "finished_speaking_sensitivity", "interrupt_sensitivity", "keywords",
)
PERSONA_SCALAR_FIELDS = (
    "name", "description", "tone", "verbosity", "punctuation",
    "emoji_usage", "slang_usage", "typos_frequency", "regional_mix",
    "simulation_type", "additional_instruction",
)

# (SimulatorAgent attr, persona.<suffix>). "description" maps from
# SimulatorAgent.prompt — preserves the legacy persona_description alias.
SIMULATOR_FALLBACK_FIELDS = (
    ("name", "name"),
    ("prompt", "prompt"),
    ("prompt", "description"),
    ("voice_provider", "voice_provider"),
    ("voice_name", "voice_name"),
    ("model", "model"),
    ("llm_temperature", "llm_temperature"),
    ("conversation_speed", "conversation_speed"),
    ("interrupt_sensitivity", "interrupt_sensitivity"),
    ("finished_speaking_sensitivity", "finished_speaking_sensitivity"),
    ("max_call_duration_in_minutes", "max_call_duration_in_minutes"),
    ("initial_message_delay", "initial_message_delay"),
    ("initial_message", "initial_message"),
)


def flatten_jsonfield_value(value):
    """Flatten a JSONField value for eval consumption.

    None/"" → "". Empty list → "". Single-element list unwrapped. Multi-element
    list comma-joined. Dict JSON-stringified. Scalar str()'d.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        if len(value) == 1:
            return "" if value[0] is None else str(value[0])
        return ", ".join("" if v is None else str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)


def flatten_persona(persona):
    """Build persona.* from the Persona model only — drives the user-facing
    eval-mapping dropdown. SimulatorAgent fields are excluded here on purpose.
    """
    flat = {}
    for field in PERSONA_LIST_FIELDS:
        raw = getattr(persona, field, None) if persona else None
        flat[f"persona.{field}"] = flatten_jsonfield_value(raw)
    for field in PERSONA_SCALAR_FIELDS:
        raw = getattr(persona, field, None) if persona else None
        flat[f"persona.{field}"] = "" if raw is None else str(raw)

    persona_metadata = getattr(persona, "metadata", None) if persona else None
    if isinstance(persona_metadata, dict):
        for meta_key, meta_value in persona_metadata.items():
            flat[f"persona.metadata.{meta_key}"] = flatten_jsonfield_value(
                meta_value
            )

    return flat


def flatten_persona_for_resolver(persona, simulator_agent):
    """Same as flatten_persona but adds SimulatorAgent fallback so legacy
    eval-config mappings like persona.prompt keep resolving. Persona wins
    on conflicts.
    """
    flat = flatten_persona(persona)

    if simulator_agent is None:
        return flat

    for sim_attr, key in SIMULATOR_FALLBACK_FIELDS:
        target = f"persona.{key}"
        if flat.get(target):
            continue
        value = getattr(simulator_agent, sim_attr, None)
        flat[target] = "" if value is None else str(value)

    return flat


def resolve_scenario_column_by_name(call_execution, column_name):
    """Look up Column.name within the call's scenario dataset, fetch the cell.

    Returns the cell value string, or "" if any of:
    - call has no row_id (chat scenarios, free-form runs)
    - the scenario has no linked dataset
    - the column name doesn't exist in that dataset
    - the cell for (row_id, column) is missing

    Column.name is not unique within a dataset (no DB constraint). First match
    by Column.created_at wins; duplicates are logged for follow-up cleanup.
    """
    import structlog

    from model_hub.models.develop_dataset import Cell, Column

    logger = structlog.get_logger(__name__)

    metadata = call_execution.call_metadata or {}
    row_id = metadata.get("row_id")
    if not row_id:
        return ""

    scenario = getattr(call_execution, "scenario", None)
    dataset = scenario.dataset if scenario else None
    if not dataset:
        return ""

    matching = list(
        Column.objects.filter(
            dataset=dataset, name=column_name, deleted=False
        ).order_by("created_at")
    )
    if not matching:
        return ""
    if len(matching) > 1:
        logger.warning(
            "duplicate_column_names_in_dataset",
            dataset_id=str(dataset.id),
            column_name=column_name,
            column_ids=[str(c.id) for c in matching],
        )

    try:
        cell = Cell.objects.get(row_id=row_id, column=matching[0], deleted=False)
        return cell.value
    except Cell.DoesNotExist:
        return ""


def resolve_persona_for_call(call_execution):
    """Pick the Persona used for this specific call.

    Priority:
    1. call_metadata.row_data.persona (UUID) — baked into the dataset row
       at execution time by temporal/activities/test_execution.py.
    2. First Persona referenced by scenario.metadata.persona_ids
       (ordered by created_at — stable across runs).
    3. None — `persona.*` keys resolve to "".

    Invalid UUIDs in either source resolve to None instead of raising;
    callers (eval resolver, serializer) treat that as "no persona bound".
    """
    import uuid as _uuid

    from django.core.exceptions import ValidationError

    from simulate.models import Persona

    metadata = call_execution.call_metadata or {}
    row_data = metadata.get("row_data") or {}
    row_persona_id = row_data.get("persona")
    if row_persona_id:
        try:
            return Persona.no_workspace_objects.get(
                id=row_persona_id, deleted=False
            )
        except (Persona.DoesNotExist, ValueError, ValidationError):
            pass

    scenario = getattr(call_execution, "scenario", None)
    if not scenario:
        return None
    scenario_meta = scenario.metadata or {}
    persona_ids = scenario_meta.get("persona_ids") or []
    if not persona_ids:
        return None

    valid_ids = []
    for pid in persona_ids:
        try:
            _uuid.UUID(str(pid))
            valid_ids.append(pid)
        except (ValueError, TypeError):
            continue
    if not valid_ids:
        return None

    return (
        Persona.no_workspace_objects.filter(id__in=valid_ids, deleted=False)
        .order_by("created_at")
        .first()
    )
