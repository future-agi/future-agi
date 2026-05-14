from __future__ import annotations

from typing import Any


class UnsupportedPersonaFilter(ValueError):
    """Raised when a persona filter cannot be translated safely."""


PERSONA_FILTER_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "persona_name"),
    "description": ("description",),
    "gender": ("gender",),
    "age_group": ("age_group", "ageGroup"),
    "occupation": ("occupation", "profession"),
    "location": ("location",),
    "personality": ("personality",),
    "communication_style": ("communication_style", "communicationStyle"),
    "language": ("language", "languages"),
    "languages": ("languages", "language"),
    "accent": ("accent",),
    "conversation_speed": ("conversation_speed", "conversationSpeed"),
    "multilingual": ("multilingual",),
    "background_sound": ("background_sound", "backgroundSound"),
    "finished_speaking_sensitivity": (
        "finished_speaking_sensitivity",
        "finishedSpeakingSensitivity",
    ),
    "interrupt_sensitivity": ("interrupt_sensitivity", "interruptSensitivity"),
    "keywords": ("keywords",),
    "tone": ("tone",),
    "verbosity": ("verbosity",),
    "punctuation": ("punctuation", "punctuation_style", "punctuationStyle"),
    "slang_usage": ("slang_usage", "slangUsage"),
    "typos_frequency": ("typos_frequency", "typosFrequency", "typo_level", "typoLevel"),
    "regional_mix": ("regional_mix", "regionalMix"),
    "emoji_usage": ("emoji_usage", "emojiUsage"),
    "additional_instruction": ("additional_instruction", "additionalInstruction"),
}


def normalize_persona_filter_field(column_id: str | None) -> str | None:
    if not column_id:
        return None
    raw = str(column_id)
    if raw == "persona":
        return ""
    if raw.startswith("persona."):
        field = raw.split(".", 1)[1]
        return field if field in PERSONA_FILTER_FIELD_ALIASES else None
    return None


def is_persona_filter_column(column_id: str | None) -> bool:
    return normalize_persona_filter_field(column_id) is not None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str) and "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def _coalesced(expr: str) -> str:
    return f"COALESCE(({expr}), '')"


def _persona_exprs(queryset, field: str) -> list[str]:
    table = queryset.model._meta.db_table
    if field == "":
        return [f"{table}.call_metadata #>> '{{row_data,persona}}'"]

    aliases = PERSONA_FILTER_FIELD_ALIASES[field]
    return [
        f"{table}.call_metadata #>> '{{row_data,persona,{alias}}}'"
        for alias in aliases
    ]


def _equals_clause(expr: str, value: Any) -> tuple[str, list[Any]]:
    text_expr = _coalesced(expr)
    text_value = str(value)
    return (
        f"(LOWER({text_expr}) = LOWER(%s) OR {text_expr} ILIKE %s)",
        [text_value, f'%"{text_value}"%'],
    )


def _contains_clause(expr: str, value: Any) -> tuple[str, list[Any]]:
    text_expr = _coalesced(expr)
    return f"{text_expr} ILIKE %s", [f"%{value}%"]


def _boolean_clause(expr: str, value: Any) -> tuple[str, list[Any]]:
    normalized = "true" if str(value).lower() in {"true", "1", "yes"} else "false"
    return f"LOWER({_coalesced(expr)}) = %s", [normalized]


def _any_clause(expressions: list[str], values: list[Any], builder) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for expr in expressions:
        for value in values:
            clause, clause_params = builder(expr, value)
            clauses.append(clause)
            params.extend(clause_params)
    return f"({' OR '.join(clauses)})", params


def apply_persona_filter(queryset, column_id, op, value, filter_type=None):
    """Apply a filter against persona fields stored in call_metadata.row_data.persona.

    Simulation calls have historically stored persona as JSON with a mix of
    scalar and list-shaped values. Equality therefore matches exact scalar text
    and quoted values inside JSON arrays, while contains remains substring based.
    """

    field = normalize_persona_filter_field(column_id)
    if field is None or not op:
        raise UnsupportedPersonaFilter(f"Unsupported persona filter: {column_id}")

    expressions = _persona_exprs(queryset, field)

    if op == "is_null":
        where = " AND ".join(
            f"({expr} IS NULL OR {_coalesced(expr)} = '')" for expr in expressions
        )
        return queryset.extra(where=[where])
    if op == "is_not_null":
        where = " OR ".join(
            f"({expr} IS NOT NULL AND {_coalesced(expr)} <> '')" for expr in expressions
        )
        return queryset.extra(where=[f"({where})"])

    values = [item for item in _as_list(value) if item is not None]
    if not values:
        raise UnsupportedPersonaFilter(f"Missing value for persona filter: {column_id}")

    if filter_type == "boolean":
        where, params = _any_clause(expressions, values, _boolean_clause)
        if op in ("equals", "eq", "in"):
            return queryset.extra(where=[where], params=params)
        if op in ("not_equals", "ne", "not_in"):
            return queryset.extra(where=[f"NOT {where}"], params=params)
        raise UnsupportedPersonaFilter(f"Unsupported boolean persona op: {op}")

    if op in ("equals", "eq", "in"):
        where, params = _any_clause(expressions, values, _equals_clause)
        return queryset.extra(where=[where], params=params)
    if op in ("not_equals", "ne", "not_in"):
        where, params = _any_clause(expressions, values, _equals_clause)
        return queryset.extra(where=[f"NOT {where}"], params=params)
    if op in ("contains", "icontains"):
        where, params = _any_clause(expressions, values, _contains_clause)
        return queryset.extra(where=[where], params=params)
    if op == "not_contains":
        where, params = _any_clause(expressions, values, _contains_clause)
        return queryset.extra(where=[f"NOT {where}"], params=params)

    raise UnsupportedPersonaFilter(f"Unsupported persona op: {op}")
