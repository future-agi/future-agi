"""Typed immutable models for unified property-catalog projection."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .codec import (
    MAX_IDENTITY_COMPONENT_BYTES,
    ZERO_UUID,
    canonical_json,
    canonical_json_sha256,
    canonical_uuid,
    casefold_text,
    combine_search_text,
    framed_sha256,
    require_sha256,
    stable_property_id,
    validate_text,
)


class PropertyKind(StrEnum):
    SYSTEM_ATTRIBUTE = "system_attribute"
    CUSTOM_ATTRIBUTE = "custom_attribute"
    EVAL_TEMPLATE = "eval_template"
    EVAL_CONFIG = "eval_config"
    ANNOTATION = "annotation"
    DATASET_COLUMN = "dataset_column"


class PropertyCategory(StrEnum):
    SYSTEM_METRIC = "system_metric"
    EVAL_METRIC = "eval_metric"
    ANNOTATION_METRIC = "annotation_metric"
    CUSTOM_ATTRIBUTE = "custom_attribute"
    CUSTOM_COLUMN = "custom_column"


class PropertyRole(StrEnum):
    METRIC = "metric"
    DIMENSION = "dimension"


class VisibilityScope(StrEnum):
    ALWAYS = "always"
    WORKSPACE_DEFAULT = "workspace_default"
    PROJECT = "project"
    AGENT_DEFINITION = "agent_definition"
    DATASET = "dataset"


class SourceAdapter(StrEnum):
    SYSTEM_MANIFEST = "system_manifest"
    SPAN_ATTRIBUTE = "span_attribute"
    EVAL_TEMPLATE = "eval_template"
    EVAL_CONFIG = "eval_config"
    SIMULATION_EVAL_CONFIG = "simulation_eval_config"
    ANNOTATION_LABEL = "annotation_label"
    DATASET_COLUMN = "dataset_column"


class EnvelopeOutcome(StrEnum):
    COMMITTED = "committed"
    GAP = "gap"


DETAIL_FIELD_ALLOWLIST = frozenset(
    {
        "unit",
        "choices",
        "choice_options",
        "allowed_aggregations",
        "data_type",
        "eval_template_id",
        "attribute_types",
        "attribute_types_exact",
    }
)


@dataclass(frozen=True, slots=True)
class PropertyDefinition:
    """Adapter-owned definition before tenant visibility is projected."""

    property_kind: PropertyKind
    source_key: str
    category: PropertyCategory
    category_rank: int
    source_rank: int
    definition_source: str
    primary_source: str
    source_tokens: tuple[str, ...]
    value_adapter: str
    name: str
    display_name: str
    value_type: str
    output_type: str
    role: PropertyRole
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.property_kind, PropertyKind):
            raise TypeError("property_kind must be a PropertyKind")
        if not isinstance(self.category, PropertyCategory):
            raise TypeError("category must be a PropertyCategory")
        if not isinstance(self.role, PropertyRole):
            raise TypeError("role must be a PropertyRole")
        if not isinstance(self.source_tokens, tuple):
            raise TypeError("source_tokens must be a tuple")
        _require_uint(self.category_rank, bits=8, field_name="category_rank")
        _require_uint(self.source_rank, bits=16, field_name="source_rank")
        for field_name in (
            "source_key",
            "definition_source",
            "primary_source",
            "value_adapter",
            "name",
            "display_name",
            "value_type",
            "output_type",
        ):
            value = getattr(self, field_name)
            validate_text(
                value,
                field=field_name,
                max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
                allow_empty=field_name in {"primary_source", "output_type"},
            )
        for index, token in enumerate(self.source_tokens):
            validate_text(
                token,
                field=f"source_tokens[{index}]",
                max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
            )
        object.__setattr__(
            self,
            "source_tokens",
            tuple(sorted(set(self.source_tokens))),
        )
        _canonicalize_details(self.details)

    @property
    def property_id(self) -> str:
        return stable_property_id(
            self.property_kind,
            self.source_key,
            primary_source=(
                self.primary_source
                if self.property_kind is PropertyKind.SYSTEM_ATTRIBUTE
                else ""
            ),
        )


@dataclass(frozen=True, slots=True)
class CanonicalDefinition:
    """Canonical, hashed definition shared by all visibility bindings."""

    property_id: str
    property_kind: PropertyKind
    category: PropertyCategory
    category_rank: int
    source_rank: int
    definition_source: str
    primary_source: str
    primary_source_folded: str
    source_tokens: tuple[str, ...]
    value_adapter: str
    name: str
    display_name: str
    sort_name_folded: str
    search_text_folded: str
    value_type: str
    output_type: str
    role: PropertyRole
    definition_json: str
    definition_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.property_kind, PropertyKind):
            raise TypeError("property_kind must be a PropertyKind")
        if not isinstance(self.category, PropertyCategory):
            raise TypeError("category must be a PropertyCategory")
        if not isinstance(self.role, PropertyRole):
            raise TypeError("role must be a PropertyRole")
        _require_uint(self.category_rank, bits=8, field_name="category_rank")
        _require_uint(self.source_rank, bits=16, field_name="source_rank")
        if self.source_tokens != tuple(sorted(set(self.source_tokens))):
            raise ValueError("source_tokens must be sorted and unique")
        require_sha256(self.definition_sha256, field="definition_sha256")
        if canonical_json_sha256(self.definition_json) != self.definition_sha256:
            raise ValueError("definition_sha256 does not match definition_json")
        try:
            decoded = json.loads(self.definition_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("definition_json is invalid") from exc
        if canonical_json(decoded) != self.definition_json:
            raise ValueError("definition_json is not canonical")
        expected_fields = {
            "category": self.category,
            "category_rank": self.category_rank,
            "definition_source": self.definition_source,
            "display_name": self.display_name,
            "name": self.name,
            "output_type": self.output_type,
            "primary_source": self.primary_source,
            "property_id": self.property_id,
            "property_kind": self.property_kind,
            "role": self.role,
            "source_rank": self.source_rank,
            "source_tokens": list(self.source_tokens),
            "value_adapter": self.value_adapter,
            "value_type": self.value_type,
        }
        if set(decoded) != {*expected_fields, "details"} or any(
            decoded.get(key) != value for key, value in expected_fields.items()
        ):
            raise ValueError("definition_json does not match canonical fields")
        if _canonicalize_details(decoded["details"]) != decoded["details"]:
            raise ValueError("definition_json details are not canonical")
        if self.primary_source_folded != self.primary_source.casefold():
            raise ValueError("primary_source_folded does not match primary_source")
        if self.sort_name_folded != self.name.casefold():
            raise ValueError("sort_name_folded does not match name")
        expected_search = combine_search_text(
            self.name,
            self.display_name,
            self.primary_source,
            self.definition_source,
            source_tokens=self.source_tokens,
        )
        if self.search_text_folded != expected_search:
            raise ValueError("search_text_folded does not match definition fields")


@dataclass(frozen=True, slots=True)
class VisibilityBinding:
    scope: VisibilityScope
    visibility_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, VisibilityScope):
            raise TypeError("scope must be a VisibilityScope")
        if self.scope is VisibilityScope.ALWAYS:
            if self.visibility_id != ZERO_UUID:
                raise ValueError("always visibility must use the all-zero UUID")
            return
        object.__setattr__(
            self,
            "visibility_id",
            canonical_uuid(self.visibility_id, field="visibility_id"),
        )


@dataclass(frozen=True, slots=True)
class PropertyBindingRow:
    """One append-only definition-to-visibility state event."""

    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    binding_id: str
    visibility_scope: VisibilityScope
    visibility_id: str
    definition: CanonicalDefinition
    source_adapter: SourceAdapter
    source_entity_id: str
    source_version: int
    source_fingerprint: str
    is_deleted: bool
    deleted_at: datetime | None
    state_sha256: str
    producer_stream_id: str
    producer_sequence: int
    first_seen: datetime | None
    last_seen: datetime | None
    emitted_at: datetime

    def __post_init__(self) -> None:
        _require_uint(
            self.catalog_epoch,
            bits=16,
            field_name="catalog_epoch",
            positive=True,
        )
        _require_uint(
            self.catalog_revision,
            bits=64,
            field_name="catalog_revision",
            positive=True,
        )
        _require_uint(
            self.projection_version,
            bits=16,
            field_name="projection_version",
            positive=True,
        )
        if not isinstance(self.visibility_scope, VisibilityScope):
            raise TypeError("visibility_scope must be a VisibilityScope")
        if not isinstance(self.source_adapter, SourceAdapter):
            raise TypeError("source_adapter must be a SourceAdapter")
        if type(self.is_deleted) is not bool:
            raise TypeError("is_deleted must be a bool")
        _require_uint(
            self.source_version,
            bits=64,
            field_name="source_version",
            positive=True,
        )
        _require_uint(
            self.producer_sequence,
            bits=64,
            field_name="producer_sequence",
            positive=True,
        )
        require_sha256(self.binding_id, field="binding_id")
        require_sha256(self.source_fingerprint, field="source_fingerprint")
        require_sha256(self.state_sha256, field="state_sha256")
        object.__setattr__(
            self,
            "organization_id",
            canonical_uuid(self.organization_id, field="organization_id"),
        )
        object.__setattr__(
            self,
            "workspace_id",
            canonical_uuid(self.workspace_id, field="workspace_id"),
        )
        object.__setattr__(
            self,
            "build_token",
            canonical_uuid(self.build_token, field="build_token"),
        )
        validate_text(
            self.source_entity_id,
            field="source_entity_id",
            max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
        )
        object.__setattr__(
            self,
            "producer_stream_id",
            canonical_uuid(self.producer_stream_id, field="producer_stream_id"),
        )
        normalized_visibility = VisibilityBinding(
            self.visibility_scope,
            self.visibility_id,
        )
        object.__setattr__(self, "visibility_id", normalized_visibility.visibility_id)
        if (
            self.visibility_scope is VisibilityScope.WORKSPACE_DEFAULT
            and self.visibility_id != self.workspace_id
        ):
            raise ValueError("workspace_default visibility_id must equal workspace_id")
        if self.is_deleted != (self.deleted_at is not None):
            raise ValueError(
                "deleted_at must be present exactly when is_deleted is true"
            )
        _require_aware_utc(self.emitted_at, field_name="emitted_at")
        if self.deleted_at is not None:
            _require_aware_utc(self.deleted_at, field_name="deleted_at")
        if (self.first_seen is None) != (self.last_seen is None):
            raise ValueError(
                "first_seen and last_seen must both be set or both be null"
            )
        if self.first_seen is not None and self.last_seen is not None:
            _require_aware_utc(self.first_seen, field_name="first_seen")
            _require_aware_utc(self.last_seen, field_name="last_seen")
            if self.first_seen > self.last_seen:
                raise ValueError("first_seen must not be after last_seen")
        expected_binding_id = make_binding_id(
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            visibility=normalized_visibility,
            property_id=self.definition.property_id,
            source_adapter=self.source_adapter,
        )
        if self.binding_id != expected_binding_id:
            raise ValueError("binding_id does not match binding fields")
        expected_state = make_state_sha256(
            binding_id=self.binding_id,
            definition_sha256=self.definition.definition_sha256,
            source_entity_id=self.source_entity_id,
            source_version=self.source_version,
            source_fingerprint=self.source_fingerprint,
            is_deleted=self.is_deleted,
            deleted_at=self.deleted_at,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
        )
        if self.state_sha256 != expected_state:
            raise ValueError("state_sha256 does not match binding state")

    @property
    def property_id(self) -> str:
        return self.definition.property_id

    @property
    def order_key(self) -> tuple[int, int, str, str, str, str]:
        return (
            self.definition.category_rank,
            self.definition.source_rank,
            self.definition.primary_source_folded,
            self.definition.sort_name_folded,
            self.definition.name,
            self.definition.property_id,
        )


@dataclass(frozen=True, slots=True)
class EnvelopeCounts:
    source_count: int
    definition_count: int
    value_count: int
    tombstone_count: int
    gap_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "source_count",
            "definition_count",
            "value_count",
            "tombstone_count",
            "gap_count",
        ):
            _require_uint(getattr(self, field_name), bits=64, field_name=field_name)


@dataclass(frozen=True, slots=True)
class PropertyCatalogEnvelope:
    """Typed definition batch metadata before transport-specific encoding."""

    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    source_adapter: SourceAdapter
    producer_stream_id: str
    sequence: int
    previous_payload_sha256: str
    source_version: int
    source_fingerprint: str
    source_batch_digest: str
    outcome: EnvelopeOutcome
    counts: EnvelopeCounts
    definitions: tuple[PropertyBindingRow, ...]
    gap_reasons: tuple[str, ...]
    terminal: bool

    def __post_init__(self) -> None:
        if not isinstance(self.source_adapter, SourceAdapter):
            raise TypeError("source_adapter must be a SourceAdapter")
        if not isinstance(self.outcome, EnvelopeOutcome):
            raise TypeError("outcome must be an EnvelopeOutcome")
        if type(self.terminal) is not bool:
            raise TypeError("terminal must be a bool")
        if not isinstance(self.definitions, tuple) or not isinstance(
            self.gap_reasons, tuple
        ):
            raise TypeError("definitions and gap_reasons must be tuples")
        _require_uint(
            self.catalog_epoch,
            bits=16,
            field_name="catalog_epoch",
            positive=True,
        )
        _require_uint(
            self.catalog_revision,
            bits=64,
            field_name="catalog_revision",
            positive=True,
        )
        _require_uint(
            self.projection_version,
            bits=16,
            field_name="projection_version",
            positive=True,
        )
        _require_uint(
            self.sequence,
            bits=64,
            field_name="sequence",
            positive=True,
        )
        _require_uint(
            self.source_version,
            bits=64,
            field_name="source_version",
            positive=True,
        )
        object.__setattr__(
            self,
            "organization_id",
            canonical_uuid(self.organization_id, field="organization_id"),
        )
        object.__setattr__(
            self,
            "workspace_id",
            canonical_uuid(self.workspace_id, field="workspace_id"),
        )
        object.__setattr__(
            self,
            "build_token",
            canonical_uuid(self.build_token, field="build_token"),
        )
        object.__setattr__(
            self,
            "producer_stream_id",
            canonical_uuid(self.producer_stream_id, field="producer_stream_id"),
        )
        require_sha256(self.source_fingerprint, field="source_fingerprint")
        require_sha256(self.source_batch_digest, field="source_batch_digest")
        require_sha256(
            self.previous_payload_sha256,
            field="previous_payload_sha256",
        )
        if self.counts.definition_count != len(self.definitions):
            raise ValueError("definition_count does not match definitions")
        if self.counts.tombstone_count != sum(
            definition.is_deleted for definition in self.definitions
        ):
            raise ValueError("tombstone_count does not match definitions")
        if self.counts.gap_count != len(self.gap_reasons):
            raise ValueError("gap_count does not match gap_reasons")
        for index, reason in enumerate(self.gap_reasons):
            validate_text(
                reason,
                field=f"gap_reasons[{index}]",
                max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
            )
        if self.outcome is EnvelopeOutcome.COMMITTED and self.counts.gap_count:
            raise ValueError("committed envelopes must be gap-free")
        # A gap envelope may retain its fully validated bounded prefix for
        # diagnostics and retry accounting. Qualification rejects any gap
        # chain, so partial rows can never become reader-visible.
        if self.outcome is EnvelopeOutcome.GAP and self.counts.gap_count == 0:
            raise ValueError("gap envelopes must have at least one reason")
        if self.terminal and (
            self.outcome is not EnvelopeOutcome.COMMITTED
            or any(
                (
                    self.counts.source_count,
                    self.counts.definition_count,
                    self.counts.value_count,
                    self.counts.tombstone_count,
                    self.counts.gap_count,
                )
            )
            or self.definitions
            or self.gap_reasons
        ):
            raise ValueError("terminal envelope must be an empty committed fence")
        for definition in self.definitions:
            if (
                definition.organization_id != self.organization_id
                or definition.workspace_id != self.workspace_id
                or definition.catalog_epoch != self.catalog_epoch
                or definition.catalog_revision != self.catalog_revision
                or definition.build_token != self.build_token
                or definition.projection_version != self.projection_version
                or definition.source_adapter is not self.source_adapter
                or definition.producer_stream_id != self.producer_stream_id
                or definition.producer_sequence != self.sequence
                or definition.source_version != self.source_version
            ):
                raise ValueError("envelope contains a definition from another scope")


def canonicalize_definition(definition: PropertyDefinition) -> CanonicalDefinition:
    """Produce the bounded canonical JSON and folded fields for a definition."""

    property_id = definition.property_id
    details = _canonicalize_details(definition.details)
    payload = canonical_json(
        {
            "category": definition.category,
            "category_rank": definition.category_rank,
            "definition_source": definition.definition_source,
            "details": details,
            "display_name": definition.display_name,
            "name": definition.name,
            "output_type": definition.output_type,
            "primary_source": definition.primary_source,
            "property_id": property_id,
            "property_kind": definition.property_kind,
            "role": definition.role,
            "source_rank": definition.source_rank,
            "source_tokens": definition.source_tokens,
            "value_adapter": definition.value_adapter,
            "value_type": definition.value_type,
        }
    )
    return CanonicalDefinition(
        property_id=property_id,
        property_kind=definition.property_kind,
        category=definition.category,
        category_rank=definition.category_rank,
        source_rank=definition.source_rank,
        definition_source=definition.definition_source,
        primary_source=definition.primary_source,
        primary_source_folded=(
            casefold_text(definition.primary_source, field="primary_source")
            if definition.primary_source
            else ""
        ),
        source_tokens=definition.source_tokens,
        value_adapter=definition.value_adapter,
        name=definition.name,
        display_name=definition.display_name,
        sort_name_folded=casefold_text(definition.name, field="name"),
        search_text_folded=combine_search_text(
            definition.name,
            definition.display_name,
            definition.primary_source,
            definition.definition_source,
            source_tokens=definition.source_tokens,
        ),
        value_type=definition.value_type,
        output_type=definition.output_type,
        role=definition.role,
        definition_json=payload,
        definition_sha256=canonical_json_sha256(payload),
    )


def make_binding_id(
    *,
    organization_id: str,
    workspace_id: str,
    visibility: VisibilityBinding,
    property_id: str,
    source_adapter: SourceAdapter,
) -> str:
    """Derive one unambiguous property-to-visibility binding identity."""

    organization_id = canonical_uuid(organization_id, field="organization_id")
    workspace_id = canonical_uuid(workspace_id, field="workspace_id")
    return framed_sha256(
        "futureagi.property-catalog.binding.v1",
        organization_id,
        workspace_id,
        visibility.scope,
        visibility.visibility_id,
        property_id,
        source_adapter,
    )


def make_state_sha256(
    *,
    binding_id: str,
    definition_sha256: str,
    source_entity_id: str,
    source_version: int,
    source_fingerprint: str,
    is_deleted: bool,
    deleted_at: datetime | None,
    first_seen: datetime | None,
    last_seen: datetime | None,
) -> str:
    """Hash all semantic state used for same-version conflict detection."""

    return framed_sha256(
        "futureagi.property-catalog.binding-state.v1",
        binding_id,
        definition_sha256,
        source_entity_id,
        source_version,
        source_fingerprint,
        is_deleted,
        deleted_at.isoformat(timespec="microseconds") if deleted_at else None,
        first_seen.isoformat(timespec="microseconds") if first_seen else None,
        last_seen.isoformat(timespec="microseconds") if last_seen else None,
    )


def _require_aware_utc(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must use a UTC offset")


def _canonicalize_details(details: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(details, Mapping):
        raise TypeError("details must be a mapping")
    unknown = set(details) - DETAIL_FIELD_ALLOWLIST
    if unknown:
        raise ValueError(
            "details contains unsupported or colliding fields: "
            + ", ".join(sorted(str(key) for key in unknown))
        )

    canonical: dict[str, Any] = {}
    for key, value in details.items():
        if key in {"unit", "data_type"}:
            validate_text(
                value,
                field=f"details.{key}",
                max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
                allow_empty=True,
            )
            canonical[key] = value
        elif key == "eval_template_id":
            canonical[key] = canonical_uuid(value, field="details.eval_template_id")
        elif key in {"choices", "choice_options"}:
            if not isinstance(value, (list, tuple)):
                raise TypeError(f"details.{key} must be a list")
            canonical[key] = list(value)
        elif key == "attribute_types":
            if not isinstance(value, (list, tuple)):
                raise TypeError("details.attribute_types must be a list")
            attribute_types: list[str] = []
            for index, attribute_type in enumerate(value):
                validate_text(
                    attribute_type,
                    field=f"details.attribute_types[{index}]",
                    max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
                )
                attribute_types.append(attribute_type)
            if attribute_types != sorted(set(attribute_types)):
                raise ValueError("details.attribute_types must be sorted and unique")
            canonical[key] = attribute_types
        elif key == "attribute_types_exact":
            if type(value) is not bool:
                raise TypeError("details.attribute_types_exact must be a bool")
            canonical[key] = value
        elif key == "allowed_aggregations":
            if not isinstance(value, (list, tuple)):
                raise TypeError("details.allowed_aggregations must be a list")
            aggregations: list[str] = []
            for index, aggregation in enumerate(value):
                validate_text(
                    aggregation,
                    field=f"details.allowed_aggregations[{index}]",
                    max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
                )
                aggregations.append(aggregation)
            canonical[key] = aggregations
    canonical_json({"details": canonical})
    return canonical


def _require_uint(
    value: int,
    *,
    bits: int,
    field_name: str,
    positive: bool = False,
) -> None:
    minimum = 1 if positive else 0
    maximum = (1 << bits) - 1
    if type(value) is not int or not minimum <= value <= maximum:
        qualifier = "positive " if positive else ""
        raise ValueError(f"{field_name} must be a {qualifier}UInt{bits}")
