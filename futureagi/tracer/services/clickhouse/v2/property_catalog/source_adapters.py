"""Current, bounded native definitions and pure property contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from tracer.services.configured_value_options import configured_value_options
from tracer.utils.property_registry import canonical_system_attribute_name

from .codec import combine_search_text
from .models import (
    PropertyCategory,
    PropertyDefinition,
    PropertyKind,
    PropertyRole,
)

_SYSTEM_PROPERTY_SPECS = (
    ("traces", "project", "Project", "string", "", ""),
    ("traces", "latency", "Latency", "number", "ms", ""),
    ("traces", "error_rate", "Error Rate", "number", "%", ""),
    ("traces", "tokens", "Tokens", "number", "tokens", ""),
    ("traces", "input_tokens", "Input Tokens", "number", "tokens", ""),
    ("traces", "output_tokens", "Output Tokens", "number", "tokens", ""),
    ("traces", "time_to_first_token", "Time to First Token", "number", "ms", ""),
    ("traces", "cost", "Cost", "number", "$", ""),
    ("traces", "session_count", "Sessions", "number", "", ""),
    ("traces", "user_count", "Users", "number", "", ""),
    ("traces", "trace_count", "Traces", "number", "", ""),
    ("traces", "span_count", "Spans", "number", "", ""),
    ("traces", "model", "Model", "string", "", ""),
    ("traces", "status", "Status", "string", "", ""),
    ("traces", "service_name", "Service Name", "string", "", ""),
    ("traces", "span_kind", "Span Kind", "string", "", ""),
    ("traces", "provider", "Provider", "string", "", ""),
    ("traces", "session", "Session", "string", "", ""),
    ("traces", "user", "User", "string", "", ""),
    ("traces", "user_id_type", "User ID Type", "string", "", ""),
    ("traces", "prompt_name", "Prompt Name", "string", "", ""),
    ("traces", "prompt_version", "Prompt Version", "string", "", ""),
    ("traces", "prompt_label", "Prompt Label", "string", "", ""),
    ("traces", "tag", "Tag", "string", "", ""),
    ("traces", "has_eval", "Has Evaluation", "boolean", "", "dimension"),
    (
        "traces",
        "has_annotation",
        "Has Annotation",
        "boolean",
        "",
        "dimension",
    ),
    (
        "traces",
        "agent_talk_percentage",
        "Agent Talk %",
        "number",
        "%",
        "",
    ),
    ("all", "dataset", "Dataset", "string", "", ""),
    ("all", "eval_source", "Eval Source", "string", "", ""),
    ("datasets", "row_count", "Row Count", "number", "", ""),
    ("datasets", "prompt_tokens", "Prompt Tokens", "number", "tokens", ""),
    (
        "datasets",
        "completion_tokens",
        "Completion Tokens",
        "number",
        "tokens",
        "",
    ),
    ("datasets", "total_tokens", "Total Tokens", "number", "tokens", ""),
    ("datasets", "response_time", "Response Time", "number", "ms", ""),
    ("datasets", "cell_error_rate", "Cell Error Rate", "number", "%", ""),
    ("datasets", "dataset", "Dataset", "string", "", ""),
    ("datasets", "eval_template", "Eval Template", "string", "", ""),
    ("datasets", "column_name", "Column Name", "string", "", ""),
    ("datasets", "column_source", "Column Source", "string", "", ""),
    ("datasets", "cell_status", "Cell Status", "string", "", ""),
    ("simulation", "call_count", "Call Count", "number", "", ""),
    ("simulation", "success_rate", "Success Rate", "number", "%", ""),
    ("simulation", "failure_rate", "Failure Rate", "number", "%", ""),
    ("simulation", "duration", "Duration", "number", "s", ""),
    ("simulation", "response_time", "Response Time", "number", "ms", ""),
    ("simulation", "agent_latency", "Agent Latency", "number", "ms", ""),
    ("simulation", "stt_latency", "STT Latency", "number", "ms", ""),
    ("simulation", "tts_latency", "TTS Latency", "number", "ms", ""),
    ("simulation", "llm_latency", "LLM Latency", "number", "ms", ""),
    ("simulation", "total_cost", "Total Cost", "number", "cents", ""),
    ("simulation", "stt_cost", "STT Cost", "number", "cents", ""),
    ("simulation", "tts_cost", "TTS Cost", "number", "cents", ""),
    ("simulation", "llm_cost", "LLM Cost", "number", "cents", ""),
    ("simulation", "customer_cost", "Customer Cost", "number", "cents", ""),
    ("simulation", "overall_score", "Overall Score", "number", "", ""),
    ("simulation", "message_count", "Message Count", "number", "", ""),
    (
        "simulation",
        "user_interruptions",
        "User Interruptions",
        "number",
        "",
        "",
    ),
    (
        "simulation",
        "user_interruption_rate",
        "User Interruption Rate",
        "number",
        "/min",
        "",
    ),
    ("simulation", "ai_interruptions", "AI Interruptions", "number", "", ""),
    (
        "simulation",
        "ai_interruption_rate",
        "AI Interruption Rate",
        "number",
        "/min",
        "",
    ),
    (
        "simulation",
        "stop_time_after_interruption",
        "Stop Time After Interruption",
        "number",
        "ms",
        "",
    ),
    ("simulation", "user_wpm", "User WPM", "number", "wpm", ""),
    ("simulation", "bot_wpm", "Bot WPM", "number", "wpm", ""),
    ("simulation", "talk_ratio", "Talk Ratio", "number", "%", ""),
    ("simulation", "simulation", "Simulation", "string", "", ""),
    ("simulation", "scenario", "Scenario", "string", "", ""),
    ("simulation", "agent_definition", "Agent", "string", "", ""),
    ("simulation", "agent_version", "Agent Version", "string", "", ""),
    ("simulation", "persona", "Persona", "string", "", ""),
    ("simulation", "call_type", "Call Type", "string", "", ""),
    ("simulation", "status", "Status", "string", "", ""),
    ("simulation", "scenario_type", "Scenario Type", "string", "", ""),
    ("simulation", "ended_reason", "Ended Reason", "string", "", ""),
    ("simulation", "run_test", "Test", "string", "", ""),
    ("simulation", "test_execution", "Test Run", "string", "", ""),
    ("simulation", "persona_gender", "Persona Gender", "string", "", ""),
    (
        "simulation",
        "persona_age_group",
        "Persona Age Group",
        "string",
        "",
        "",
    ),
    ("simulation", "persona_location", "Persona Location", "string", "", ""),
    (
        "simulation",
        "persona_profession",
        "Persona Profession",
        "string",
        "",
        "",
    ),
    (
        "simulation",
        "persona_personality",
        "Persona Personality",
        "string",
        "",
        "",
    ),
    (
        "simulation",
        "persona_communication_style",
        "Persona Communication Style",
        "string",
        "",
        "",
    ),
    ("simulation", "persona_accent", "Persona Accent", "string", "", ""),
    ("simulation", "persona_language", "Persona Language", "string", "", ""),
    (
        "simulation",
        "persona_conversation_speed",
        "Persona Conversation Speed",
        "string",
        "",
        "",
    ),
)

# Logical list/filter surfaces have distinct registry identities even when
# their value adapter routes to the same CH facts (spans/voice -> traces and
# users -> sessions).  Keep these definitions separate from dashboard aliases.
_SYSTEM_PROPERTY_SPECS += (
    ("traces", "trace_name", "Trace Name", "string", "", ""),
    ("traces", "input", "Input", "string", "", ""),
    ("traces", "output", "Output", "string", "", ""),
    ("traces", "start_time", "Timestamp", "datetime", "", "dimension"),
    ("traces", "total_tokens", "Total Tokens", "number", "tokens", ""),
    ("traces", "trace_id", "Trace Id", "string", "", "dimension"),
    ("traces", "prompt_tokens", "Prompt Tokens", "number", "tokens", ""),
    (
        "traces",
        "completion_tokens",
        "Completion Tokens",
        "number",
        "tokens",
        "",
    ),
    ("traces", "session_id", "Session Id", "string", "", "dimension"),
    ("traces", "user_id", "User Id", "string", "", "dimension"),
    ("traces", "tags", "Tags", "array", "", "dimension"),
    ("spans", "span_name", "Span Name", "string", "", "dimension"),
    ("spans", "status", "Status", "string", "", "dimension"),
    ("spans", "input", "Input", "string", "", "dimension"),
    ("spans", "output", "Output", "string", "", "dimension"),
    ("spans", "latency_ms", "Duration", "number", "ms", ""),
    ("spans", "total_tokens", "Tokens", "number", "tokens", ""),
    ("spans", "cost", "Total Cost", "number", "$", ""),
    ("spans", "model", "Model", "string", "", "dimension"),
    ("spans", "start_time", "Timestamp", "datetime", "", "dimension"),
    ("spans", "span_id", "Span Id", "string", "", "dimension"),
    ("spans", "trace_id", "Trace Id", "string", "", "dimension"),
    ("spans", "prompt_tokens", "Prompt Tokens", "number", "tokens", ""),
    (
        "spans",
        "completion_tokens",
        "Completion Tokens",
        "number",
        "tokens",
        "",
    ),
    ("spans", "provider", "Provider", "string", "", "dimension"),
    ("spans", "user_id", "User Id", "string", "", "dimension"),
    ("spans", "user_id_type", "User Id Type", "string", "", "dimension"),
    ("spans", "user_id_hash", "User Id Hash", "string", "", "dimension"),
    ("sessions", "session_id", "Session Id", "string", "", "dimension"),
    ("sessions", "first_message", "First Message", "string", "", "dimension"),
    ("sessions", "last_message", "Last Message", "string", "", "dimension"),
    ("sessions", "duration", "Duration", "number", "s", ""),
    ("sessions", "total_cost", "Total Cost", "number", "$", ""),
    ("sessions", "total_traces_count", "Total Traces", "number", "", ""),
    ("sessions", "start_time", "Start Time", "datetime", "", "dimension"),
    ("sessions", "end_time", "End Time", "datetime", "", "dimension"),
    ("sessions", "user_id", "User Id", "string", "", "dimension"),
    ("sessions", "user_id_type", "User Id Type", "string", "", "dimension"),
    ("sessions", "user_id_hash", "User Id Hash", "string", "", "dimension"),
    ("sessions", "total_tokens", "Total Tokens", "number", "tokens", ""),
    ("users", "user_id", "User Id", "string", "", "dimension"),
    ("users", "user_id_type", "User Id Type", "string", "", "dimension"),
    ("users", "user_id_hash", "User Id Hash", "string", "", "dimension"),
    ("users", "activated_at", "Activated At", "datetime", "", "dimension"),
    ("users", "last_active", "Last Active", "datetime", "", "dimension"),
    ("users", "num_active_days", "Active Days", "number", "days", ""),
    ("users", "total_cost", "Total Cost", "number", "$", ""),
    ("users", "total_tokens", "Total Tokens", "number", "tokens", ""),
    ("users", "input_tokens", "Input Tokens", "number", "tokens", ""),
    ("users", "output_tokens", "Output Tokens", "number", "tokens", ""),
    ("users", "num_traces", "Traces", "number", "", ""),
    ("users", "num_sessions", "Sessions", "number", "", ""),
    (
        "users",
        "avg_session_duration",
        "Average Session Duration",
        "number",
        "s",
        "",
    ),
    ("users", "avg_trace_latency", "Average Trace Latency", "number", "ms", ""),
    ("users", "num_llm_calls", "LLM Calls", "number", "", ""),
    (
        "users",
        "num_guardrails_triggered",
        "Guardrails Triggered",
        "number",
        "",
        "",
    ),
    (
        "users",
        "num_traces_with_errors",
        "Traces With Errors",
        "number",
        "",
        "",
    ),
    ("users", "active_users", "Active Users", "number", "", ""),
    ("users", "avg_cost_per_user", "Average Cost Per User", "number", "$", ""),
    (
        "users",
        "avg_traces_per_user",
        "Average Traces Per User",
        "number",
        "",
        "",
    ),
    ("voice_calls", "call_status", "Call Status", "string", "", "dimension"),
    ("voice_calls", "cost_cents", "Cost", "number", "cents", ""),
    ("voice_calls", "call_id", "Call Id", "string", "", "dimension"),
    ("voice_calls", "call_type", "Call Type", "string", "", "dimension"),
    ("voice_calls", "ended_reason", "Ended Reason", "string", "", "dimension"),
    ("voice_calls", "duration", "Duration", "number", "s", ""),
    ("voice_calls", "turn_count", "Turn Count", "number", "", ""),
    (
        "voice_calls",
        "agent_talk_percentage",
        "Agent Talk %",
        "number",
        "%",
        "",
    ),
    (
        "voice_calls",
        "avg_agent_latency_ms",
        "Average Agent Latency",
        "number",
        "ms",
        "",
    ),
    ("voice_calls", "bot_wpm", "Bot WPM", "number", "wpm", ""),
    ("voice_calls", "user_wpm", "User WPM", "number", "wpm", ""),
    (
        "voice_calls",
        "user_interruption_count",
        "User Interruptions",
        "number",
        "",
        "",
    ),
    (
        "voice_calls",
        "user_interruption_rate",
        "User Interruption Rate",
        "number",
        "/min",
        "",
    ),
    (
        "voice_calls",
        "ai_interruption_count",
        "AI Interruptions",
        "number",
        "",
        "",
    ),
    (
        "voice_calls",
        "ai_interruption_rate",
        "AI Interruption Rate",
        "number",
        "/min",
        "",
    ),
    ("voice_calls", "talk_ratio", "Talk Ratio", "number", "%", ""),
    ("voice_calls", "agent_latency", "Agent Latency", "number", "ms", ""),
    ("voice_calls", "ai_interruptions", "AI Interruptions", "number", "", ""),
    (
        "voice_calls",
        "user_interruptions",
        "User Interruptions",
        "number",
        "",
        "",
    ),
    (
        "voice_calls",
        "stop_time_after_interruption",
        "Stop Time After Interruption",
        "number",
        "ms",
        "",
    ),
    ("voice_calls", "llm_cost", "LLM Cost", "number", "cents", ""),
    ("voice_calls", "stt_cost", "STT Cost", "number", "cents", ""),
    ("voice_calls", "tts_cost", "TTS Cost", "number", "cents", ""),
    ("voice_calls", "total_cost", "Total Cost", "number", "cents", ""),
    ("voice_calls", "customer_cost", "Customer Cost", "number", "cents", ""),
    ("voice_calls", "llm_latency", "LLM Latency", "number", "ms", ""),
    ("voice_calls", "stt_latency", "STT Latency", "number", "ms", ""),
    ("voice_calls", "tts_latency", "TTS Latency", "number", "ms", ""),
    ("voice_calls", "response_time", "Response Time", "number", "ms", ""),
    (
        "prompts",
        "prompt_template_version",
        "Versions",
        "string",
        "",
        "dimension",
    ),
    ("prompts", "prompt_label_name", "Label Name", "string", "", "dimension"),
    (
        "prompts",
        "avg_input_tokens",
        "Median Input Tokens",
        "number",
        "tokens",
        "",
    ),
    (
        "prompts",
        "avg_output_tokens",
        "Median Output Tokens",
        "number",
        "tokens",
        "",
    ),
    ("prompts", "unique_traces", "No. of traces", "number", "", ""),
    ("prompts", "avg_cost", "Median Cost", "number", "$", ""),
    ("prompts", "avg_latency", "Median Latency", "number", "ms", ""),
    ("prompts", "first_used", "First Used", "datetime", "", "dimension"),
    ("prompts", "last_used", "Last Used", "datetime", "", "dimension"),
)

_SYSTEM_VALUE_ADAPTER_BY_SOURCE = {
    "spans": "system_traces",
    "voice_calls": "system_traces",
    "users": "system_sessions",
}
_CATALOG_BACKED_SYSTEM_VALUE_IDENTITIES = frozenset({("traces", "model")})
_SYSTEM_PROPERTY_IDENTITIES = frozenset(
    (
        source,
        canonical_system_attribute_name(source, raw_name),
    )
    for source, raw_name, *_ in _SYSTEM_PROPERTY_SPECS
)


def system_property_value_adapter(
    definition_source: str,
    metric_name: str,
) -> str | None:
    """Return the checked-in value adapter for one known system property.

    The helper is deliberately pure so latency-sensitive API dispatch can
    bypass an unnecessary ClickHouse catalog probe for definitions whose
    manifest already binds them to an established native reader.
    """

    source = str(definition_source or "").strip()
    name = canonical_system_attribute_name(source, metric_name)
    identity = (source, name)
    if identity not in _SYSTEM_PROPERTY_IDENTITIES:
        return None
    if identity in _CATALOG_BACKED_SYSTEM_VALUE_IDENTITIES:
        return "span_attribute_value"
    return _SYSTEM_VALUE_ADAPTER_BY_SOURCE.get(source, f"system_{source}")


class PropertySourceError(RuntimeError):
    """A source could not be read completely within the fixed contract."""


def canonical_system_definitions() -> tuple[PropertyDefinition, ...]:
    """Return the complete, DB-independent system-definition manifest."""

    source_rank = {
        "traces": 0,
        "spans": 1,
        "sessions": 2,
        "users": 3,
        "voice_calls": 4,
        "prompts": 5,
        "all": 6,
        "datasets": 7,
        "simulation": 8,
    }
    definitions: list[PropertyDefinition] = []
    # Older UI surfaces spell a few native ids with ``_id``.  Collapse only
    # those declared aliases before projection so pagination cannot expose two
    # logical properties for the same fact.
    seen_system_identities: set[tuple[str, str]] = set()
    for (
        source,
        raw_name,
        display_name,
        value_type,
        unit,
        explicit_role,
    ) in _SYSTEM_PROPERTY_SPECS:
        name = canonical_system_attribute_name(source, raw_name)
        identity = (source, name)
        if identity in seen_system_identities:
            continue
        seen_system_identities.add(identity)
        role = (
            PropertyRole(explicit_role)
            if explicit_role
            else (
                PropertyRole.DIMENSION
                if value_type == "string"
                else PropertyRole.METRIC
            )
        )
        details: dict[str, Any] = {}
        if unit:
            details["unit"] = unit
        if source in {"datasets", "simulation"} and value_type == "string":
            details["allowed_aggregations"] = ("count", "count_distinct")
        resolved_value_adapter = system_property_value_adapter(source, name)
        if resolved_value_adapter is None:
            raise PropertySourceError("system manifest value adapter drifted")
        if resolved_value_adapter == "span_attribute_value":
            # The collector observes this system key; this manifest does not
            # claim its historical types are exact current-span membership.
            details.update(
                {
                    "allowed_aggregations": ("count", "count_distinct"),
                    "attribute_types": ("string",),
                    "attribute_types_exact": False,
                    "data_type": "string",
                }
            )
        definitions.append(
            PropertyDefinition(
                property_kind=PropertyKind.SYSTEM_ATTRIBUTE,
                source_key=name,
                category=PropertyCategory.SYSTEM_METRIC,
                category_rank=0,
                source_rank=source_rank[source],
                definition_source="system_manifest",
                primary_source=source,
                source_tokens=("system", source, name),
                value_adapter=resolved_value_adapter,
                name=name,
                display_name=display_name,
                value_type=value_type,
                output_type=value_type,
                role=role,
                details=details,
            )
        )
    result = tuple(sorted(definitions, key=lambda item: item.property_id))
    return result


def _eval_definition(
    row: Mapping[str, Any],
    *,
    kind: PropertyKind,
    primary_source: str = "all",
) -> PropertyDefinition:
    template_config = (
        row.get("config")
        or row.get("eval_template__config")
        or row.get("template__config")
        or {}
    )
    choices = (
        row.get("choices")
        or row.get("eval_template__choices")
        or row.get("template__choices")
        or []
    )
    output_type = _eval_output_type(template_config)
    name = str(row["id"])
    display_name = str(
        row.get("name")
        or row.get("eval_template__name")
        or row.get("template__name")
        or name
    )
    details: dict[str, Any] = {}
    normalized_choices = (
        tuple(choice for choice in choices if choice is not None)
        if output_type in {"CHOICE", "CHOICES"}
        else ()
    )
    if output_type == "PASS_FAIL":
        normalized_choices = ("Passed", "Failed")
    if normalized_choices:
        details["choices"] = normalized_choices
    template_id = row.get("eval_template_id") or row.get("template_id")
    if template_id:
        details["eval_template_id"] = str(template_id)
    return PropertyDefinition(
        property_kind=kind,
        source_key=name,
        category=PropertyCategory.EVAL_METRIC,
        category_rank=1,
        source_rank={"all": 0, "simulation": 1, "datasets": 2, "prompts": 3}[
            primary_source
        ],
        definition_source=(
            "eval_template" if kind is PropertyKind.EVAL_TEMPLATE else "eval_config"
        ),
        primary_source=primary_source,
        source_tokens=("eval", primary_source),
        value_adapter=(
            "eval_template" if kind is PropertyKind.EVAL_TEMPLATE else "eval_config"
        ),
        name=name,
        display_name=display_name,
        value_type="number",
        output_type=output_type,
        role=PropertyRole.METRIC,
        details=details,
    )


def _relational_display_name(
    row: Mapping[str, Any],
    *,
    fallback_prefix: str,
) -> str:
    """Keep malformed legacy names from aborting a current definition page."""

    source_id = str(row["id"])
    raw_name = row.get("name")
    display_name = str(raw_name) if raw_name is not None else ""
    if display_name.strip():
        return display_name
    return f"{fallback_prefix} {source_id}"


def _annotation_definition(row: Mapping[str, Any]) -> PropertyDefinition:
    label_type = str(row.get("type") or "numeric")
    settings = row.get("settings") if isinstance(row.get("settings"), Mapping) else {}
    details: dict[str, Any] = {"data_type": label_type}
    choice_options = ()
    if label_type == "categorical":
        choice_options = configured_value_options(settings.get("options"))
    elif label_type == "star":
        choice_options = tuple(
            {"value": str(i), "label": f"{i} star{'s' if i != 1 else ''}"}
            for i in range(1, settings.get("no_of_stars", 5) + 1)
        )
    elif label_type == "thumbs_up_down":
        choice_options = (
            {"value": "thumbs_up", "label": "Thumbs Up"},
            {"value": "thumbs_down", "label": "Thumbs Down"},
        )
    if choice_options:
        details["choices"] = tuple(option["label"] for option in choice_options)
        details["choice_options"] = choice_options
    return PropertyDefinition(
        property_kind=PropertyKind.ANNOTATION,
        source_key=str(row["id"]),
        category=PropertyCategory.ANNOTATION_METRIC,
        category_rank=2,
        source_rank=0,
        definition_source="annotation_label",
        primary_source="both",
        source_tokens=("annotation", "datasets", "traces"),
        value_adapter="annotation_label",
        name=str(row["id"]),
        display_name=_relational_display_name(
            row,
            fallback_prefix="Annotation",
        ),
        value_type=label_type,
        output_type=label_type,
        role=PropertyRole.METRIC,
        details=details,
    )


def _dataset_column_definition(row: Mapping[str, Any]) -> PropertyDefinition:
    data_type = str(row["data_type"])
    value_type, role = _dataset_type_contract(data_type)
    return PropertyDefinition(
        property_kind=PropertyKind.DATASET_COLUMN,
        source_key=str(row["id"]),
        category=PropertyCategory.CUSTOM_COLUMN,
        category_rank=4,
        source_rank=0,
        definition_source="dataset_column",
        primary_source="datasets",
        source_tokens=("dataset", "column", data_type),
        value_adapter="dataset_column",
        name=str(row["id"]),
        display_name=_relational_display_name(
            row,
            fallback_prefix="Dataset column",
        ),
        value_type=value_type,
        output_type=data_type,
        role=role,
        details={"data_type": data_type},
    )


def _dataset_type_contract(data_type: str) -> tuple[str, PropertyRole]:
    if data_type in {"float", "integer"}:
        return "number", PropertyRole.METRIC
    if data_type == "boolean":
        return "boolean", PropertyRole.METRIC
    if data_type == "datetime":
        return "datetime", PropertyRole.DIMENSION
    if data_type in {"array", "images"}:
        return "array", PropertyRole.DIMENSION
    if data_type == "json":
        return "json", PropertyRole.DIMENSION
    if data_type in {
        "text",
        "image",
        "audio",
        "document",
        "others",
        "persona",
    }:
        return "text", PropertyRole.DIMENSION
    raise PropertySourceError(f"unsupported dataset data_type: {data_type}")


def resolve_span_attribute_type(observed_types: Sequence[str]) -> str:
    """Return one associative/commutative type union for a workspace key."""

    normalized = frozenset(str(value).strip().lower() for value in observed_types)
    allowed = {"string", "number", "boolean", "array", "map", "json"}
    if not normalized or not normalized <= allowed:
        raise PropertySourceError("span attribute group has an unsupported type")
    if len(normalized) == 1:
        return next(iter(normalized))
    return "json"


_SPAN_TYPE_AGGREGATIONS = {
    "string": frozenset({"count", "count_distinct"}),
    "number": frozenset({"avg", "count", "count_distinct", "max", "min", "sum"}),
    "boolean": frozenset({"count", "count_distinct"}),
    "array": frozenset({"count", "count_distinct"}),
    "map": frozenset({"count", "count_distinct"}),
    "json": frozenset({"count", "count_distinct"}),
}


def _span_attribute_aggregations(observed_types: tuple[str, ...]) -> tuple[str, ...]:
    intersection = set(_SPAN_TYPE_AGGREGATIONS[observed_types[0]])
    for observed_type in observed_types[1:]:
        intersection.intersection_update(_SPAN_TYPE_AGGREGATIONS[observed_type])
    return tuple(sorted(intersection))


def _eval_output_type(config: object) -> str:
    if not isinstance(config, Mapping):
        return "SCORE"
    output = str(config.get("output") or "").upper().replace("/", "_").replace(" ", "_")
    return output if output in {"PASS_FAIL", "CHOICE", "CHOICES", "SCORE"} else "SCORE"


def definition_order(definition):
    return (
        definition.category_rank,
        definition.source_rank,
        definition.primary_source.casefold(),
        definition.name.casefold(),
        definition.name,
        definition.property_id,
    )


def definition_metric(definition):
    metric = dict(definition.details)
    metric.update(
        name=definition.name,
        property_id=definition.property_id,
        property_kind=str(definition.property_kind),
        display_name=definition.display_name,
        category=str(definition.category),
        source=definition.primary_source,
        sources=list(definition.source_tokens),
        type=definition.value_type,
        output_type=definition.output_type,
        role=str(definition.role),
    )
    return metric


def source_matches(source, primary_source, tokens):
    if (
        not source
        or primary_source in {"all", "both"}
        or source == primary_source
        or source in tokens
    ):
        return True
    return source in {"spans", "voice_calls", "prompts"} and primary_source == "traces"


def definition_matches(definition, query):
    return (
        (not query.get("category") or query["category"] == definition.category)
        and (
            not query.get("property_kind")
            or query["property_kind"] == definition.property_kind
        )
        and (not query.get("role") or query["role"] == definition.role)
        and source_matches(
            query.get("source", ""), definition.primary_source, definition.source_tokens
        )
        and (
            not query.get("search")
            or query["search"]
            in combine_search_text(
                definition.name,
                definition.display_name,
                definition.primary_source,
                definition.definition_source,
                source_tokens=definition.source_tokens,
            )
        )
    )


class CurrentDefinitionSource:
    """Bounded current metadata reads. No projection, watermark, or build transaction."""

    def __init__(self, deadline):
        self.deadline = deadline

    def _read(self, read):
        from django.db import connection, transaction

        from tracer.services.postgres_read_policy import application_postgres_reads

        with application_postgres_reads(
            connection=connection,
            atomic=transaction.atomic,
            read_only=True,
            check_request=lambda: self.deadline.remaining_ms(floor_ms=1),
        ):
            return read()

    def _families(self, scope, query):
        from django.db.models import Exists, OuterRef, Q

        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.develop_dataset import Column
        from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
        from model_hub.models.run_prompt import PromptEvalConfig
        from model_hub.models.score import Score
        from simulate.models import SimulateEvalConfig
        from tracer.models.custom_eval_config import CustomEvalConfig

        org, workspace = scope["organization_id"], scope["workspace_id"]
        projects = scope["project_ids"]
        template_tenant = (
            Q(eval_template__organization_id__isnull=True)
            | Q(eval_template__organization_id=org)
        ) & (
            Q(eval_template__workspace_id=workspace)
            | Q(eval_template__workspace_id__isnull=True)
        )
        configs = CustomEvalConfig.no_workspace_objects.filter(
            template_tenant,
            project_id__in=projects,
            project__organization_id=org,
            project__workspace_id=workspace,
            project__deleted=False,
            eval_template__deleted=False,
        )
        config_fields = (
            "id",
            "name",
            "eval_template_id",
            "eval_template__name",
            "eval_template__config",
            "eval_template__choices",
        )
        dataset_configs = UserEvalMetric.no_workspace_objects.filter(
            Q(organization_id=org) | Q(organization_id__isnull=True),
            Q(workspace_id=workspace) | Q(workspace_id__isnull=True),
            Q(template__organization_id=org)
            | Q(template__organization_id__isnull=True),
            Q(template__workspace_id=workspace)
            | Q(template__workspace_id__isnull=True),
            dataset__organization_id=org,
            dataset__workspace_id=workspace,
            dataset__deleted=False,
            template__deleted=False,
            column_deleted=False,
        )
        if scope.get("dataset_id"):
            dataset_configs = dataset_configs.filter(dataset_id=scope["dataset_id"])
        prompt_configs = PromptEvalConfig.no_workspace_objects.filter(
            template_tenant,
            prompt_template__organization_id=org,
            prompt_template__workspace_id=workspace,
            prompt_template__deleted=False,
            eval_template__deleted=False,
        )
        source = query.get("source", "")
        if query.get("per_eval_config") or query.get("property_kind") == "eval_config":
            if source not in {"datasets", "dataset_column", "prompts", "simulation"}:
                yield (
                    1,
                    0,
                    "all",
                    "eval_metric",
                    "eval_config",
                    configs,
                    config_fields,
                    lambda row: _eval_definition(row, kind=PropertyKind.EVAL_CONFIG),
                )
            if source in {"", "datasets"}:
                yield (
                    1,
                    2,
                    "datasets",
                    "eval_metric",
                    "eval_config",
                    dataset_configs,
                    (
                        "id",
                        "name",
                        "template_id",
                        "template__name",
                        "template__config",
                        "template__choices",
                    ),
                    lambda row: _eval_definition(
                        row, kind=PropertyKind.EVAL_CONFIG, primary_source="datasets"
                    ),
                )
            if source in {"", "prompts"}:
                yield (
                    1,
                    3,
                    "prompts",
                    "eval_metric",
                    "eval_config",
                    prompt_configs,
                    config_fields,
                    lambda row: _eval_definition(
                        row, kind=PropertyKind.EVAL_CONFIG, primary_source="prompts"
                    ),
                )
        else:
            relationships = configs.filter(eval_template_id=OuterRef("pk"))
            templates = EvalTemplate.no_workspace_objects.filter(
                Q(organization_id=org) | Q(organization_id__isnull=True),
            ).filter(Q(workspace_id=workspace) | Q(workspace_id__isnull=True))
            dataset_relationships = dataset_configs.filter(template_id=OuterRef("pk"))
            prompt_relationships = prompt_configs.filter(
                eval_template_id=OuterRef("pk")
            )
            if source == "datasets" or scope.get("dataset_id"):
                templates = templates.filter(Exists(dataset_relationships))
            elif source == "prompts":
                templates = templates.filter(Exists(prompt_relationships))
            elif scope.get("workspace_scope"):
                templates = templates.filter(
                    Q(organization_id=org)
                    | Exists(relationships)
                    | Exists(dataset_relationships)
                    | Exists(prompt_relationships)
                )
            else:
                templates = templates.filter(Exists(relationships))
            yield (
                1,
                0,
                "all",
                "eval_metric",
                "eval_template",
                templates,
                ("id", "name", "config", "choices"),
                lambda row: _eval_definition(row, kind=PropertyKind.EVAL_TEMPLATE),
            )
        agent_id = scope.get("agent_definition_id")
        if agent_id:
            simulations = SimulateEvalConfig.no_workspace_objects.filter(
                template_tenant,
                run_test__organization_id=org,
                run_test__workspace_id=workspace,
                run_test__deleted=False,
                run_test__agent_definition_id=agent_id,
                run_test__agent_definition__organization_id=org,
                run_test__agent_definition__workspace_id=workspace,
                run_test__agent_definition__deleted=False,
                eval_template__deleted=False,
            )
            yield (
                1,
                1,
                "simulation",
                "eval_metric",
                "eval_config",
                simulations,
                config_fields,
                lambda row: _eval_definition(
                    row, kind=PropertyKind.EVAL_CONFIG, primary_source="simulation"
                ),
            )
        scores = Score.no_workspace_objects.filter(
            Q(trace_id__isnull=False) | Q(observation_span_id__isnull=False),
            organization_id=org,
            workspace_id=workspace,
            tracer_project_id__in=projects,
            label_id=OuterRef("pk"),
        )
        live_label_project = Q(
            project__deleted=False,
            project__organization_id=org,
            project__workspace_id=workspace,
        )
        dataset_labels = source == "datasets" or bool(scope.get("dataset_id"))
        if not dataset_labels:
            live_label_project &= Q(project_id__in=projects)
        labels = AnnotationsLabels.no_workspace_objects.filter(
            Q(workspace_id=workspace) | Q(workspace_id__isnull=True),
            Q(project_id__isnull=True) | live_label_project,
            organization_id=org,
        )
        if not dataset_labels and not scope.get("workspace_scope"):
            labels = labels.filter(Q(project_id__in=projects) | Exists(scores))
        yield (
            2,
            0,
            "both",
            "annotation_metric",
            "annotation",
            labels,
            ("id", "name", "type", "settings"),
            _annotation_definition,
        )
        columns = Column.no_workspace_objects.filter(
            dataset__organization_id=org,
            dataset__workspace_id=workspace,
            dataset__deleted=False,
        )
        if scope.get("dataset_id"):
            columns = columns.filter(dataset_id=scope["dataset_id"])
        yield (
            4,
            0,
            "datasets",
            "custom_column",
            "dataset_column",
            columns,
            ("id", "name", "data_type"),
            _dataset_column_definition,
        )

    def read_page(self, *, scope, query, after, limit):
        """Each requested family returns at most limit rows, ordered before slicing."""
        definitions = []
        if not query.get("category") or query["category"] == "system_metric":
            definitions.extend(
                definition
                for definition in canonical_system_definitions()
                if definition_matches(definition, query)
                and (after is None or definition_order(definition) > after)
            )
        for (
            rank,
            source_rank,
            primary,
            category,
            kind,
            queryset,
            fields,
            convert,
        ) in self._families(scope, query):
            prefix = (rank, source_rank, primary.casefold())
            if (
                (query.get("category") and query["category"] != category)
                or (query.get("property_kind") and query["property_kind"] != kind)
                or not source_matches(query.get("source", ""), primary, ())
                or (after is not None and prefix < after[:3])
            ):
                continue
            role = query.get("role")
            if kind == "dataset_column" and role:
                numeric = {"integer", "float", "boolean"}
                queryset = (
                    queryset.filter(data_type__in=numeric)
                    if role == "metric"
                    else queryset.exclude(data_type__in=numeric)
                )
            elif role == "dimension":
                continue
            if after is not None and prefix == after[:3]:
                queryset = queryset.filter(id__gt=after[4])
            # Use the native PostgreSQL search contract. Ordering is UUID based,
            # never a page-local re-sort of locale-dependent display names.
            search = query.get("search", "")
            if search and not any(search in token for token in (primary, kind)):
                from django.db.models import Q

                condition = Q(name__icontains=search)
                if "eval_template__name" in fields:
                    condition |= Q(eval_template__name__icontains=search)
                if "template__name" in fields:
                    condition |= Q(template__name__icontains=search)
                try:
                    condition |= Q(id=UUID(search))
                except ValueError:
                    pass
                queryset = queryset.filter(condition)
            rows = self._read(
                lambda queryset=queryset, fields=fields: list(
                    queryset.order_by("id").values(*fields)[:limit]
                )
            )
            definitions.extend(convert(row) for row in rows)
        return tuple(sorted(definitions, key=definition_order)[:limit])

    def resolve(self, *, scope, property_id, source=""):
        from tracer.utils.property_registry import parse_property_registry_id

        decoded = parse_property_registry_id(property_id)
        if decoded["property_kind"] == "system_attribute":
            return next(
                (
                    d
                    for d in canonical_system_definitions()
                    if d.property_id == property_id
                ),
                None,
            )
        query = {
            "property_kind": decoded["property_kind"],
            "source": source,
            "per_eval_config": decoded["property_kind"] == "eval_config",
        }
        for _, _, primary, _, kind, queryset, fields, convert in self._families(
            scope, query
        ):
            if kind != decoded["property_kind"] or not source_matches(
                source, primary, ()
            ):
                continue
            row = self._read(
                lambda queryset=queryset, fields=fields: (
                    queryset.filter(id=decoded["metric_name"]).values(*fields).first()
                )
            )
            if row is not None:
                return convert(row)
        return None
