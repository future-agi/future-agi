"""Bounded, read-only definition sources for the unified property catalog.

PostgreSQL-backed adapters run their complete page loop inside one read-only,
repeatable-read transaction. They use the stable ``(updated_at, id)`` keyset,
never OFFSET, and enforce one shared PostgreSQL wall: 8.5 seconds for normal
runs, 120 seconds for an explicit scheduled reconcile, or 540 seconds for an
explicit initial backfill. All modes retain the same row and byte caps.
Non-PostgreSQL adapters retain those bounds and may use the caller's longer
overall source wall when an explicitly bounded rollout shares that deadline
with every other operation.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from tracer.services.configured_value_options import configured_value_options
from tracer.utils.property_registry import canonical_system_attribute_name

from .codec import (
    ZERO_UUID,
    canonical_json,
    framed_sha256,
    require_sha256,
)
from .models import (
    PropertyCategory,
    PropertyDefinition,
    PropertyKind,
    PropertyRole,
    SourceAdapter,
    VisibilityBinding,
    VisibilityScope,
    canonicalize_definition,
)
from .projection import PostgresReadBudget, PostgresSnapshotContext
from .runtime_limits import RUNTIME_LIMITS

DEFAULT_MAX_PAGE_BYTES = RUNTIME_LIMITS.source_max_page_bytes
DEFAULT_MAX_TOTAL_BYTES = RUNTIME_LIMITS.source_max_total_bytes
MAX_TOTAL_BYTES = RUNTIME_LIMITS.source_max_total_bytes
MAX_SOURCE_ADAPTER_WALL_SECONDS = (
    RUNTIME_LIMITS.initial_backfill_source_adapter_wall_seconds
)
PROPERTY_SOURCE_DB_ALIAS = "default"
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _lifecycle_timestamp_fields(*prefixes: str) -> tuple[str, ...]:
    """Return update and deletion clocks for rows and their dependencies."""

    return tuple(
        f"{prefix}__{field}" if prefix else field
        for prefix in prefixes
        for field in ("updated_at", "deleted_at")
    )


# One checked-in manifest replaces request-time assembly of the finite system
# family.  The tuple fields are source, name, display name, value type, unit,
# and optional explicit role.  ``agent_talk_percentage`` is included even
# though the legacy endpoint exposed it only after a simulator-project lookup.
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
SYSTEM_MANIFEST_EXPECTED_COUNT = 180
SYSTEM_MANIFEST_EXPECTED_SHA256 = (
    "04eb2a4874197be84cbc1347f52181bdf9d453717889af76c1bdc0d323707054"
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


class PropertySourceDeadlineExceeded(PropertySourceError):
    """The shared adapter wall expired before a complete page was returned."""


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
            # The collector currently materializes this system key in the
            # revision-pinned native value catalog. Other system definitions
            # retain their existing native adapters.
            resolved_value_adapter = "span_attribute_value"
            details.update(
                {
                    "allowed_aggregations": ("count", "count_distinct"),
                    "attribute_types": ("string",),
                    "attribute_types_exact": True,
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
    if len(result) != SYSTEM_MANIFEST_EXPECTED_COUNT:
        raise PropertySourceError("system manifest count drifted")
    if _system_manifest_digest(result) != SYSTEM_MANIFEST_EXPECTED_SHA256:
        raise PropertySourceError("system manifest digest drifted")
    return result


def system_manifest_sha256() -> str:
    return _system_manifest_digest(canonical_system_definitions())


def _system_manifest_digest(
    definitions: tuple[PropertyDefinition, ...],
) -> str:
    return framed_sha256(
        "futureagi.property-catalog.system-manifest.v1",
        *(
            canonicalize_definition(definition).definition_sha256
            for definition in definitions
        ),
    )


@dataclass(frozen=True, slots=True)
class SourceReadBudget:
    postgres: PostgresReadBudget = PostgresReadBudget()
    max_page_bytes: int = DEFAULT_MAX_PAGE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    adapter_wall_timeout_seconds: float = RUNTIME_LIMITS.source_adapter_wall_seconds
    shared_deadline: Any | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.max_page_bytes <= MAX_TOTAL_BYTES:
            raise ValueError(f"max_page_bytes must be between 1 and {MAX_TOTAL_BYTES}")
        if not self.max_page_bytes <= self.max_total_bytes <= MAX_TOTAL_BYTES:
            raise ValueError(
                f"max_total_bytes must be between max_page_bytes and {MAX_TOTAL_BYTES}"
            )
        if (
            type(self.adapter_wall_timeout_seconds) not in {int, float}
            or isinstance(self.adapter_wall_timeout_seconds, bool)
            or not 0
            < self.adapter_wall_timeout_seconds
            <= MAX_SOURCE_ADAPTER_WALL_SECONDS
        ):
            raise ValueError(
                "adapter_wall_timeout_seconds must be in (0, "
                f"{MAX_SOURCE_ADAPTER_WALL_SECONDS}]"
            )
        if self.shared_deadline is not None and not callable(
            getattr(self.shared_deadline, "remaining_ms", None)
        ):
            raise TypeError("shared_deadline must expose remaining_ms")


@dataclass(frozen=True, slots=True, order=True)
class SourceKeysetCursor:
    updated_at: datetime
    source_entity_id: str

    def __post_init__(self) -> None:
        _require_utc(self.updated_at, "updated_at")
        if not self.source_entity_id:
            raise ValueError("source_entity_id must not be empty")

    def encode(self) -> str:
        payload = canonical_json(
            {
                "id": self.source_entity_id,
                "updated_at": self.updated_at.isoformat(timespec="microseconds"),
                "v": 1,
            }
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    @classmethod
    def decode(cls, value: str | None) -> SourceKeysetCursor | None:
        if value is None:
            return None
        if not value or len(value) > 4096:
            raise PropertySourceError("invalid source keyset cursor")
        try:
            padding = "=" * (-len(value) % 4)
            payload = base64.urlsafe_b64decode(value + padding)
            decoded = json.loads(payload.decode("utf-8"))
            if set(decoded) != {"id", "updated_at", "v"} or decoded["v"] != 1:
                raise ValueError
            updated_at = datetime.fromisoformat(decoded["updated_at"])
            return cls(updated_at, str(decoded["id"]))
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise PropertySourceError("invalid source keyset cursor") from exc


@dataclass(frozen=True, slots=True)
class SourceDefinitionRecord:
    source_adapter: SourceAdapter
    source_entity_id: str
    source_updated_at: datetime
    definition: PropertyDefinition
    visibilities: tuple[VisibilityBinding, ...]
    source_fingerprint: str
    is_deleted: bool = False
    deleted_at: datetime | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_adapter, SourceAdapter):
            raise TypeError("source_adapter must be a SourceAdapter")
        if not self.source_entity_id:
            raise ValueError("source_entity_id must not be empty")
        _require_utc(self.source_updated_at, "source_updated_at")
        require_sha256(self.source_fingerprint, field="source_fingerprint")
        # A returned source entity carries its *complete* current visibility
        # set.  Relationship-owned definitions therefore need to represent an
        # empty set after their final relationship is soft-deleted: the
        # reconciler uses that touched entity to tombstone its previous
        # bindings during an incremental pass.
        visibility_keys = {
            (visibility.scope, visibility.visibility_id)
            for visibility in self.visibilities
        }
        if len(visibility_keys) != len(self.visibilities):
            raise ValueError("source definition contains duplicate visibilities")
        if self.is_deleted != (self.deleted_at is not None):
            raise ValueError("deleted_at must be present exactly for deleted records")
        if self.deleted_at is not None:
            _require_utc(self.deleted_at, "deleted_at")
        if (self.first_seen is None) != (self.last_seen is None):
            raise ValueError("first_seen and last_seen must both be present or absent")
        if self.first_seen is not None and self.last_seen is not None:
            _require_utc(self.first_seen, "first_seen")
            _require_utc(self.last_seen, "last_seen")
            if self.first_seen > self.last_seen:
                raise ValueError("first_seen must not follow last_seen")

    @property
    def cursor(self) -> SourceKeysetCursor:
        return SourceKeysetCursor(self.source_updated_at, self.source_entity_id)

    @property
    def encoded_bytes(self) -> int:
        canonical = canonicalize_definition(self.definition)
        scope_bytes = sum(
            len(visibility.scope.encode("utf-8"))
            + len(visibility.visibility_id.encode("utf-8"))
            for visibility in self.visibilities
        )
        return (
            len(self.source_entity_id.encode("utf-8"))
            + len(canonical.definition_json.encode("utf-8"))
            + scope_bytes
            + 256
        )


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source_adapter: SourceAdapter
    records: tuple[SourceDefinitionRecord, ...]
    next_cursor: str | None
    terminal: bool
    source_count: int
    source_bytes: int
    source_digest: str
    page_count: int

    def __post_init__(self) -> None:
        if self.terminal != (self.next_cursor is None):
            raise ValueError("terminal snapshots cannot expose a continuation")
        if min(self.source_count, self.source_bytes, self.page_count) < 0:
            raise ValueError("snapshot counters must be non-negative")
        if self.source_count != len(self.records):
            raise ValueError("source_count does not match records")
        require_sha256(self.source_digest, field="source_digest")


class SourcePageLoader(Protocol):
    def __call__(
        self,
        *,
        context: PostgresSnapshotContext,
        cursor: SourceKeysetCursor | None,
        limit: int,
    ) -> Sequence[SourceDefinitionRecord]: ...


@dataclass(frozen=True, slots=True)
class SpanAttributeKeyGroup:
    """One complete workspace/key union read from the revision-pinned value table."""

    attribute_key: str
    observed_types: tuple[str, ...]
    project_ids: tuple[str, ...]
    catalog_revision: int
    revision_fenced_at: datetime
    first_seen: datetime
    last_seen: datetime

    def __post_init__(self) -> None:
        if not self.attribute_key:
            raise ValueError("attribute_key must not be empty")
        if not self.observed_types:
            raise ValueError("observed_types must not be empty")
        if not self.project_ids:
            raise ValueError("project_ids must not be empty")
        if type(self.catalog_revision) is not int or not (
            1 <= self.catalog_revision < (1 << 64)
        ):
            raise ValueError("catalog_revision must be a positive UInt64")
        object.__setattr__(
            self, "observed_types", tuple(sorted(set(self.observed_types)))
        )
        object.__setattr__(self, "project_ids", tuple(sorted(set(self.project_ids))))
        for field_name in ("revision_fenced_at", "first_seen", "last_seen"):
            _require_utc(getattr(self, field_name), field_name)
        if self.first_seen > self.last_seen:
            raise ValueError("first_seen must not follow last_seen")


class SpanAttributeGroupPageLoader(Protocol):
    """Return complete key groups ordered by ``(updated_at, attribute_key)``."""

    def __call__(
        self,
        *,
        context: PostgresSnapshotContext,
        cursor: SourceKeysetCursor | None,
        limit: int,
    ) -> Sequence[SpanAttributeKeyGroup]: ...


class DefinitionSourceAdapter(Protocol):
    source_adapter: SourceAdapter
    read_only: bool
    isolation_level: str
    requires_postgres_snapshot: bool

    def read_snapshot(
        self,
        *,
        context: PostgresSnapshotContext,
        budget: SourceReadBudget,
        cursor: str | None = None,
    ) -> SourceSnapshot: ...


@dataclass(frozen=True, slots=True)
class _PostgresRevisionSnapshotAuthority:
    context: PostgresSnapshotContext
    budget: PostgresReadBudget
    deadline: float
    monotonic: Callable[[], float]

    def require_scope(
        self,
        *,
        context: PostgresSnapshotContext,
        budget: PostgresReadBudget,
    ) -> None:
        if context != self.context:
            raise PropertySourceError(
                "PostgreSQL property source changed scope inside one revision snapshot"
            )
        if budget != self.budget:
            raise PropertySourceError(
                "PostgreSQL property source changed budget inside one revision snapshot"
            )
        _require_time(self.deadline, self.monotonic)


_ACTIVE_POSTGRES_REVISION_SNAPSHOT: ContextVar[
    _PostgresRevisionSnapshotAuthority | None
] = ContextVar("active_property_catalog_postgres_revision_snapshot", default=None)


@contextmanager
def postgres_revision_snapshot(
    *,
    context: PostgresSnapshotContext,
    budget: PostgresReadBudget,
    monotonic: Callable[[], float] = time.monotonic,
) -> Iterator[None]:
    """Bind one authoritative PG snapshot to an entire catalog revision.

    Callers must wrap the complete loop across every PostgreSQL adapter and
    every resumed keyset segment.  Default relational adapters fail closed
    outside this scope, preventing a revision from silently combining rows
    observed in independently opened transactions.
    """

    active = _ACTIVE_POSTGRES_REVISION_SNAPSHOT.get()
    if active is not None:
        active.require_scope(context=context, budget=budget)
        yield
        return

    deadline = monotonic() + budget.wall_timeout_seconds
    authority = _PostgresRevisionSnapshotAuthority(
        context=context,
        budget=budget,
        deadline=deadline,
        monotonic=monotonic,
    )
    with _django_repeatable_read_snapshot(
        deadline=deadline,
        statement_timeout_ms=budget.statement_timeout_ms,
        monotonic=monotonic,
    ):
        token = _ACTIVE_POSTGRES_REVISION_SNAPSHOT.set(authority)
        try:
            yield
        finally:
            _ACTIVE_POSTGRES_REVISION_SNAPSHOT.reset(token)


class _BoundedSourceAdapter:
    read_only = True
    isolation_level = "repeatable_read"

    def __init__(
        self,
        *,
        source_adapter: SourceAdapter,
        page_loader: SourcePageLoader,
        postgres_snapshot: bool,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source_adapter = source_adapter
        self._page_loader = page_loader
        # Public coordinator contract: injected loaders remain usable as pure
        # test doubles, while every default relational loader must be enclosed
        # by one revision-wide PostgreSQL snapshot.
        self.requires_postgres_snapshot = postgres_snapshot
        self._monotonic = monotonic

    def read_snapshot(
        self,
        *,
        context: PostgresSnapshotContext,
        budget: SourceReadBudget,
        cursor: str | None = None,
    ) -> SourceSnapshot:
        active_snapshot = _ACTIVE_POSTGRES_REVISION_SNAPSHOT.get()
        if self.requires_postgres_snapshot:
            if active_snapshot is None:
                raise PropertySourceError(
                    "PostgreSQL property adapters require one revision snapshot session"
                )
            active_snapshot.require_scope(
                context=context,
                budget=budget.postgres,
            )
            monotonic = active_snapshot.monotonic
            deadline = active_snapshot.deadline
        else:
            monotonic = self._monotonic
            wall_ms = max(1, int(budget.adapter_wall_timeout_seconds * 1_000))
            if budget.shared_deadline is not None:
                try:
                    wall_ms = budget.shared_deadline.remaining_ms(cap_ms=wall_ms)
                except Exception as exc:
                    raise PropertySourceDeadlineExceeded(
                        "property source deadline exceeded"
                    ) from exc
            deadline = monotonic() + wall_ms / 1_000
        decoded_cursor = SourceKeysetCursor.decode(cursor)
        records: list[SourceDefinitionRecord] = []
        source_bytes = 0
        source_digest = _EMPTY_SHA256
        page_count = 0
        terminal = False

        while not terminal and len(records) < budget.postgres.max_total_rows:
            _require_time(deadline, monotonic)
            remaining_rows = budget.postgres.max_total_rows - len(records)
            page_limit = min(budget.postgres.max_rows_per_page, remaining_rows)
            loaded = tuple(
                self._page_loader(
                    context=context,
                    cursor=decoded_cursor,
                    limit=page_limit + 1,
                )
            )
            _require_time(deadline, monotonic)
            _validate_page(
                loaded,
                adapter=self.source_adapter,
                cursor=decoded_cursor,
                max_rows=page_limit + 1,
            )
            page_count += 1
            terminal = len(loaded) <= page_limit
            page = loaded[:page_limit]
            page_bytes = sum(record.encoded_bytes for record in page)
            if page_bytes > budget.max_page_bytes:
                raise PropertySourceError("source page exceeded max_page_bytes")
            if source_bytes + page_bytes > budget.max_total_bytes:
                raise PropertySourceError("source snapshot exceeded max_total_bytes")
            for record in page:
                record_bytes = record.encoded_bytes
                records.append(record)
                source_bytes += record_bytes
                source_digest = framed_sha256(
                    "futureagi.property-catalog.source-snapshot.v1",
                    source_digest,
                    record.source_fingerprint,
                )
            if page:
                decoded_cursor = page[-1].cursor
            if not page:
                terminal = True

        next_cursor = None if terminal else decoded_cursor.encode()
        return SourceSnapshot(
            source_adapter=self.source_adapter,
            records=tuple(records),
            next_cursor=next_cursor,
            terminal=terminal,
            source_count=len(records),
            source_bytes=source_bytes,
            source_digest=source_digest,
            page_count=page_count,
        )


class SystemManifestAdapter(_BoundedSourceAdapter):
    """Immutable, workspace-projected system definitions."""

    def __init__(
        self,
        definitions: Sequence[PropertyDefinition] | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if definitions is None:
            definitions = canonical_system_definitions()
        ordered = tuple(sorted(definitions, key=lambda item: item.property_id))

        def load_page(
            *,
            context: PostgresSnapshotContext,
            cursor: SourceKeysetCursor | None,
            limit: int,
        ) -> Sequence[SourceDefinitionRecord]:
            records = tuple(
                _make_source_record(
                    source_adapter=SourceAdapter.SYSTEM_MANIFEST,
                    source_entity_id=definition.property_id,
                    source_updated_at=context.snapshot_cutoff,
                    definition=definition,
                    visibilities=(
                        VisibilityBinding(VisibilityScope.ALWAYS, ZERO_UUID),
                    ),
                )
                for definition in ordered
            )
            return _slice_records(records, cursor=cursor, limit=limit)

        super().__init__(
            source_adapter=SourceAdapter.SYSTEM_MANIFEST,
            page_loader=load_page,
            postgres_snapshot=False,
            monotonic=monotonic,
        )


class EvalTemplateSourceAdapter(_BoundedSourceAdapter):
    def __init__(self, *, page_loader: SourcePageLoader | None = None) -> None:
        super().__init__(
            source_adapter=SourceAdapter.EVAL_TEMPLATE,
            page_loader=page_loader or _load_eval_template_page,
            postgres_snapshot=page_loader is None,
        )


class EvalConfigSourceAdapter(_BoundedSourceAdapter):
    def __init__(self, *, page_loader: SourcePageLoader | None = None) -> None:
        super().__init__(
            source_adapter=SourceAdapter.EVAL_CONFIG,
            page_loader=page_loader or _load_eval_config_page,
            postgres_snapshot=page_loader is None,
        )


class SimulationEvalConfigSourceAdapter(_BoundedSourceAdapter):
    def __init__(self, *, page_loader: SourcePageLoader | None = None) -> None:
        super().__init__(
            source_adapter=SourceAdapter.SIMULATION_EVAL_CONFIG,
            page_loader=page_loader or _load_simulation_eval_config_page,
            postgres_snapshot=page_loader is None,
        )


class AnnotationLabelSourceAdapter(_BoundedSourceAdapter):
    def __init__(self, *, page_loader: SourcePageLoader | None = None) -> None:
        super().__init__(
            source_adapter=SourceAdapter.ANNOTATION_LABEL,
            page_loader=page_loader or _load_annotation_label_page,
            postgres_snapshot=page_loader is None,
        )


class DatasetColumnSourceAdapter(_BoundedSourceAdapter):
    def __init__(self, *, page_loader: SourcePageLoader | None = None) -> None:
        super().__init__(
            source_adapter=SourceAdapter.DATASET_COLUMN,
            page_loader=page_loader or _load_dataset_column_page,
            postgres_snapshot=page_loader is None,
        )


class SpanAttributeDefinitionSourceAdapter(_BoundedSourceAdapter):
    """Project deterministic workspace-level key/type unions from CH values.

    The injected loader must pin ``catalog_revision`` and return a complete
    project/type union for each key; raw Kafka batches must never define a
    property independently because their local type order is nondeterministic.
    """

    isolation_level = "revision_pinned_clickhouse"

    def __init__(
        self,
        *,
        group_page_loader: SpanAttributeGroupPageLoader,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        def load_page(
            *,
            context: PostgresSnapshotContext,
            cursor: SourceKeysetCursor | None,
            limit: int,
        ) -> Sequence[SourceDefinitionRecord]:
            groups = tuple(
                group_page_loader(
                    context=context,
                    cursor=cursor,
                    limit=limit,
                )
            )
            if any(
                group.catalog_revision != context.catalog_revision for group in groups
            ):
                raise PropertySourceError(
                    "span attribute group is not pinned to the build revision"
                )
            records = tuple(_span_attribute_record(group) for group in groups)
            _validate_page(
                records,
                adapter=SourceAdapter.SPAN_ATTRIBUTE,
                cursor=cursor,
                max_rows=limit,
            )
            return records

        super().__init__(
            source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
            page_loader=load_page,
            postgres_snapshot=False,
            monotonic=monotonic,
        )


def default_postgres_source_adapters() -> tuple[DefinitionSourceAdapter, ...]:
    return (
        EvalTemplateSourceAdapter(),
        EvalConfigSourceAdapter(),
        SimulationEvalConfigSourceAdapter(),
        AnnotationLabelSourceAdapter(),
        DatasetColumnSourceAdapter(),
    )


def _load_eval_template_page(
    *,
    context: PostgresSnapshotContext,
    cursor: SourceKeysetCursor | None,
    limit: int,
) -> Sequence[SourceDefinitionRecord]:
    from django.db.models import Exists, Max, OuterRef, Q, Subquery
    from django.db.models.functions import Greatest

    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.project import Project

    # Keep relationship change detection separate from active visibility.  A
    # deleted final config/project must still advance the global template's
    # keyset watermark so incremental reconciliation can remove its stale
    # project binding.  ``all_objects`` is intentional on both sides.
    scoped_projects = (
        Project.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .filter(
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            id__in=context.project_ids,
        )
        .order_by()
        .values("id")
    )
    relationships = (
        CustomEvalConfig.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .filter(
            eval_template_id=OuterRef("pk"),
            project_id__in=Subquery(scoped_projects),
        )
        .annotate(
            _relationship_updated_at=Greatest(
                "updated_at",
                "deleted_at",
                "project__updated_at",
                "project__deleted_at",
            )
        )
        .filter(_relationship_updated_at__lte=context.snapshot_cutoff)
    )
    # Aggregate in PostgreSQL instead of sorting/materializing raw relationship
    # rows. The result cardinality is one timestamp per template regardless of
    # how many configs exist in the project history.
    latest_relationship_update = (
        relationships.order_by()
        .values("eval_template_id")
        .annotate(_latest_relationship_updated_at=Max("_relationship_updated_at"))
        .values("_latest_relationship_updated_at")[:1]
    )
    queryset = (
        EvalTemplate.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .annotate(
            _has_workspace_relationship=Exists(relationships),
            _relationship_updated_at=Subquery(latest_relationship_update),
            _catalog_updated_at=Greatest(
                *_lifecycle_timestamp_fields(""),
                "_relationship_updated_at",
            ),
        )
        .filter(_catalog_updated_at__lte=context.snapshot_cutoff)
        .filter(
            Q(
                organization_id=context.organization_id,
                workspace_id=context.workspace_id,
            )
            | Q(
                organization_id=context.organization_id,
                workspace_id__isnull=True,
            )
            | Q(organization_id__isnull=True, _has_workspace_relationship=True)
        )
    )
    rows = _keyset_values(
        queryset,
        cursor=cursor,
        limit=limit,
        order_field="_catalog_updated_at",
        fields=(
            "id",
            "name",
            "config",
            "choices",
            "organization_id",
            "workspace_id",
            "deleted",
            "deleted_at",
            "updated_at",
            "_catalog_updated_at",
        ),
    )
    project_ids, relationship_versions = _eval_template_projects(
        context=context,
        template_ids=tuple(str(row["id"]) for row in rows),
    )
    return tuple(
        _make_source_record(
            source_adapter=SourceAdapter.EVAL_TEMPLATE,
            source_entity_id=str(row["id"]),
            source_updated_at=row["_catalog_updated_at"],
            definition=_eval_definition(row, kind=PropertyKind.EVAL_TEMPLATE),
            visibilities=tuple(
                sorted(
                    {
                        *(
                            (
                                VisibilityBinding(
                                    VisibilityScope.WORKSPACE_DEFAULT,
                                    context.workspace_id,
                                ),
                            )
                            if row["organization_id"] is not None
                            else ()
                        ),
                        *(
                            VisibilityBinding(
                                VisibilityScope.PROJECT,
                                project_id,
                            )
                            for project_id in project_ids.get(str(row["id"]), ())
                        ),
                    },
                    key=lambda item: (item.scope, item.visibility_id),
                )
            ),
            is_deleted=bool(row["deleted"]),
            deleted_at=_deleted_at(row),
            dependency_versions=relationship_versions.get(str(row["id"]), ()),
        )
        for row in rows
    )


def _load_eval_config_page(
    *,
    context: PostgresSnapshotContext,
    cursor: SourceKeysetCursor | None,
    limit: int,
) -> Sequence[SourceDefinitionRecord]:
    from django.db.models.functions import Greatest

    from tracer.models.custom_eval_config import CustomEvalConfig

    queryset = (
        CustomEvalConfig.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .annotate(
            _catalog_updated_at=Greatest(
                *_lifecycle_timestamp_fields("", "project", "eval_template"),
            )
        )
        .filter(
            project__organization_id=context.organization_id,
            project__workspace_id=context.workspace_id,
            project_id__in=context.project_ids,
            _catalog_updated_at__lte=context.snapshot_cutoff,
        )
    )
    rows = _keyset_values(
        queryset,
        cursor=cursor,
        limit=limit,
        order_field="_catalog_updated_at",
        fields=(
            "id",
            "name",
            "project_id",
            "project__updated_at",
            "project__deleted_at",
            "project__deleted",
            "eval_template_id",
            "eval_template__name",
            "eval_template__config",
            "eval_template__choices",
            "eval_template__deleted",
            "eval_template__updated_at",
            "eval_template__deleted_at",
            "deleted",
            "deleted_at",
            "updated_at",
            "_catalog_updated_at",
        ),
    )
    return tuple(
        _make_source_record(
            source_adapter=SourceAdapter.EVAL_CONFIG,
            source_entity_id=str(row["id"]),
            source_updated_at=row["_catalog_updated_at"],
            definition=_eval_definition(row, kind=PropertyKind.EVAL_CONFIG),
            visibilities=(
                VisibilityBinding(VisibilityScope.PROJECT, str(row["project_id"])),
            ),
            is_deleted=bool(
                row["deleted"]
                or row["project__deleted"]
                or row["eval_template__deleted"]
            ),
            deleted_at=_deleted_at(row),
            dependency_versions=_dependency_versions(
                row,
                "project",
                "eval_template",
            ),
        )
        for row in rows
    )


def _load_simulation_eval_config_page(
    *,
    context: PostgresSnapshotContext,
    cursor: SourceKeysetCursor | None,
    limit: int,
) -> Sequence[SourceDefinitionRecord]:
    from django.db.models.functions import Greatest

    from simulate.models import SimulateEvalConfig

    queryset = (
        SimulateEvalConfig.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .annotate(
            _catalog_updated_at=Greatest(
                *_lifecycle_timestamp_fields(
                    "",
                    "run_test",
                    "run_test__agent_definition",
                    "eval_template",
                ),
            )
        )
        .filter(
            run_test__organization_id=context.organization_id,
            run_test__workspace_id=context.workspace_id,
            run_test__agent_definition_id__isnull=False,
            run_test__agent_definition__organization_id=context.organization_id,
            run_test__agent_definition__workspace_id=context.workspace_id,
            _catalog_updated_at__lte=context.snapshot_cutoff,
        )
    )
    rows = _keyset_values(
        queryset,
        cursor=cursor,
        limit=limit,
        order_field="_catalog_updated_at",
        fields=(
            "id",
            "name",
            "run_test__agent_definition_id",
            "run_test__deleted",
            "run_test__updated_at",
            "run_test__deleted_at",
            "run_test__agent_definition__deleted",
            "run_test__agent_definition__updated_at",
            "run_test__agent_definition__deleted_at",
            "eval_template_id",
            "eval_template__name",
            "eval_template__config",
            "eval_template__choices",
            "eval_template__deleted",
            "eval_template__updated_at",
            "eval_template__deleted_at",
            "deleted",
            "deleted_at",
            "updated_at",
            "_catalog_updated_at",
        ),
    )
    return tuple(
        _make_source_record(
            source_adapter=SourceAdapter.SIMULATION_EVAL_CONFIG,
            source_entity_id=str(row["id"]),
            source_updated_at=row["_catalog_updated_at"],
            definition=_eval_definition(
                row,
                kind=PropertyKind.EVAL_CONFIG,
                primary_source="simulation",
            ),
            visibilities=(
                VisibilityBinding(
                    VisibilityScope.AGENT_DEFINITION,
                    str(row["run_test__agent_definition_id"]),
                ),
            ),
            is_deleted=bool(
                row["deleted"]
                or row["run_test__deleted"]
                or row["run_test__agent_definition__deleted"]
                or row["eval_template__deleted"]
            ),
            deleted_at=_deleted_at(row),
            dependency_versions=_dependency_versions(
                row,
                "run_test",
                "run_test__agent_definition",
                "eval_template",
            ),
        )
        for row in rows
    )


def _load_annotation_label_page(
    *,
    context: PostgresSnapshotContext,
    cursor: SourceKeysetCursor | None,
    limit: int,
) -> Sequence[SourceDefinitionRecord]:
    from django.db.models import Max, OuterRef, Q, Subquery
    from django.db.models.functions import Greatest

    from model_hub.models.develop_annotations import AnnotationsLabels
    from model_hub.models.score import Score
    from tracer.models.project import Project
    from tracer.services.annotation_label_source import AnnotationLabelScoresProjectPG

    scoped_projects = (
        Project.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .filter(
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            id__in=context.project_ids,
        )
        .order_by()
        .values("id")
    )
    score_project_state = (
        Project.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .filter(
            id=OuterRef("tracer_project_id"),
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            id__in=context.project_ids,
        )
        .order_by()
        .annotate(
            _catalog_updated_at=Greatest(*_lifecycle_timestamp_fields("")),
        )
    )
    related_score = (
        Score.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .filter(
            AnnotationLabelScoresProjectPG._trace_span_scope(),
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            label_id=OuterRef("pk"),
            tracer_project_id__in=Subquery(scoped_projects),
        )
        .annotate(
            _project_updated_at=Subquery(
                score_project_state.values("_catalog_updated_at")[:1]
            )
        )
        .annotate(
            _relationship_updated_at=Greatest(
                *_lifecycle_timestamp_fields(""),
                "_project_updated_at",
            )
        )
        .filter(_relationship_updated_at__lte=context.snapshot_cutoff)
    )
    # One aggregate row replaces the previous raw-score sort. This remains
    # index-scoped by label and the bounded project set while avoiding work
    # proportional to the number of Score rows returned to Python.
    latest_score_update = (
        related_score.order_by()
        .values("label_id")
        .annotate(_latest_relationship_updated_at=Max("_relationship_updated_at"))
        .values("_latest_relationship_updated_at")[:1]
    )
    queryset = (
        AnnotationsLabels.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .annotate(
            _score_relationship_updated_at=Subquery(latest_score_update),
            _catalog_updated_at=Greatest(
                *_lifecycle_timestamp_fields("", "project"),
                "_score_relationship_updated_at",
            ),
        )
        .filter(
            organization_id=context.organization_id,
            _catalog_updated_at__lte=context.snapshot_cutoff,
        )
        .filter(Q(workspace_id=context.workspace_id) | Q(workspace_id__isnull=True))
    )
    queryset = queryset.filter(
        Q(project_id__isnull=True)
        | Q(
            project_id__in=context.project_ids,
            project__organization_id=context.organization_id,
            project__workspace_id=context.workspace_id,
        )
    )
    rows = _keyset_values(
        queryset,
        cursor=cursor,
        limit=limit,
        order_field="_catalog_updated_at",
        fields=(
            "id",
            "name",
            "type",
            "settings",
            "project_id",
            "project__deleted",
            "project__updated_at",
            "project__deleted_at",
            "deleted",
            "deleted_at",
            "updated_at",
            "_catalog_updated_at",
        ),
    )
    project_ids, relation_versions = _annotation_score_projects(
        context=context,
        label_ids=tuple(str(row["id"]) for row in rows),
    )
    records = []
    for row in rows:
        label_id = str(row["id"])
        visibilities: set[VisibilityBinding] = set()
        if row["project_id"]:
            visibilities.add(
                VisibilityBinding(VisibilityScope.PROJECT, str(row["project_id"]))
            )
        else:
            visibilities.add(
                VisibilityBinding(
                    VisibilityScope.WORKSPACE_DEFAULT,
                    context.workspace_id,
                )
            )
        visibilities.update(
            VisibilityBinding(VisibilityScope.PROJECT, project_id)
            for project_id in project_ids.get(label_id, ())
        )
        records.append(
            _make_source_record(
                source_adapter=SourceAdapter.ANNOTATION_LABEL,
                source_entity_id=label_id,
                source_updated_at=row["_catalog_updated_at"],
                definition=_annotation_definition(row),
                visibilities=tuple(
                    sorted(
                        visibilities, key=lambda item: (item.scope, item.visibility_id)
                    )
                ),
                is_deleted=bool(row["deleted"] or row["project__deleted"]),
                deleted_at=_deleted_at(row),
                dependency_versions=(
                    *_dependency_versions(row, "project"),
                    *relation_versions.get(label_id, ()),
                ),
            )
        )
    return tuple(records)


def _load_dataset_column_page(
    *,
    context: PostgresSnapshotContext,
    cursor: SourceKeysetCursor | None,
    limit: int,
) -> Sequence[SourceDefinitionRecord]:
    from django.db.models.functions import Greatest

    from model_hub.models.develop_dataset import Column

    queryset = (
        Column.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .annotate(
            _catalog_updated_at=Greatest(
                *_lifecycle_timestamp_fields("", "dataset"),
            )
        )
        .filter(
            dataset__organization_id=context.organization_id,
            dataset__workspace_id=context.workspace_id,
            dataset_id__isnull=False,
            _catalog_updated_at__lte=context.snapshot_cutoff,
        )
    )
    rows = _keyset_values(
        queryset,
        cursor=cursor,
        limit=limit,
        order_field="_catalog_updated_at",
        fields=(
            "id",
            "name",
            "data_type",
            "dataset_id",
            "dataset__deleted",
            "dataset__updated_at",
            "dataset__deleted_at",
            "deleted",
            "deleted_at",
            "updated_at",
            "_catalog_updated_at",
        ),
    )
    return tuple(
        _make_source_record(
            source_adapter=SourceAdapter.DATASET_COLUMN,
            source_entity_id=str(row["id"]),
            source_updated_at=row["_catalog_updated_at"],
            definition=_dataset_column_definition(row),
            visibilities=(
                VisibilityBinding(VisibilityScope.DATASET, str(row["dataset_id"])),
            ),
            is_deleted=bool(row["deleted"] or row["dataset__deleted"]),
            deleted_at=_deleted_at(row),
            dependency_versions=_dependency_versions(row, "dataset"),
        )
        for row in rows
    )


def _keyset_values(
    queryset: Any,
    *,
    cursor: SourceKeysetCursor | None,
    limit: int,
    fields: tuple[str, ...],
    order_field: str = "updated_at",
) -> list[Mapping[str, Any]]:
    if cursor is not None:
        from django.db.models import Q

        queryset = queryset.filter(
            Q(**{f"{order_field}__gt": cursor.updated_at})
            | Q(
                **{
                    order_field: cursor.updated_at,
                    "id__gt": cursor.source_entity_id,
                }
            )
        )
    return list(queryset.order_by(order_field, "id").values(*fields)[:limit])


def _scoped_project_relationship_states(
    context: PostgresSnapshotContext,
) -> dict[str, tuple[datetime, bool, datetime | None]]:
    """Load the finite authorized project lifecycle once per source page."""

    from django.db.models.functions import Greatest

    from tracer.models.project import Project

    rows = (
        Project.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .annotate(
            _catalog_updated_at=Greatest(*_lifecycle_timestamp_fields("")),
        )
        .filter(
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            id__in=context.project_ids,
            _catalog_updated_at__lte=context.snapshot_cutoff,
        )
        .order_by()
        .values_list("id", "updated_at", "deleted", "deleted_at")
    )
    return {
        str(project_id): (updated_at, bool(deleted), deleted_at)
        for project_id, updated_at, deleted, deleted_at in rows
    }


def _group_project_relationships(
    rows: Sequence[tuple[Any, ...]],
    *,
    project_states: Mapping[str, tuple[datetime, bool, datetime | None]],
    relation_name: str,
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
]:
    """Resolve visibility from bounded entity/project aggregates.

    ``rows`` contains one row per entity/project pair, never one row per Score
    or eval config. Active cardinality is all the visibility contract needs;
    the latest relationship and project clocks advance incremental source
    watermarks when that cardinality changes.
    """

    projects: dict[str, set[str]] = {}
    versions: dict[str, set[str]] = {}
    for entity_id, project_id, active_count, relationship_updated_at in rows:
        entity_key = str(entity_id)
        project_key = str(project_id)
        project_state = project_states.get(project_key)
        if project_state is None:
            continue
        project_updated_at, project_deleted, project_deleted_at = project_state
        active_count = int(active_count or 0)
        if active_count > 0 and not project_deleted:
            projects.setdefault(entity_key, set()).add(project_key)
        versions.setdefault(entity_key, set()).update(
            (
                ":".join(
                    (
                        relation_name,
                        project_key,
                        str(active_count),
                        _timestamp_text(relationship_updated_at),
                    )
                ),
                ":".join(
                    (
                        "project",
                        project_key,
                        _timestamp_text(project_updated_at),
                        str(project_deleted).lower(),
                        (
                            _timestamp_text(project_deleted_at)
                            if project_deleted_at
                            else ""
                        ),
                    )
                ),
            )
        )
    return (
        {key: tuple(sorted(values)) for key, values in projects.items()},
        {key: tuple(sorted(values)) for key, values in versions.items()},
    )


def _annotation_score_projects(
    *,
    context: PostgresSnapshotContext,
    label_ids: tuple[str, ...],
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
]:
    if not label_ids:
        return {}, {}
    from django.db.models import Count, Max, Q
    from django.db.models.functions import Greatest

    from model_hub.models.score import Score
    from tracer.services.annotation_label_source import AnnotationLabelScoresProjectPG

    project_states = _scoped_project_relationship_states(context)
    if not project_states:
        return {}, {}
    relationship_clock = Greatest(*_lifecycle_timestamp_fields(""))
    pairs = list(
        Score.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .filter(
            AnnotationLabelScoresProjectPG._trace_span_scope(),
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            label_id__in=label_ids,
            tracer_project_id__isnull=False,
            tracer_project_id__in=tuple(project_states),
        )
        .annotate(_relationship_updated_at=relationship_clock)
        .filter(_relationship_updated_at__lte=context.snapshot_cutoff)
        .order_by()
        .values("label_id", "tracer_project_id")
        .annotate(
            _active_count=Count("id", filter=Q(deleted=False)),
            _latest_relationship_updated_at=Max("_relationship_updated_at"),
        )
        .values_list(
            "label_id",
            "tracer_project_id",
            "_active_count",
            "_latest_relationship_updated_at",
        )
    )
    return _group_project_relationships(
        pairs,
        project_states=project_states,
        relation_name="scores",
    )


def _eval_template_projects(
    *,
    context: PostgresSnapshotContext,
    template_ids: tuple[str, ...],
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
]:
    if not template_ids:
        return {}, {}
    from django.db.models import Count, Max, Q
    from django.db.models.functions import Greatest

    from tracer.models.custom_eval_config import CustomEvalConfig

    project_states = _scoped_project_relationship_states(context)
    if not project_states:
        return {}, {}
    relationship_clock = Greatest(*_lifecycle_timestamp_fields(""))

    rows = list(
        CustomEvalConfig.all_objects.using(PROPERTY_SOURCE_DB_ALIAS)
        .filter(
            eval_template_id__in=template_ids,
            project_id__in=tuple(project_states),
        )
        .annotate(_relationship_updated_at=relationship_clock)
        .filter(_relationship_updated_at__lte=context.snapshot_cutoff)
        .order_by()
        .values("eval_template_id", "project_id")
        .annotate(
            _active_count=Count("id", filter=Q(deleted=False)),
            _latest_relationship_updated_at=Max("_relationship_updated_at"),
        )
        .values_list(
            "eval_template_id",
            "project_id",
            "_active_count",
            "_latest_relationship_updated_at",
        )
    )
    return _group_project_relationships(
        rows,
        project_states=project_states,
        relation_name="eval_configs",
    )


def _eval_definition(
    row: Mapping[str, Any],
    *,
    kind: PropertyKind,
    primary_source: str = "all",
) -> PropertyDefinition:
    template_config = row.get("config") or row.get("eval_template__config") or {}
    choices = row.get("choices") or row.get("eval_template__choices") or []
    output_type = _eval_output_type(template_config)
    name = str(row["id"])
    display_name = str(row.get("name") or row.get("eval_template__name") or name)
    details: dict[str, Any] = {}
    normalized_choices = tuple(choice for choice in choices if choice is not None)
    if output_type == "PASS_FAIL":
        normalized_choices = ("Passed", "Failed")
    if normalized_choices:
        details["choices"] = normalized_choices
    template_id = row.get("eval_template_id")
    if template_id:
        details["eval_template_id"] = str(template_id)
    return PropertyDefinition(
        property_kind=kind,
        source_key=name,
        category=PropertyCategory.EVAL_METRIC,
        category_rank=1,
        source_rank=0 if primary_source == "all" else 1,
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
    """Keep malformed legacy names from aborting a workspace projection."""

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
    options = settings.get("options") if isinstance(settings, Mapping) else None
    choice_options = configured_value_options(options)
    if choice_options:
        details["choices"] = tuple(option["label"] for option in choice_options)
        details["choice_options"] = choice_options
    elif label_type == "thumbs_up_down":
        details["choices"] = ("Thumbs Up", "Thumbs Down")
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


def _span_attribute_record(group: SpanAttributeKeyGroup) -> SourceDefinitionRecord:
    value_type = resolve_span_attribute_type(group.observed_types)
    observed_types = tuple(sorted(set(group.observed_types)))
    # Dashboard metric pickers request role=metric and can aggregate only keys
    # whose complete workspace type union is numeric.  Keep mixed-type keys as
    # dimensions: their resolved JSON contract is valid for filtering but not
    # for numeric aggregation.
    role = PropertyRole.METRIC if value_type == "number" else PropertyRole.DIMENSION
    definition = PropertyDefinition(
        property_kind=PropertyKind.CUSTOM_ATTRIBUTE,
        source_key=group.attribute_key,
        category=PropertyCategory.CUSTOM_ATTRIBUTE,
        category_rank=3,
        source_rank=0,
        definition_source="span_attribute_value_catalog",
        primary_source="traces",
        source_tokens=("attribute", "span", "traces", *group.observed_types),
        value_adapter="span_attribute_value",
        name=group.attribute_key,
        display_name=group.attribute_key,
        value_type=value_type,
        output_type=value_type,
        role=role,
        details={
            "allowed_aggregations": _span_attribute_aggregations(observed_types),
            "attribute_types": observed_types,
            "attribute_types_exact": True,
            "data_type": value_type,
        },
    )
    return _make_source_record(
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        source_entity_id=group.attribute_key,
        # The keyset is ingestion/control ordered. Event-time first/last_seen
        # are metadata only, so a late span with an old start_time cannot fall
        # behind the reconciler watermark.
        source_updated_at=group.revision_fenced_at,
        definition=definition,
        visibilities=tuple(
            VisibilityBinding(VisibilityScope.PROJECT, project_id)
            for project_id in group.project_ids
        ),
        first_seen=group.first_seen,
        last_seen=group.last_seen,
        dependency_versions=(
            f"catalog_revision:{group.catalog_revision}",
            *(f"type:{value}" for value in group.observed_types),
        ),
    )


def _eval_output_type(config: object) -> str:
    if not isinstance(config, Mapping):
        return "SCORE"
    output = str(config.get("output") or "").upper().replace("/", "_").replace(" ", "_")
    return output if output in {"PASS_FAIL", "CHOICE", "CHOICES", "SCORE"} else "SCORE"


def _make_source_record(
    *,
    source_adapter: SourceAdapter,
    source_entity_id: str,
    source_updated_at: datetime,
    definition: PropertyDefinition,
    visibilities: tuple[VisibilityBinding, ...],
    is_deleted: bool = False,
    deleted_at: datetime | None = None,
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
    dependency_versions: tuple[str, ...] = (),
) -> SourceDefinitionRecord:
    if source_updated_at.tzinfo is None:
        raise PropertySourceError("source updated_at is not timezone-aware")
    source_updated_at = source_updated_at.astimezone(UTC)
    if deleted_at is not None:
        if deleted_at.tzinfo is None:
            raise PropertySourceError("source deleted_at is not timezone-aware")
        deleted_at = deleted_at.astimezone(UTC)
    if first_seen is not None:
        if first_seen.tzinfo is None:
            raise PropertySourceError("source first_seen is not timezone-aware")
        first_seen = first_seen.astimezone(UTC)
    if last_seen is not None:
        if last_seen.tzinfo is None:
            raise PropertySourceError("source last_seen is not timezone-aware")
        last_seen = last_seen.astimezone(UTC)
    canonical = canonicalize_definition(definition)
    fingerprint = framed_sha256(
        "futureagi.property-catalog.source-record.v1",
        source_adapter,
        source_entity_id,
        canonical.definition_sha256,
        *(f"{item.scope}:{item.visibility_id}" for item in visibilities),
        is_deleted,
        deleted_at.isoformat(timespec="microseconds") if deleted_at else None,
        *sorted(dependency_versions),
    )
    return SourceDefinitionRecord(
        source_adapter=source_adapter,
        source_entity_id=source_entity_id,
        source_updated_at=source_updated_at,
        definition=definition,
        visibilities=visibilities,
        source_fingerprint=fingerprint,
        is_deleted=is_deleted,
        deleted_at=deleted_at,
        first_seen=first_seen,
        last_seen=last_seen,
    )


def _deleted_at(row: Mapping[str, Any]) -> datetime | None:
    if not (
        row.get("deleted")
        or row.get("project__deleted")
        or row.get("run_test__deleted")
        or row.get("run_test__agent_definition__deleted")
        or row.get("eval_template__deleted")
        or row.get("dataset__deleted")
    ):
        return None
    candidates = tuple(
        value
        for key, value in row.items()
        if key == "deleted_at" or key.endswith("__deleted_at")
        if isinstance(value, datetime)
    )
    if not candidates:
        candidates = tuple(
            value
            for key, value in row.items()
            if key == "updated_at" or key.endswith("__updated_at")
            if isinstance(value, datetime)
        )
    if not candidates:
        raise PropertySourceError("deleted source row has no deletion timestamp")
    return max(candidates)


def _dependency_versions(
    row: Mapping[str, Any],
    *prefixes: str,
) -> tuple[str, ...]:
    versions: list[str] = []
    for prefix in prefixes:
        for suffix in ("updated_at", "deleted_at"):
            value = row.get(f"{prefix}__{suffix}")
            if isinstance(value, datetime):
                versions.append(f"{prefix}:{suffix}:{_timestamp_text(value)}")
        deleted = row.get(f"{prefix}__deleted")
        if deleted is not None:
            versions.append(f"{prefix}:deleted:{bool(deleted)}")
    return tuple(sorted(versions))


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise PropertySourceError("source dependency timestamp is not timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _slice_records(
    records: Sequence[SourceDefinitionRecord],
    *,
    cursor: SourceKeysetCursor | None,
    limit: int,
) -> tuple[SourceDefinitionRecord, ...]:
    return tuple(
        record for record in records if cursor is None or record.cursor > cursor
    )[:limit]


def _validate_page(
    records: Sequence[SourceDefinitionRecord],
    *,
    adapter: SourceAdapter,
    cursor: SourceKeysetCursor | None,
    max_rows: int,
) -> None:
    if len(records) > max_rows:
        raise PropertySourceError("source loader exceeded requested row limit")
    cursors = [record.cursor for record in records]
    if cursors != sorted(cursors) or len(cursors) != len(set(cursors)):
        raise PropertySourceError("source loader returned non-keyset order")
    if cursor is not None and any(item <= cursor for item in cursors):
        raise PropertySourceError("source loader did not advance its keyset")
    if any(record.source_adapter is not adapter for record in records):
        raise PropertySourceError("source loader returned another adapter's row")


@contextmanager
def _django_repeatable_read_snapshot(
    *,
    deadline: float,
    statement_timeout_ms: int,
    monotonic: Callable[[], float],
) -> Iterator[None]:
    from django.db import connections, transaction

    connection = connections[PROPERTY_SOURCE_DB_ALIAS]

    if connection.vendor != "postgresql":
        raise PropertySourceError("property sources require PostgreSQL")
    if connection.in_atomic_block:
        raise PropertySourceError(
            "property source snapshot cannot inherit an existing transaction"
        )

    def bounded_execute(execute, sql, params, many, context):  # type: ignore[no-untyped-def]
        wall_remaining_ms = int((deadline - monotonic()) * 1000)
        if wall_remaining_ms <= 0:
            raise PropertySourceDeadlineExceeded("property source deadline exceeded")
        remaining_ms = min(statement_timeout_ms, wall_remaining_ms)
        context["cursor"].cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (str(remaining_ms),),
        )
        result = execute(sql, params, many, context)
        _require_time(deadline, monotonic)
        return result

    with transaction.atomic(using=PROPERTY_SOURCE_DB_ALIAS):
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        with connection.execute_wrapper(bounded_execute):
            yield


def _require_time(deadline: float, monotonic: Callable[[], float]) -> None:
    if monotonic() >= deadline:
        raise PropertySourceDeadlineExceeded("property source deadline exceeded")


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")


__all__ = [
    "AnnotationLabelSourceAdapter",
    "DatasetColumnSourceAdapter",
    "DefinitionSourceAdapter",
    "EvalConfigSourceAdapter",
    "EvalTemplateSourceAdapter",
    "PropertySourceDeadlineExceeded",
    "PropertySourceError",
    "SimulationEvalConfigSourceAdapter",
    "SpanAttributeDefinitionSourceAdapter",
    "SpanAttributeKeyGroup",
    "SourceDefinitionRecord",
    "SourceKeysetCursor",
    "SourceReadBudget",
    "SourceSnapshot",
    "SystemManifestAdapter",
    "default_postgres_source_adapters",
    "postgres_revision_snapshot",
]
