"""Validated current property definitions and stable identity metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .codec import (
    MAX_IDENTITY_COMPONENT_BYTES,
    canonical_json,
    canonical_uuid,
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
    """One definition already restricted to the current authorized native scope."""

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
                allow_controls=(
                    self.property_kind is PropertyKind.CUSTOM_ATTRIBUTE
                    and field_name in {"source_key", "name", "display_name"}
                ),
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
